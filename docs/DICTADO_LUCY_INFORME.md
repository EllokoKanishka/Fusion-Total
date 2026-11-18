# Dictado con Lucy — entrega 18/11/2026

Plan por puntos y continuidad: [DICTADO_LUCY_PLAN.md](DICTADO_LUCY_PLAN.md). Consolidación previa respaldada en GitHub: commit 3dff19da04ee06c5620e8a186c88b98b8c3512fa.

## Uso

Abrir Dictado, dejar activado **Órdenes con «Lucy»** e iniciar el micrófono.
Dictar normalmente; luego decir «Lucy, cambia verde por azul» o «Lucy», hacer
una pausa y decir la orden. También funciona texto normal seguido de la
invocación dentro de la misma transcripción. El nombre citado entre comillas
y las menciones dentro de una frase se conservan como texto.

Para el ejemplo de Diego:

- Original: La casa es azul, la casa es verde y la pradera es azul.
- Orden: Lucy, cambia verde por azul, azul por verde.
- Resultado: La casa es verde, la casa es azul y la pradera es verde.
- Respuesta: «Listo. Apliqué 3 sustituciones».

Se reemplazan las coincidencias originales de todos los pares, sin cascada.
Si falta un término o los fragmentos se superponen, se rechaza toda la edición.
Un solo deshacer vuelve al original. Una sustitución individual conserva el
comportamiento anterior: la ocurrencia más próxima al cursor.

Las confirmaciones son breves, mediante la voz de Panda. El grabador se detiene
durante síntesis y reproducción para evitar transcribir la respuesta, y vuelve
a escuchar después. Si falla la voz, el cambio queda guardado y la interfaz
informa el fallo. Las órdenes fallidas no se anuncian como aplicadas. Cerrar,
detener o iniciar otro pedido invalida una confirmación todavía pendiente.

El selector de asistente conserva la elección del usuario. Las sustituciones
comunes funcionan mediante reglas, sin esperar un LLM; las órdenes libres
siguen usando el asistente seleccionado y su validación acotada. No se cambió
la personalidad ni se instaló un modelo nuevo.

## Validación

- 879 pruebas Python en cada versión 3.11 y 3.12, sin fallos, errores, skips ni
  intentos bloqueados. Tres stress; 95 JavaScript.
- Dos Chrome E2E: flujo habitual y captura/orden/confirmación/reanudación.
  El segundo usa entrada de audio sintetizada y MediaRecorder real, con
  transcripción y voz sintéticas de fixture. No captura el micrófono físico
  ni mide precisión de reconocimiento de una voz humana.
- Cobertura: 91,33 % líneas / 81,55 % ramas; umbrales sin cambios y aprobados.
  Mypy sobre 67 fuentes, Ruff, formato y sintaxis JS aprobados.
- Regresiones: invocación sola, prefijo literal, acentos, órdenes desactivadas,
  comillas, lote ambiguo/no coincidente/superpuesto, undo, fallo TTS/reproducción,
  cierre durante síntesis y respuesta única de asistente.

La subida inicial reveló un problema del runner al serializar un skip de una
prueba dependiente de Homebrew. Ahora conserva id/motivo serializables y la
prueba controla explícitamente el escenario, sin exigir Homebrew al CI. No se
rebajaron umbrales ni se ocultaron fallos. El cierre remoto del punto 6 se indica abajo.

Los cambios se cargan al reiniciar Panda y recargar la interfaz. No se
reinició una sesión de dictado del usuario ni se modificaron sus borradores.
Queda después la auditoría general que pidió Diego, como trabajo separado.
La evidencia privada está en la carpeta local hermana
Panda-Fusion-auditoria/dictado-lucy-20261118/evidencia.

La integración remota también encontró una aserción dependiente de los diccionarios opcionales del host. El fixture ahora cubre vocabulario presente y ausente explícitamente. Además se rechaza combinar sustituciones con un borrado en una misma orden ambigua. Wheel final reconstruida, instalada fuera del checkout y smoke de API/assets/cierre aprobado sin arrancar motores neurales.

## Cierre remoto

Código final: `620c3e6ce1477bdad1f53c55884b268fae765e21`.
[CI](https://github.com/EllokoKanishka/Fusion-Total/actions/runs/44243429471),
[E2E](https://github.com/EllokoKanishka/Fusion-Total/actions/runs/44243430014) y
[Security](https://github.com/EllokoKanishka/Fusion-Total/actions/runs/44243429101):
**aprobados**. La documentación de cierre posterior conserva ese código.
La prueba añadida de diccionarios deja documents.py en 96,79 % local y pasa el
umbral remoto sin modificarlo. Los seis puntos del plan están cerrados.
