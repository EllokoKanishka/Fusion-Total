# Dictado con Lucy — plan y continuidad

Pedido de Diego, 18/11/2026. Autorizado implementar por puntos sin aprobación intermedia. Al terminar este trabajo se retoma una auditoría general, como etapa posterior separada.

1. **En curso:** guardar la consolidación vigente en GitHub, con documentación de resultados y sin datos personales ni credenciales. Verificar el commit remoto.
2. **Pendiente:** diagnóstico del flujo de dictado y regresiones del ejemplo real: texto normal, Lucy + orden, Lucy sola + orden siguiente, cambios múltiples sin reemplazos en cascada.
3. **Pendiente:** implementar activación y edición segura/reversible, preservando el texto dictado, con protección frente a resultados tardíos y órdenes ambiguas.
4. **Pendiente:** confirmación hablada breve tras una orden aplicada, con micrófono suspendido mientras responde y regreso automático al dictado. Mostrar fallos sin anunciar cambios inexistentes.
5. **Pendiente:** pruebas Python/JavaScript/Chrome del flujo y fallos; verificar que la respuesta no se transcribe como dictado. Documentar qué se probó con audio real o con fixtures.
6. **Pendiente:** guardar la implementación final en GitHub, verificar el remoto, entregar informe y continuidad.

Decisiones iniciales: palabra de activación Lucy (mantener variantes de transcripción ya soportadas). Respuesta oral corta habilitada inicialmente. Un intercambio como «verde por azul, azul por verde» se aplica sobre el texto original, sin que un reemplazo vuelva a afectar lo recién reemplazado. Un deshacer debe revertir la edición completa. No usar voz de Doctora 7854 dentro de Panda: las respuestas de producto usan el TTS de Panda 7853.

Estado actual: empezando punto 1. Evidencia local de consolidación en ../Panda-Fusion-auditoria/INFORME_PUNTO_9_13.md; se conservarán allí los logs y respaldos privados. No persistir memoria externa sin solicitud.
