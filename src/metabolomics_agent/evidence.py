"""Durable local checkpoints and reports, independent of the future stage router.

An atomic evidence index is the commit record. Unregistered files are never
treated as valid outputs. A stopped process leaves a running attempt that is
reconciled as interrupted when evidence is reopened.
"""

import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

from .evidence_models import EvidenceArtifact, OperationRecord, StageIssue, StageReport
from .serialization import clean_json, dumps


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class EvidenceStore:
    def __init__(self, root: Path, *, recover=False):
        self.root = root.resolve()
        self.active = None
        self.current_operation = None
        self.artifacts = {}
        self.attempts = []
        index = self.root / "evidence_index.json"
        if index.exists():
            data = json.loads(index.read_text())
            if data.get("schema_version") != 1:
                raise ValueError("Unsupported evidence schema version.")
            self.artifacts = {key: EvidenceArtifact.model_validate(value)
                              for key, value in data["artifacts"].items()}
            self.attempts = data["attempts"]
            self.validate()
            if recover:
                self.recover_interrupted()

    def path(self, relative):
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root) or candidate == self.root:
            raise ValueError("Evidence path must be inside the run directory.")
        return candidate

    def atomic_write(self, relative, writer):
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            writer(temporary)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            temporary.replace(target)
            # Persist the directory entry as well as file contents.
            descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def write_json(self, relative, value):
        self.atomic_write(relative, lambda path: path.write_text(dumps(value) + "\n"))

    def _index(self):
        self.write_json("evidence_index.json", {
            "schema_version": 1,
            "artifacts": {key: value.model_dump() for key, value in self.artifacts.items()},
            "attempts": self.attempts,
        })

    def _journal(self, event, **values):
        if self.active is None:
            return
        target = self.path(f"{self._directory()}/operations.jsonl")
        with target.open("a") as handle:
            handle.write(json.dumps(clean_json({"time": now(), "event": event, **values}),
                                    allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def validate(self):
        for artifact in self.artifacts.values():
            target = self.path(artifact.path)
            if not target.is_file() or digest(target) != artifact.sha256:
                raise ValueError(f"Missing or modified evidence artifact: {artifact.id}")
            if artifact.schema_path:
                schema = self.path(artifact.schema_path)
                if not schema.is_file() or digest(schema) != artifact.schema_sha256:
                    raise ValueError(f"Missing or modified table schema: {artifact.id}")
            if any(parent not in self.artifacts for parent in artifact.parent_ids):
                raise ValueError(f"Unknown artifact parent: {artifact.id}")
        for relative in self.attempts:
            StageReport.model_validate_json(self.path(relative).read_text())

    def _directory(self, report=None):
        report = report or self.active
        if report is None:
            raise ValueError("No active evidence attempt.")
        return f"stages/{report.stage_id}/{report.attempt_id}"

    def _save_report(self, report=None):
        report = report or self.active
        directory = self._directory(report)
        manifest = [a.model_dump() for a in self.artifacts.values()
                    if a.stage_id == report.stage_id and a.attempt_id == report.attempt_id]
        self.write_json(f"{directory}/artifact_manifest.json", {
            "schema_version": 1, "artifacts": manifest,
        })
        lines = [f"# {report.stage_id}: {report.attempt_id}", "",
                 f"Tool: `{report.tool}`. Status: **{report.status}**.", "",
                 f"Started: {report.started_at}. Finished: {report.finished_at or 'pending'}.", "",
                 f"Registered outputs: {len(report.output_artifact_ids)}.", "",
                 "## Operations", ""]
        lines += [f"- {op.name}: {op.status}" + (f" — {op.error}" if op.error else "")
                  for op in report.operations]
        lines += ["", "## Checks", ""]
        lines += [f"- {check['code']}: {check['status']}; expected "
                  f"{check['expected']}; observed {check['observed']}" for check in report.checks]
        lines += ["", "## Issues", ""]
        lines += ([f"- [{issue.severity}; {issue.code}] {issue.message}"
                   for issue in report.issues] or [
                       "No issues detected in the recorded checks." if report.no_issues_detected
                       else "No issues recorded; this is not a complete quality assessment."
                   ])
        lines += ["", "## Limitations", "", *[f"- {item}" for item in report.limitations],
                  "", "## Outputs", ""]
        for artifact in manifest:
            relative = os.path.relpath(self.path(artifact["path"]), self.path(directory))
            lines.append(f"- [{artifact['id']}: {artifact['kind']}]({relative})")
        lines += ["", "Numerical checkpoints and individual sample tables remain local.",
                  "See stage_report.json for provenance, checks, and continuation details.", ""]
        self.atomic_write(f"{directory}/stage_report.md",
                          lambda path: path.write_text("\n".join(lines)))
        self.write_json(f"{directory}/stage_report.json", report.model_dump())

    @contextmanager
    def attempt(self, stage_id, tool, *, inputs=(), parameters=None, context=None,
                prompt_snapshot=None):
        if self.active is not None:
            raise ValueError("Nested evidence attempts are not allowed.")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", stage_id):
            raise ValueError("Invalid stage ID.")
        directory = self.root / "stages" / stage_id
        count = max((int(p.name.split("_")[1]) for p in directory.glob("attempt_*")
                     if p.is_dir() and re.fullmatch(r"attempt_\d+", p.name)), default=0)
        report = StageReport(stage_id=stage_id, attempt_id=f"attempt_{count + 1:03d}",
                             tool=tool, started_at=now(), input_artifact_ids=list(inputs),
                             parameters=clean_json(parameters or {}))
        self.active = report
        try:
            self.path(self._directory()).mkdir(parents=True, exist_ok=False)
            self.write_json(f"{self._directory()}/context.json", context or {})
            self.write_json(f"{self._directory()}/prompt_snapshot.json", prompt_snapshot or {
                "applicable": False, "reason": "No model prompt was supplied for this attempt."
            })
            self._save_report()
            self.attempts.append(f"{self._directory()}/stage_report.json")
            self._index()
            self._journal("attempt_started", tool=tool)
            yield report
        except BaseException as error:
            report.status = "failed" if isinstance(error, Exception) else "interrupted"
            report.issues.append(StageIssue(
                code="EXECUTION_FAILED" if report.status == "failed" else "EXECUTION_INTERRUPTED",
                severity="error", source="tool_error", message=str(error) or type(error).__name__,
                blocking=True, evidence_ids=list(report.output_artifact_ids),
            ))
            raise
        finally:
            try:
                if report.status == "running":
                    if any(issue.blocking and issue.resolution == "open" for issue in report.issues):
                        report.status = "blocked"
                    else:
                        report.status = ("completed_with_warnings" if any(
                            issue.severity in {"warning", "error"} for issue in report.issues
                        ) else "completed")
                report.finished_at = now()
                report.no_issues_detected = (False if report.issues else
                                             True if report.checks else None)
                report.continuation = {
                    "outputs_committed": list(report.output_artifact_ids),
                    "requires_review": report.status in {"failed", "interrupted", "blocked"},
                    "routing": "Not implemented until workflow delivery C.",
                }
                self._save_report()
                self._journal("attempt_finished", status=report.status)
            finally:
                self.active = None
                self.current_operation = None

    @contextmanager
    def operation(self, name, *, parameters=None, inputs=()):
        if self.active is None:
            yield
            return
        if self.current_operation is not None:
            raise ValueError("Nested evidence operations are not allowed.")
        record = OperationRecord(id=f"op{len(self.active.operations) + 1:03d}", name=name,
                                 status="running", started_at=now(),
                                 parameters=clean_json(parameters or {}),
                                 input_artifact_ids=list(inputs))
        self.active.operations.append(record)
        self.current_operation = record
        self._save_report()
        self._journal("operation_started", operation=record.model_dump())
        try:
            yield
        except BaseException as error:
            record.status = "failed" if isinstance(error, Exception) else "interrupted"
            record.error = str(error) or type(error).__name__
            raise
        else:
            record.status = "completed"
        finally:
            record.finished_at = now()
            self._save_report()
            self._journal("operation_finished", operation=record.model_dump())
            self.current_operation = None

    def skip(self, name, reason):
        if self.active is not None:
            self.active.operations.append(OperationRecord(
                id=f"op{len(self.active.operations) + 1:03d}", name=name, status="skipped",
                started_at=now(), finished_at=now(), parameters={"reason": reason},
            ))
            self._save_report()

    def check(self, code, expected, observed, *, passed, message, severity="warning"):
        if self.active is not None:
            self.active.checks.append(clean_json({
                "code": code, "status": "passed" if passed else "failed",
                "expected": expected, "observed": observed,
            }))
            if not passed:
                self.active.issues.append(StageIssue(
                    code=code, severity=severity, source="computed_check", message=message,
                    evidence_ids=list(self.active.output_artifact_ids),
                    blocking=severity == "error",
                ))
            self._save_report()

    def _register(self, aid, kind, relative, *, parent_ids=(), plan_id=None, dataset_id=None,
                  scale=None, parameters=None, schema_path=None, rows=None, columns=None,
                  individual_samples=False):
        if aid in self.artifacts:
            raise ValueError(f"Evidence ID already exists: {aid}")
        if any(parent not in self.artifacts for parent in parent_ids):
            raise ValueError("Cannot register evidence with an unknown parent.")
        artifact = EvidenceArtifact(
            id=aid, kind=kind, path=relative, sha256=digest(self.path(relative)),
            bytes=self.path(relative).stat().st_size,
            stage_id=self.active.stage_id if self.active else None,
            attempt_id=self.active.attempt_id if self.active else None,
            operation_id=self.current_operation.id if self.current_operation else None,
            parent_ids=list(parent_ids), plan_id=plan_id, dataset_id=dataset_id, scale=scale,
            parameters=clean_json(parameters or {}), schema_path=schema_path,
            schema_sha256=digest(self.path(schema_path)) if schema_path else None,
            rows=rows, columns=columns, individual_samples=individual_samples,
        )
        self.artifacts[aid] = artifact
        try:
            self._index()
        except BaseException:
            del self.artifacts[aid]
            raise
        if self.active:
            self.active.output_artifact_ids.append(aid)
            if plan_id and plan_id not in self.active.plan_ids:
                self.active.plan_ids.append(plan_id)
            if self.current_operation:
                self.current_operation.output_artifact_ids.append(aid)
            self._save_report()
            self._journal("artifact_committed", artifact_id=aid, operation_id=artifact.operation_id)
        return artifact

    def register_file(self, relative, kind, *, aid=None, **provenance):
        """Register an already atomically written immutable document or figure."""
        if "parent_ids" not in provenance and self.active:
            provenance["parent_ids"] = self.active.input_artifact_ids
        return self._register(aid or f"e{uuid4().hex}", kind, relative, **provenance)

    def table(self, frame, kind, *, aid=None, relative=None, **provenance):
        aid = aid or f"e{uuid4().hex}"
        relative = relative or f"{self._directory()}/artifacts/{aid}_{kind}.csv"
        if self.path(relative).exists():
            raise FileExistsError(self.path(relative))
        schema_path = relative + ".schema.json"
        if not frame.columns.is_unique or any(not isinstance(column, str) for column in frame.columns):
            raise ValueError("Persisted tables require unique string column names.")
        schema = {"schema_version": 1, "columns": [
            {"name": column, "dtype": str(frame[column].dtype), "missing_rows": []}
            for column in frame.columns
        ], "missing_encoding": "empty CSV cell plus explicit missing row positions"}
        # Missing positions refer to row positions, not the caller's DataFrame index.
        for column, entry in zip(frame.columns, schema["columns"]):
            entry["missing_rows"] = [i for i, missing in enumerate(frame[column].isna()) if missing]
        self.atomic_write(relative, lambda path: frame.to_csv(
            path, index=False, float_format="%.17g"
        ))
        self.write_json(schema_path, schema)
        return self._register(aid, kind, relative, schema_path=schema_path,
                              rows=len(frame), columns=len(frame.columns), **provenance)

    def read_table(self, aid):
        artifact = self.artifacts[aid]
        path = self.path(artifact.path)
        if digest(path) != artifact.sha256:
            raise ValueError(f"Modified evidence artifact: {aid}")
        if not artifact.schema_path:
            raise ValueError("Evidence artifact is not a typed table.")
        schema_path = self.path(artifact.schema_path)
        if digest(schema_path) != artifact.schema_sha256:
            raise ValueError(f"Modified table schema: {aid}")
        schema = json.loads(schema_path.read_text())
        text = {entry["name"]: str for entry in schema["columns"]
                if entry["dtype"] in {"object", "str"} or entry["dtype"].startswith("string")}
        numeric_na = {entry["name"]: [""] for entry in schema["columns"]
                      if entry["name"] not in text}
        types = {entry["name"]: entry["dtype"] for entry in schema["columns"]
                 if entry["name"] not in text and not entry["dtype"].startswith("datetime")}
        frame = pd.read_csv(path, dtype={**types, **text}, keep_default_na=False, na_values=numeric_na,
                            float_precision="round_trip")
        for entry in schema["columns"]:
            name, dtype = entry["name"], entry["dtype"]
            if name in text:
                frame.loc[entry["missing_rows"], name] = pd.NA
            if dtype.startswith("datetime"):
                frame[name] = pd.to_datetime(frame[name])
            frame[name] = frame[name].astype(dtype)
        return frame

    def recover_interrupted(self):
        for relative in self.attempts:
            report = StageReport.model_validate_json(self.path(relative).read_text())
            if report.status != "running":
                continue
            report.status = "interrupted"
            report.finished_at = now()
            committed = [a for a in self.artifacts.values()
                         if a.stage_id == report.stage_id and a.attempt_id == report.attempt_id]
            report.output_artifact_ids = [a.id for a in committed]
            for operation in report.operations:
                operation.output_artifact_ids = [a.id for a in committed
                                                 if a.operation_id == operation.id]
                if operation.status == "running":
                    operation.status = "interrupted"
                    operation.finished_at = now()
            report.issues.append(StageIssue(
                code="PROCESS_INTERRUPTED", severity="error", source="tool_error",
                message="The process stopped before this attempt was finalized. Committed outputs remain available.",
                evidence_ids=report.output_artifact_ids, blocking=True,
            ))
            report.no_issues_detected = False
            report.continuation = {"requires_review": True,
                                   "outputs_committed": report.output_artifact_ids}
            self._save_report(report)
