# Plan actualizado: flujo por etapas con evidencias e informes

Este documento describe el plan acordado. A–F están completadas; G sigue
pendiente. Los detalles están en [delivery-B.md](delivery-B.md) y
[delivery-C.md](delivery-C.md), [delivery-D.md](delivery-D.md) y
[delivery-E.md](delivery-E.md) y [delivery-F.md](delivery-F.md). Los prompts de `prompts/` son los textos aprobados;
C instala sus copias en el paquete y activa las instrucciones por etapa. La
evaluación y refinamiento previstos en G siguen pendientes.

## 1. Alcance y decisiones

El flujo tendrá cinco etapas: coordinación, preparación y exploración,
análisis univariante, visualización e interpretación e informe. Se elimina
la etapa multivariante de la propuesta anterior, sin crear una etapa artificial
para mantener la numeración de seis.

Fuera del alcance: PCA, PLS/OPLS, t-SNE, UMAP, clustering, análisis de
correlaciones/covarianzas entre variables, redes lipídicas, corrección de lotes
y procedimientos basados en QC, incluidos LOESS y SERRF. No se ampliará ninguno
de esos métodos en esta implementación. Los metadatos de lote podrán describirse
y generar advertencias de confusión, pero no se corregirán efectos de lote.

Se mantiene un único agente, con instrucciones comunes y un prompt en inglés
por etapa. La lista de herramientas y sus esquemas procede del registro Python,
no de copias de los esquemas en los prompts. Los prompts sí explican cuándo y
cómo usar las herramientas. Los permisos se aplican también al ejecutar llamadas.

Primera versión: comparación entre dos grupos, independiente o pareada, con los
métodos ya implementados; descriptivos ampliados, visualizaciones sin PCA ni
clustering y persistencia por etapa. ANOVA, Kruskal–Wallis y post hoc quedan como
ampliaciones univariantes posteriores, no como herramientas ya disponibles.

## 2. Estructura y responsabilidades

| Etapa | Prompt propuesto | Entradas | Resultados y salida |
|---|---|---|---|
| 01 Coordinación | [01_coordination.md](prompts/01_coordination.md) | Petición, diseño declarado, capacidades y resumen de carga | Objetivo, alcance, ruta provisional y requisitos de finalización |
| 02 Preparación y exploración | [02_preparation.md](prompts/02_preparation.md) | Entradas originales, metadatos y plan de flujo | Diagnósticos, descriptivos, decisiones y representaciones preparadas |
| 03 Análisis univariante | [03_univariate.md](prompts/03_univariate.md) | Datos preparados, diseño e incidencias relevantes | Plan estadístico congelado, pruebas, efectos, intervalos y correcciones |
| 04 Visualización | [04_visualization.md](prompts/04_visualization.md) | Artefactos autorizados y resultados | Figuras, datos representados, selección de variables y pies |
| 05 Interpretación e informe | [05_reporting.md](prompts/05_reporting.md) | Informes de todas las etapas y evidencias vigentes | Informe final, cobertura del objetivo y limitaciones acumuladas |

[common.md](prompts/common.md) acompaña siempre al prompt de la etapa activa. El contexto se forma
con esa base, el prompt activo, el objetivo, los planes y un traspaso validado.
La conversación completa sigue guardada localmente, pero no es el único mecanismo
para transportar decisiones entre etapas.

La coordinación plantea la ruta antes de los diagnósticos; no fija tratamientos
de ausentes ni pruebas sin evidencia suficiente. Preparación fija sus decisiones
después de inspeccionar los datos. Univariante registra su plan antes de probar.
Un objetivo puramente descriptivo puede omitir univariante con motivo registrado.

La visualización diagnóstica de etapa 02 puede usar histogramas y boxplots; las
figuras de presentación se consolidan en etapa 04. Así no se obliga a esperar al
final para inspeccionar el efecto de las transformaciones.

## 3. Datos guardados: etapa, operación y versión

Cada ejecución de una etapa tendrá un identificador de intento inmutable. Una
revisión crea otro intento, nunca sobrescribe sus resultados anteriores.

```text
runs/<run_id>/
  inputs/                         # copias locales de entradas y metadatos
  stages/
    01_coordination/attempt_001/
    02_preparation/attempt_001/
      context.json                # contexto exacto suministrado al modelo
      prompt_snapshot.json        # textos, versiones y hashes de prompts
      artifacts/                  # tablas y figuras de este intento
      operations.jsonl            # operaciones, parámetros y eventos
      artifact_manifest.json      # procedencia, esquemas e integridad
      stage_report.json           # resultado estructurado de la etapa
      stage_report.md             # representación legible del JSON
      handoff.json                # traspaso validado
    03_univariate/attempt_001/
    04_visualization/attempt_001/
    05_reporting/attempt_001/
  workflow_state.json             # punteros y estado recuperable actual
  report.md
```

Todas las etapas guardan manifiesto e informe, incluso cuando no transforman
datos. En ese caso registran las entradas referenciadas y que no hubo una nueva
transformación; no fabrican una copia con apariencia de datos nuevos.

En preparación se guardan, cuando se generen, los resultados de cada operación:

1. Entrada seleccionada, alineada con metadatos y grupos excluidos registrados.
2. Matriz con ceros tratados como ausentes, si se elige esa política.
3. Matriz filtrada y tabla de variables retiradas con sus motivos.
4. Matriz imputada y máscara de celdas imputadas.
5. Concentraciones tras normalización, si se aplica.
6. Matriz en escala de prueba, con transformación registrada.
7. Matriz para heatmap sin clustering, únicamente si se solicita; centrado y
   escalado se identifican como preparación gráfica, nunca como PCA.

Las operaciones que no se apliquen constarán como omitidas; no generan matrices
ficticias. Cada operación exitosa se persiste antes de iniciar la siguiente.
Si una operación falla, se conserva la última matriz válida y se registra cuál
era su entrada y qué se intentó. No se publican salidas parcialmente escritas
como artefactos válidos.

En univariante se guarda la tabla completa, incluidas variables no evaluables,
no solamente los mejores resultados. En visualización se guardan las figuras y
las tablas exactas que las alimentan: valores, puntos, etiquetas, selección y
estado de omisiones. Si se emplean bins o resúmenes gráficos, se guardan sus
parámetros y valores derivados. La biblioteca puede realizar internamente
operaciones de dibujo, pero la fuente y la configuración deben reproducirse.

Cada artefacto tendrá: ID, ruta relativa, SHA-256, tipo, etapa/intento, operación,
padres, plan, dimensiones, esquema de columnas y tipos, IDs de muestras/variables,
escala, parámetros, política de valores ausentes y versiones de bibliotecas.
Las identidades se almacenan en esquemas/índices locales y no se incluyen
indiscriminadamente en el contexto remoto.

Se usarán inicialmente CSV con esquema JSON explícito, precisión numérica
suficiente y validación de ida y vuelta para números e identificadores de texto.
Las tablas mantendrán IDs como columnas explícitas y ausencias diferenciadas
de ceros. JSON empleará `null` con códigos de motivo para valores no definidos.
Se validarán rutas dentro del directorio de ejecución, hashes y referencias.
La restauración actual supone artefactos planos; deberá adaptarse a subdirectorios
sin relajar los controles de rutas.

## 4. Informe obligatorio de etapa

`stage_report.json` será la fuente estructurada. `stage_report.md` se generará
desde ella sin otra llamada al modelo. Cada etapa declara explícitamente si se
detectaron incidencias; la ausencia de incidencias no implica validación total
de la calidad científica ni de aspectos que no se hayan comprobado.

El esquema incluirá:

| Campo | Contenido |
|---|---|
| `schema_version`, `stage_id`, `attempt_id` | Versión y procedencia |
| `status`, `started_at`, `finished_at` | Ejecución y tiempos |
| `input_artifact_ids`, `output_artifact_ids` | Entradas y salidas persistidas |
| `plan_ids`, `operations` | Decisiones aplicadas y parámetros |
| `checks` | Comprobación, expectativa, resultado observado, estado y evidencia |
| `issues` | Incidencias actuales, heredadas y su resolución |
| `no_issues_detected` | `true` solo tras comprobaciones sin incidencias; `false` si se detectan; `null` si no se pudo evaluar |
| `limitations`, `unchecked_assumptions` | Límites y supuestos no verificables |
| `agent_assessment` | Interpretación del modelo separada de comprobaciones calculadas |
| `handoff_id`, `continuation` | Traspaso y condiciones para continuar |

Estados finales: `completed`, `completed_with_warnings`, `blocked`, `failed`,
`interrupted` y `skipped`. El estado activo es `running`. `blocked` identifica
un requisito esencial sin resolver; `failed`, una ejecución fallida. Un resultado
no significativo no es un fallo, y una advertencia científica no es una excepción
de software.

Cada incidencia incluirá código estable, severidad (`info`, `warning`, `error`),
origen (`computed_check`, `tool_error`, `agent_assessment`, `inherited`), evidencia,
alcance (muestras/variables/artefactos), impacto en consumidores, acción sugerida,
estado de resolución y condiciones que impiden continuar.

Los controles Python crean los hechos computados y determinan los bloqueos. El
modelo puede añadir dudas justificadas, pero no borrar un error, cambiar un valor
computado ni declarar resuelto un problema sin nueva evidencia.

Ejemplos de incidencias: logaritmo con entradas no positivas; identificadores
desalineados; demasiados ausentes; normalización previa desconocida; incertidumbre
por imputación; datos observados insuficientes; efecto no definido; p-valor no
finito; figuras que omiten variables; dependencia fuera de versión vigente.

Se guardará también un informe si una etapa falla o se interrumpe. Habrá registro
incremental y finalización en el manejador de errores; ante cierre abrupto del
proceso, la recuperación detectará el intento `running` sin finalizar y lo
marcará `interrupted`, conservando las operaciones ya confirmadas. Si falla el
almacenamiento no puede garantizarse guardar un informe: la ejecución no se
declarará completa y emitirá un error operativo explícito.

También se inicializará el registro de ejecución antes de cargar/validar las
tablas, para dejar constancia de errores de entrada. Actualmente `RunState`
intenta cargar los datos antes de crear su directorio de salida; habrá que
separar esa inicialización del comienzo del análisis. Un fallo de carga generará
un informe de inicialización, sin simular etapas científicas ejecutadas.

## 5. Información que pasa a la siguiente etapa

Sí debe pasar parte de esta información, mediante `handoff.json` generado y
validado por el programa. No bastará un párrafo libre del modelo.

| Se transmite | Motivo |
|---|---|
| Objetivo, diseño, grupos y dirección de efectos | Mantener el significado del análisis |
| IDs y versiones de planes/datasets/artefactos vigentes | Evitar mezclar resultados de distintas preparaciones |
| Escalas, normalización, transformación e imputación | Elegir y explicar correctamente cálculos y figuras |
| Resúmenes de tamaño muestral, ausencias y filtros | Evaluar condiciones de los métodos |
| Incidencias abiertas relevantes y limitaciones acumuladas | Evitar que desaparezcan entre etapas |
| Restricciones de uso y requisitos de continuación | Impedir uso de entradas inválidas |
| Acciones pendientes y entregables requeridos | Coordinar la siguiente tarea |

Las matrices completas y los valores individuales permanecen locales. El modelo
recibe agregados acotados y IDs; las herramientas locales acceden a las tablas
completas por esos IDs. El filtrado evita que `handoff.json` o los datos de figuras
se conviertan en una vía indirecta de envío de tablas individuales.

Ejemplos: tras imputar, univariante recibe método, cantidad imputada y recuentos
observados, y el informe conserva esa limitación; ante fold change indefinido,
visualización recibe las variables afectadas y documenta su omisión del volcano;
ante datos insuficientes para una variable, se omite esa prueba, no necesariamente
toda la etapa. Si no queda ninguna prueba válida y era un objetivo requerido,
se bloquea ese objetivo y se informa, sin fabricar resultados.

Las incidencias de seguridad/validez y las limitaciones científicas relevantes
persisten hasta resolución documentada o informe final. Los detalles puramente
operativos resueltos permanecen en el historial local. El informe final puede
consultar todos los informes, no solo el último traspaso.

Una revisión de preparación crea otro intento e identifica los consumidores
afectados. Los resultados anteriores se conservan como históricos y dejan de
contar para completar el plan vigente. Las revisiones adaptativas de métodos
después de observar resultados siguen siendo exploratorias.

## 6. Herramientas y disponibilidad

Las siguientes herramientas existen hoy, aunque algunas necesitarán adaptación:
`inspect_dataset`, `set_plan`, `preprocess`, `compare_groups`, `make_plot`,
`read_result` y `finish_analysis`. `run_pca` existe, pero no se expondrá ni se
ejecutará en el nuevo flujo. Tampoco se generará la representación PCA por defecto.

| Etapa | Herramientas científicas actuales reutilizadas | Herramientas propuestas nuevas |
|---|---|---|
| Coordinación | `read_result` cuando haya evidencias | `get_workflow_state`, `set_workflow_plan` |
| Preparación | `inspect_dataset`, `preprocess`, `read_result`, `make_plot` restringido a diagnóstico | `set_preprocessing_plan`, `summarize_dataset` |
| Univariante | `compare_groups`, `read_result` | `inspect_univariate`, `set_univariate_plan` |
| Visualización | `make_plot`, `read_result` | `set_figure_plan` |
| Informe | `read_result`, `finish_analysis` adaptado | `validate_workflow` |

Herramientas transversales nuevas: `get_stage_context`, `read_stage_report`,
`submit_stage_assessment` y `request_revision`. Se expondrán solo donde sean
pertinentes. `submit_stage_assessment` registra valoración, evidencia, supuestos
y tareas pendientes; no convierte por sí sola una etapa en válida. El programa
verifica contratos, persiste el informe y decide la transición.

La creación de checkpoints, informes y traspasos es responsabilidad del programa;
no depende de que el LLM recuerde llamar a una herramienta de guardado.

El plan actual mezcla preprocesamiento, prueba y escalado de PCA. Se separará en
`WorkflowPlan`, `PreprocessingPlan`, `UnivariatePlan` y `FigurePlan`, con adaptador
para los planes guardados actuales. Cambiar de etapa no creará automáticamente
una nueva comparación ni convertirá un método ya previsto en una revisión.

Descriptivos de primera versión: n observado/ausente, ceros, media, mediana, SD,
varianza muestral, mínimo, máximo, rango, Q1, Q3, IQR, deciles, asimetría, curtosis
y CV con política explícita de indefinición. Moda opcional con advertencia para
datos continuos. Se computarán por grupo y escala solicitada, diferenciando
original, preparado y origen imputado. Q1/Q3/IQR dejarán de ser solo auxiliares
internos para detectar atípicos.

Figuras de primera versión: distribuciones antes/después, boxplots con puntos,
volcano de p ajustado y heatmap sin clustering opcional. El heatmap tendrá una
representación gráfica propia explícita; no depende de `pca_values`. Si una figura
no es aplicable al objetivo, su ausencia se justifica sin invalidar todo el flujo.

Los prompts describen el contrato de destino. C implementa las herramientas de
coordinación, planes y traspasos; D incorpora `summarize_dataset` y la procedencia
observada/imputada. E incorpora el diagnóstico univariante, checkpoints de pruebas,
auditoría e incidencias. F incorpora las fuentes y representaciones gráficas
persistidas, heatmap sin clustering e informe condicionado al objetivo.
El catálogo dinámico es la autoridad
de capacidades en cada ejecución.

## 7. Cambios en el código

| Componente | Trabajo |
|---|---|
| `agent.py` | Construcción de contexto por etapa, transiciones, límites globales y recuperación |
| `models.py` | Planes separados, `StageResult`, `StageIssue`, `StageReport`, `StageHandoff`, manifiestos y versiones |
| `state.py` | Artefactos por intento/operación, procedencia, integridad, restauración e invalidación de dependencias |
| `tools.py` | Catálogo por etapa y validación de permisos también en ejecución |
| `analysis.py` | Checkpoints de preparación, descriptivos y separación del plan estadístico |
| `plots.py` | Datos y configuración por figura; heatmap independiente de PCA; incidencias por omisión |
| `report.py` | Informe final condicionado al objetivo, informes de etapa y limitaciones acumuladas |
| `cli.py`, demo | Progreso por etapa, rutas a informes y ejemplo sin requisitos multivariantes |
| `prompts/` | Instalación de las versiones en inglés propuestas en esta documentación |

Se conservarán las evidencias de ejecuciones antiguas. Se versionará el esquema
de estado: una ejecución antigua no pasará silenciosamente al nuevo protocolo.
Se permitirá inspección de artefactos antiguos; la reanudación usará el protocolo
y prompts originales cuando sea compatible, o se rechazará con motivo concreto.
No se borrará el código PCA solo para reorganizar el flujo; quedará fuera del
catálogo nuevo y de sus requisitos, pruebas y demo operativa.

## 8. Secuencia de implementación

| Entrega | Alcance | Condición de aceptación |
|---|---|---|
| A | Crear entorno local, instalar dependencias y ejecutar pruebas/demo actuales | Referencia numérica y de artefactos antes de cambios; sin API key |
| B | Modelos de evidencia, persistencia por operación e informes JSON/MD | Cada salida válida es recuperable; fallo/interrupción conserva última salida confirmada |
| C | Planes separados, cinco etapas, permisos y traspasos | Estado y transiciones validados en Python; multivariante fuera del flujo |
| D | Preparación y descriptivos ampliados | Tablas por escala/grupo y checkpoints concordantes con cálculos de referencia |
| E | Univariante con plan explícito e informe de incidencias | Resultados actuales preservados; máscaras y n observado no se sustituyen por n imputado |
| F | Figuras y fuente persistida; informe final por objetivo | No exige PCA ni volcano en análisis descriptivos; vincula todas las limitaciones relevantes |
| G | Prompts completos, demo por etapas, restauración y evaluación integrada | Ejecuciones reproducibles y comparables; evaluación real del LLM separada de pruebas simuladas |

## 9. Verificación y criterios globales

- Regresión numérica de pruebas, efectos, correcciones y dirección de grupos.
- Descriptivos contra bibliotecas de referencia, incluidos casos no definidos.
- Checkpoints para cada operación; ceros, ausentes e IDs de texto preservados.
- Fallo tras imputación y antes del log: datos imputados recuperables e informe
  que identifica la operación fallida, sin dataset final falso.
- Hash incorrecto, ruta inválida o artefacto ausente impiden usar esa evidencia.
- Ninguna herramienta fuera de etapa puede ejecutarse aunque el modelo la invoque.
- Datos individuales de tablas y figuras no se filtran al contexto remoto.
- Toda advertencia heredada relevante aparece en su consumidor y en el informe;
  una resolución requiere evidencia y conserva su historial.
- Revisión de preparación impide combinar pruebas o figuras del intento anterior
  con el dataset nuevo sin identificación explícita.
- Reanudar no repite operaciones confirmadas; usar prompts distintos requiere
  un cambio de versión explícito y no altera el intento previo.
- Cada etapa cerrada tiene informe, manifiesto y traspaso, incluso si no transformó
  datos; un salto tiene motivo y dependencias validadas.
- El nuevo catálogo y la demo no llaman a PCA ni exigen resultados multivariantes.
- No se confunden pruebas offline del funcionamiento con evaluación de decisiones
  de un LLM real. Solo esta última requiere proveedor/modelo y credenciales.

El entorno y la referencia de A están preparados. La persistencia de B, la
coordinación de C, los descriptivos de D, la auditoría univariante de E y las
figuras e informes de F están implementados y verificados. Las cinco etapas y
sus prompts están activos en nuevas ejecuciones; G conserva el trabajo pendiente
descrito anteriormente.
