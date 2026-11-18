# Auditoría general de Panda Fusión

18 de noviembre de 2026. Revisión iniciada sobre `f9947f5`.

## Alcance

El inventario cubrió los 405 archivos versionados de la revisión, incluidos el lector activo, las capas compatibles, los lanzadores, los recursos de interfaz, Tauri, la automatización y las pruebas. El análisis estático compiló 219 módulos Python y comprobó sintaxis de JavaScript y Bash, identificadores HTML, importaciones del frontend y recursos. El cruce de interfaz y API verificó 53 rutas referidas por el frontend, 85 registros HTTP únicos y 21 recursos estáticos servidos por HTTP.

La revisión manual recorrió los puntos de entrada y las conexiones que afectan a la lectura, el dictado, el diálogo, el ciclo de vida, el empaquetado y la investigación. Las suites ejercitaron además importación, persistencia, notas, preparación de documentos, trabajos multimedia, exportación y compatibilidad. El inventario estático y la cobertura documentan el alcance de esas pruebas.

## Reparaciones

1. **Arranque y configuración.** La CLI carga `.env` antes de resolver valores por defecto. El escritorio Chromium, la CLI y systemd comparten la política de arranque de voz y transcripción. Panda comprueba el puerto antes de iniciar motores y exige coincidencia entre proceso, runtime y PID de salud para reutilizar una instancia. El arranque en segundo plano espera la salud del PID que acaba de iniciar. Los diagnósticos distinguen el Ollama dedicado y la URL efectiva del lector. Bohemia carga su configuración sin consultar el Ollama compartido.
2. **API y portabilidad.** La API y los lanzadores usan la dirección configurada en IPv4 e IPv6 local. Se retiró el registro duplicado de PDF a DOCX. El diálogo usa el mismo análisis del tipo de contenido para distinguir JSON y audio. Una petición con un tipo JSON escrito en mayúsculas y con parámetro de codificación sigue la ruta de texto.
3. **Lectura sincronizada.** Con el resaltado desactivado, el navegador aborta su consulta pendiente, deja de sondear y evita pedir marcas para nuevos bloques. Si el servidor ya aceptó un trabajo de alineación, puede terminar y guardar su resultado. Al reactivar el resaltado se toma la posición actual del audio. Al avanzar o retroceder se actualizan las palabras que cruzaron el límite de lectura.
4. **Investigación.** Una orden como «buscá las fuentes en este PDF» sigue en el lector. Las frases entre comillas y las búsquedas locales explícitas dejan de disparar investigación externa por coincidencias de palabras. Las solicitudes explícitas de internet funcionan en chat y diálogo.
5. **Limpieza y distribución.** Se retiraron el helper de rutas de systemd sin consumidores y la política de arranque duplicada. Se conservó la verificación de rutas con espacios de systemd. La validación del wheel detecta archivos obsoletos heredados de `build/` y verifica sus 23 recursos de interfaz. La instalación desde wheel puede consultar la salud con `fusionctl smoke`. En el checkout, `fusionctl test` usa el mismo aislamiento que CI y muestra su salida.

## Evidencia de validación

- 890 pruebas Python en 3.11 y 890 en 3.12. Tres pruebas de estrés adicionales.
- Cobertura de Python activo de 91,38 % de líneas y 81,74 % de ramas. Pasaron los umbrales globales y los de los módulos críticos.
- 108 pruebas Node y dos recorridos de Chrome. Se ejercitaron el flujo habitual del lector, el encendido y apagado del resaltado, el avance y retroceso por palabra, y la captura y confirmación del dictado.
- Wheel instalado en un entorno nuevo, desde un directorio externo al repositorio. Se verificaron salud, PID personalizado, reutilización, cierre y convivencia con un servidor de prueba en el mismo puerto en IPv4 e IPv6.
- Pasaron Ruff, mypy, ShellCheck, la compilación de Tauri, los controles de documentación y repositorio y la auditoría de dependencias Python y npm.
- En la instancia en uso, la API informó disponibilidad de TTS, STT, chat e investigación. La ventana quedó abierta durante la auditoría. Las pruebas de Chrome usaron audio sintético y la verificación de salud midió disponibilidad.

## Continuidad

El próximo arranque de Panda carga el backend actualizado. Se conservaron las capas de compatibilidad y las muestras de biblioteca registradas en su auditoría. Queda admitida una revisión de ramas poco cubiertas en recursos, ciclo de vida y proveedores al ampliar esas áreas. La elección de licencia del repositorio sigue siendo una decisión del mantenedor.
