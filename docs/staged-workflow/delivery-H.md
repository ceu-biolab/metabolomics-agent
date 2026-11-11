# Entrega H: evaluación reutilizable y primer conjunto real

Estado: **H1–H7 implementadas y verificadas**.
H8 queda **pendiente por indicación del usuario**. No se han utilizado claves
ni proveedores LLM remotos.

## Funcionalidad

- Contratos versionados para datasets, casos, referencias, criterios, paquetes
  de entrada por etapa y resultados. Un dataset admite varios objetivos.
- Evaluación por las cinco etapas productivas, con PCA identificado como bloque
  propio. Aplicabilidad, dependencias, entradas necesarias y criterios críticos
  se registran antes de la ejecución.
- Comparación exacta, alternativas aceptables, valores numéricos, conjuntos,
  tablas alineadas por ID y pesos PCA con equivalencia de signo/subespacio.
- Auditorías numéricas, gráficas y de procedencia de G reutilizadas; tolerancia
  absoluta cero para p originales y ajustados. Los controles no confunden
  consistencia numérica con validez científica de una decisión.
- Ejecución nueva programada, auditoría de una ejecución existente sin modificarla
  y etapa aislada desde un paquete offline congelado/verificado. La continuación
  se puede observar o detener ante un incumplimiento crítico.
- Informes JSON/Markdown por etapa/intento y de caso, con denominadores,
  cobertura, fallos, bloqueos y criterios no evaluados. Se guardan tiempos de la
  etapa, peticiones, llamadas y tokens declarados, con sus límites de medición.
- Verificación de hallazgos cuantitativos estructurados en el runtime y en la
  auditoría externa. El texto libre conserva revisión pendiente.
- Revisión humana registrada por criterio, revisor, fecha y hash exacto de
  evidencia; una revisión de otra ejecución no aprueba la actual.

La guía de uso y alta de estudios está en [evaluation-guide.md](evaluation-guide.md).
El contrato JSON se publica en `evaluations/evaluation-case.schema.json`.

## Cambios científicos acotados

El perfil gráfico nuevo es versión 3. Los perfiles anteriores conservan sus
modelos y prompts congelados. Diagnósticos e informes estructurados tienen
indicadores de versión propios; no se migra un run guardado silenciosamente.

La carga descriptiva/PCA conserva grupos explícitos, incluidos tres poblaciones,
sin crear una comparación ficticia. La inferencia sigue siendo de dos grupos.
Se añade Lilliefors normal como opción identificada y un gráfico de pesos PCA
PC1–PC2 con etiquetas por identidad de variable. Shapiro y los gráficos de barras
de pesos siguen disponibles. No se añaden batch/QC ni otros métodos multivariantes.

Los prompts actualizados están en inglés y sus copias documentales se mantienen
sin diferencias. Las versiones guardadas en ejecuciones anteriores permanecen
auditables.

## Datos y referencias

Se descargó el Excel original a `data/Lipidomics_dataset.xlsx`, excluido de Git.
Se contrastaron los adjuntos de las páginas de datos, comparaciones y PCA: los
tres son idénticos, de 356489 bytes. La procedencia de descarga queda en
`data/lipidomics_download_provenance.json`.

El conjunto contiene 227 muestras, N=97, T=109 y PAN=21, y 127 lípidos, sin
ausencias, ceros ni variables constantes. Las unidades físicas no se inventan:
se pide confirmarlas en la fuente antes de interpretación biológica.

Se registraron tres casos en `evaluations/cases/lipidomics/`, con referencias
generadas fuera de las funciones productivas y origen identificado. Pasan ocho
controles impresos en el tutorial, incluidos los dos p de Lilliefors.

| Caso | Muestras | Resultado numérico comprobado |
|---|---:|---|
| Student/Holm–Šidák | 206 | 127 tests y 48 variables con p ajustado < 0.05 |
| Mann–Whitney/Holm–Šidák | 206 | 127 tests y 48 variables con p ajustado < 0.05 |
| PCA Python | 227 | 127 variables, 10 componentes, autoscalado ddof=0 y sin log |

Los grados de libertad se comprueban en `univariate_audit`, donde el motor los
guarda. Las tablas principales, los diagnósticos Lilliefors, cohortes con sus
grupos, matrices intermedias, espectro PCA y pesos tienen referencias separadas.
Las tablas numéricas originales no se retocaron para corregir adaptadores del
evaluador.

El heatmap significativo es una ampliación propia de Student, sin clustering y
con dos páginas para las 48 variables. Se verifica la selección y la matriz
dibujada. La reconstrucción PCA del manuscrito utiliza otro dataset y no se
presenta como reproducida con este archivo.

Mann–Whitney puede producir un aviso de `log1p(-1)` al corregir p=1 mediante
Holm–Šidák. Es el límite matemático de esa operación; el valor ajustado 1 es
correcto y coincide con la referencia. No se sustituyeron p ni métodos.

## Verificación y cobertura

La validación final se registra en `runs/validation-H-20261111/validation.json`
y sus informes JUnit/casos. Pasaron **183 pruebas distintas**: la suite completa
de 181 y la última suite H de 26, con 24 solapadas y dos incorporaciones. Los
diez casos de regresión G y la auditoría de una ejecución histórica de G pasan.
Dos ejemplos adicionales con semillas 919 y 920 se ejecutaron y evaluaron con
el mismo motor sin adaptarlo a sus datos. Las pruebas añaden casos con otro dataset/semilla,
análisis descriptivo, omisión de PCA, bloqueo esperado y referencia incompleta;
comprueban corrupción numérica con hashes actualizados, diseño incorrecto,
afirmación inventada con ID válido, tres grupos en PCA, signos/subespacios,
aislamiento y revisión humana asociada a evidencia exacta.

El criterio científico manual y el de legibilidad de figuras permanecen
**no evaluados** en los casos reales. La cobertura expresa esas carencias;
superar la parte automatizada no implica validación biológica o clínica.
Los revisores de los tests son fixtures ficticios, no revisiones científicas.

La cobertura final es 15/17 en Student (88.24 %), 14/16 en Mann–Whitney
(87.50 %) y 13/15 en PCA (86.67 %). En cada caso los dos criterios pendientes
son revisión científica y legibilidad, ambos explícitamente opcionales para el
cierre del software offline. Todas las comprobaciones automáticas requeridas
superan la evaluación. Los informes de `final-audits/` enlazan la evidencia
guardada bajo `cases/`; `publication.json` verifica los archivos publicados.

El registro de la evaluación del LLM solo prepara información pública y deja
modelo/proveedor/presupuesto sin fijar. H8 no se ha ejecutado. Las referencias
numéricas, guiones y respuestas esperadas no se envían al modelo.
