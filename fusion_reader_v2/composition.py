from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import ProviderSettings, Settings, create_settings
from .conversation import ConversationCore, OllamaChatProvider, OpenClawChatProvider, SelectableChatProvider
from .dialogue import (
    AutoSTTProvider,
    FasterWhisperServerSTTProvider,
    NullSTTProvider,
    STTProvider,
    WhisperCliSTTProvider,
)
from .dictation_assistant import DictationAssistant
from .facade import FusionReaderV2, VoiceSettings
from .local_web_bridge import AutoExternalResearchBridge, SearxngResearchBridge
from .metrics import VoiceMetricsStore
from .notes import ReaderNotesStore
from .openclaw_bridge import (
    ExternalResearchBridge,
    ExternalResearchResult,
    NullExternalResearchBridge,
    OpenClawResearchBridge,
)
from .tts import AllTalkProvider, AudioCache, TTSProvider

if TYPE_CHECKING:
    from http.server import ThreadingHTTPServer


@dataclass(frozen=True)
class ProviderBundle:
    tts: TTSProvider
    stt: STTProvider
    conversation: ConversationCore
    research: ExternalResearchBridge
    dictation_assistant: DictationAssistant | None = None


def create_providers(settings: Settings) -> ProviderBundle:
    tts = AllTalkProvider(
        base_url=settings.providers.tts_url,
        default_voice=settings.providers.voice,
        timeout_seconds=settings.providers.tts_timeout_seconds,
        owner_file=settings.providers.tts_owner_file,
    )
    conversation = ConversationCore(
        SelectableChatProvider(
            {
                "local": OllamaChatProvider(
                    base_url=settings.providers.ollama_url,
                    default_model=settings.providers.chat_model,
                ),
                "openai": OpenClawChatProvider(
                    command=settings.providers.openclaw_command,
                    agent=settings.providers.openai_chat_agent,
                    default_model=settings.providers.openai_chat_model,
                    timeout_seconds=settings.providers.openai_chat_timeout_seconds,
                    enabled=settings.providers.openai_chat_enabled,
                ),
            },
            selected=settings.providers.chat_provider,
        )
    )
    return ProviderBundle(
        tts=tts,
        stt=create_stt_provider(settings.providers),
        conversation=conversation,
        research=create_research_provider(settings.providers),
        dictation_assistant=DictationAssistant(
            {
                "local": OllamaChatProvider(
                    base_url=settings.providers.ollama_url,
                    default_model=settings.providers.dictation_model,
                    timeout_seconds=settings.providers.dictation_timeout_seconds,
                ),
                "local14b": OllamaChatProvider(
                    base_url=settings.providers.ollama_url,
                    default_model=settings.providers.dictation_14b_model,
                    timeout_seconds=settings.providers.dictation_timeout_seconds,
                ),
                "openai": OpenClawChatProvider(
                    command=settings.providers.openclaw_command,
                    agent=settings.providers.openai_chat_agent,
                    default_model=settings.providers.openai_dictation_model,
                    timeout_seconds=settings.providers.openai_dictation_timeout_seconds,
                    enabled=settings.providers.openai_chat_enabled,
                ),
            },
            selected=settings.providers.dictation_assistant,
        ),
    )


def create_stt_provider(settings: ProviderSettings) -> STTProvider:
    if settings.stt_provider == "none":
        provider: STTProvider = NullSTTProvider(enabled=False)
    elif settings.stt_provider == "server":
        provider = FasterWhisperServerSTTProvider(
            base_url=settings.stt_url,
            timeout_seconds=settings.stt_timeout_seconds,
        )
    elif settings.stt_provider == "cli":
        provider = WhisperCliSTTProvider(
            command=settings.stt_command,
            model=settings.stt_model,
            timeout_seconds=settings.stt_timeout_seconds,
            threads=settings.stt_threads,
        )
    else:
        provider = AutoSTTProvider(
            primary=FasterWhisperServerSTTProvider(
                base_url=settings.stt_url,
                timeout_seconds=settings.stt_timeout_seconds,
            ),
            fallback=WhisperCliSTTProvider(
                command=settings.stt_command,
                model=settings.stt_model,
                timeout_seconds=settings.stt_timeout_seconds,
                threads=settings.stt_threads,
            ),
        )
    provider.requested_provider = settings.stt_provider
    return provider


def create_research_provider(settings: ProviderSettings) -> ExternalResearchBridge:
    if settings.research_provider == "none":
        return NullExternalResearchBridge(
            ExternalResearchResult(False, detail="bridge_disabled", provider="null_external_research")
        )
    searxng = SearxngResearchBridge(
        base_url=settings.searxng_url,
        timeout_seconds=settings.searxng_timeout_seconds,
        enabled=settings.searxng_enabled,
    )
    openclaw = OpenClawResearchBridge(
        command=settings.openclaw_command,
        agent="fusion-research",
        timeout_seconds=settings.openclaw_timeout_seconds,
        retry_attempts=settings.openclaw_retries,
        enabled=settings.openclaw_enabled,
        environment={},
    )
    if settings.research_provider == "searxng":
        return searxng
    if settings.research_provider == "openclaw":
        return openclaw
    return AutoExternalResearchBridge(searxng=searxng, openclaw=openclaw)


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
        dictation_assistant=bundle.dictation_assistant,
        external_research=bundle.research,
        cache=AudioCache(
            effective.paths.cache,
            max_bytes=effective.limits.cache_max_bytes,
            max_age_days=effective.limits.cache_max_age_days,
        ),
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
        job_max_items=effective.limits.job_max_items,
        job_ttl_seconds=effective.limits.job_ttl_seconds,
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
    "create_research_provider",
    "create_stt_provider",
    "create_settings",
]
