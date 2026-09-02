# Agente Raiz — Fusion Reader v2

Este directorio contiene una definición operativa complementaria del agente
especialista. Las reglas de `AGENTS.md` y la documentación canónica prevalecen
si aparece cualquier diferencia.

El agente no es un asistente general. Es un constructor y operador de un lector conversacional **voice-first**: todo gira alrededor de lectura por voz neural, navegacion clara y conversacion limitada al contenido leido.

## Archivos

- `agent.yaml`: ficha tecnica del agente raiz, capacidades, limites, puertos y criterios de calidad.
- `system_prompt.md`: prompt de sistema que debe recibir el modelo del lector.

## Raiz De Continuidad

Al retomar trabajo, leer:

1. `AGENTS.md`
2. `FUSION_READER_V2_STATE.md`
3. `docs/ARCHITECTURE.md`
4. `docs/OPERATIONS.md`
5. `docs/CONTRACTS.md`

## Direccion Actual

- Construir v2 en `fusion_reader_v2/`.
- Mantener el prototipo viejo sólo como laboratorio y compatibilidad.
- Priorizar voz neural humana y fluida sobre cualquier mejora secundaria.
- Usar AllTalk/XTTS como primer proveedor.
- Resolver RTX 5090 en entorno aislado, no rompiendo entornos existentes.

## Contratos

- Fusion v1/prototipo HTTP: `http://127.0.0.1:8000`
- Fusion Reader v2 API: `http://127.0.0.1:8010`
- AllTalk/XTTS Fusion GPU: `http://127.0.0.1:7853`
- Owner Fusion requerido: `runtime/fusion_reader_v2/tts_owner.json`
- AllTalk/XTTS fallback CPU legacy: `http://127.0.0.1:7851`
- Doctora Lucy/Antigravity TTS: `http://127.0.0.1:7854` (no usar desde Fusion)
- Puerto 7852: historico/no asignado
- Voz candidata: `female_03.wav`
- Idioma de lectura: `es`

## Tests

```bash
python3 -m unittest tests.test_fusion_reader_v2 -v
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```
