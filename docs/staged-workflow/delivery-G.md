# Entrega G: evaluación y verificación offline

Estado: parte offline implementada y verificada. La evaluación de un LLM real
permanece preparada y **no ejecutada**; no se han utilizado credenciales ni APIs.

## Alcance de la entrega

G reúne las pruebas de ejecución, restauración, integridad, privacidad y cálculo
en una evaluación reproducible, con casos identificados e informes JSON/Markdown.
Añade una auditoría de ejecuciones guardadas que no restaura ni modifica sus datos.

La evaluación utiliza un proveedor determinista de respuestas programadas. Comprueba
que el programa entrega los prompts y herramientas correctos y ejecuta las decisiones
registradas de forma coherente. No demuestra que un modelo comprenda las instrucciones,
elija el método adecuado o redacte una interpretación científicamente válida.

Se mantienen el alcance estadístico actual, las bibliotecas del artículo, las
convenciones de preparación/PCA y la exclusión de corrección de batch y QC.
No se añaden dependencias.

## Comandos

Ejecutar todos los casos, sin API key:

```bash
.venv/bin/python -m metabolomics_agent evaluate-offline --output runs/evaluation-G
```

Elegir casos y semilla:

```bash
.venv/bin/python -m metabolomics_agent evaluate-offline \
  --case paired --case pca_only --seed 42 --output runs/evaluation-selected
```

Si se selecciona `resume`, se incluye también `independent` como referencia de
una ejecución ininterrumpida. El directorio de salida debe ser nuevo. La semilla,
los datos generados, los paquetes y las decisiones programadas quedan registrados.
La reproducción numérica se refiere a los datos y versiones documentados; los
IDs, fechas y rutas de los informes pueden diferir entre ejecuciones.

Auditar una ejecución por etapas existente:

```bash
.venv/bin/python -m metabolomics_agent audit-run \
  --run runs/evaluation-G/cases/paired/run --output runs/audit-paired
```

La salida de la auditoría debe estar fuera de la ejecución inspeccionada. No se
sobrescriben informes ni se usa `RunState.restore`, por lo que la auditoría no
reconcilia ni modifica intentos históricos. Los perfiles anteriores conservan
sus capacidades; no se les exigen fuentes gráficas anteriores a F. El protocolo
`legacy` anterior al flujo por etapas conserva sus pruebas independientes y queda
fuera de este nuevo comando.

Códigos de salida: 0 para comprobaciones superadas; 1 para comprobaciones fallidas;
2 para una auditoría coherente de un análisis todavía incompleto. La evaluación
completa puede superar todos sus casos incluyendo un fallo deliberado: en ese
caso se conserva explícitamente el estado incompleto del análisis afectado.

## Casos de evaluación

| Caso | Comprobación principal |
|---|---|
| `independent` | Preparación, Welch/BH, distribuciones, volcano, boxplots y heatmap significativo |
| `paired` | Alineación por identificadores, t pareada y conexiones de pares en boxplots |
| `descriptive` | Descriptivos y omisiones justificadas de pruebas y figuras |
| `pca_combined` | PCA exploratorio seguido de univariante, con resultados independientes |
| `pca_only` | PCA y descriptivos sin pruebas univariantes |
| `optional_pca` | Omisión explícita de un PCA opcional |
| `no_discoveries` | Pruebas válidas sin descubrimientos y heatmap omitido con motivo |
| `resume` | Reanudación con un proveedor nuevo, usando snapshots sin los archivos originales |
| `guardrails` | Rechazo de herramientas fuera de etapa y de lectura de tablas individuales |
| `unsafe_log` | Conservación de matrices imputadas cuando el logaritmo encuentra un cero |

Las entradas son sintéticas y las decisiones están aisladas en `case.json`.
Las acciones programadas no se introducen en los prompts como respuestas esperadas.
El proveedor de reanudación obtiene su posición del historial persistido; no
requiere conservar un contador Python del proceso anterior.

En `resume` se detiene el agente después de la comparación y antes de cerrar su
etapa. Los archivos originales se retiran temporalmente, se restaura desde los
snapshots y después se recuperan esos archivos para conservar entradas utilizables.
La comparación, concentraciones y escala de prueba deben coincidir byte por byte
con `independent`; preparación y pruebas se ejecutan una sola vez.

`unsafe_log` termina en `needs_input`, sin dataset final ni comparación falsos.
Se conservan los checkpoints de imputación y máscara y el informe incompleto.
Que ese caso supere su evaluación significa que el bloqueo se ha gestionado como
estaba previsto; no significa que el análisis solicitado haya concluido.

## Comprobaciones y oráculos

La auditoría separa:

- Integridad SHA-256, esquemas, referencias de artefactos e índice de estado.
- Estados, informes, manifiestos y traspasos de las etapas vigentes, con omisiones
  justificadas y propagación de incidencias hasta el informe final.
- Prompt congelado, bloque activo y consistencia entre peticiones, también tras
  reanudar. Se guarda un inventario de huellas y longitudes de los prompts.
- Herramientas ofrecidas y utilizadas, con validación de sus parámetros para el
  perfil actual y detección de cambios en sus esquemas.
- Ausencia de identificadores individuales inequívocos en las peticiones y de
  lecturas autorizadas de tablas privadas. Los identificadores breves o coincidentes
  con etiquetas públicas se declaran como ambiguos/exentos: es un cribado, no una
  prueba formal de anonimización.
- Preparación respecto a entradas congeladas: filtros, imputación, normalización,
  escala de prueba y máscara.
- Descriptivos respecto a NumPy/SciPy, con recuentos, cuantiles, forma, CV y moda
  cuando se solicita. Se respetan los valores no definidos y la escala registrada.
- Pruebas, efectos, intervalos y correcciones respecto a SciPy/statsmodels. Los
  recuentos originales observados se mantienen separados de los valores disponibles
  tras imputación. La corrección conserva su familia declarada.
- PCA respecto a su fuente, escalado, espectro, ecuaciones de autovectores y
  reconstrucción desde el modelo guardado, admitiendo la indeterminación de signo
  y las representaciones equivalentes en subespacios degenerados.
- Datos de dibujo: bins, puntos, pares, boxplots, volcano, selección y cobertura de
  heatmaps, scores, varianza y pesos PCA.

Los oráculos comprueban la ejecución de los métodos registrados. No determinan
si sus supuestos científicos están justificados en una población real ni validan
por sí solos una afirmación narrativa del agente.

Una respuesta truncada con llamadas sin respuesta queda identificada como un
análisis incompleto y con lote no seguro para reanudación automática; no se trata
como un cálculo completado. La restauración mantiene las reglas existentes para
rechazar esos lotes pendientes.

## Ajustes de prompts y errores

Se han revisado en inglés las instrucciones comunes y por etapa, conservando
sus copias documentales idénticas. Las instrucciones distinguen capacidades
vigentes de perfiles archivados, aclaran que una revisión no cambia el alcance
inmutable del flujo y exigen declarar las adaptaciones univariantes informadas
por exploración PCA como exploratorias.

Los rechazos que el agente produce antes de llamar al registro de herramientas
se incorporan ahora al informe de etapa mediante `AGENT_TOOL_CALL_REJECTED`, además
del historial de eventos. Esto evita que un intento de herramienta no ofrecida o
un cierre de lote inválido desaparezca de los informes posteriores.

## Artefactos de evaluación

```text
evaluation-G/
  evaluation.json             # resultados por caso, comprobaciones y versiones
  evaluation.md               # resumen legible; estado de evaluación y de análisis separados
  live_evaluation_manifest.json
  manifest.json               # hashes relativos de los archivos producidos
  cases/<case>/
    case.json                 # estímulos programados y expectativas offline
    input/                    # datos sintéticos y study.json
    run/                      # ejecución real del agente con proveedor programado
    audit.json                # comprobaciones individuales y huellas de prompts
    audit.md
```

La suite y sus auditorías pueden archivarse con todo su árbol de archivos. Los
manifiestos y enlaces de casos usan rutas relativas. Las rutas absolutas guardadas
en metadatos históricos describen la ubicación original del cálculo o auditoría;
las tablas científicas y snapshots permanecen locales y no se reescriben al copiar.

## Evaluación del LLM preparada y pendiente

`live_evaluation_manifest.json` contiene los estudios, peticiones y criterios de
revisión, con `execution_status="not_run"`. Modelo, proveedor, presupuesto de tokens
y gasto máximo quedan sin configurar. Solo se nombra la variable de credencial;
no se lee ni se copia su valor.

La rúbrica reserva revisión humana para objetivo, diseño, preparación, elección
de métodos, fidelidad de las afirmaciones, selección/PCA, adaptaciones y limitaciones.
Sus criterios permanecen en `not_assessed`. La evaluación futura debe admitir
alternativas justificadas: no se exige reproducir la secuencia del proveedor offline.
Los estímulos de guardas y de reanudación son pruebas del programa y no se presentan
como tareas de razonamiento del modelo.

No se ha creado ni ejecutado un evaluador que consuma APIs automáticamente.
La ejecución con un proveedor real y su revisión científica constituyen la parte
pendiente de G.

## Verificación de esta entrega

- **142 pruebas superadas**: las 128 anteriores y 14 de G.
- **10/10 escenarios** superados, incluido un análisis deliberadamente incompleto
  cuyo bloqueo y checkpoints se conservan de forma explícita.
- **11.705 valores contrastados** con referencias numéricas y **4.093 comprobaciones**
  de ejecución, estructura, privacidad, prompts y resultados.
- Pruebas que impiden conexiones de red y construcción de proveedores reales.
- Detección de corrupción de artefactos, cambios de prompts/esquemas y resultados
  numéricos incorrectos incluso con hashes válidos.
- Reanudación con proveedor nuevo y fuentes temporalmente ausentes; salidas
  científicas idénticas byte por byte y sin repetir preparación ni pruebas.
- Informes, manifiestos y protocolo de revisión del LLM generados, con evaluación
  real explícitamente pendiente y cero llamadas API.
- Corpus final publicado mediante copia verificada de **1.530 archivos**; auditoría
  de la copia publicada superada, sin modificar sus registros científicos.

El corpus definitivo está en `evaluation-final`. Se conserva la primera pasada
en la unidad montada como interrumpida: la validación se completó en almacenamiento
local para evitar la latencia de escritura y luego se publicaron los archivos
confirmando todos sus hashes.

Evidencias locales, excluidas de Git bajo `runs/`:

- [Validación estructurada](../../runs/validation-G-20261110/validation.json).
- [Pruebas anteriores](../../runs/validation-G-20261110/pytest-core.log).
- [Pruebas de G](../../runs/validation-G-20261110/pytest-evaluation-final.log).
- [Informe de los diez casos](../../runs/validation-G-20261110/evaluation-final/evaluation.md).
- [Resultados completos JSON](../../runs/validation-G-20261110/evaluation-final/evaluation.json).
- [Manifiesto de archivos](../../runs/validation-G-20261110/evaluation-final/manifest.json).
- [Verificación de publicación](../../runs/validation-G-20261110/publication.json).
- [Auditoría de la copia publicada](../../runs/validation-G-20261110/audit-published-paired/audit.md).
- [Caso pareado](../../runs/validation-G-20261110/evaluation-final/cases/paired/audit.md).
- [Reanudación](../../runs/validation-G-20261110/evaluation-final/cases/resume/audit.json).
- [Error de logaritmo controlado](../../runs/validation-G-20261110/evaluation-final/cases/unsafe_log/run/incomplete.md).
- [Protocolo LLM pendiente](../../runs/validation-G-20261110/evaluation-final/live_evaluation_manifest.json).
