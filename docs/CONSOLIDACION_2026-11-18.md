# Consolidación de Panda Fusión — cierre local 18/11/2026

Se aplicaron los bloques 9.1–9.13 de la auditoría: integridad de estado/documentos, errores cloud y recuperación acotada, seguridad del dictado, fidelidad Unicode/OCR, intenciones/contexto, cancelación, recursos propios GPU, arranque/instalación, rendimiento de caché/PDF/audio, fronteras HTTP/archivos, pruebas y empaquetado. Las decisiones y cambios están en los fuentes y sus regresiones versionadas.

Validación local: 870 pruebas Python en 3.11 y 3.12, 3 de stress, 82 JavaScript, Chrome E2E aprobado, mypy sobre 67 archivos, Ruff/formato, ShellCheck de 24 scripts, cargo check offline/locked y wheel instalada fuera del checkout. Cobertura: 91,30 % líneas / 81,44 % ramas. Auditoría de dependencias final sin vulnerabilidades conocidas; Pillow 13.0.0. No son resultados de CI remoto.

Prueba real con voz Panda, Whisper large-v3-turbo CUDA y Qwen3:14b-q8_0, contexto configurado 32768: pico de uso global GPU 26694 MiB de 32607 MiB. Primera síntesis 8,89 s; repetición cacheada 0,008 s. STT, alineación y análisis concurrentes. TTS/STT/Ollama exclusivos se cerraron y sus hijos fueron recogidos. VRAM global residual 708 MiB: no se promete liberar memoria de otros procesos.

La carga controlada de 24 GiB elegida por Diego sustituyó una partida de juego: admisión denegada antes de inferencia nativa bajo presión; recuperación al retirar la carga. Llamada real OpenAI mediante fusion-dialogue aprobada, sin fallback local. No se promete disponibilidad externa perpetua.

Chrome comprobó seek, velocidades 1–3, colores y persistencia. Resaltado con WAV neural y tiempos ASR previamente medidos entregados por fixture local: al avanzar/retroceder se marcaron/desmarcaron palabras. No representa una inferencia STT nueva dentro del navegador.

Límites: micrófono físico, lectura prolongada de libros, FPS de juegos y ranking de modelos filosóficos no medidos. Las pruebas neurales son previas al reinicio; se repitieron después suites y empaquetado. El cierre desde la ventana habitual requiere la ronda de uso real.

Se conservaron 133 métodos legacy no equivalentes como referencia; sólo se retiraron 41 cuerpos AST idénticos. No se borraron modelos, audio, biblioteca ni entornos externos. .env personal y checkpoints privados permanecen fuera de Git. El estado de sesión cambió fuera del parche durante el trabajo y se preservó, sin restaurarlo.

Informes completos, evidencia y parches por punto permanecen en la carpeta local hermana Panda-Fusion-auditoria. Este documento resume los resultados que pueden publicarse sin incorporar borradores, documentos ni secretos.
