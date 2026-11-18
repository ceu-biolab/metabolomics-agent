# Entrega B: evidencias, checkpoints e informes

Estado: implementada y verificada. La entrega C no se ha iniciado.

## Comportamiento incorporado

- Modelos versionados de artefacto, operación, incidencia e informe.
- Escrituras atómicas con sincronización, manifiestos, SHA-256, tipos de columnas
  y posiciones de ausentes. La lectura conserva identificadores de texto,
  cadenas vacías, textos como `NA`, ceros y valores ausentes diferenciados.
- Checkpoints locales de entrada seleccionada, tratamiento de ceros, filtrado,
  imputación y su máscara, normalización y transformación, cuando se aplican.
  Las operaciones omitidas quedan identificadas sin producir matrices ficticias.
- Un dataset ejecutable solo se publica cuando se han guardado todas sus tablas.
- Informes `stage_report.json` y `stage_report.md`, manifiesto por intento,
  `operations.jsonl`, contexto y copia del prompt vigente.
- Registro de fallos de carga antes de iniciar el análisis, errores de herramienta,
  advertencias de Python y limitaciones de los resultados.
- Recuperación de intentos interrumpidos tras un cierre abrupto, conservando
  operaciones confirmadas e identificando las que no llegaron a finalizar.
- Validación de integridad, rutas y referencias; lectura de estados anteriores.
  Una revisión crea un intento nuevo sin sobrescribir los informes cerrados.
- Los checkpoints e informes adicionales permanecen locales; las respuestas de
  herramientas al modelo no incorporan matrices ni estos informes completos.

## Alcance de integración

B proporciona la infraestructura y la conecta con las llamadas del agente actual.
Cada llamada queda registrada como un intento dentro de su área correspondiente.
Todavía no constituye la coordinación completa de cinco etapas: el agrupamiento,
sus criterios de transición, los permisos por etapa y `handoff.json` pertenecen a C.
Tampoco se han activado los nuevos prompts.

Los resultados originales conservan sus identificadores `a...` y sus archivos CSV
en la raíz para mantener compatibilidad. Los checkpoints adicionales emplean IDs
`e...` y se guardan bajo `stages/<area>/attempt_<n>/artifacts/`. El catálogo global
está en `evidence_index.json`. Las tablas tienen un archivo `.schema.json` asociado.

El PCA existente sigue presente únicamente por compatibilidad con el flujo de
referencia actual y se registra bajo `legacy_multivariate`. No se ha añadido ningún
método multivariante. Su retirada del catálogo del flujo nuevo continúa prevista
en C; B no altera las decisiones del agente ni sus requisitos de finalización.
La persistencia de fuentes exactas de figuras corresponde a F; por ahora se
conservan las figuras y sus referencias de entrada.

La recuperación de B permite inspeccionar matrices confirmadas tras un fallo.
No reanuda automáticamente una transformación a mitad de una llamada ni repite
la operación interrumpida: esa continuación requiere revisión explícita. Las
llamadas completas siguen conservando el mecanismo de reanudación existente.

## Verificación

- **50 pruebas superadas**, incluidas las 37 de referencia, con la prueba de carga
  inválida adaptada intencionadamente al requisito de conservar un informe.
- Pruebas de fallo del logaritmo después de imputar, escritura fallida, salida
  abrupta de un subproceso, recuperación, manipulación de contenido/esquema/ruta,
  advertencias y compatibilidad con estados antiguos.
- Comprobaciones estáticas de errores de Python e imports superadas en los archivos
  modificados y nuevos (`ruff check --select F,E9`).
- Demo offline completa: **35 artefactos registrados**, incluidos los 20 originales,
  y **15 intentos con sus informes**. Ninguna llamada a un modelo remoto.
- Las **15 tablas CSV originales coinciden byte por byte** con la referencia de A.
- Se conservan dos incidencias por uso de imputación y dos advertencias conocidas
  de Seaborn/Matplotlib. Ahora estas advertencias se registran en los informes
  locales del intento de visualización.

Evidencias locales:

- [Resumen de validación](../../runs/validation-B-20261110T104133882278Z/validation.json).
- [Registro de pruebas](../../runs/validation-B-20261110T104133882278Z/pytest.log).
- [Resultados JUnit](../../runs/validation-B-20261110T104133882278Z/pytest.xml).
- [Informe de la demo](../../runs/validation-B-20261110T104133882278Z/demo/report.md).
- [Ejemplo de informe de preparación](../../runs/validation-B-20261110T104133882278Z/demo/stages/02_preparation/attempt_002/stage_report.md).

Los resultados bajo `runs/` están excluidos de Git. Los enlaces anteriores apuntan
a los archivos conservados en este entorno local.
