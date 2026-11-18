# Ampliación: selección de heatmap y PCA exploratorio

Estado: implementada y verificada.

Esta ampliación posterior a F incorpora la selección automática de variables
significativas para heatmaps y un PCA exploratorio opcional. Conserva las cinco
etapas, los informes JSON/MD por etapa, los checkpoints por operación, la
procedencia de observaciones/imputaciones y la privacidad de las tablas de muestras.
La corrección de lotes y los procedimientos basados en QC siguen excluidos.
La entrega G del plan anterior conserva su evaluación general pendiente.

## Bibliotecas y referencia metodológica

El apartado «Multivariate statistics in R and Python» del
[artículo de Idkowiak et al.](https://www.nature.com/articles/s41467-025-63751-1)
propone scikit-learn para PCA en Python y Matplotlib/Seaborn para visualizar.
La implementación utiliza esas bibliotecas, incluida `StandardScaler` para las
convenciones de centrado/escalado, y no añade dependencias.

Los parámetros de esta aplicación se registran explícitamente; no se presenta
la demo sintética como reproducción exacta de las figuras del artículo/GitBook.
En particular, SD con `ddof=0` es la convención de StandardScaler; se admite
`ddof=1` cuando se declara en el plan. Se conservan los cálculos del protocolo
histórico para sus ejecuciones anteriores.

## 1. Contratos y compatibilidad

Las ejecuciones nuevas guardan `visualization_version=2` y `pca_version=1`.
Las anteriores conservan sus versiones, esquemas de herramientas y prompts
congelados. No se migran silenciosamente ni se les exigen fuentes nuevas.

El plan de flujo añade `pca_requirement`:

- `disabled`: se mantiene la ruta sin PCA.
- `optional`: se solicita el bloque PCA, pero puede omitirse con una justificación
  explícita mediante `skip_pca`.
- `required`: requiere un PCA completo; no admite esa omisión.

Las figuras PCA exigidas por el objetivo requieren `pca_requirement="required"`.
El ID interno `03_univariate` se conserva por compatibilidad; esta etapa puede
incluir primero el bloque PCA y después el bloque univariante. El prompt PCA se
activa únicamente en su bloque. Tras evaluar PCA, un objetivo descriptivo avanza
a visualización y omite las pruebas; un objetivo de dos grupos continúa con ellas.

## 2. Heatmap seleccionado por resultados

Ejemplo de especificación:

```json
{
  "kind": "heatmap",
  "source_id": "d001",
  "selection_mode": "significant",
  "comparison_id": "c001",
  "scale": "testing",
  "centering": "feature_mean",
  "scaling": "feature_sd",
  "rows_per_page": 40,
  "selection_rationale": "Display valid features below the saved adjusted-p threshold as a descriptive summary."
}
```

La modalidad `explicit` sigue admitiendo una lista no vacía de variables. En
`significant`, la lista debe estar vacía y se resuelve en Python con las filas
válidas de la comparación cuyo p ajustado es estrictamente menor que su alfa
registrado. La comparación debe ser vigente y pertenecer al mismo dataset.
No se recalculan pruebas ni se cambia la familia de corrección. Se mantiene el
orden de la tabla de comparación.

Se guardan `heatmap_selection` y `gNNN_selection.json`: cada candidato, sus
resultados, inclusión/exclusión y motivo, orden final, corrección, alfa,
comparación de origen y justificación. No se limita la selección automática a
las 12 variables del contrato anterior.

Se muestran todas las variables seleccionadas en páginas de hasta `rows_per_page`
filas (40 por defecto). Cada página conserva fuente, representación gráfica,
configuración e imagen, con número de página y total. Un registro
`gNNN_heatmap.json` identifica el grupo de figuras y su estado. La finalización
comprueba el conjunto completo de páginas; una página fallida conserva las
anteriores pero no satisface el requisito.

Cero variables significativas produce `skipped_no_significant`, con selección
persistida, operación omitida e incidencia informativa. Es una omisión justificada,
no una imagen vacía ficticia. Las variables seleccionadas deben tener datos
completos y finitos: no se eliminan ni imputan silenciosamente para dibujarlas.

Los pies e informes advierten que la selección utiliza las mismas diferencias
entre grupos que se visualizan; la separación observada es descriptiva y no una
validación independiente. Este filtro no alimenta el PCA principal.

## 3. PCA con matriz y plan independientes

Herramientas disponibles exclusivamente en el bloque PCA:

| Herramienta | Función |
|---|---|
| `inspect_pca_inputs` | Recuentos, ausentes, imputaciones, variación y límites dimensionales de la fuente propuesta |
| `set_pca_plan` | Congelar diagnóstico, dataset, escala, variables y parámetros |
| `run_pca` | Ejecutar el plan una vez y guardar checkpoints/modelo/resultados |
| `skip_pca` | Registrar una omisión justificada cuando el objetivo lo permite |

La fuente puede ser `concentrations` o `testing` (predeterminada). Una lista vacía
selecciona todas las variables retenidas. Las selecciones explícitas deben
justificarse independientemente de los p-values. Se utiliza una huella de valores,
orden, máscara y parámetros de procedencia para impedir cambios entre diagnóstico
y ejecución. Los grupos y parejas no entran como variables del ajuste.

Convenciones del plan:

| Parámetro | Convención |
|---|---|
| Centrado | Media por variable |
| Escalado | `none`, `auto` o `pareto`; predeterminado `auto` |
| Divisor auto | SD con `scaling_ddof=0` por defecto; se admite 1 explícitamente |
| Divisor Pareto | Raíz cuadrada de esa SD |
| Componentes | Dos por defecto, con límites de muestras/variables y rango numérico |
| Algoritmo | `sklearn.decomposition.PCA`, SVD completa, sin whitening |
| Propósito | Exploratorio |

No se repite el logaritmo ni se modifican concentraciones o escala de prueba.
Se rechazan fuentes incompletas, no finitas, variables seleccionadas constantes,
divisores inválidos o componentes que superen el rango. No se imputan, filtran
variables o retiran muestras de forma implícita en el PCA. Un fallo no puede
convertirse en un resultado completo ni reejecutarse con el mismo plan.

Se guardan, por operación:

1. `pca_input`: valores seleccionados en la escala registrada.
2. `pca_scaling`: medias y divisores por variable.
3. `pca_matrix`: representación independiente centrada/escalada.
4. `pca_scores`: coordenadas de muestras, con metadatos añadidos después del ajuste.
5. `pca_loadings`: pesos de los autovectores (`components_.T`), no correlaciones
   entre variables y componentes.
6. `pca_variance`: varianza, proporción, acumulado y valor singular por componente.
7. `pcNNN_model.json`: parámetros numéricos suficientes para reproducir la
   transformación, orden de variables, convenciones y versiones.
8. `pcNNN_pca.json`: estado del intento, artefactos, dimensiones e incidencias.

Los fallos e interrupciones conservan los checkpoints confirmados y su estado
real. Las imputaciones utilizadas se cuentan desde la máscara, sin considerarlas
nuevas mediciones. Se explicita que su incertidumbre no se propaga al PCA.

## 4. Figuras e informes

`make_plot` admite fuentes PCA vigentes y completas:

- `pca_scores`: dos componentes distintos, etiquetas con varianza explicada y
  grupos representados por color después del ajuste.
- `pca_scree`: varianza por componente y acumulada de los componentes calculados.
  No se presenta como espectro completo ni se fuerza el acumulado a 100%.
- `pca_loadings`: pesos de un componente; lista explícita o mayores pesos absolutos,
  con desempate por nombre y límite declarado (`max_loading_features`).

Las figuras no reajustan PCA ni añaden contrastes o elipses de confianza. La
separación visual no demuestra significación, causalidad o predicción. El PCA
ordinario no modela la dependencia entre pares.

Los informes integran requisitos, omisiones opcionales, plan y escala PCA,
convención de SD, recuentos, varianza explicada/restante, archivos del modelo y
limitaciones. Para heatmaps identifican selección, cobertura de páginas y
advertencia sobre la selección con los mismos datos. Las revisiones conservan
el historial y retiran los consumidores de fuentes sustituidas.

Las matrices y scores individuales permanecen locales. `read_result` solo admite
las tablas agregadas autorizadas; los traspasos/contextos llevan IDs, recuentos,
estados y resúmenes. Los prompts comunes y por etapa están actualizados en inglés;
`03_pca.md` se congela con el resto únicamente en ejecuciones que admiten este perfil.

## 5. Verificación y uso

- **128 pruebas superadas**, incluidas las 110 anteriores y 18 casos de la ampliación.
- Referencias scikit-learn para centrado, autoscaling y Pareto con ambas convenciones
  de SD; fuentes originales conservadas, diagnósticos y límites de rango.
- Selección significativa exacta, cero variables seleccionadas, rechazo de fuentes
  sustituidas y paginación de más de 12 variables sin pérdida de cobertura.
- Fallos controlados en una página y en el ajuste, e interrupción del PCA con
  conservación de matrices y restauración del estado correspondiente.
- Restauración entre ajuste y evaluación PCA; contratos anteriores a la ampliación
  conservados; acceso a matrices/scores individuales bloqueado.
- **2.468 comprobaciones numéricas adicionales** en las dos demos: matrices,
  centros/divisores, scores, pesos, varianzas, reconstrucción desde el JSON del
  modelo y datos de dibujo.
- Demo combinada: **28 llamadas**, **84 artefactos**, **7 figuras**, 12 variables
  ajustadas en PCA y **3 variables significativas** seleccionadas para el heatmap.
- Demo descriptiva/PCA: **22 llamadas**, **65 artefactos** y **5 figuras**, sin
  pruebas univariantes ni volcano.
- Concentraciones y escala de prueba idénticas byte por byte a F en ambas demos;
  la comparación principal de la demo combinada también es idéntica. Su SHA-256
  sigue siendo `d9ae5cdf5671d83c473eefe5042801a7e20cdcdad06a3b96e1147a5e9793fb65`.
- Manifiestos y enlaces de informes comprobados; peticiones guardadas sin IDs de
  muestras; **cero llamadas API** y sin dependencias nuevas.
- Revisión visual local de scores, varianza y pesos PCA. La demo no atribuye esa
  revisión al agente que solo recibe metadatos agregados.

Evidencias locales, excluidas de Git:

- [Validación estructurada](../../runs/validation-pca-heatmap-20261110/validation.json).
- [Pruebas completas](../../runs/validation-pca-heatmap-20261110/pytest-full.log).
- [Pruebas específicas](../../runs/validation-pca-heatmap-20261110/pytest-extension.log).
- [Informe combinado](../../runs/validation-pca-heatmap-20261110/demo-final/report.md).
- [Informe descriptivo/PCA](../../runs/validation-pca-heatmap-20261110/demo-pca-only-final/report.md).
- [Modelo PCA reconstruible](../../runs/validation-pca-heatmap-20261110/demo-final/pc001_model.json).
- [Escalado PCA](../../runs/validation-pca-heatmap-20261110/demo-final/a016_pca_scaling.csv).
- [Matriz PCA](../../runs/validation-pca-heatmap-20261110/demo-final/a017_pca_matrix.csv).
- [Pesos PCA](../../runs/validation-pca-heatmap-20261110/demo-final/a019_pca_loadings.csv).
- [Varianza PCA](../../runs/validation-pca-heatmap-20261110/demo-final/a020_pca_variance.csv).
- [Criterio y variables del heatmap](../../runs/validation-pca-heatmap-20261110/demo-final/g001_selection.json).
- [Estado y cobertura del heatmap](../../runs/validation-pca-heatmap-20261110/demo-final/g001_heatmap.json).
- [Herramientas del bloque PCA](../../runs/validation-pca-heatmap-20261110/tool-schemas-pca.json).

```bash
.venv/bin/python -m metabolomics_agent demo --pca
.venv/bin/python -m metabolomics_agent demo --pca-only
.venv/bin/python -m metabolomics_agent tool-schemas --stage 03_univariate --analysis-block pca
```

La primera demo combina PCA y univariante con heatmap de variables significativas.
La segunda usa un objetivo descriptivo/PCA y no ejecuta pruebas de comparación.
La demo ordinaria conserva la ruta sin PCA y utiliza la selección automática.
Las pruebas offline no constituyen una evaluación de las decisiones de un LLM real.
