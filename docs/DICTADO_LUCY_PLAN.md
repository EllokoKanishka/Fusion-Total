# Dictado con Lucy — plan y continuidad

Pedido de Diego, 18/11/2026. **Puntos 1–6 terminados.** Código aplicado localmente y guardado en GitHub. Autorizado implementar y subir sin aprobaciones intermedias.

1. **Terminado — respaldo:** consolidación 9.1–9.13 guardada en main, commit 3dff19da04ee06c5620e8a186c88b98b8c3512fa; remoto verificado. Sin .env personal, biblioteca, audio ni credenciales.
2. **Terminado — diagnóstico:** regresiones reproducidas antes del cambio: invocación, intercambio de colores y confirmación. Se reutilizó el dictado existente y sus guardias de sesión/revisión.
3. **Terminado — edición:** Lucy + orden, Lucy sola + frase siguiente y texto normal seguido de invocación. Sustituciones múltiples sobre el original, sin cascada, sin cambios parciales ante términos ausentes/superpuestos, un deshacer completo. Las órdenes ambiguas se rechazan.
4. **Terminado — voz:** una frase breve tras el resultado, sin confirmaciones por dictado normal. Grabación suspendida durante síntesis/reproducción; reanudación automática, también tras fallos de voz. Audio tardío invalidado por cierre o nuevo pedido. Usa TTS de Panda, no el de Doctora.
5. **Terminado — verificación:** 879/879 Python 3.11 y 3.12, sin fallos/errores/skips/bloqueos; 3 stress; 95 JS; 2 Chrome E2E. Cobertura local 91,33 % líneas / 81,55 % ramas; umbrales aprobados. Mypy67, Ruff/formato, sintaxis, documentación, constraints y wheel instalada fuera del árbol aprobados.
6. **Terminado — entrega:** implementación y ajustes en GitHub; código final verificado en commit 620c3e6ce1477bdad1f53c55884b268fae765e21, con CI, E2E y Security aprobados. Cierre documental posterior sin cambios de código. [Informe de uso y alcance](DICTADO_LUCY_INFORME.md).

## Decisiones y límites

Palabra de activación Lucy, manteniendo variantes STT soportadas. Confirmación oral habilitada inicialmente, como pidió Diego. El intercambio verde/azul afecta las coincidencias de todos los pares originales; una sustitución individual mantiene la ocurrencia más próxima al cursor. Selector de asistente y personalidad conservados. No se instalaron modelos.

Chrome usó un MediaStream de tono propio y MediaRecorder real, transcripción de fixture, interpretación HTTP real y WAV TTS sintético. Verificó una sola respuesta, grabador suspendido y reanudación. No se abrió el micrófono físico ni se midió reconocimiento neural de voz humana. Las mediciones neurales de la consolidación están documentadas por separado. Cargar los cambios reiniciando Panda y recargando la interfaz.

## Ajustes encontrados al subir

- El runner no podía serializar un skip de unittest: ahora registra id y motivo; la prueba Homebrew controla el escenario con mocks en todos los hosts.
- Una aserción editorial dependía de diccionarios opcionales del equipo: ahora prueba vocabulario presente/ausente explícitamente.
- El gate remoto de documents.py quedó inicialmente en 94,89 % frente al 95 % exigido. Se añadió una prueba de diccionarios ausentes/ilegibles y metadatos Hunspell; no se rebajó el umbral. Resultado local final: 96,79 % y gate remoto aprobado.

## Evidencia y próximos pasos

- [CI del código final](https://github.com/EllokoKanishka/Fusion-Total/actions/runs/44243429471).
- [E2E remoto](https://github.com/EllokoKanishka/Fusion-Total/actions/runs/44243430014).
- [Security remoto](https://github.com/EllokoKanishka/Fusion-Total/actions/runs/44243429101).
- Evidencia y continuidad privadas: carpeta local hermana Panda-Fusion-auditoria/dictado-lucy-20261118.

**Siguiente etapa: auditoría general posterior solicitada por Diego. Pendiente; no confundir con los seis puntos de esta implementación, ya cerrados.** Revisar la integración final y el uso real, incluyendo micrófono físico y cierre desde la ventana habitual. No persistir memoria externa sin solicitud.

## Seguimiento de la prueba con micrófono

1. Confirmado en la API abierta que el proceso viejo devolvía una sola sustitución para la orden de dos colores, aunque el archivo JavaScript ya estaba actualizado.
2. Implementado el rechazo inmediatamente después de una orden. «No, quedó mal escrito» deshace la última edición por voz si el borrador sigue en esa revisión, y Lucy escucha la corrección siguiente. Si la orden previa no aplicó cambios, pregunta qué debe cambiar. Las frases fuera de ese intercambio siguen siendo dictado.
3. Agregada tolerancia a puntos y comas que la transcripción puede introducir entre pares, además del rechazo de una orden que termina en «y». Al editar desde el final del borrador, el cursor vuelve al final para seguir dictando.
4. Terminado. Se reinició Panda y su API devolvió las dos sustituciones para la orden exacta y las variantes con punto o coma. El servidor entrega la interfaz actualizada. Pasaron 881 pruebas Python 3.12, 97 de JavaScript y 2 de Chrome. Las pruebas de seguimiento simulan la transcripción y el audio de respuesta.
5. El servicio de usuario usaba rutas que systemd no resolvía. Ahora el lanzador compartido lee la configuración y entra en la carpeta del proyecto. Arranque real del servicio y cuatro pruebas del instalador aprobados.
