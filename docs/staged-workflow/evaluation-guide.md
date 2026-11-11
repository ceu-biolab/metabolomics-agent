# Evaluar nuevos conjuntos de datos con H

La unidad es dataset + pregunta + protocolo, con un `case_id` estable y una
configuración versionada. Los mismos datos pueden tener varios casos. La
comparación utiliza artefactos locales y no necesita credenciales.

## Primer caso real

El archivo original está en `data/Lipidomics_dataset.xlsx` en este entorno.
`data/` está excluido de Git. Para otro checkout, descargar el conjunto número 1
desde la [página de los autores](https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/introduction/example-data-sets).
Las configuraciones y referencias pequeñas de `evaluations/cases/lipidomics/`
sí forman parte de la entrega; su manifiesto exige el hash del Excel registrado.

```bash
.venv/bin/python -m metabolomics_agent evaluate-case \
  --case evaluations/cases/lipidomics/student_t.json --output runs/H-student

.venv/bin/python -m metabolomics_agent evaluate-case \
  --case evaluations/cases/lipidomics/mannwhitney.json --output runs/H-mannwhitney

.venv/bin/python -m metabolomics_agent evaluate-case \
  --case evaluations/cases/lipidomics/pca.json --output runs/H-pca
```

Cada salida debe ser nueva. Student/Mann–Whitney usan N/T, 206 muestras, 127
lípidos, concentraciones sin transformación/imputación/normalización adicional,
corrección Holm–Šidák y alfa 0.05. PCA usa N/PAN/T, 227 muestras, autoscalado con
ddof=0 y diez componentes, sin log. El heatmap significativo de Student es una
ampliación propia sin clustering; su selección no es una validación independiente.

Para regenerar referencias desde un archivo original verificado, sin sobrescribir
las canónicas:

```bash
.venv/bin/python -m metabolomics_agent register-lipidomics \
  --dataset data/Lipidomics_dataset.xlsx --output runs/H-regenerated-cases
```

El comando verifica los números publicados antes de registrar casos. Las tablas
completas son cálculos desde los scripts de los autores, no tablas publicadas
íntegramente. `references/producer.py` es una copia del código productor, con hash;
la entrada ejecutable es el comando anterior dentro del paquete y entorno
registrados. `reference_metadata.json` registra paquetes, solver y controles.

## Crear un caso reutilizable

Un ejemplo completo pequeño:

```bash
.venv/bin/python -m metabolomics_agent make-evaluation-example \
  --variant descriptive --seed 913 --output runs/H-example-case

.venv/bin/python -m metabolomics_agent evaluate-case \
  --case runs/H-example-case/case.json --output runs/H-example-evaluation
```

Cambiar `--variant` a `independent`, `paired`, `pca_only`, `optional_pca`,
`no_discoveries` o `unsafe_log` permite explorar distintos contratos. Una semilla
nueva genera otro dataset, no evidencia de generalización biológica.

Para un estudio propio:

1. Crear `DatasetManifest`: origen, archivos y SHA-256, columnas, unidades y
   normalización conocida/desconocida. `dataset.root` se resuelve respecto a la
   carpeta de `case.json`; los archivos declarados no pueden escapar de esa raíz.
2. Definir `study`, petición **en inglés**, población y diseño. La carga
   `analysis_scope="descriptive"` usa `selected_groups` explícitos, sin grupos
   de referencia/tratamiento ficticios. `two_group` conserva referencia,
   tratamiento y requisitos de diseño. Los metadatos declarados en el manifiesto
   se convierten en criterios verificables cuando son requisitos del caso.
3. Elegir `reproduction` o `open_analysis`. Fijar los requisitos antes de observar
   el resultado. Para alternativas justificadas se admite `one_of`; aceptar dos
   tests no significa exigir los mismos valores p o el mismo estimando.
4. Registrar etapas/bloques, dependencias y artefactos de entrada necesarios.
   PCA es un bloque separado de evaluación que comparte la etapa productiva 03.
5. Añadir métricas con selector local, validador, procedencia, obligatoriedad,
   gravedad y tolerancias. `observation.path` recorre campos de JSON mediante
   claves/índices; no ejecuta expresiones. Un selector de tabla debe identificar
   un único artefacto por ID o por tipo. Si existen varias versiones, usar el ID
   concreto y comprobar su vigencia con los criterios del caso.
6. Incorporar referencias de un colaborador, scripts externos o propiedades
   matemáticas. Registrar fuente, revisión e independencia. Las matrices/tablas
   se comparan por claves únicas, no por posición. Compartir SciPy no demuestra
   independencia frente a un error de esa biblioteca.
7. Para lanzar un caso nuevo offline, aportar el estímulo `script`. También puede
   evaluarse una ejecución existente sin ese estímulo. Los guiones no son la
   trayectoria obligatoria de un futuro LLM y sus respuestas no se incluyen en
   el contexto del modelo.

El esquema está en `evaluations/evaluation-case.schema.json`; la validación
Python además comprueba condiciones cruzadas, referencias y dependencias.

Validadores disponibles: `exact`, `one_of`, `numeric`, `set`, `table`,
`pca_weights`, `audit` y `manual`. No se evalúa código recibido en la configuración.
`audit` exige prefijos de controles explícitos y no aprueba un ámbito vacío.
Una expectativa omitida es distinta de una expectativa explícita `null`:
`expected_is_set` conserva esta distinción al serializar.

Para PCA, `component_blocks` permite declarar bloques prácticamente degenerados.
El evaluador compara proyectores, admite signos equivalentes y rotaciones dentro
de los bloques registrados; las ecuaciones y scores se comprueban por auditoría.

## Aplicabilidad y referencias incompletas

Una etapa requerida debe tener criterios. `conditional` exige una condición
verificable; `out_of_scope` no suma aprobados. `optional` permite definir
alternativas de ejecución/omisión, pero el autor debe registrar sus criterios.
Las condiciones de etapa/métrica comparan una observación con `equals`; `negate`
invierte esa condición. Por ejemplo, se pueden comprobar el motivo de omisión
cuando `state.pcas == {}` y el ajuste cuando esa condición sea falsa.

`requires_artifact_kinds` expresa dependencias científicas: si no existe una
matriz de prueba válida, la inferencia queda bloqueada aunque detectar y detener
la preparación haya superado su evaluación de comportamiento.

Sin referencia, registrar el criterio pendiente, sin inventar una tabla esperada.
Una referencia declarada pero ausente, corrupta o desalineada constituye un
incumplimiento del contrato. El informe distingue aprobación, fallo, no
aplicabilidad, falta de evaluación y bloqueo por dependencia.

La tasa de aprobación usa criterios evaluados; la cobertura usa criterios
aplicables. Se publican ambos denominadores. Un error crítico no se compensa con
celdas correctas y un ámbito obligatorio sin evaluar no queda validado. Las
incidencias del dato, como una imputación alta, se describen aparte del desempeño.

## Evaluar evidencia guardada e identificar un fallo

```bash
.venv/bin/python -m metabolomics_agent evaluate-case \
  --case evaluations/cases/lipidomics/student_t.json \
  --run runs/H-student/scientific_run --output runs/H-student-recheck
```

La salida debe estar fuera del análisis. La auditoría no restaura ni modifica la
ejecución inspeccionada. Compara hashes y configuración de entrada con el caso.
`evaluation.json/.md` resume; `stages/<bloque>/<intento>/evaluation.json/.md`
identifica el criterio y sus evidencias. El análisis conserva sus informes y
checkpoints originales. `evaluator.json` guarda entorno y hashes del código.

`continuation_policy="observe"` conserva la ejecución completa para diagnóstico.
`stop_on_critical` detiene la continuación al detectar un criterio crítico fallido
en una etapa cerrada. Solo se transmite el impedimento al agente, no el número
esperado ni las respuestas privadas. La evaluación sigue mostrando el fallo.

Para aislar una etapa:

```bash
.venv/bin/python -m metabolomics_agent evaluate-case \
  --case path/to/case.json --stage 02_preparation --output runs/H-isolated
```

Este modo exige `stage_packages` en el caso. Cada paquete debe estar dentro del
directorio del caso y declarar raíz, procedencia e inventario completo con hashes
de un snapshot offline incompleto que comience en la etapa solicitada. Se copia
a la nueva salida y se restaura mediante los contratos productivos. Debe contener
estado, evidencias, planes, metadatos, advertencias, conversación y prompts
congelados; no basta con una matriz CSV. No se aceptan ficheros omitidos del
inventario ni un snapshot que comience en otra fase. Las etapas anteriores son
entradas de referencia inyectadas, no etapas ejecutadas en ese intento aislado.

Un experto debe validar que ese paquete representa una entrada de referencia
adecuada. La prueba de aislamiento en `tests/test_case_evaluation.py` muestra cómo
generar y sellar un prefijo controlado. Una etapa correcta con entrada validada
permite distinguir errores locales de errores heredados.

## Revisión humana y afirmaciones numéricas

Los hallazgos estructurados del informe identifican comparación, variable si
procede, métrica y valor. El runtime rechaza números incompatibles con sus tablas;
la auditoría los comprueba de nuevo. Citar un ID correcto no verifica el texto
libre que lo acompaña. El alcance realmente comprobado se declara en el informe.

Una métrica `manual` queda pendiente hasta aportar un `ManualReview` como
referencia revisada: `case_id`, `metric_id`, revisor, fecha, hash de
`evidence_index.json`, veredicto y explicación. El registro debe corresponder
exactamente al caso, criterio y evidencia. `review_scope` distingue ciencia,
legibilidad y narrativa. Una revisión de otro intento no se reutiliza como
aprobación; los tests emplean revisores ficticios identificados como fixtures.

La parte offline no llama a un proveedor remoto ni carga `.env`.
`live_evaluation_manifest.json` prepara únicamente petición/configuración pública,
con proveedor, modelo, presupuesto e intentos sin fijar. H8 permanece pendiente
por indicación del usuario; las referencias y guiones quedan fuera del manifiesto.

Códigos de salida: 0, evaluación superada; 1, fallo; 2, cobertura obligatoria
pendiente/bloqueada o ámbito no evaluado. Que una detención esperada devuelva 0
no significa que el objetivo científico se haya completado.
