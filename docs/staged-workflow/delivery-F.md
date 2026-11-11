# Entrega F: figuras reproducibles e informe por objetivo

Estado: implementada y verificada. G sigue pendiente.

## Contrato de figuras

Las ejecuciones nuevas registran `visualization_version=1`. `set_figure_plan` y
`make_plot` utilizan especificaciones con tipo, fuente vigente, variables,
justificación de selección, escala, centrado/escalado gráfico, orden y pie.
La ejecución debe coincidir con la especificación registrada completa. Cambiar
la presentación exige una revisión que conserva los archivos anteriores.

Se mantienen distribuciones antes/después, boxplots y volcano, y se añade un
heatmap sin clustering. Los planes de flujo pueden exigir cualquiera de estas
figuras; una figura adicional puede incluirse en el plan de visualización. Los
requisitos se comprueban según el objetivo: un análisis descriptivo no requiere
comparación, volcano ni PCA.

Las variables explícitas se conservan en el orden solicitado. Sin selección,
se toman las seis primeras variables para distribuciones/boxplots y todas las
filas de comparación para volcano. El heatmap exige una selección explícita.
No se ordenan ni seleccionan variables por significación de forma implícita.

## Fuentes y transformaciones gráficas persistidas

Cada figura conserva una cadena de artefactos con hashes y relaciones de origen:

| Artefacto | Contenido |
|---|---|
| `plot_source` | Valores seleccionados de la fuente, incluidos ausentes; grupo, muestra y pareja cuando procede |
| `plot_data` | Valores exactos usados al dibujar: bins y frecuencias, puntos disponibles, coordenadas volcano o matriz de heatmap en formato largo |
| `plot_summary` | Para boxplots: cuartiles, mediana, IQR, extremos de bigotes, media, n y recuento de atípicos |
| `*_plot.json` | Especificación, selección efectiva, métodos, etiquetas, orden local de muestras, factores de transformación gráfica, parámetros de dibujo, versiones y omisiones |
| Imagen PNG | Figura enlazada a sus tablas y configuración |
| `*_plot_failure.json` o documento de fallo | Estado y causa cuando no se completa la imagen |

La fuente se confirma antes de preparar la representación gráfica; los datos
para dibujar y la configuración quedan guardados antes de renderizar la imagen.
Un fallo conserva las salidas confirmadas y no publica una figura válida ni
satisface un requisito. Los resultados estadísticos anteriores permanecen intactos.

Las tablas de muestras y configuraciones con sus identificadores permanecen
locales y no pueden consultarse con `read_result`. Los resúmenes de boxplot sí
son agregados. El contexto y los traspasos transmiten referencias, selección,
escala, pie, recuentos de omisiones/imputaciones y advertencias. También se
conservan las figuras diagnósticas de preparación y sus referencias posteriores;
una revisión de preparación las retira de la selección vigente.

## Convenciones de cada figura

**Distribuciones.** Comparan valores originales con la escala preparada elegida:
`concentrations` o `testing`. Se guardan 18 bins por variable/representación, sus
bordes y frecuencias, y todos los valores fuente. Los bordes pueden diferir entre
paneles; son histogramas de recuentos, no estimaciones de densidad con bins comunes.
Los ausentes se cuentan como omisiones.

**Boxplots.** Usan la escala registrada y muestran todos los puntos disponibles
sin jitter. Bigotes de 1,5 IQR; los atípicos siguen visibles como puntos. En diseños
pareados se dibujan líneas para pares completos, conservando los puntos disponibles
sin contraparte. La configuración informa cuántas líneas se trazaron. No se añaden
pruebas ni anotaciones de significación. La fuente identifica celdas imputadas
cuando existe la máscara; no se presentan como nuevas mediciones originales.

**Volcano.** Utiliza el fold change y los p ajustados guardados. Solo dibuja puntos
con fold change finito y p ajustado finito en [0,1]. Los puntos omitidos se cuentan.
Un p ajustado cero conserva ese valor en la fuente y se sustituye por el mínimo
positivo normal de float64 únicamente para calcular la altura de dibujo. La tabla
gráfica guarda ambos valores y `-log10`. Colores y línea horizontal utilizan el
alfa y la corrección de la comparación. Las etiquetas siguen el orden declarado
o de la fuente, sin una selección oculta por p. Una figura vacía no es válida.

**Heatmap.** Parte de `concentrations` o `testing`, sin acceder a matrices PCA.
Puede restar la media por variable y/o dividir por su SD muestral (`ddof=1`);
los valores predeterminados son sin centrado y sin escalado. Se guardan los
centros, divisores y valores resultantes. Solo se admiten variables seleccionadas
completas y finitas, y SD positiva si se solicita escalado; no se rellenan ausentes
ni se sustituyen constantes silenciosamente. El orden es el de entrada o el de
referencia/tratamiento, conservando el orden de entrada dentro de cada grupo.
No se calculan clusters, dendrogramas, embeddings ni pruebas multivariantes.
La escala gráfica nunca modifica las concentraciones o la matriz de prueba.

## Informes y limitaciones acumuladas

El informe final identifica el objetivo registrado, su alcance, las figuras
exigidas y los estados de las etapas. Describe las omisiones justificadas de
pruebas/volcano en objetivos descriptivos y de la etapa de presentación si no
se solicitaron figuras. Incluye los planes y representaciones vigentes; los
intentos históricos siguen disponibles en sus archivos e informes.

Añade efectos e intervalos disponibles junto a los p y fold changes, enlaces a
las fuentes de figuras, pies, escalas, parámetros, recuentos y advertencias.
Los intervalos siguen sin corrección por multiplicidad. Las métricas no definidas
conservan sus motivos en la auditoría univariante.

Las incidencias heredadas aparecen con código, severidad, estado y referencias,
y se incluyen las suposiciones no comprobadas registradas en las etapas. Una
finalización válida no elimina incertidumbres científicas ni valida las
interpretaciones del agente. Siguen excluidos el análisis multivariante,
la corrección de lotes y los procedimientos basados en QC.

Si el agente se detiene, `incomplete.md` enlaza los resultados parciales y los
estados de los intentos, sin declarar cumplimiento. Se conserva además una copia
inmutable registrada de cada informe parcial. Las fuentes de figuras fallidas
se encuentran entre las evidencias del intento correspondiente.

## Compatibilidad y prompts

Las ejecuciones sin `visualization_version` mantienen el contrato anterior,
sus esquemas, prompts congelados y generación de figuras. No se migran
silenciosamente ni se les exigen fuentes gráficas que antes no guardaban.

Se han ampliado en inglés los prompts de visualización e informe, junto con sus
copias documentales. Los esquemas de herramientas siguen siendo la autoridad
sobre argumentos y capacidades. No se añaden dependencias ni se requiere API key
para las pruebas y demo locales.

## Verificación

**110 pruebas superadas**, incluidas las 94 anteriores; además, se han repetido
las 16 pruebas de figuras tras los ajustes finales y la comprobación específica
de etiquetas/privacidad. La demo completa termina con **21 llamadas**, **57
artefactos** y cuatro figuras con sus fuentes, datos de dibujo y configuraciones,
más un resumen agregado de boxplot. La validación realiza **868
comprobaciones numéricas** de histogramas, posiciones volcano, resúmenes de
boxplots y escala del heatmap. Las concentraciones, valores de prueba y comparación
principal son idénticos **byte por byte a E**; el SHA-256 de la comparación sigue
siendo `d9ae5cdf5671d83c473eefe5042801a7e20cdcdad06a3b96e1147a5e9793fb65`.

Se comprueban las opciones de centrado/escalado, pares incompletos, omisiones,
p ajustado cero, fallos controlados de preparación gráfica y renderizado,
conservación de resultados anteriores, privacidad, restauración, revisiones,
compatibilidad anterior a F e informes descriptivos con omisiones justificadas.
También se ha revisado localmente la legibilidad del heatmap y sus etiquetas.

Evidencias locales, excluidas de Git bajo `runs/`:

- [Validación estructurada](../../runs/validation-F-20261110T125100Z/validation.json).
- [Pruebas completas](../../runs/validation-F-20261110T125100Z/pytest-final.log).
- [Informe de la demo](../../runs/validation-F-20261110T125100Z/demo-reviewed/report.md).
- [Fuente del histograma](../../runs/validation-F-20261110T125100Z/demo-reviewed/a019_plot_source.csv).
- [Bins y frecuencias](../../runs/validation-F-20261110T125100Z/demo-reviewed/a020_plot_data.csv).
- [Configuración de distribuciones](../../runs/validation-F-20261110T125100Z/demo-reviewed/a019_plot.json).
- [Datos volcano](../../runs/validation-F-20261110T125100Z/demo-reviewed/a023_plot_data.csv).
- [Resumen de boxplots](../../runs/validation-F-20261110T125100Z/demo-reviewed/a027_plot_summary.csv).
- [Matriz de heatmap en formato largo](../../runs/validation-F-20261110T125100Z/demo-reviewed/a030_plot_data.csv).
- [Configuración de heatmap](../../runs/validation-F-20261110T125100Z/demo-reviewed/a029_plot.json).
- [Informe de visualización](../../runs/validation-F-20261110T125100Z/demo-reviewed/stages/04_visualization/attempt_001/stage_report.md).

No se ha evaluado un modelo real ni se han hecho llamadas remotas. No se añaden
dependencias ni credenciales. La entrega G no se ha iniciado.
