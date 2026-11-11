# Plan H: evaluación reutilizable por etapas y reproducción de Lipidomics_dataset.xlsx

Estado: **H1–H7 implementadas y verificadas; H8 pendiente por indicación del
usuario**. Fecha: 11 de noviembre de 2026. Este documento conserva la
especificación acordada de la infraestructura reutilizable. Los resultados,
cobertura y límites están en [delivery-H.md](delivery-H.md); el uso y registro
de nuevos estudios se explican en [evaluation-guide.md](evaluation-guide.md).

## 1. Objetivo y alcance

Crear una infraestructura que evalúe las salidas de cada etapa contra criterios
explícitos y versionados. La unidad de evaluación será **conjunto de datos +
pregunta científica + protocolo/condiciones aceptables**. Un archivo podrá tener
varios casos; las expectativas no se deducirán retrospectivamente de lo que haya
hecho el agente.

`Lipidomics_dataset.xlsx` será el primer conjunto real registrado. Los valores
127 lípidos y 227 muestras serán propiedades de ese conjunto, nunca constantes
del motor genérico. Se reutilizarán la persistencia de B–F, la evaluación offline
de G y la auditoría de ejecuciones guardadas.

Se conservan las cinco etapas actuales y un bloque PCA evaluable por separado
dentro de la etapa estadística. Fuera del alcance: batch/QC, ANOVA, Kruskal–Wallis,
post hoc, clustering, sPCA, PPCA, PLS/OPLS, t-SNE, UMAP, redes y datos crudos de MS.
Admitir tres grupos en descriptivos/PCA no añadirá inferencia multigrupo.

Se distinguirán dos modalidades:

- `reproduction`: protocolo fijado y concordancia con resultados de referencia.
- `open_analysis`: restricciones y alternativas aceptables declaradas; se
  verifican cálculos, coherencia y cumplimiento, manteniendo revisión experta
  cuando no exista una decisión científica verificable automáticamente.

No se exigirán una secuencia única de herramientas ni una redacción literal.
Los prompts y las peticiones nuevas al agente estarán en inglés. Los esquemas
de herramientas seguirán procediendo del registro Python.

## 2. Contratos y separación de responsabilidades

Se proponen contratos tipados, con validación estricta y versiones explícitas:

| Contrato | Contenido |
|---|---|
| `DatasetManifest` | Archivos y hashes, origen, hoja, unidades, IDs, metadatos, escalas y normalización conocida/desconocida |
| `EvaluationCase` | Dataset, pregunta, modalidad, población, diseño, requisitos, condiciones aceptables, resultado de ejecución esperado y política de continuación |
| `StageExpectation` | Etapa/bloque, aplicabilidad, artefactos requeridos, entradas de referencia, dependencias y criterios |
| `MetricSpec` | ID estable, validador permitido, campos comparados, umbral/tolerancia, gravedad, carácter obligatorio y procedencia de la expectativa |
| `ReferenceManifest` | Tablas/matrices, hashes, script productor, versiones, revisión y nivel de independencia respecto al código productivo |
| `MetricResult` | Esperado, observado, denominador, tolerancias, veredicto, gravedad, evidencias y explicación |
| `StageEvaluation` | Intento evaluado, métricas, cobertura, incidencias heredadas, aplicabilidad y criterios pendientes |
| `CaseEvaluation` | Resultados por etapa, estado científico, resultado de evaluación, referencias y límites de lo comprobado |

Cada referencia distinguirá resultados impresos por los autores, resultados
recalculados desde sus scripts, referencias de un colaborador, comprobaciones
matemáticas y ausencia de referencia. Conservará fuente, fecha, autor/revisor y
estado de revisión; compartir una biblioteca con producción limita la
independencia de la comprobación y se declarará.

El evaluador será externo al proveedor LLM y no usará la autoevaluación del
agente como prueba suficiente. Expectativas privadas, scripts programados y
respuestas esperadas no se introducirán en prompts, herramientas o traspasos.
Los criterios que formen parte legítima del encargo científico sí estarán en la
petición, por ejemplo comparar T frente a N mediante Student/Holm–Šidák.

Las condiciones se expresarán mediante campos y validadores registrados, sin
ejecutar expresiones arbitrarias procedentes de JSON. Las rutas, hashes y
referencias de artefactos se validarán con las garantías existentes.

## 3. Aplicabilidad, estados y aceptación

Cada bloque o entregable declarará `required`, `optional`, `conditional` o
`out_of_scope`. Las condiciones dependerán del objetivo y de hechos verificables,
no solo de la decisión del agente. Se fijarán antes de la ejecución, aunque
algunas se resolverán después usando resultados concretos.

Ejemplos:

| Condición del caso | Comportamiento esperado |
|---|---|
| Objetivo descriptivo sin contrastes | Omitir univariante y registrar motivo |
| No se requiere transformar | Preparación revisada y completada, valores preservados y operaciones omitidas registradas |
| PCA fuera del objetivo | Omitir solo el bloque PCA |
| PCA obligatorio con entradas incompatibles | Registrar impedimento; detenerse o pedir información conforme al contrato |
| Ninguna variable significativa | Registrar omisión del heatmap significativo, sin fabricar una selección |
| Figura requerida y viable | Generarla; no admitir una omisión discrecional |

La etapa estadística podrá contener PCA sin univariante o univariante sin PCA.
No se marcará toda la preparación como no aplicable por no generar una matriz
transformada, ni toda la estadística por omitir uno de sus bloques.

Se mantendrán separados:

- `execution_status`: estado observado del flujo y sus operaciones, conservando
  los estados productivos actuales y sus omisiones/bloqueos.
- `evaluation_status`: `passed`, `failed`, `not_applicable`, `not_assessed` o
  `blocked_by_dependency`, por criterio y etapa.
- `expected_outcome_match`: concordancia con el resultado de ejecución esperado,
  incluido un caso que deba terminar correctamente en `needs_input`.

Una detención correcta puede superar su evaluación de comportamiento sin
completar el objetivo científico. Los bloques dependientes permanecerán
identificados como bloqueados; el informe no afirmará que sus resultados se
validaron. Las expectativas de esa rama y el estado científico incompleto serán
visibles en el resumen del caso.

Reglas de agregación:

1. Un incumplimiento crítico impide aprobar el ámbito afectado; otros aciertos
   no lo compensan.
2. Un criterio obligatorio sin evaluar impide declarar validado completamente
   ese ámbito. Se publicará la parte comprobada y la pendiente.
3. Los criterios no aplicables no cuentan en el denominador ni suman aprobados.
4. Los no evaluados y bloqueados se contabilizan por separado, con motivo.
5. La tasa de aprobación será aprobados / criterios efectivamente evaluados;
   la cobertura será evaluados / aplicables. Se mostrarán también los recuentos
   de criterios obligatorios, críticos, pendientes y bloqueados. Un denominador
   vacío producirá `null` con explicación, nunca 100 %.
6. Se fijará el conjunto de criterios del caso antes de ejecutarlo. No se
   inflará la nota contando miles de celdas correctas frente a un fallo de diseño:
   las celdas comparadas serán una métrica auxiliar de cobertura numérica.

## 4. Catálogo de métricas por etapa

Los umbrales serán específicos de cada caso y métrica. Los recuentos, identidades,
estados y decisiones categóricas esperadas se contrastarán exactamente. Las
comparaciones numéricas alinearán por IDs, comprobarán forma, escala y valores
no definidos antes de aplicar tolerancias.

### 01 Coordinación

| Métrica propuesta | Medición y aceptación |
|---|---|
| `coordination.requirement_coverage` | Requisitos satisfechos / aplicables; todos los obligatorios satisfechos |
| `coordination.design_match` | Diseño, referencia, dirección y pairing concordantes; discrepancia crítica |
| `coordination.cohort_match` | IDs y recuentos por población previstos, sin inclusiones/exclusiones silenciosas |
| `coordination.scope_violations` | Recuento de operaciones fuera del alcance; objetivo cero |
| `coordination.applicability_match` | Bloques requeridos/omitidos según reglas del caso y motivos verificables |

En reproducción se exige el protocolo fijado. En análisis abierto se aceptan
planes compatibles con las condiciones registradas; las justificaciones no
automatizables conservan estado de revisión pendiente.

### 02 Preparación, exploración y descriptivos

| Métrica propuesta | Medición y aceptación |
|---|---|
| `preparation.identity_integrity` | Identidades, orden documentado y correspondencia con metadatos; cero pérdidas no justificadas |
| `preparation.diagnostic_accuracy` | Recuentos de ausencias, ceros, duplicados y n por grupo frente a entrada congelada |
| `preparation.filter_match` | Variables retiradas, conjunto conservado y motivos frente a reglas/referencia |
| `preparation.imputation_match` | Máscara, recuentos y valores imputados; ausencia de cambios ajenos a la operación declarada |
| `preparation.matrix_agreement` | Error absoluto máximo, error relativo definido y fracción dentro de tolerancia por matriz intermedia |
| `preparation.descriptive_agreement` | Resúmenes por grupo/escala/origen, con cuantiles, ddof y motivos de indefinición explícitos |
| `preparation.operation_traceability` | Operaciones aplicadas con padres, parámetros, escalas, hashes y checkpoints válidos |

Ausencia de imputación/normalización se verifica como política de no modificación;
no se exige una matriz transformada ficticia. Los cambios se comparan respecto a
la transformación declarada, sin exigir conservar valores que debían cambiar.

Porcentaje de ausencias, celdas imputadas, variables filtradas y atípicos serán
también descriptores del dataset. No son puntuaciones de calidad del agente por
sí solos. Un umbral de alerta requiere justificación en el contrato; no habrá
un máximo universal de ausencias aplicable a cualquier estudio.

### 03 Análisis univariante

| Métrica propuesta | Medición y aceptación |
|---|---|
| `univariate.protocol_match` | Prueba, escala, alternativa, alfa, política de valores y corrección declaradas |
| `univariate.sample_count_match` | N observado/disponible/imputado y pares efectivos por variable |
| `univariate.numeric_agreement` | Estadístico, grados de libertad, efectos, intervalos y p originales/ajustados frente a referencia |
| `univariate.family_match` | Identidades de las hipótesis de la corrección; ninguna eliminación silenciosa |
| `univariate.effect_direction_match` | Referencia/tratamiento y signo coherentes, tratando ceros y valores indefinidos explícitamente |
| `univariate.decision_agreement` | Concordancia de estados evaluable/no evaluable y decisiones con el alfa registrado |
| `univariate.selection_agreement` | Precisión, sensibilidad y Jaccard frente al conjunto significativo de referencia |

La selección compara análisis, no identifica efectos biológicos verdaderos.
Conjuntos vacíos tendrán estado explícito: si ambos son vacíos, la coincidencia
exacta pasa; cocientes no definidos quedan en `null`, sin divisiones por cero.
Un p no significativo, una prueba de normalidad no rechazada o un gran número de
descubrimientos no serán criterios autónomos de éxito.

### Bloque PCA

| Métrica propuesta | Medición y aceptación |
|---|---|
| `pca.input_match` | Muestras, variables y matriz acordadas; sin selección por p salvo protocolo explícito |
| `pca.scaling_match` | Centros, divisores, convención ddof y matriz frente a referencia |
| `pca.variance_agreement` | Autovalores y varianza explicada de los componentes solicitados |
| `pca.coordinate_agreement` | Scores y pesos admitiendo cambios de signo coherentes entre ambos |
| `pca.subspace_agreement` | Comparación de subespacios en bloques de autovalores prácticamente degenerados, con umbral declarado |
| `pca.reconstruction_agreement` | Reconstrucción y residuo acordes con la referencia para el número retenido de componentes |
| `pca.unsupervised_input` | Etiquetas y resultados univariantes excluidos de las entradas numéricas del ajuste |

La reconstrucción truncada no tiene por qué tener error cero. La separación
visual entre grupos no será una métrica de aprobación. El evaluador distinguirá
pesos de autovectores, correlaciones variable-componente y contribuciones.

### 04 Visualización

| Métrica propuesta | Medición y aceptación |
|---|---|
| `visualization.required_coverage` | Figuras obligatorias viables generadas / obligatorias aplicables |
| `visualization.source_agreement` | Puntos, resúmenes, bins y coordenadas frente a sus artefactos vigentes |
| `visualization.label_accuracy` | Variables, grupos, componentes, unidades y escalas correctos |
| `visualization.selection_agreement` | Identidades seleccionadas, criterio y comparación de origen |
| `visualization.omission_accountability` | Omisiones de valores/figuras con recuentos y motivos comprobables |
| `visualization.reproducibility` | Datos gráficos, configuración y procedencia suficientes para regenerar la salida |

En heatmaps significativos se comprobarán el ajuste, alfa y familia de origen,
así como la matriz centrada/escalada exclusivamente para dibujo. La revisión de
legibilidad será humana y quedará pendiente hasta realizarse. La igualdad de
píxeles no será requisito de concordancia científica.

### 05 Interpretación e informe

| Métrica propuesta | Medición y aceptación |
|---|---|
| `reporting.quantitative_fidelity` | Afirmaciones numéricas verificadas/correctas y discrepantes frente a tablas vigentes |
| `reporting.objective_coverage` | Preguntas obligatorias respondidas o impedimentos comunicados |
| `reporting.limitation_coverage` | Incidencias y limitaciones relevantes trasladadas / requeridas |
| `reporting.evidence_validity` | Evidencias existentes, vigentes y correspondientes a cada hallazgo |
| `reporting.conclusion_review` | Concordancia o discrepancias científicas revisadas respecto a una referencia experta |

Para automatizar cifras se propondrá un contrato de hallazgo estructurado con
comparación, variable, métrica, valor y evidencia. La generación de frases
cuantitativas podrá ser determinista. Un ID válido no demuestra que el texto
libre sea verdadero. No se declarará 100 % de fidelidad de toda la narrativa
por haber revisado solo las afirmaciones estructuradas; se publicará el ámbito
realmente verificado y lo que requiera revisión humana.

La plausibilidad biológica, causalidad y suficiencia científica no se resolverán
con una búsqueda de palabras. Un LLM auxiliar podrá apoyar una revisión futura,
pero su juicio se identificará como tal y no sustituirá la referencia numérica.

### Métricas transversales y tolerancias

- Cobertura de manifiestos, integridad, dependencias, escalas, planes y versiones.
- Incidencias relevantes recibidas, resueltas con evidencia y propagadas; cero
  pérdidas de advertencias críticas en los traspasos.
- Fallos por dependencia identificados sin atribuirlos automáticamente al
  algoritmo de la etapa posterior.
- Reanudación sin repetir operaciones confirmadas y sin mezclar intentos vigentes
  con resultados sustituidos.
- Tiempos, llamadas y consumo de tokens por etapa; son métricas de eficiencia y
  se presentarán separadas de la validez científica. El coste monetario solo se
  calculará si existe una tarifa identificada y verificable.
- Error absoluto y relativo con tratamiento de ceros, infinitos y ausencias;
  máscaras y motivos de indefinición se contrastarán explícitamente.
- Tolerancias por métrica y precisión disponible en la fuente. Un resultado
  publicado redondeado no exige precisión que la fuente no proporciona.
- Para p pequeños no se reutilizará una tolerancia absoluta genérica que acepte
  cero como equivalente a cualquier p diminuto. Se usarán referencias de
  precisión suficiente y tolerancias justificadas; un cero por underflow se
  documentará. No se calcularán errores relativos dividiendo por cero.

## 5. Ejecución, persistencia e informes

Se implementarán dos modos complementarios:

1. `end_to_end`: evaluar los artefactos reales de todo el flujo y la propagación
   de problemas entre etapas.
2. `stage_isolated`: alimentar una etapa con un paquete de entradas de referencia
   válido y ejecutar las herramientas productivas a través de sus contratos.
   No se saltarán permisos o validaciones mediante invocaciones internas libres.

Un paquete aislado incluirá matrices, metadatos, planes, escalas, advertencias,
procedencia y dependencias necesarias. Se registrará que es una entrada inyectada
de referencia, no una etapa previa realmente ejecutada. Este modo permite
distinguir un fallo local de otro heredado.

El ejecutor admitirá evaluar evidencia existente y lanzar ejecuciones nuevas
programadas offline. Los resultados de evaluación se guardarán **fuera de la
ejecución inspeccionada**, con referencia a etapa, intento y hashes; no se
reescribirán informes científicos ni snapshots históricos.

Estructura propuesta, todavía no creada:

```text
evaluations/cases/<case_id>/
  dataset_manifest.json
  case.json
  expectations.json
  references/manifest.json
  references/<tables_and_scripts>

runs/<evaluation_id>/
  evaluation.json
  evaluation.md
  case_snapshot.json
  reference_manifest.json
  stages/<stage_id>/<attempt_id>/evaluation.json
  stages/<stage_id>/<attempt_id>/evaluation.md
  scientific_run/                  # solo cuando el ejecutor crea un análisis
```

El análisis productivo conserva sus datos intermedios, informes JSON/MD y
traspasos habituales. El evaluador añadirá el resultado objetivo de cada etapa.
Podrá observar los límites estables de etapa durante una ejecución controlada
o auditar después una ejecución ya guardada. La política declarada del caso
determinará qué incumplimientos críticos detienen pasos dependientes y cuáles
permiten continuar para diagnóstico; siempre se conservará lo ya producido.

Al siguiente bloque se transmitirán los hechos operativos necesarios:
restricciones, fallos de validación y advertencias relevantes con sus evidencias.
No se transmitirán cifras esperadas ni soluciones privadas del evaluador. La
evaluación completa quedará local, vinculada al intento correspondiente.

Con pocas referencias se declarará cobertura parcial. Se admitirán comprobaciones
de integridad, propiedades matemáticas y cambios controlados del dato, sin
presentarlas como validación de conclusiones biológicas desconocidas.

## 6. Secuencia H1–H8 y entregables

### H1. Contrato genérico y catálogo de criterios

- Definir los contratos de la sección 2, sus versiones y reglas de agregación.
- Registrar el catálogo de métricas por etapa y la aplicabilidad por bloque.
- Separar datos visibles para el agente de expectativas reservadas.
- Documentar cómo registrar un dataset, varios objetivos para el mismo archivo
  y un caso sin resultados numéricos de referencia.
- Preparar un caso sintético mínimo para validar el contrato sin descargar datos.

**Aceptación:** el contrato expresa reproducción, análisis abierto, omisión válida,
bloqueo esperado y revisión pendiente; rechaza incoherencias y no convierte la
ausencia de referencia en un aprobado.

### H2. Evaluadores reutilizables por etapa

- Extraer/adaptar comprobaciones de `run_audit.py` y `evaluation.py`, conservando
  la compatibilidad de las auditorías de G y sus salidas.
- Implementar comparación por IDs, máscaras, estados y políticas numéricas por
  métrica, con errores máximos y denominadores explícitos.
- Añadir cobertura de requisitos, aplicabilidad y propagación de incidencias.
- Mantener cálculo de referencia separado de la función productiva comprobada.
- Preparar el contrato de verificación de hallazgos cuantitativos y el estado
  pendiente de las afirmaciones no verificadas automáticamente.

**Aceptación:** un fallo crítico no se compensa con aciertos; matrices desalineadas,
p pequeños erróneos y valores indefinidos mal representados se detectan. Los
descriptores de calidad del dato no se confunden con errores del agente.

### H3. Ejecutor genérico e informes por etapa

- Implementar evaluación de ejecuciones existentes y ejecuciones programadas
  nuevas, con modos completo y aislado.
- Guardar snapshots de casos, referencias, planes, entorno, código identificable
  y resultados por etapa/intento, sin mutar la ejecución evaluada.
- Producir JSON y Markdown con validez, concordancia, cobertura y revisión
  científica pendiente; registrar fallos locales y por dependencia.
- Añadir CLI genérica propuesta: `evaluate-case --case <case.json> --output <dir>`
  y `evaluate-case --case <case.json> --run <existing_run> --output <dir>`.
- Ofrecer `--stage` únicamente con un paquete de referencia validado para ese
  bloque. Definir códigos de salida y agregación sin perder la distinción entre
  éxito de evaluación y análisis incompleto esperado.

**Aceptación:** el mismo ejecutor evalúa al menos dos casos de demostración con
expectativas diferentes. Ningún comando offline carga credenciales o llama a un
proveedor remoto. No aparecen respuestas esperadas en el contexto del agente.

### H4. Registrar Lipidomics_dataset.xlsx y generar sus referencias

- Descargar el conjunto número 1 desde la fuente de los autores; conservar el
  Excel original intacto y registrar hash, URL, fecha, hoja y tipos.
- Verificar 127 lípidos y 227 muestras: N=97, T=109, PAN=21; IDs únicos, columnas
  `Sample Name`/`Label`, ausencias, ceros, constantes y valores no finitos.
- Contrastar los adjuntos de las páginas utilizadas; no asumir que archivos con
  el mismo nombre tienen idéntico contenido. Toda selección de muestras queda
  fijada mediante IDs e identificación de exclusiones.
- Documentar normalización previa a NIST SRM 1950 y no añadir otra normalización
  para esta reproducción. No introducir ausencias artificiales ni usar el
  dataset número 2 como validación independiente.
- Producir referencias locales mediante scripts mínimos de los autores y
  bibliotecas científicas, sin llamar a las funciones productivas auditadas.
- Ampliar la comprobación de números publicados y guardar tablas completas,
  diagnósticos, matrices, parámetros y manifiestos.

Casos iniciales, con nombres propuestos:

| Caso | Población | Protocolo fijado |
|---|---|---|
| `lipidomics.student_t_T_vs_N` | 206 muestras T/N | Concentraciones originales; Student agrupado bilateral; Holm–Šidák en los 127 lípidos; alfa 0.05 |
| `lipidomics.mannwhitney_T_vs_N` | 206 muestras T/N | Concentraciones originales; Mann–Whitney bilateral; Holm–Šidák en los 127 lípidos; alfa 0.05 |
| `lipidomics.pca_all_groups` | 227 muestras N/PAN/T | Concentraciones originales sin log; StandardScaler, ddof=0; 10 componentes; todas las variables |

Cada ejecución parte del original; Student y Mann–Whitney son casos separados,
evitando invalidar el primero mediante una revisión del segundo. Los análisis
univariantes no imputan, no normalizan de nuevo y no transforman. Si el archivo
obliga a retirar variables o tratar ausencias, se documentará la incompatibilidad
antes de sustituir el protocolo o reducir la familia de 127 pruebas.

La referencia t utilizará `statsmodels.stats.weightstats.ttest_ind` con
`usevar='pooled'`; Mann–Whitney usará SciPy y las correcciones statsmodels. Se
reproducirá el diagnóstico Lilliefors de PC 34:1 en ambos grupos. Se registrarán
las convenciones de Mann–Whitney, empates y versiones empleadas.

Comprobaciones publicadas ya recogidas parcialmente en `benchmark.py`:

| Resultado | Valor publicado |
|---|---:|
| PC 34:1, t | 2.0056017821278194 |
| PC 34:1, p sin ajustar | 0.04621995094509444 |
| PC 34:1, grados de libertad | 204 |
| PC 36:5, U | 2467 |
| PC 36:5, p | 4.0874221736427684e-11 |
| SM 41:1;O2, p t ajustado | 1.080575e-19 |

Las tablas completas recalculadas no se presentarán como valores íntegramente
publicados. La referencia PCA ejecutará el código Python de los autores y
registrará el solver realmente resuelto; se justificará la comparación con el
solver completo determinista del proyecto mediante equivalencia numérica.

**Aceptación:** archivo identificado, población verificada y referencias que
superan los controles publicados antes de comparar con el motor. Una divergencia
se registra; no se cambian métodos para obtener el resultado deseado.

### H5. Adaptaciones mínimas del flujo científico

- Añadir ámbito de carga descriptivo/PCA explícito antes de la coordinación que
  admita N/PAN/T. Actualmente la selección inicial conserva solo dos grupos.
  No usar etiquetas ficticias ni reagrupar pacientes para conservar PAN.
- Mantener validaciones de dos grupos y pairing en las pruebas univariantes;
  actualizar estado, descriptivos, auditoría y figuras para el nuevo ámbito.
- Añadir selección explícita de Lilliefors normal en los diagnósticos necesarios
  para el caso, preservando Shapiro y los perfiles históricos. No elegir pruebas
  automáticamente mediante un umbral de normalidad.
- Incorporar un gráfico de pesos PCA PC1–PC2 para todas las variables, además de
  las barras por componente existentes. Usar IDs del índice de loadings: el
  ejemplo publicado parece desplazar las etiquetas al incluir `Label` entre
  los nombres utilizados para anotar.
- Añadir el contrato de hallazgos cuantitativos que permita comprobar las cifras
  del informe. La narrativa libre no verificada seguirá indicada como pendiente.
- Versionar los cambios de modelos, capacidades, herramientas y prompts. No
  migrar silenciosamente ejecuciones ni evaluaciones de G.
- Mantener la selección de herramientas por etapa y las matrices individuales
  locales. Los nuevos prompts y objetivos de caso serán en inglés.

**Aceptación:** PCA conserva las 227 muestras y 127 variables; univariante conserva
las 206 previstas; Lilliefors y Shapiro quedan identificados; las etiquetas son
correctas y las ejecuciones históricas mantienen sus contratos.

### H6. Ejecutar y evaluar los tres casos reales offline

- Preparar peticiones y proveedores programados específicos de cada caso que
  recorran las herramientas y etapas reales, sin proporcionar las referencias
  numéricas al agente.
- Contrastar matrices y descriptivos intermedios, estadística y familias, PCA,
  fuentes de figuras, traspasos y hallazgos numéricos estructurados.
- Generar scores PCA PC1–PC2 con tres grupos, scree de 10 componentes y pesos
  PC1–PC2. La selección PCA no depende de significación univariante.
- Producir distribuciones/boxplots declarados y volcano por comparación; fijar
  explícitamente el fold change utilizado por el proyecto. Distinguir cualquier
  adaptación respecto a una figura publicada.
- Añadir heatmap significativo de Student/Holm–Šidák como ampliación propia. Otro
  heatmap de Mann–Whitney, si se solicita, tendrá su comparación y selección
  separadas. No añadir clustering.
- Ejecutar evaluación aislada donde haya discrepancias para localizar su origen;
  la revisión visual humana y científica quedará registrada por separado.

**Aceptación:** criterios obligatorios automatizables satisfechos, sin discrepancias
numéricas inexplicadas; cobertura y pendientes visibles. Se distingue resultado
publicado, cálculo desde código publicado y ampliación propia. No se afirma haber
reproducido todas las figuras del artículo: la reconstrucción PCA del manuscrito
usa otro conjunto de datos de gota.

### H7. Demostrar reutilización y detectar regresiones

- Registrar un segundo dataset sintético independiente y varios objetivos,
  incluidos descriptivo sin inferencia, PCA omitido y bloqueo previsto.
- Añadir un caso sin referencia numérica completa; comprobar que solo se aprueba
  lo verificable y el resto conserva `not_assessed`.
- Introducir errores controlados: emparejamiento, pérdida de muestras PAN,
  transformación accidental, p diminutos alterados, familia incompleta, etiquetas
  desplazadas y una cifra inventada con un ID de evidencia válido.
- Probar la localización por etapa y las dependencias: fallo de preparación
  frente al comportamiento de estadística con una entrada de referencia válida.
- Verificar invariancia al reordenar muestras/variables una vez alineadas por ID,
  reanudación y ausencia de repetición de operaciones. Las propiedades tendrán
  sus precondiciones; no se exigirá invariancia donde el método no la garantice.
- Ejecutar las pruebas de regresión pertinentes y la suite offline de G; conservar
  `reference-check` y documentar su relación con los nuevos casos.
- Publicar una guía de alta de un estudio de un colaborador, con o sin código y
  resultados de referencia. El segundo caso no exigirá cambiar el ejecutor.

**Aceptación:** los errores introducidos se detectan en su ámbito; las omisiones
válidas no producen falsos fallos ni aumentan artificialmente la aprobación;
añadir un dataset requiere configuración/referencias y, solo si procede, un
validador nuevo reutilizable. La regresión de G se conserva.

### H8. Evaluación posterior con un LLM real

- Reutilizar exactamente los contratos y evaluadores de las ejecuciones offline;
  cambiar solo el proveedor y registrar la diferencia.
- Mantener referencias y respuestas programadas fuera del contexto del modelo.
- Fijar proveedor/modelo, versiones de prompts, límites de coste/tiempo y número
  de intentos antes de ejecutar; no usar la trayectoria programada como única
  solución admisible de un análisis abierto.
- Medir finalización, actuación correcta ante bloqueos, protocolo, concordancia,
  cobertura, fidelidad narrativa, abstención, latencia y consumo por etapa.
- Revisar interpretación y legibilidad con un experto; conservar estados
  pendientes en lugar de convertir evaluaciones automáticas en revisión humana.
- Reservar estudios futuros para evaluación independiente: los casos usados para
  ajustar prompts no constituirán por sí solos prueba de generalización.

**Aceptación:** informe con varios intentos según el protocolo acordado y límites
de la evidencia, separado del éxito offline. H1–H7 no requieren API key; H8
necesitará acceso a un modelo y las credenciales que exija su proveedor. Esta
evaluación permanece pendiente y separada de las ejecuciones offline.

## 7. Cambios propuestos en el repositorio

Nombres orientativos, a concretar durante la implementación sin duplicar
responsabilidades que ya estén resueltas:

| Componente | Cambio previsto |
|---|---|
| `evaluation_models.py` y registro de casos | Contratos tipados, aplicabilidad y referencias por dataset/objetivo |
| Validadores de evaluación | Métricas registradas, comparación numérica por campo, criterios y agregación |
| `evaluation.py`, `evaluation_cases.py`, `run_audit.py` | Adaptadores de G, ejecución completa/aislada y auditoría sin mutación |
| `evidence_models.py`, informes de evaluación | Resultados vinculados a etapa/intento; esquemas históricos preservados |
| `models.py`, `data.py`, `state.py`, `workflow_models.py`, `workflow.py` | Ámbito descriptivo/PCA multigrupo explícito; inferencia de dos grupos intacta |
| Diagnósticos, `pca.py`, `plots.py` y `tools.py` | Lilliefors explícito, gráfico de pesos bidimensional y contratos de capacidades |
| `report.py` y modelos de hallazgos | Verificación de afirmaciones cuantitativas y cobertura narrativa declarada |
| `cli.py` | Entrada genérica por caso y evaluación de una ejecución ya guardada |
| `benchmark.py` | Referencias publicadas reutilizadas conservando el comando actual |
| `evaluations/cases/` | Configuraciones y referencias pequeñas con procedencia; datos grandes bajo la política del repositorio |
| Documentación y pruebas | Guía de nuevos casos, entregas H, aceptación por etapa y regresión |

No se requiere un framework nuevo ni varios agentes. Se priorizan pandas,
NumPy, SciPy, statsmodels, scikit-learn, Matplotlib, Seaborn y openpyxl, ya
presentes. Una necesidad de dependencia nueva se identificará y justificará
antes de cambiar el entorno.

## 8. Orden de trabajo y cierre

Secuencia prevista: H1 → H2 → H3 → H4 → H5 → H6 → H7; H8 se realiza después y
permanece separada de la aceptación offline. H1–H3 deben demostrar primero la
reutilización con casos pequeños antes de introducir particularidades del Excel.

H1–H7 se considerarán completas cuando exista un evaluador reutilizable, los tres
casos reales reproducibles, informes por etapa con cobertura honesta y una
demostración adicional de configuración sin modificar el ejecutor. Las revisiones
científicas o visuales no realizadas seguirán pendientes y no serán requisito
oculto para afirmar que el software offline está implementado.

La entrega incluirá un documento de resultados con lo implementado, lo ejecutado,
criterios satisfechos, discrepancias y revisiones pendientes. La implementación
avanzará por las subentregas que autorice el usuario. Ante un imprevisto que
impida cumplir el alcance acordado, se conservarán evidencias y se consultará
cómo proceder antes de sustituir el protocolo.

## 9. Fuentes del primer caso

- [Artículo de buenas prácticas](https://doi.org/10.1038/s41467-025-63751-1).
- [Descripción y descarga del dataset](https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/introduction/example-data-sets).
- [Comparaciones de dos muestras en Python](https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/metabolites-and-lipids-univariate-statistical-analysis-in-python/two-sample-comparisons-in-python).
- [PCA en Python para Lipidomics_dataset.xlsx](https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/metabolites-and-lipids-multivariate-statistical-analysis-in-python/principal-component-analysis).
- [PCA en R y reconstrucción del manuscrito con otro dataset](https://laboratory-of-lipid-metabolism-a.gitbook.io/omics-data-visualization-in-r-and-python/metabolites-and-lipids-multivariate-statistical-analysis-in-r/principal-component-analysis-pca).
- [Tolerancias de comparación de NumPy](https://numpy.org/doc/stable/reference/generated/numpy.isclose.html).
