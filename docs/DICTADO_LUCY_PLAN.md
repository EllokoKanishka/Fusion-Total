# Dictado con Lucy — plan y continuidad

Pedido de Diego, 18/11/2026. Autorizado implementar por puntos sin aprobación intermedia. Al terminar este trabajo se retoma una auditoría general, como etapa posterior separada.

1. **Terminado:** guardar la consolidación vigente en GitHub, con documentación de resultados y sin datos personales ni credenciales. Verificar el commit remoto.
2. **Terminado:** diagnóstico del flujo de dictado y regresiones del ejemplo real: texto normal, Lucy + orden, Lucy sola + orden siguiente, cambios múltiples sin reemplazos en cascada.
3. **Terminado:** implementar activación y edición segura/reversible, preservando el texto dictado, con protección frente a resultados tardíos y órdenes ambiguas.
4. **Terminado:** confirmación hablada breve tras una orden aplicada, con micrófono suspendido mientras responde y regreso automático al dictado. Mostrar fallos sin anunciar cambios inexistentes.
5. **Terminado:** pruebas Python/JavaScript/Chrome del flujo y fallos; verificar que la respuesta no se transcribe como dictado. Documentar qué se probó con audio real o con fixtures.
6. **En curso:** guardar la implementación final en GitHub, verificar el remoto, entregar informe y continuidad.

Decisiones iniciales: palabra de activación Lucy (mantener variantes de transcripción ya soportadas). Respuesta oral corta habilitada inicialmente. Un intercambio como «verde por azul, azul por verde» se aplica sobre el texto original, sin que un reemplazo vuelva a afectar lo recién reemplazado. Un deshacer debe revertir la edición completa. No usar voz de Doctora 7854 dentro de Panda: las respuestas de producto usan el TTS de Panda 7853.

Estado actual: punto 1 terminado, commit remoto 3dff19da04ee06c5620e8a186c88b98b8c3512fa verificado. Diagnóstico punto 2: activación básica existente; faltan feedback oral y sustituciones múltiples atómicas. Evidencia local de consolidación en ../Panda-Fusion-auditoria/INFORME_PUNTO_9_13.md; se conservarán allí los logs y respaldos privados. No persistir memoria externa sin solicitud.

Diagnóstico reproducido antes de corregir: 6 regresiones Python con 3 fallos y 1 error; 3 regresiones nuevas JS fallan. Primera corrección: 27 Python y 25 JS focalizadas pasan. Falta ampliar fallos/Chrome y validar la suite completa; no declarar todavía terminada la implementación.

Puntos 3 y 4 implementados: lotes de 2–8 sustituciones, sin cascada y sin cambios parciales si faltan términos o se superponen; una sola entrada de deshacer. Texto previo a Lucy se conserva; Lucy sola arma la frase siguiente. Una orden ambigua marcada no pasa a interpretación LLM. Feedback breve usa /api/dictation/speak y el reproductor de Panda; captura suspendida hasta finalizar, vuelve incluso si TTS/reproducción falla. Respuestas tardías tras cierre o nuevo pedido se descartan. 34 pruebas JS focalizadas aprobadas; sin prueba de micrófono físico todavía.

Punto 5: 878/878 Python 3.11 y 3.12, cero fallos/errores/skips/bloqueos; 3 stress, 95 JS, 2 Chrome E2E. Segundo E2E usa un MediaStream de tono sintetizado y MediaRecorder real, respuesta STT de fixture, interpretación HTTP real y WAV TTS sintético: confirma una sola vez, grabador inactivo al pedir voz y reanudado al terminar. No abre micrófono físico ni comprueba reconocimiento neural de una voz humana. Cobertura 91,28 % líneas / 81,45 % ramas, umbrales aprobados. Mypy67/Ruff/formato pasan.
Incidencia de subida inicial: CI falló al serializar un skip de Whisper Homebrew; runner ahora registra id/motivo como JSON y la prueba del fallback usa mocks explícitos en todos los hosts. E2E y Security de esa primera subida sí pasaron. Falta verificar CI del commit final antes de cerrar punto 6.
