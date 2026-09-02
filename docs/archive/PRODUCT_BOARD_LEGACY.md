# Seguimiento Fusion Reader (histórico)

Este archivo queda como tablero corto de producto. La historia larga vive en
`docs/HISTORY.md`.

## Completado resumido

- v2 voice-first operativa en `fusion_reader_v2/`
- TTS GPU Fusion reservado en `7853`
- frontera de voz `7853/7854` verificada por script
- `Dialogar` con STT persistente, TTS neural y barge-in inicial
- notas por documento y bloque
- personalidad por modo
- `Modo libre`
- bridge externo puntual con `fusion-research`
- ruta aislada de investigación externa con `SearXNG` local primero

## Estado operativo actual

- `tests.test_fusion_reader_v2`: `119 OK`
- `verify_voice_port_isolation.sh`: `OK`
- `OpenClaw main`: pertenece a Antigravity/Telegram
- `Fusion`: usa `SearXNG` local primero y `OpenClaw fusion-research` solo como fallback

## Pendientes reales

- [ ] Probar `Dialogar` con micrófono real en más escenarios
- [ ] Ajustar VAD/barge-in según ruido ambiente y eco
- [ ] Afinar warmup/keep-hot de AllTalk GPU
- [ ] Mejorar OCR fino para PDFs escaneados largos
- [ ] Mejorar filtrado/ranking académico de resultados `SearXNG`
- [ ] Evaluar síntesis profunda futura `SearXNG + ConversationCore` sin romper la frontera actual

## Próximo criterio

No meter features nuevas que crucen fronteras ya cerradas. Si aparece una mejora
de búsqueda externa, debe seguir estas reglas:

- no tocar `OpenClaw main`
- no tocar Telegram
- no tocar Brave/global `web_search`
- no tocar Antigravity
