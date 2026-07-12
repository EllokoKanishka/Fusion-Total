from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import Settings, create_settings
from .conversation import ConversationCore, OllamaChatProvider
from .dialogue import STTProvider, default_stt_provider
from .facade import FusionReaderV2, VoiceSettings
from .local_web_bridge import default_external_research_bridge
from .metrics import VoiceMetricsStore
from .notes import ReaderNotesStore
from .openclaw_bridge import ExternalResearchBridge
from .tts import AllTalkProvider, AudioCache, TTSProvider

if TYPE_CHECKING:
    from http.server import ThreadingHTTPServer


@dataclass(frozen=True)
class ProviderBundle:
    tts: TTSProvider
    stt: STTProvider
    conversation: ConversationCore
    research: ExternalResearchBridge


def create_providers(settings: Settings) -> ProviderBundle:
    tts = AllTalkProvider(
        base_url=settings.providers.tts_url,
        default_voice=settings.providers.voice,
        timeout_seconds=settings.providers.tts_timeout_seconds,
        owner_file=settings.providers.tts_owner_file,
    )
    conversation = ConversationCore(
        OllamaChatProvider(
            base_url=settings.providers.ollama_url,
            default_model=settings.providers.chat_model,
        )
    )
    return ProviderBundle(
        tts=tts,
        stt=default_stt_provider(),
        conversation=conversation,
        research=default_external_research_bridge(),
    )


def create_fusion_reader(
    settings: Settings | None = None,
    providers: ProviderBundle | None = None,
) -> FusionReaderV2:
    effective = settings or create_settings()
    bundle = providers or create_providers(effective)
    return FusionReaderV2(
        tts=bundle.tts,
        stt=bundle.stt,
        conversation=bundle.conversation,
        external_research=bundle.research,
        cache=AudioCache(effective.paths.cache),
        metrics=VoiceMetricsStore(effective.paths.metrics),
        notes=ReaderNotesStore(effective.paths.notes),
        voice=VoiceSettings(
            voice=effective.providers.voice,
            language=effective.providers.language,
        ),
        prefetch_wait_seconds=effective.limits.prefetch_wait_seconds,
        prefetch_ahead=effective.limits.prefetch_ahead,
        prefetch_workers=effective.limits.prefetch_workers,
        session_state_path=effective.paths.session,
        audio_export_root=effective.paths.downloads,
    )


def create_http_server(
    app: FusionReaderV2,
    settings: Settings | None = None,
) -> "ThreadingHTTPServer":
    from .web.server import create_http_server as build_server

    return build_server(app, settings or create_settings())


__all__ = [
    "ProviderBundle",
    "create_fusion_reader",
    "create_http_server",
    "create_providers",
    "create_settings",
]
