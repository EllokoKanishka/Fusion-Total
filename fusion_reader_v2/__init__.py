"""Fusion Reader v2: voice-first conversational reader core."""

from .documents import ImportedDocument, import_document_bytes, import_document_path
from .conversation import ChatResult, ConversationCore, NullChatProvider, OllamaChatProvider
from .dialogue import AutoSTTProvider, FasterWhisperServerSTTProvider, NullSTTProvider, STTProvider, TranscriptResult, WhisperCliSTTProvider, default_stt_provider
from .local_web_bridge import AutoExternalResearchBridge, SearxngResearchBridge, default_external_research_bridge
from .notes import ReaderNote, ReaderNotesStore
from .openclaw_bridge import ExternalResearchBridge, ExternalResearchResult, NullExternalResearchBridge, OpenClawResearchBridge
from .reader import Document, ReaderSession, split_text
from .config import ConfigurationError, Settings, create_settings
from .facade import FusionReaderV2
from .tts import AllTalkProvider, AudioArtifact, AudioCache, NullTTSProvider
from .metrics import VoiceMetric, VoiceMetricsStore
from .audio_export import AudioExportJob, AudioExportRequest, AudioExportSnapshot
from .version import __version__

__all__ = [
    "AllTalkProvider",
    "AudioExportJob",
    "AutoExternalResearchBridge",
    "AudioArtifact",
    "AudioCache",
    "AudioExportRequest",
    "AudioExportSnapshot",
    "AutoSTTProvider",
    "ChatResult",
    "ConversationCore",
    "ConfigurationError",
    "Document",
    "ExternalResearchBridge",
    "ExternalResearchResult",
    "FusionReaderV2",
    "FasterWhisperServerSTTProvider",
    "ImportedDocument",
    "NullTTSProvider",
    "NullChatProvider",
    "NullExternalResearchBridge",
    "NullSTTProvider",
    "OllamaChatProvider",
    "OpenClawResearchBridge",
    "ReaderNote",
    "ReaderNotesStore",
    "ReaderSession",
    "SearxngResearchBridge",
    "STTProvider",
    "Settings",
    "TranscriptResult",
    "VoiceMetric",
    "VoiceMetricsStore",
    "WhisperCliSTTProvider",
    "default_external_research_bridge",
    "default_stt_provider",
    "create_settings",
    "import_document_bytes",
    "import_document_path",
    "split_text",
    "__version__",
]
