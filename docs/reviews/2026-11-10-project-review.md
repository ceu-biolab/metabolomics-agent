**Revisión del proyecto — 10 de noviembre de 2026**

**Dictamen.** El proyecto tiene una arquitectura razonable y aplica numerosas buenas prácticas de ingeniería de agentes: herramientas acotadas, cálculos deterministas, contratos tipados, control de transiciones, separación de escalas, persistencia de evidencias y límites de ejecución. Es una buena base para un asistente científico supervisado. Todavía no permite afirmar que el agente elija métodos adecuados o produzca interpretaciones científicamente fiables de forma autónoma. La evaluación del LLM está pendiente y hay limitaciones funcionales concretas que conviene resolver.

Revisión del árbol de trabajo, incluidos los cambios y archivos sin confirmar que ya existían. HEAD de referencia: `1b018d4f5c593b26910731b5e3af9cda8c37201a`; ese commit, por sí solo, no identifica todo el contenido revisado. Se examinaron código, prompts activos y documentales, pruebas y evidencias de evaluación guardadas. No se modificó la implementación ni se ejecutó un proveedor LLM remoto. Los archivos de esta carpeta son documentación y pruebas de diagnóstico adicionales de la revisión.

**Arquitectura y alcance científico**

El sistema combina un flujo de fases impuesto por Python con decisiones del LLM dentro de cada fase. El modelo puede elegir herramientas y parámetros, examinar salidas y pedir revisiones; Python decide qué operaciones están permitidas y si existe evidencia suficiente para avanzar. Es apropiado describirlo como un agente acotado por un flujo científico explícito. La distinción entre workflows y agentes, y la preferencia por soluciones sencillas y observables, son coherentes con [Building effective agents](https://www.anthropic.com/research/building-effective-agents).

No hace falta introducir varios agentes ni un framework adicional para mejorar este diseño. La prioridad es reforzar los contratos y evaluar el comportamiento del modelo. La separación entre `Provider`, `Agent`, `ToolRegistry`, `Workflow`, funciones estadísticas y `EvidenceStore` es útil, aunque las funciones de despacho, validación e informe ya acumulan bastantes responsabilidades y variantes históricas.

El alcance real es análisis estadístico de tablas previamente cuantificadas de dos grupos. No es un flujo completo desde datos de espectrometría de masas: no incluye extracción/alineamiento de picos, anotación, evaluación instrumental con blancos/QC ni corrección de deriva o batch. Esa restricción es defendible para una primera versión y está documentada. La preparación, inferencia y visualización cubren un subconjunto del proceso más amplio discutido por [Idkowiak et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC12485147/); usar sus bibliotecas no equivale a validar automáticamente todas las decisiones científicas.

**Evaluación de las fases**

| Fase | Valoración | Mejora principal |
|---|---|---|
| Coordinación | Correcta: define objetivo, diseño y entregables antes de ejecutar análisis. | Convertir las restricciones esenciales del usuario en campos verificables; actualmente el modelo redacta el objetivo y elige el alcance que luego se valida. |
| Preparación | Sólida: inspección, descriptivos originales, plan, transformación y descriptivos posteriores. | Separar representación para inferencia de representación para PCA/visualización cuando sus necesidades de imputación difieran. |
| Estadística | Adecuada: diagnóstico antes del test, diseño pareado explícito, efectos y corrección por multiplicidad. | Distinguir corrección del análisis de una rama adicional de sensibilidad; preservar ambos resultados como evidencia utilizable. |
| PCA opcional | Es razonable situarla antes de los resultados univariantes y mantener escalado propio. | Congelar antes de la exploración las hipótesis que se quieran presentar como primarias. El orden por sí solo no demuestra independencia de las decisiones. |
| Visualización | Buena separación entre inferencia y transformaciones exclusivamente gráficas. | Facilitar revisión visual humana; el modelo solo ve descriptores y no debe afirmar que ha inspeccionado los PNG. |
| Informe | Buena combinación de secciones calculadas y narrativa separada. | Verificar afirmaciones cuantitativas de forma estructurada y representar la revisión científica como un estado distinto de la finalización técnica. |

La carga/validación inicial se realiza antes de la coordinación. Es aceptable para una CLI con contrato estricto: una entrada inválida produce un informe local de inicialización. Significa también que el LLM no ayuda a resolver archivos que no llegan a cargarse. Además, incluso el modo descriptivo exige actualmente dos grupos y al menos tres muestras por grupo; conviene hacer visible esta restricción en la interfaz.

La transformación independiente de concentraciones, valores de test y representación PCA está bien resuelta. También lo están el alineamiento por identidad en diseños pareados, el rechazo de logaritmos no positivos, la ausencia de eliminación automática de outliers y la distinción entre cero descubrimientos y ausencia de tests válidos.

**Hallazgos prioritarios y evidencia**

**1. Alta prioridad: la gestión de contexto impide completar el ejemplo con el presupuesto por defecto.**

En `agent.py:99–120` se envían todo el historial, el prompt de la fase, las herramientas y un contexto que incluye un handoff con información solapada. Se calcula una cota por bytes del conjunto completo en cada petición. El ajuste basado en consumo real solo se aplica cuando `not state.workflow`, es decir, al protocolo antiguo. Esto no coincide con la explicación general del README sobre el uso de los tokens reales para estimaciones posteriores.

Reproducción inicial: `Agent(..., DemoProvider(), LLMConfig(model="offline"))`, sobre el ejemplo sintético. Resultado: `token_limit` tras 12 peticiones/herramientas, en `03_univariate`, después de `compare_groups`, con **0 tokens declarados consumidos** por el proveedor simulado. Tras implementar la ventana por fase (pasos 1–3 del plan posterior), el mismo estímulo avanzó hasta 18 peticiones y se detuvo en `04_visualization`. La gestión del presupuesto y el crecimiento dentro de una fase siguen pendientes. La demo CLI y la prueba de extremo a extremo elevan el presupuesto a 500.000, por lo que no detectan este problema de la configuración habitual de 60.000.

No es una medición del consumo de un modelo real: demuestra que la cota preventiva, junto con el contexto creciente, puede detener una ejecución aunque el presupuesto contabilizado no se haya gastado. Recomendación: medir tokens por proveedor cuando sea viable, mantener cotas documentadas, reducir duplicaciones y usar memoria de fase con acceso a historial bajo demanda. Calibrar límites con trayectorias reales y añadir un caso de ejecución con la configuración publicada.

**2. Alta prioridad: una sensibilidad invalida el análisis primario como evidencia consultable.**

`Workflow.request_revision` (`workflow.py:554–594`) vacía `comparison_ids` y otras dependencias. `read_result` (`tools.py:287–301`) rechaza las tablas anteriores; el informe toma solo las comparaciones actuales (`report.py:8–13`). Esta política es adecuada cuando un análisis estaba equivocado y debe sustituirse, pero no representa una sensibilidad que debe coexistir con un análisis primario válido.

Reproducción: análisis Welch `c001`, revisión estadística y Mann–Whitney `c002`. El estado vigente contiene solo `c002`; leer la tabla de `c001` devuelve `The requested result belongs to superseded evidence`, y `c001` deja de ser un ID admisible para los hallazgos finales. Sus archivos siguen guardados y el historial del modelo puede conservar parte de los resultados, pero no existe una vía segura para consultarlos y compararlos en detalle durante la síntesis.

Recomendación: separar `supersedes` de `sensitivity_of`; conservar ramas válidas, definir qué resultado es primario y proporcionar una herramienta determinista de comparación de efectos, direcciones y decisiones. Un cambio de Welch a Mann–Whitney también cambia el estimando y no debe presentarse automáticamente como confirmación de la misma hipótesis.

**3. Alta prioridad para uso científico: citar evidencia existente no valida la afirmación.**

`Finding` contiene texto libre e IDs (`models.py:82–84`). `finish_analysis` comprueba la existencia/vigencia de esos IDs (`report.py:29–33`), pero no la concordancia del texto con las tablas.

Reproducción controlada: se entregó una afirmación inventada de «999 variables significativas» y prueba de un mecanismo causal, citando `c002`. La herramienta devolvió `ok: true`, el informe incorporó el texto y la ejecución terminó como `complete`. El resultado real de esa comparación tenía **3 variables significativas**. Este experimento utiliza una llamada programada y no demuestra que un LLM real haya cometido ese error; demuestra que la infraestructura lo acepta.

El proyecto ya declara esta limitación y separa correctamente la interpretación de los resúmenes calculados. Aun así, recomiendo hallazgos estructurados con comparación, variable, métrica, valor y alcance; generación determinista de frases numéricas; revisión científica explícita para interpretación libre. El estado `complete` debe significar ejecución completa, sin confundirse con validación científica.

La misma separación se aplica al objetivo: validar todos los entregables elegidos por el modelo no prueba que estos satisfagan la petición original. El alcance y los requisitos esenciales deben conservar una relación verificable con las instrucciones del usuario.

**4. Prioridad media: las cabeceras duplicadas se aceptan tras un renombrado silencioso.**

`read_table` usa directamente los lectores de pandas (`data.py:9–15`). La comprobación posterior de unicidad opera sobre los nombres ya interpretados (`data.py:55–57`). Una tabla CSV con dos columnas llamadas `lipid` se acepta como `lipid` y `lipid.1`, sin advertencia específica ni decisión de identificación.

En metabolómica dos señales pueden compartir anotación, pero deben tener identificadores de feature inequívocos y una anotación separada. Recomendación: validar cabeceras originales antes de su normalización, rechazar duplicados o exigir una política explícita con un mapa persistido. Añadir casos de nombres repetidos, espacios, identificadores especiales y columnas completamente vacías.

**5. Prioridad media: la lectura acotada de resultados no dispone de paginación.**

`ReadResultArgs` limita la respuesta a 20 filas (`models.py:76–79`) y `read_result` siempre aplica `head(limit)`. Se pueden pedir nombres de features conocidos, pero no recorrer de forma general una tabla desconocida. El contexto, handoffs y `read_stage_report` también cortan incidencias a las primeras 20, sin cursor ni orden por gravedad (`workflow.py:232–240`, `273–274`, `461–473`).

La limitación de contexto es correcta; falta una forma de recuperar lo omitido. En paneles grandes puede sesgar la revisión hacia las primeras variables o los primeros resultados. Un problema bloqueante seguirá impidiendo cerrar la fase por la validación completa de Python, pero el modelo puede no recibir su descripción entre las primeras incidencias.

Recomendación: paginación, inventario de variables, filtros por estado/gravedad y resúmenes globales calculados; separar explícitamente filas totales, mostradas y cursor siguiente. Conservar todas las incidencias bloqueantes y advertir del truncamiento.

**6. Prioridad media: la congelación de una ejecución no incluye toda la implementación.**

Se guardan prompts, perfiles de capacidades, versiones de paquetes y solicitudes. Es positivo. Sin embargo, `ToolRegistry.schemas` regenera esquemas desde las clases instaladas; `RunState.restore` no exige coincidencia del código o del entorno con el original. La auditoría compara algunos esquemas guardados con el registro actual. `run.json` no registra commit, árbol con cambios ni hashes del código fuente, y `requirements-tested.txt` declara correctamente que no es un lockfile completo.

Por tanto, conservar prompts y usar el mismo nombre de modelo/endpoint no garantiza una continuación idéntica tras actualizar el proyecto. Recomendación: hash del código o versión de build inequívoca, dependencias resueltas, contrato de herramientas versionado y comprobación explícita de compatibilidad al reanudar. Mantener la posibilidad de migración deliberada y registrada.

**7. Prioridad media: la durabilidad de artefactos es más fuerte que la reanudación transaccional de llamadas.**

Los archivos se escriben atómicamente y se registran con hash. Sin embargo, la ejecución de herramienta, el snapshot del estado y la escritura de su respuesta en `conversation.json` son pasos separados (`agent.py:174–176`, `tools.py:349–352`). Un corte después de calcular pero antes de registrar la respuesta deja un lote pendiente que `Agent.run` rechaza al reanudar. El proyecto documenta este comportamiento; no lo presentaría como recuperación automática de cualquier fallo.

Recomendación: diario duradero por `call_id`, estados de llamada y resultados recuperables; devolver una respuesta ya comprometida sin repetir el cálculo. Probar terminaciones en los límites entre persistencias. Las pruebas actuales de reanudación son valiosas, pero no cubren todas esas ventanas.

**Preparación e inferencia: decisiones que requieren más política científica**

- La imputación simple se incluye en los tests. El mínimo de tres observaciones originales y la máscara de procedencia evitan inflar artificialmente el criterio de elegibilidad, pero no resuelven la incertidumbre de imputación ni aseguran potencia o calibración inferencial. Hay advertencias explícitas, lo cual es correcto. Para ampliar el uso, ofrecer políticas de inferencia separadas y comparar alternativas justificadas.
- La imputación kNN se hace sobre abundancias sin escalado previo, antes de normalizar/logaritmizar (`analysis.py:153–154`). La distancia por defecto es `nan_euclidean`, según la [documentación de KNNImputer](https://scikit-learn.org/stable/modules/generated/sklearn.impute.KNNImputer.html). Por construcción, cambios de escala entre variables pueden alterar los vecinos. No afirmo que toda imputación kNN actual sea inválida: recomiendo una representación de distancia explícita, parámetros persistidos y análisis de sensibilidad cuando las magnitudes difieran mucho.
- El umbral global de ausencias y las medianas globales son políticas concretas, no elecciones universales. Los diagnósticos por grupo existen, pero faltan decisiones estructuradas sobre ausencia diferencial, límites de detección, blancos, dilución, estándares internos y normalización previa. No conviene inferir un mecanismo de ausencia solo de la matriz.
- PCA utiliza el mismo dataset preparado que la inferencia, aunque tiene escalado independiente. Un conjunto con ausencias podría permitir tests por feature y necesitar imputación únicamente para PCA. Conviene representar ambas ramas sin forzar una misma política de valores usados.
- Las pruebas son bilaterales, las correcciones se aplican dentro de cada comparación y los intervalos no están ajustados por multiplicidad. Estas limitaciones están bien descritas. La corrección aplicada no cubre búsquedas adaptativas entre planes.
- El fold change usa razones de medias aritméticas en concentraciones preparadas, mientras que los tests pueden operar en escala logarítmica; en pareados incompletos incluso pueden usar poblaciones distintas. El código lo registra. Mantendría esta claridad y añadiría, si el objetivo lo necesita, un efecto específicamente pareado o compatible con el estimando logarítmico.
- La presencia de confusión batch/grupo se advierte pero no impide inferencia. Esto encaja con un prototipo exploratorio. Para análisis destinados a sustentar conclusiones, establecería una política explícita de revisión de confusión y de limitaciones del diseño.

**Prompts**

Los prompts están bien orientados científicamente. Son explícitos sobre capacidades, escalas, pairing, multiplicidad, valores no definidos, imputaciones, interpretación de Mann–Whitney y Shapiro, revisiones exploratorias y límites de PCA. La instrucción de tratar nombres y tablas como datos no confiables es adecuada. También es acertado prohibir afirmar una inspección visual que las herramientas no permiten.

La mejora principal no es añadir más advertencias. Hay entre 520 y 1.300 palabras por fase, además de 673 palabras comunes, esquemas, historial y contexto. Parte del contenido repite contratos que Python ya impone; el prompt de visualización contiene instrucciones para varias versiones, aunque los runs guardan sus prompts históricos. Recomiendo generar un prompt específico para cada perfil de capacidades, mantener reglas científicas esenciales y llevar detalles extensos a documentación consultable.

Añadiría ejemplos breves de decisiones difíciles: normalización desconocida, batch confundido, imputación alta, petición no soportada, falta de descubrimientos y diferencia entre sustitución y sensibilidad. Las justificaciones deberían identificar hechos observados, supuestos, alternativas y consecuencias mediante campos estructurados, sin solicitar razonamiento interno extenso.

Hay desajustes entre instrucciones e interfaz: se pide revisar informes históricos, pero `read_stage_report` solo acepta una fase y devuelve su último intento; se pide leer más allá de resultados destacados, pero no hay paginación; se prohíbe inventar cifras, pero el informe solo valida IDs. Resolvería estos desajustes antes de ampliar los prompts. Las pruebas verifican que las copias documentales coinciden con las activas; mantener dos copias sigue siendo una carga de mantenimiento evitable mediante generación automática.

**Auditoría**

Es uno de los aspectos más sólidos del proyecto. Se conservan entradas y hashes, tablas tipadas con posiciones de valores ausentes, planes, máscaras, matrices intermedias, operaciones, informes por intento, handoffs, conversación, peticiones, salidas previas a corrección, familia de hipótesis, razones de valores indefinidos y fuentes/configuraciones de figuras. PCA conserva matrices, escalado y parámetros suficientes para contrastar la representación. La auditoría numérica independiente de las funciones productivas detecta alteraciones que un hash actualizado por sí solo no detectaría.

Estas garantías deben describirse con precisión:

| Propiedad | Estado |
|---|---|
| Trazabilidad de datos y cálculos | Buena cobertura para el alcance implementado. |
| Integridad accidental de artefactos registrados | Comprobada con hashes y esquemas. |
| Reproducción numérica | Buena base, condicionada por versiones y código disponible. |
| Validación de decisiones científicas del LLM | Pendiente. |
| Validación semántica del informe | Parcial: IDs y secciones calculadas, sin verificación general del texto libre. |
| Recuperación ante cualquier corte | No: existen lotes pendientes que requieren intervención. |
| Registro resistente a manipulación deliberada | No acreditado: no hay firma ni anclaje externo de todo el registro. |
| Anonimización formal | No: hay minimización de tablas individuales y cribado de identificadores, con excepciones documentadas. |

Los JSON de control, conversación, eventos e informes mutables no están todos protegidos del mismo modo que los artefactos científicos inmutables. Los hashes locales no son firmas. No hace falta convertir un prototipo local en un sistema regulado, pero sí evitar equiparar «auditable» con «inmutable frente a cualquier alteración».

Añadiría identificador y versión efectiva del modelo cuando el proveedor los exponga, identificador de respuesta, parámetros efectivos por petición, duración de llamada y herramienta, estimación y uso real de tokens, motivo estructurado de parada y huella del código. Las justificaciones y evaluaciones del agente son registros de lo declarado, no una prueba de que su motivación o razonamiento hayan sido correctos.

**Verificación y límites de esta revisión**

La suite ejecutada durante esta revisión terminó con **142 pruebas superadas**, código de salida 0, en **630,18 segundos** (`.venv/bin/python -m pytest -q`). Incluye pruebas numéricas, protocolos con transporte simulado y una evaluación de diez casos deterministas. Los resultados de verificación están resumidos en [2026-11-10-validation.json](2026-11-10-validation.json). Se inspeccionó además el corpus existente `runs/validation-G-20261110/evaluation-final/evaluation.json`, que registra 10/10 casos, 11.705 valores contrastados y ninguna evaluación LLM real; esas cifras pertenecen a ese corpus guardado.

Las pruebas adicionales y sus resultados están en [2026-11-10-probes.py](2026-11-10-probes.py) y [2026-11-10-probes.json](2026-11-10-probes.json). Ejecutar el script desde la raíz del repositorio con `.venv/bin/python docs/reviews/2026-11-10-probes.py`; crea un directorio temporal nuevo y no usa API. La afirmación falsa es exclusivamente un estímulo de diagnóstico y no un resultado científico.

La revisión estática con Ruff 0.17.4 devolvió 68 diagnósticos, principalmente formato/importaciones, concatenaciones y algunas advertencias de variables/cierres. No se aplicaron correcciones automáticas ni se interpreta ese recuento como 68 fallos funcionales. No se encontró configuración de CI en el árbol revisado. Conviene fijar reglas de lint y ejecutar pruebas en CI, con compatibilidad de versiones explícita.

No se ejecutaron modelos reales ni se reprodujo aquí el benchmark externo con el XLSX de los autores. La coincidencia con SciPy/statsmodels confirma ejecución, no validez de supuestos poblacionales ni control del error después de selección adaptativa. La evaluación futura debe medir el sistema completo modelo+herramientas y repetir tareas, además de evaluar trayectorias y resultados; esa distinción también está desarrollada en [Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents).

**Orden de trabajo recomendado**

1. Corregir contexto/presupuestos, soportar ramas de sensibilidad y rechazar cabeceras ambiguas.
2. Añadir recuperación de resultados paginada y un contrato de afirmaciones cuantitativas verificables.
3. Ejecutar una evaluación real del agente con varios intentos por caso y revisión experta: elección del diseño, preparación, inferencia, fidelidad narrativa, abstención, resistencia a instrucciones dentro de datos y coste/latencia.
4. Comparar con una alternativa determinista que use las mismas herramientas para demostrar qué aporta el LLM.
5. Fortalecer versión de código/entorno, compatibilidad al reanudar y persistencia transaccional de llamadas.
6. Ampliar capacidades científicas solo después de demostrar fiabilidad en el alcance actual.

**Criterio de uso.** Lo consideraría un prototipo de investigación bien encaminado y útil con supervisión científica. Antes de adoptarlo como analista autónomo exigiría evaluación real del modelo, verificación de afirmaciones y solución de los límites de contexto y sensibilidad identificados. Mantendría su arquitectura básica.
