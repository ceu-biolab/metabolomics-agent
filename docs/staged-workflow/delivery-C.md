# Entrega C: coordinación de cinco etapas

Estado: implementada y verificada. D–G no se han iniciado.

## Flujo activo

Las nuevas ejecuciones de la CLI y `RunState` usan `staged_v1`:

1. Coordinación: objetivo, alcance descriptivo/comparativo y figuras requeridas.
2. Preparación: inspección, plan de preparación y datos preparados.
3. Univariante: plan estadístico independiente y comparación ejecutada.
4. Visualización: plan de figuras, fuentes vigentes y figuras generadas.
5. Informe: validación del objetivo y finalización.

El programa verifica los requisitos antes de avanzar. Los objetivos descriptivos
pueden omitir univariante y, si así se registró, visualización. Cada omisión deja
informe y traspaso con motivo. Un intento de omitir trabajo requerido se rechaza.

Cada etapa mantiene un intento abierto durante sus llamadas de herramientas,
aprovechando los checkpoints y manifiestos de B. Al finalizar genera los informes
JSON/Markdown y un `handoff.json` registrado con hash. Las etapas no se completan
simplemente porque el modelo lo afirme.

## Contratos y permisos

Se incorporan `WorkflowPlan`, `PreprocessingPlan`, `UnivariatePlan`, `FigurePlan`,
`StageAssessment`, `WorkflowState` y `StageHandoff`. El plan de preparación ya no
incluye una prueba estadística ni escalado para PCA.

Las herramientas expuestas dependen de la etapa. El mismo permiso se comprueba
al ejecutar, junto con argumentos, plan vigente, fuentes y dependencias. Durante
preparación solo se admiten gráficos diagnósticos de distribución y boxplot;
durante visualización las figuras deben pertenecer al plan registrado.

El catálogo se puede inspeccionar con:

```bash
.venv/bin/python -m metabolomics_agent tool-schemas --stage 03_univariate
```

`get_stage_context`, `get_workflow_state` y `read_stage_report` proporcionan
información acotada. `submit_stage_assessment` solicita cierre validado;
`request_revision` abre un nuevo intento del mismo ámbito o uno anterior.

## Traspasos y privacidad

Los traspasos incluyen diseño, alcance, planes, identificadores vigentes,
resúmenes de muestras/variables/ausencias, preparación aplicada, incidencias y
limitaciones. Se valida su formato, destino, integridad y referencias. Las
matrices completas y sus valores individuales permanecen locales.

Las advertencias relevantes pasan a los consumidores posteriores. Por ejemplo,
la incertidumbre de imputación se conserva en univariante, visualización e informe.
Los recuentos agregados de incidencias en `validation.json` incluyen esas
propagaciones; no representan imputaciones o errores independientes adicionales.

## Revisiones y reanudación

Una revisión conserva los resultados anteriores y retira las dependencias
afectadas del conjunto vigente. Una revisión de preparación invalida comparaciones
y figuras posteriores; una revisión estadística invalida sus comparaciones y
figuras. Las revisiones después de examinar pruebas se registran como exploratorias.
No se admite ejecutar dos veces el mismo plan estadístico.

`state.json` versión 3 es la instantánea autoritativa del protocolo nuevo;
`workflow_state.json` facilita inspección. La restauración recupera planes, datos,
posición y textos de prompts. Se mantienen los controles existentes de llamadas
pendientes y de identidad de modelo, endpoint y API. Una llamada parcialmente
confirmada o truncada no se vuelve a ejecutar automáticamente.

Los informes finales registrados tienen versiones inmutables por intento, junto
a la vista cómoda `report.md`, para conservar evidencia si se interrumpe el cierre.

## Prompts en inglés

Se han instalado los seis módulos aprobados (base común y cinco etapas) en
`src/metabolomics_agent/prompts/stages/`. Su uso técnico se integra en C para hacer
operativa la coordinación; la evaluación y el refinamiento previstos en G siguen
pendientes.

Cada petición usa la base común y las instrucciones de su etapa, con contexto y
herramientas actualizados. El paquete completo de prompts queda congelado en
`run.json`; `requests/request_<n>.json` conserva los mensajes y herramientas
realmente enviados al proveedor. Las definiciones ejecutables de herramientas
siguen separadas de los prompts y determinan las capacidades disponibles.

## Exclusión del análisis multivariante

El catálogo nuevo no contiene `run_pca` ni acepta figuras PCA. La preparación no
calcula ni guarda matrices PCA. El flujo y la demo no exigen PCA para finalizar.
Tampoco se incorporan clustering, correlaciones, embeddings, PLS/OPLS, corrección
de lotes o procedimientos basados en QC.

El código de compatibilidad del protocolo `legacy` permanece para verificar y
reanudar ejecuciones antiguas con sus reglas originales. Los estados antiguos no
se convierten silenciosamente al protocolo nuevo. Las pruebas numéricas históricas
utilizan ese protocolo de forma explícita.

## Verificación

- **60 pruebas superadas**, incluidas las 50 anteriores.
- Comprobaciones estáticas y de compilación superadas.
- Pruebas de permisos, argumentos gráficos, cierre prematuro, planes separados,
  privacidad del traspaso, omisiones descriptivas y revisión con invalidación.
- Demo con cinco etapas e informes/traspasos, sin artefactos PCA.
- Reanudación desde univariante sin repetir preparación ni multiplicar datasets.
- Rechazo de un traspaso modificado antes de consumirlo.
- Demo por CLI completada: **16 llamadas**, **5 traspasos**, **27 artefactos** y
  **16 peticiones con contexto guardado**, mediante controlador offline.
- La tabla de la comparación principal coincide **byte por byte con B**.
- Ninguna llamada a un modelo remoto ni uso de API key.

Evidencias locales:

- [Validación estructurada](../../runs/validation-C-20261110T110555879610Z/validation.json).
- [Pruebas](../../runs/validation-C-20261110T110555879610Z/pytest.log).
- [Informe de la demo](../../runs/validation-C-20261110T110555879610Z/demo/report.md).
- [Informe de preparación](../../runs/validation-C-20261110T110555879610Z/demo/stages/02_preparation/attempt_001/stage_report.md).
- [Traspaso de preparación](../../runs/validation-C-20261110T110555879610Z/demo/stages/02_preparation/attempt_001/handoff.json).
- [Estado del flujo](../../runs/validation-C-20261110T110555879610Z/demo/workflow_state.json).

Los resultados bajo `runs/` se conservan localmente y están excluidos de Git.

## Pendiente

D añadirá los descriptivos ampliados. E completará los diagnósticos y controles
específicos del análisis univariante sobre estos contratos. F incorporará fuentes
exactas de figuras, mapas de calor sin clustering y mejoras del informe. G evaluará
las decisiones de un LLM real y refinará prompts y escenarios de integración.
Los ajustes mínimos de finalización por objetivo necesarios para C ya están activos.
