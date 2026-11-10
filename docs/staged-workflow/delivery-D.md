# Entrega D: preparación y descriptivos ampliados

Estado: implementada y verificada. E–G no se han iniciado.

## Herramienta y alcance

`summarize_dataset` está disponible en preparación. Guarda tablas agregadas por
variable y grupo declarado, u opcionalmente para el conjunto seleccionado.
Permite resumir todas las variables o una selección explícita. No añade análisis
multivariante, corrección de lotes ni procedimientos basados en QC.

Los argumentos incluyen `dataset_id`, `scale`, `grouping`, `value_policy`,
`features` e `include_mode`. Las combinaciones válidas son:

| Datos | Escala | Significado |
|---|---|---|
| `raw` | `original` | Tabla de entrada seleccionada para los grupos del estudio |
| Dataset preparado vigente | `concentrations` | Valores sin logaritmo después de imputación/normalización elegidas |
| Dataset preparado vigente | `testing` | Valores usados en pruebas, con la transformación registrada |

`grouping="group"` usa el grupo del estudio; `overall` combina las muestras
seleccionadas y lo etiqueta explícitamente. No agrupa por lote ni covariables.

## Estadísticos y convenciones

Se guardan media, mediana, SD y varianza muestrales, mínimo, máximo, rango,
Q1, Q3, IQR, deciles p10–p90, asimetría, curtosis de exceso y CV. La moda es
opcional. Las convenciones están en cada documento `sNNN_summary.json`:

- SD y varianza: `ddof=1`; requieren dos valores disponibles.
- Cuantiles: interpolación lineal. Con un valor, los cuantiles coinciden con él.
- Asimetría muestral ajustada: mínimo tres valores y variación no nula.
- Curtosis de Fisher corregida por sesgo: mínimo cuatro valores no constantes;
  referencia normal igual a cero.
- CV: SD/media, como razón y no porcentaje. Requiere SD estimable y media positiva
  en escala de razón sin logaritmo. La escala de prueba con log2/log10 no admite CV.
- Moda: frecuencias de valores exactos, sin discretizar. Solo se devuelve un valor
  si existe un máximo repetido único. No se elige arbitrariamente entre empates
  ni se presenta un valor de una serie toda distinta como moda informativa.

Los ceros siguen siendo datos medidos en `raw/original`. Si el plan los reclasifica
como ausentes, esa decisión solo afecta a los datos preparados y sus recuentos.
Los ceros de una representación logarítmica no se interpretan como ausencia.

## Observaciones e imputaciones

Cada dataset preparado nuevo guarda una máscara booleana alineada con las
variables finales y los IDs de muestras. Esta máscara es la fuente de procedencia;
no se deduce si una celda fue imputada comparando valores transformados.

Se conserva también una máscara de todo falso cuando no hubo imputación. Es un
registro de procedencia, no una operación de imputación ficticia. La operación
omitida sigue figurando como omitida. Si una variable desaparece tras normalizar,
se retira también de la máscara final.

| Recuento | Definición |
|---|---|
| `n_total` | Muestras del grupo o conjunto seleccionado |
| `n_observed_input`, `n_missing_input` | Presencia/ausencia según la política de entrada aplicable; en datos preparados contempla ceros reclasificados |
| `n_original_missing`, `n_zero_input` | Ausentes y ceros físicos en la entrada original |
| `n_available_source`, `n_missing_source` | Valores presentes/ausentes en la representación resumida |
| `n_imputed_source` | Celdas rellenadas identificadas por la máscara |
| `n_used`, `n_observed_used`, `n_imputed_used` | Valores efectivamente usados y su procedencia |
| `n_excluded_imputed` | Imputaciones excluidas por `observed_only` |
| `n_zero_source`, `n_zero_used` | Ceros en la representación y selección resumidas |

`including_imputed` resume la representación completa disponible.
`observed_only` excluye celdas imputadas, pero conserva los valores normalizados
o transformados ya calculados. No vuelve a normalizar: una observación normalizada
puede depender de imputaciones a través del factor de normalización de su muestra.

## Valores no definidos e informes

Cada resumen genera una tabla de estadísticas, una tabla de motivos por
variable/grupo/estadístico y un documento JSON de parámetros y métodos. Los
valores no definidos son ausentes en CSV y `null` en las respuestas JSON; nunca
se sustituyen por ceros.

Entre los motivos están `no_observations`, `insufficient_observations`,
`constant_values`, `zero_mean`, `not_ratio_scale`, `non_finite_result`,
`no_repeated_values` y `multiple_modes`. Un CV no aplicable en escala logarítmica
es informativo y no un fallo de ejecución. Los valores no finitos derivados de
un cálculo se dejan ausentes con su motivo; los datos fuente no finitos se rechazan.

Los motivos se registran como comprobaciones/incidencias agregadas, sin bloquear
por una limitación descriptiva esperable. Los resúmenes y sus referencias pasan
al traspaso y al contexto de las etapas siguientes. `read_result` permite consultar
filas agregadas de estadísticas o motivos; no permite leer la máscara ni matrices
de muestras. El informe final enlaza las tablas y sus métodos.

## Finalización y versiones

Las ejecuciones nuevas registran `descriptive_version=1`. Para cerrar preparación
se exigen resúmenes completos por grupo de:

1. `raw/original/observed_only`, antes de seleccionar el plan de preparación.
2. Dataset preparado en `concentrations/including_imputed`.
3. Dataset preparado en `testing/including_imputed`.

Los resúmenes parciales u overall no sustituyen estos requisitos. Los dos últimos
se guardan con sus escalas explícitas incluso si no hubo transformación y contienen
los mismos valores. Las revisiones invalidan los resúmenes vigentes de preparación
sin borrar sus artefactos históricos.

Al reanudar ejecuciones anteriores a D se mantienen sus capacidades, reglas de
finalización y prompts congelados; no se introduce la herramienta ni se exigen
retroactivamente resúmenes o máscaras que no se habían registrado.

El prompt de preparación y su copia documental se han actualizado en inglés con
el contrato y las convenciones. Las ejecuciones existentes conservan sus textos.

## Verificación

- **74 pruebas superadas**, incluidas las 60 anteriores.
- Referencias calculables a mano y concordancia con NumPy/SciPy para varianza,
  cuantiles, asimetría y curtosis.
- Casos con grupos vacíos de observaciones para una variable, una/dos/tres
  observaciones, constantes, ceros, moda única, empates y valores todos distintos.
- Procedencia de ceros reclasificados e imputaciones después de normalizar y
  transformar; restauración sin los archivos de entrada originales.
- Máscara alineada después de retirar variables que se vuelven constantes.
- Rechazo de fuentes, políticas, grupos y variables no admitidos; requisitos de
  cobertura y conservación de versiones anteriores.
- Demo completa con **19 llamadas**, **3 resúmenes**, **37 artefactos** y una
  máscara que identifica **17 celdas imputadas**.
- **1.672 comprobaciones** de los valores descriptivos de la demo contra referencias.
- Concentraciones, escala de prueba y comparación principal idénticas **byte por
  byte a C**. Las tablas numéricas de referencia no se han alterado.
- Sin PCA, llamadas remotas, API key ni dependencias nuevas. Los archivos de las
  peticiones del modelo no contienen identificadores de muestras.

Evidencias locales:

- [Validación estructurada](../../runs/validation-D-20261110T114411860254Z/validation.json).
- [Pruebas](../../runs/validation-D-20261110T114411860254Z/pytest.log).
- [Informe completo de la demo](../../runs/validation-D-20261110T114411860254Z/demo/report.md).
- [Estadísticos originales](../../runs/validation-D-20261110T114411860254Z/demo/a004_descriptive_statistics.csv).
- [Métodos del resumen original](../../runs/validation-D-20261110T114411860254Z/demo/s001_summary.json).
- [Estadísticos preparados](../../runs/validation-D-20261110T114411860254Z/demo/a010_descriptive_statistics.csv).
- [Estadísticos en escala de prueba](../../runs/validation-D-20261110T114411860254Z/demo/a012_descriptive_statistics.csv).
- [Motivos en escala de prueba](../../runs/validation-D-20261110T114411860254Z/demo/a013_descriptive_undefined.csv).
- [Informe de preparación](../../runs/validation-D-20261110T114411860254Z/demo/stages/02_preparation/attempt_001/stage_report.md).

Los resultados de `runs/` son locales y están excluidos de Git. E continuará con
los controles del análisis univariante; F y G siguen pendientes.
