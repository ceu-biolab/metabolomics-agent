# Entrega E: análisis univariante con auditoría

Estado: implementada y verificada. F y G siguen pendientes.

## Diagnóstico y plan estadístico

La nueva herramienta `inspect_univariate` examina el dataset preparado vigente
antes de registrar el plan estadístico. Persiste una tabla agregada por variable
y un documento `uvNNN_diagnostics.json`, sin ejecutar comparaciones entre grupos.
Incluye tamaños disponibles y observados, imputaciones, varianzas, empates,
recuentos de pares y diagnósticos de Shapiro. En diseños pareados se examinan las
diferencias; en independientes, los grupos. Los diagnósticos sobre valores
preparados y sobre observaciones de origen se identifican por separado.

Shapiro se calcula con 3–5.000 valores no constantes. Los casos no evaluables
tienen un motivo explícito. El diagnóstico no demuestra normalidad ni selecciona
automáticamente una prueba.

`set_univariate_plan` exige el ID del diagnóstico vigente y fija dataset, prueba,
corrección, alfa, propósito y justificación. La primera versión mantiene:

- Dos grupos independientes o pareados; dirección tratamiento respecto a referencia.
- Welch, t con varianza combinada, Mann–Whitney, t pareada o Wilcoxon pareado.
- Alternativa bilateral; BH, Holm–Šidák, Holm o Bonferroni.
- Familia de hipótesis: variables con prueba válida de la comparación.
- Uso de los valores preparados disponibles, incluidas las imputaciones registradas.

Una huella de los datos y metadatos pertinentes impide ejecutar el plan si su
fuente ha cambiado desde el diagnóstico. Cada plan solo puede ejecutarse una vez,
incluso si su intento termina sin comparación completa. Una revisión conserva
los artefactos previos y exige un plan nuevo. Después de ejecutar pruebas, las
comparaciones posteriores deben declararse exploratorias, aunque falle la
corrección del primer intento.

## Observaciones, imputaciones y pares

La elegibilidad sigue requiriendo al menos tres observaciones originales por
grupo y, en diseños pareados, tres pares originalmente observados completos.
También se exigen tres valores o pares disponibles para la prueba. Las
imputaciones no sustituyen estos mínimos.

La máscara persistida en D permite distinguir observaciones e imputaciones sin
inferir su origen a partir de los valores transformados. La auditoría registra
valores disponibles, imputados, ausentes, pares completos, diferencias cero y
empates. Se comprueba la alineación de matrices y se rechazan fuentes no finitas.

Los recuentos históricos `n_reference` y `n_treatment`, y los recuentos de pares y
valores preparados candidatos, se conservan también para variables descartadas.
La auditoría añade `test_executed` y `n_test_*`: estos últimos identifican los
valores realmente suministrados a una prueba y son cero cuando se omite por
inelegibilidad. Una prueba ejecutada puede producir un resultado no válido.

En diseños pareados, la inferencia utiliza pares completos. El fold change
mantiene la definición anterior: razón de medias aritméticas de todos los valores
disponibles por grupo en concentraciones sin logaritmo. Si esa población difiere
de los pares usados en inferencia, se registra `FOLD_CHANGE_POPULATION`.

## Salidas persistidas e incidencias

Cada intento genera evidencias independientes:

| Artefacto | Contenido |
|---|---|
| `univariate_raw` | Todas las variables, incluidas las omitidas; resultados anteriores a la corrección |
| `univariate_audit` | Procedencia, recuentos, ejecución, grados de libertad y nivel de confianza |
| `univariate_issues` | Motivos por variable y métrica, advertencias y severidad |
| `uaNNN_univariate.json` | Métodos, versiones, familia de hipótesis, miembros/exclusiones y estado del intento |
| `comparison` | Resultado final cuando la prueba y corrección se completan correctamente |

Los resultados sin corregir se confirman antes de intentar la corrección. Si no
hay pruebas válidas o falla la corrección, se conservan los checkpoints y la
auditoría, se registra un bloqueo y no se publica una comparación completa. La
etapa no puede cerrarse como completada usando ese intento.

Solo entran en corrección los resultados con estadístico finito y p finito en
[0,1]. Por ejemplo, una t infinita con p=0 se marca `undefined_test` y no se
presenta como descubrimiento. Esta comprobación añade una regla explícita para
casos degenerados; los resultados válidos de referencia se conservan.

Se registran advertencias de SciPy, imputaciones utilizadas, empates, diferencias
pareadas cero, pocos pares con diferencias no nulas y métricas no definidas.
Wilcoxon mantiene la convención previa para todas las diferencias cero:
estadístico 0 y p=1. Los métodos de rangos mantienen la selección `auto` de SciPy,
con sus parámetros y versión registrados.

Los efectos e intervalos mantienen sus convenciones: Hedges' g con SD combinada
y factor aproximado de muestra pequeña, correlación biserial de rangos con signo
para Mann–Whitney e intervalos por variable sobre diferencias de medias en escala
de prueba para las pruebas t. Los intervalos no se corrigen por multiplicidad.
Una métrica no implementada para una prueba queda ausente con motivo explícito.
No se sustituye por cero ni se trata siempre como fallo.

Los informes JSON/MD de etapa y el traspaso incorporan los IDs y resúmenes de estas
evidencias. `read_result` permite consultar tablas agregadas; las matrices y los
identificadores individuales permanecen locales. El informe final enlaza los
checkpoints y la auditoría y admite filas con p no definido.

## Compatibilidad y prompts

Las ejecuciones nuevas guardan `univariate_version=1`. Las anteriores sin esa
marca conservan el contrato anterior, sus prompts congelados y sus reglas; no se
les exige retroactivamente un diagnóstico o una máscara inexistente.

El prompt de univariante y su copia documental están actualizados en inglés con
el orden de herramientas, los campos del plan, los mínimos observados, las
convenciones estadísticas, la interpretación de incidencias y las reglas de
revisión. No se añaden dependencias ni llamadas externas.

## Verificación

- **94 pruebas superadas**, incluidas las 74 anteriores.
- Referencias SciPy para las cinco pruebas, efectos, intervalos y grados de libertad;
  las cuatro correcciones excluyen las variables no elegibles.
- Casos de imputación, pares incompletos, empates, diferencias cero, datos constantes,
  p no definido, ninguna prueba válida y ausencia de descubrimientos.
- Fallo controlado de corrección: checkpoints recuperables, etapa bloqueada y
  revisión exploratoria obligatoria tras haber ejecutado pruebas.
- Diagnóstico obligatorio, rechazo de fuentes modificadas, restauración y
  compatibilidad con ejecuciones anteriores a E.
- Demo completa con **20 llamadas**, **43 artefactos** y **12 variables evaluadas**;
  **17 celdas imputadas** identificadas en las entradas de las pruebas.
- **108 comprobaciones numéricas adicionales** sobre la demo.
- Concentraciones, escala de prueba y comparación principal idénticas **byte por
  byte a D**. SHA-256 de la comparación:
  `d9ae5cdf5671d83c473eefe5042801a7e20cdcdad06a3b96e1147a5e9793fb65`.
- Manifiestos verificados; sin IDs de muestras en las peticiones guardadas al modelo.
- Demo y pruebas sin API key, llamadas remotas ni PCA. El análisis multivariante,
  la corrección de lotes y los procedimientos basados en QC siguen fuera del alcance.

Evidencias locales (excluidas de Git bajo `runs/`):

- [Validación estructurada](../../runs/validation-E-20261110T122454429636Z/validation.json).
- [Pruebas](../../runs/validation-E-20261110T122454429636Z/pytest.log).
- [Informe de la demo](../../runs/validation-E-20261110T122454429636Z/demo/report.md).
- [Diagnósticos](../../runs/validation-E-20261110T122454429636Z/demo/a014_univariate_diagnostics.csv).
- [Checkpoint sin corrección](../../runs/validation-E-20261110T122454429636Z/demo/a015_univariate_raw.csv).
- [Auditoría](../../runs/validation-E-20261110T122454429636Z/demo/a016_univariate_audit.csv).
- [Incidencias](../../runs/validation-E-20261110T122454429636Z/demo/a017_univariate_issues.csv).
- [Métodos y familia](../../runs/validation-E-20261110T122454429636Z/demo/ua001_univariate.json).
- [Informe de etapa](../../runs/validation-E-20261110T122454429636Z/demo/stages/03_univariate/attempt_001/stage_report.md).
