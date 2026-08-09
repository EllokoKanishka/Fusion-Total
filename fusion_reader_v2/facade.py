"""Public Fusion Reader facade."""

from __future__ import annotations

import json
import hashlib
import os
import threading
import time
import re
import tempfile
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from pathlib import Path

from .config import environment_value

from .audio_export import (
    AudioExportJob,
    AudioExportSnapshot,
    concat_wav_files,
)
from .conversation import ConversationCore
from .dialogue import STTProvider, default_stt_provider
from .dictation import interpret_dictation_transcript
from .metrics import VoiceMetric, VoiceMetricsStore
from .notes import ReaderNotesStore
from .local_web_bridge import default_external_research_bridge
from .openclaw_bridge import ExternalResearchBridge, ExternalResearchResult
from .reader import Document, ReaderSession, chunk_index_for_offset
from .tts import AllTalkProvider, AudioArtifact, AudioCache, TTSProvider
from .pdf_to_docx import find_downloads_dir
from .services.lifecycle import BackgroundLifecycleService, BackgroundShutdownContext
from .services.notes import NotesService
from .services.research import ResearchService
from .services.reader import ReaderService
from .services.preparation import PreparationService, new_prepare_status
from .services.dialogue import DialogueService
from .owned_subprocess import run_owned
from .services.audio_export import AudioExportService
from .services.audio import AudioService
from .services.persistence import AtomicJSONStore
from .services.session_persistence import SessionPersistenceService, SessionPreferences


@dataclass
class VoiceSettings:
    voice: str = field(
        default_factory=lambda: environment_value("FUSION_READER_VOICE", "female_03.wav") or "female_03.wav"
    )
    language: str = "es"


class FusionReaderV2:
    def __init__(
        self,
        tts: TTSProvider | None = None,
        cache: AudioCache | None = None,
        voice: VoiceSettings | None = None,
        metrics: VoiceMetricsStore | None = None,
        conversation: ConversationCore | None = None,
        external_research: ExternalResearchBridge | None = None,
        stt: STTProvider | None = None,
        notes: ReaderNotesStore | None = None,
        prefetch_wait_seconds: float = 25.0,
        prefetch_ahead: int | None = None,
        prefetch_workers: int | None = None,
        session_state_path: Path | str | None = "runtime/fusion_reader_v2/session_state.json",
        audio_export_root: Path | str | None = None,
        job_max_items: int = 256,
        job_ttl_seconds: float = 6 * 60 * 60,
    ) -> None:
        self.session = ReaderSession()
        self.tts = tts or AllTalkProvider()
        self.cache = cache or AudioCache()
        self.voice = voice or VoiceSettings()
        self.metrics = metrics or VoiceMetricsStore()
        self.conversation = conversation or ConversationCore()
        self.external_research = external_research or default_external_research_bridge()
        self.stt = stt or default_stt_provider()
        self.notes = notes or ReaderNotesStore()
        self.prefetch_wait_seconds = prefetch_wait_seconds
        self.prefetch_ahead = max(
            0,
            int(
                prefetch_ahead
                if prefetch_ahead is not None
                else environment_value("FUSION_READER_PREFETCH_AHEAD", "3") or "3"
            ),
        )
        self.prefetch_workers = max(
            1,
            int(
                prefetch_workers
                if prefetch_workers is not None
                else environment_value("FUSION_READER_PREFETCH_WORKERS", "1") or "1"
            ),
        )
        self.tts_segment_chars = max(240, int(environment_value("FUSION_READER_TTS_SEGMENT_CHARS", "900") or "900"))
        self._executor = ThreadPoolExecutor(
            max_workers=self.prefetch_workers, thread_name_prefix="fusion-reader-v2-tts"
        )
        self._prefetch_executors: list[ThreadPoolExecutor] = [self._executor]
        self._prefetch_lock = threading.Lock()
        self._prefetch_futures: dict[tuple, Future[AudioArtifact]] = {}
        self._prefetch_started: dict[tuple, float] = {}
        self._prefetch_future: Future[AudioArtifact] | None = None
        self._prefetch_index: int | None = None
        self._prefetch_started_ts: float | None = None
        self._prefetch_promoted_keys: set[tuple] = set()
        self._tts_lock = threading.Lock()
        self._tts_gate = threading.Condition()
        self._document_generation = 0
        # Canonical coordination order for lifecycle-sensitive background work:
        # _background_work_condition -> {_audio_export_lock, _prepare_lock, _prefetch_lock} -> _tts_gate.
        # Never call _background_work_is_open() while holding _prefetch_lock or _tts_gate.
        self._lifecycle_service = BackgroundLifecycleService(
            capture_prefetch=self._capture_prefetch_for_shutdown,
            clear_prefetch=self._clear_prefetch_queue_impl,
            reset_prefetch=self._reset_prefetch_queue_impl,
        )
        self.dialogue_tts_max_chars = int(environment_value("FUSION_READER_DIALOGUE_TTS_MAX_CHARS", "520") or "520")
        self.fast_note_ack = (environment_value("FUSION_READER_FAST_NOTE_ACK", "0") or "0").strip().lower() not in {
            "0",
            "false",
            "no",
        }
        self.fast_dialogue_ack = (
            environment_value("FUSION_READER_FAST_DIALOGUE_ACK", "0") or "0"
        ).strip().lower() not in {
            "0",
            "false",
            "no",
        }
        self._reference_documents: dict[str, dict] = {}
        self._laboratory_focus: dict = {}
        self._main_source_path = ""
        self._main_source_type = ""
        self.dialogue_allow_supreme = (
            environment_value("FUSION_READER_DIALOGUE_ALLOW_SUPREME", "0") or "0"
        ).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._dialogue_service = DialogueService(
            conversation=self.conversation,
            reasoning_mode=str(getattr(self.conversation, "default_reasoning_mode", "thinking") or "thinking"),
            dialogue_allow_supreme=self.dialogue_allow_supreme,
            persist=lambda: self._persist_session_state(),
            trace=lambda event: self._append_dialogue_trace(event),
            services_status=lambda: self._dialogue_services_status(),
        )
        self.session_state_path = Path(session_state_path) if session_state_path else None
        self._session_store = (
            AtomicJSONStore(self.session_state_path, schema_version=1, max_bytes=32 * 1024 * 1024)
            if self.session_state_path is not None
            else None
        )
        self._persistence_service = SessionPersistenceService(
            session=self.session,
            store=self._session_store,
            voice=self.voice,
            conversation=self.conversation,
            references=self._reference_documents,
            get_preferences=self._session_preferences,
            apply_preferences=self._apply_session_preferences,
            get_main_source=lambda: (self._main_source_path, self._main_source_type),
            set_main_source=self._set_main_source,
            reset_preparation=self._reset_prepare_for_new_document,
            build_document_record=self._document_record,
        )
        self._notes_service = NotesService(
            session=self.session,
            notes=self.notes,
            dialogue_history=self._dialogue_service.dialogue_history,
            dialogue_lock=self._dialogue_service.dialogue_lock,
            chat_history=self._dialogue_service.chat_history,
            chat_lock=self._dialogue_service.chat_lock,
            looks_like_note_request=self._looks_like_note_request,
        )
        self._research_service = ResearchService(self.external_research, self._external_research_snapshot)
        self.dialogue_trace_path = (
            (self.session_state_path.parent / "dialogue_trace.jsonl") if self.session_state_path else None
        )
        self.audio_export_root = (
            (Path(audio_export_root) if audio_export_root is not None else find_downloads_dir()).expanduser().resolve()
        )
        self._audio_export_service = AudioExportService(
            session=self.session,
            voice=self.voice,
            cache=self.cache,
            tts=self.tts,
            output_root=self.audio_export_root,
            background_condition=self._background_work_condition,
            background_is_open_locked=lambda: self._background_work_is_open_locked(),
            before_registration=lambda: self._before_audio_export_registration(),
            wait_for_interactive_tts=lambda: self._wait_for_interactive_tts(),
            synthesize=lambda text, voice, language: self._synthesize_cached_with_settings(text, voice, language),
            max_items=job_max_items,
            ttl_seconds=job_ttl_seconds,
        )
        self._preparation_service = PreparationService(
            session=self.session,
            voice=self.voice,
            cache=self.cache,
            tts=self.tts,
            background_condition=self._background_work_condition,
            background_is_open_locked=lambda: self._background_work_is_open_locked(),
            document_generation=lambda: self._document_generation,
            before_registration=lambda: self._before_prepare_registration(),
            synthesize=lambda text, voice, language: self._synthesize_cached_with_settings(text, voice, language),
            human_error=lambda detail: self._human_tts_error(detail, action="prepare"),
        )
        self._lifecycle_service.bind_background_services(
            audio_export=self._audio_export_service,
            preparation=self._preparation_service,
        )
        self._audio_service = AudioService(
            tts=self.tts,
            voice=self.voice,
            metrics=self.metrics,
            synthesize=self._synthesize_cached,
            play=self._play,
            record_metric=self._record_voice_metric,
            persist=self._persist_session_state,
            clear_prefetch=self._clear_prefetch_queue,
            prepare_status=self.prepare_status,
            cancel_prepare=self.cancel_prepare,
            status=self.status,
        )
        self._reader_service = ReaderService(
            self.session,
            voice=self.voice,
            cache=self.cache,
            persist=self._persist_session_state,
            prefetch_current=self.prefetch_current,
            prefetch_next=self.prefetch_next,
            status=self.status,
            document_generation=lambda: self._document_generation,
            artifact_for_index=self._artifact_for_index,
            play=self._play,
            record_metric=self._record_voice_metric,
            human_tts_error=lambda detail: self._human_tts_error(detail, action="read"),
            tts_gate=self._tts_gate,
        )
        self._restore_session_state()
        if self.session.document:
            self._document_generation = max(1, self._document_generation)

    @property
    def _audio_export_lock(self):
        return self._audio_export_service.lock

    @property
    def _audio_export_cancel(self):
        return self._audio_export_service.cancel_event

    @property
    def _audio_export_thread(self):
        return self._audio_export_service.thread

    @_audio_export_thread.setter
    def _audio_export_thread(self, value):
        self._audio_export_service.thread = value

    @property
    def _audio_export_jobs(self):
        return self._audio_export_service.jobs

    @property
    def _audio_export_active_job_id(self):
        return self._audio_export_service.active_job_id

    @_audio_export_active_job_id.setter
    def _audio_export_active_job_id(self, value):
        self._audio_export_service.active_job_id = value

    @property
    def _audio_export_latest_job_id(self):
        return self._audio_export_service.latest_job_id

    @_audio_export_latest_job_id.setter
    def _audio_export_latest_job_id(self, value):
        self._audio_export_service.latest_job_id = value

    @property
    def _background_work_condition(self):
        return self._lifecycle_service.condition

    @property
    def _background_work_state(self):
        return self._lifecycle_service.state

    @_background_work_state.setter
    def _background_work_state(self, value):
        self._lifecycle_service.state = value

    @property
    def _background_work_active_tts(self):
        return self._lifecycle_service.active_tts

    @_background_work_active_tts.setter
    def _background_work_active_tts(self, value):
        self._lifecycle_service.active_tts = value

    @property
    def _background_work_shutdown_context(self):
        return self._lifecycle_service.shutdown_context

    @_background_work_shutdown_context.setter
    def _background_work_shutdown_context(self, value):
        self._lifecycle_service.shutdown_context = value

    @property
    def _background_work_closing(self):
        return self._lifecycle_service.state == "closing"

    @property
    def _background_work_closed(self):
        return self._lifecycle_service.state == "closed"

    @property
    def _chat_lock(self):
        return self._dialogue_service.chat_lock

    @property
    def _chat_history(self):
        return self._dialogue_service.chat_history

    @_chat_history.setter
    def _chat_history(self, value):
        self._dialogue_service.chat_history[:] = value

    @property
    def _dialogue_lock(self):
        return self._dialogue_service.dialogue_lock

    @property
    def _dialogue_history(self):
        return self._dialogue_service.dialogue_history

    @_dialogue_history.setter
    def _dialogue_history(self, value):
        self._dialogue_service.dialogue_history[:] = value

    @property
    def reasoning_mode(self):
        return self._dialogue_service.reasoning_mode

    @reasoning_mode.setter
    def reasoning_mode(self, value):
        self._dialogue_service.reasoning_mode = value

    @property
    def laboratory_mode(self):
        return self._dialogue_service.laboratory_mode

    @laboratory_mode.setter
    def laboratory_mode(self, value):
        self._dialogue_service.laboratory_mode = value

    @property
    def profile(self):
        return self._dialogue_service.profile

    @profile.setter
    def profile(self, value):
        self._dialogue_service.profile = value

    @property
    def veil(self):
        return self._dialogue_service.veil

    @veil.setter
    def veil(self, value):
        self._dialogue_service.veil = value

    @property
    def dialogue_allow_supreme(self):
        service = self.__dict__.get("_dialogue_service")
        if service is not None:
            return service.dialogue_allow_supreme
        return bool(self.__dict__.get("_dialogue_allow_supreme_bootstrap", False))

    @dialogue_allow_supreme.setter
    def dialogue_allow_supreme(self, value):
        service = self.__dict__.get("_dialogue_service")
        if service is not None:
            service.dialogue_allow_supreme = bool(value)
        else:
            self.__dict__["_dialogue_allow_supreme_bootstrap"] = bool(value)

    @property
    def _interactive_tts_pending(self):
        service = self.__dict__.get("_reader_service")
        return service.interactive_tts_pending if service is not None else 0

    @_interactive_tts_pending.setter
    def _interactive_tts_pending(self, value):
        self._reader_service.interactive_tts_pending = value

    @property
    def _read_request_sequence(self):
        return self._reader_service.read_request_sequence

    @_read_request_sequence.setter
    def _read_request_sequence(self, value):
        self._reader_service.read_request_sequence = value

    @property
    def _read_lock(self):
        return self._reader_service.read_lock

    @property
    def _prepare_lock(self):
        return self._preparation_service.lock

    @property
    def _prepare_cancel(self):
        return self._preparation_service.cancel_event

    @_prepare_cancel.setter
    def _prepare_cancel(self, value):
        self._preparation_service.cancel_event = value

    @property
    def _prepare_thread(self):
        return self._preparation_service.thread

    @_prepare_thread.setter
    def _prepare_thread(self, value):
        self._preparation_service.thread = value

    @property
    def _prepare_generation(self):
        return self._preparation_service.generation

    @_prepare_generation.setter
    def _prepare_generation(self, value):
        self._preparation_service.generation = value

    @property
    def _prepare_status(self):
        return self._preparation_service.state

    @_prepare_status.setter
    def _prepare_status(self, value):
        self._preparation_service.state = value

    def _set_background_work_state_locked(self, state: str) -> None:
        self._lifecycle_service.set_state_locked(state)

    def _begin_tts_operation(self) -> bool:
        return self._lifecycle_service.begin_tts_operation()

    def _end_tts_operation(self) -> None:
        self._lifecycle_service.end_tts_operation()

    def _background_work_is_open(self) -> bool:
        return self._lifecycle_service.is_open()

    def _background_work_is_open_locked(self) -> bool:
        return self._lifecycle_service.is_open_locked()

    def _wait_for_active_tts_locked(self, deadline: float) -> None:
        self._lifecycle_service.wait_for_active_tts_locked(deadline)

    def _capture_background_shutdown_context(self, context: BackgroundShutdownContext) -> None:
        self._lifecycle_service.capture_shutdown_context(context)

    def _before_audio_export_registration(self) -> None:
        return

    def _before_prepare_registration(self) -> None:
        return

    def _effective_reasoning_mode(self, *, dialogue: bool = False) -> dict:
        return self._dialogue_service.effective_reasoning(dialogue=dialogue)

    def _append_dialogue_trace(self, event: dict) -> None:
        if self.dialogue_trace_path is None:
            return
        try:
            self.dialogue_trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self.dialogue_trace_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=True) + "\n")
        except Exception:
            return

    def _document_record(
        self,
        doc_id: str,
        title: str,
        text: str,
        source_path: str = "",
        source_type: str = "",
    ) -> dict:
        document = Document.from_text(doc_id, title, text)
        preview = " ".join(document.text.split())[:220]
        return {
            "doc_id": document.doc_id,
            "title": document.title,
            "text": document.text,
            "source_path": str(source_path or ""),
            "source_type": str(source_type or "text"),
            "total": len(document.chunks),
            "preview": preview,
        }

    def _public_document_record(self, record: dict) -> dict:
        return {
            "doc_id": str(record.get("doc_id") or ""),
            "title": str(record.get("title") or ""),
            "source_path": str(record.get("source_path") or ""),
            "source_type": str(record.get("source_type") or ""),
            "total": int(record.get("total") or 0),
            "preview": str(record.get("preview") or ""),
        }

    def _snapshot_document_record(self, record: dict) -> dict:
        document = Document.from_text(
            str(record.get("doc_id") or ""),
            str(record.get("title") or ""),
            str(record.get("text") or ""),
        )
        return {
            **self._public_document_record(record),
            "text": document.text,
            "chunks": [
                {
                    "chunk_number": index + 1,
                    "text": chunk,
                }
                for index, chunk in enumerate(document.chunks)
            ],
        }

    def _main_document_record(self) -> dict | None:
        document = self.session.document
        if not document:
            return None
        return self._document_record(
            document.doc_id,
            document.title,
            document.text,
            source_path=self._main_source_path,
            source_type=self._main_source_type,
        )

    def _reference_document_items(self) -> list[dict]:
        return [self._public_document_record(item) for item in self._reference_documents.values()]

    def _all_document_records(self) -> list[dict]:
        records: list[dict] = []
        main_record = self._main_document_record()
        if main_record:
            records.append(
                {
                    **self._snapshot_document_record(main_record),
                    "role": "main",
                    "current": int(self.session.status().get("current") or 0),
                }
            )
        for item in self._reference_documents.values():
            records.append({**self._snapshot_document_record(item), "role": "reference"})
        return records

    def _normalize_search_text(self, text: str) -> str:
        lowered = str(text or "").strip().lower().replace("¿", "").replace("¡", "")
        plain = "".join(char for char in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(char))
        return " ".join(plain.split())

    def _meaningful_search_terms(self, text: str) -> list[str]:
        stopwords = {
            "a",
            "al",
            "alguna",
            "alguno",
            "andá",
            "anda",
            "andar",
            "bloque",
            "busca",
            "buscá",
            "buscar",
            "chunk",
            "con",
            "consulta",
            "de",
            "del",
            "dice",
            "donde",
            "dónde",
            "documento",
            "el",
            "en",
            "esa",
            "ese",
            "esta",
            "este",
            "exactamente",
            "habla",
            "ir",
            "la",
            "las",
            "lo",
            "los",
            "main",
            "muestra",
            "mostrame",
            "muéstrame",
            "para",
            "parte",
            "por",
            "principal",
            "que",
            "qué",
            "quiero",
            "sección",
            "seccion",
            "sobre",
            "texto",
            "ver",
            "vamos",
            "y",
        }
        tokens = re.findall(r"[a-záéíóúñ0-9_./-]{3,}", self._normalize_search_text(text))
        out: list[str] = []
        for token in tokens:
            if token in stopwords or token in out:
                continue
            out.append(token)
        return out

    def _normalized_external_key(self, text: str) -> str:
        return ResearchService.normalized_key(text)

    def _looks_like_external_research_request(self, text: str) -> bool:
        return self._research_service.is_explicit_request(text)

    def _external_research_snapshot(self) -> dict:
        snapshot = self.reader_snapshot()
        with self._chat_lock:
            snapshot["laboratory_history"] = list(self._chat_history)
        with self._dialogue_lock:
            snapshot["dialogue_history"] = list(self._dialogue_history)
        return snapshot

    def _run_external_research(self, message: str) -> ExternalResearchResult:
        return self._research_service.research(message)

    def _external_research_chat_response(self, message: str, started: float) -> dict | None:
        if not self._looks_like_external_research_request(message):
            return None
        snapshot = self.session.status()
        result = self._run_external_research(message)
        answer = str(result.answer or "").strip()
        if result.ok:
            self._remember_chat_turn(message, answer)
        return {
            "ok": result.ok,
            "answer": answer,
            "model": result.model or "openclaw_bridge",
            "detail": result.detail,
            "duration_ms": result.duration_ms or int((time.perf_counter() - started) * 1000),
            "reasoning_mode": self.reasoning_mode,
            "reasoning_passes": 1,
            "provider": result.provider or "openclaw_bridge",
            "external_research": True,
            "external_query": result.query or str(message or "").strip(),
            "external_summary": result.summary,
            "external_findings": list(result.findings),
            "external_sources": list(result.sources),
            "doc_id": snapshot.get("doc_id") or "",
            "title": snapshot.get("title") or "",
            "current": snapshot.get("current") or 0,
            "total": snapshot.get("total") or 0,
        }

    def _resolve_document_record(self, selector: str = "") -> dict | None:
        clean_selector = self._normalize_search_text(selector)
        records = self._all_document_records()
        if not clean_selector:
            return records[0] if records else None
        for record in records:
            title = self._normalize_search_text(record.get("title") or "")
            doc_id = self._normalize_search_text(record.get("doc_id") or "")
            if clean_selector == title or clean_selector == doc_id:
                return record
        for record in records:
            haystack = " ".join(
                [
                    self._normalize_search_text(record.get("title") or ""),
                    self._normalize_search_text(record.get("doc_id") or ""),
                ]
            ).strip()
            if clean_selector and clean_selector in haystack:
                return record
        selector_terms = self._meaningful_search_terms(clean_selector)
        ranked: list[tuple[int, dict]] = []
        for record in records:
            haystack = " ".join(
                [
                    self._normalize_search_text(record.get("title") or ""),
                    self._normalize_search_text(record.get("doc_id") or ""),
                    self._normalize_search_text(record.get("preview") or ""),
                ]
            ).strip()
            score = sum(1 for term in selector_terms if term in haystack)
            if score > 0:
                ranked.append((score, record))
        if ranked:
            ranked.sort(key=lambda item: item[0], reverse=True)
            return ranked[0][1]
        return None

    def _set_laboratory_focus(self, record: dict, chunk_index: int, query: str = "", reason: str = "") -> dict:
        chunks_value = record.get("chunks")
        chunks = chunks_value if isinstance(chunks_value, list) else []
        if chunk_index < 0 or chunk_index >= len(chunks):
            raise IndexError("chunk_out_of_bounds")
        item = chunks[chunk_index]
        text = str(item.get("text") or "").strip()
        focus = {
            "ok": True,
            "doc_id": str(record.get("doc_id") or ""),
            "title": str(record.get("title") or ""),
            "role": str(record.get("role") or "reference"),
            "source_type": str(record.get("source_type") or "text"),
            "total": int(record.get("total") or len(chunks)),
            "chunk_index": int(chunk_index),
            "chunk_number": int(item.get("chunk_number") or chunk_index + 1),
            "text": text,
            "query": str(query or "").strip(),
            "reason": str(reason or "").strip(),
            "updated_ts": time.time(),
        }
        self._laboratory_focus = focus
        return dict(focus)

    def laboratory_focus_status(self) -> dict:
        return dict(self._laboratory_focus) if self._laboratory_focus else {}

    def _focus_record(self) -> dict | None:
        focus = self.laboratory_focus_status()
        if not focus:
            return None
        record = self._resolve_document_record(str(focus.get("doc_id") or focus.get("title") or ""))
        if not record:
            return None
        return record

    def _new_prepare_status(self) -> dict:
        return new_prepare_status()

    def _new_audio_export_job(self, snapshot: AudioExportSnapshot) -> AudioExportJob:
        return self._audio_export_service.new_job(snapshot)

    def _resolve_audio_export_snapshot(
        self,
        mode: str,
        block: int | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> AudioExportSnapshot:
        return self._audio_export_service.resolve_snapshot(mode, block, start, end)

    def load_text(
        self,
        doc_id: str,
        title: str,
        text: str,
        prefetch: bool = True,
        source_path: str = "",
        source_type: str = "",
    ) -> dict:
        self._begin_document_lifecycle()
        self._laboratory_focus = {}
        status = self.session.load(Document.from_text(doc_id, title, text))
        self._main_source_path = str(source_path or "")
        self._main_source_type = str(source_type or "text")
        self._reference_documents.pop(str(status.get("doc_id") or ""), None)
        self._persist_session_state(text=str(text or ""), source_path=source_path, source_type=source_type)
        if prefetch:
            self.prefetch_current()
        return self.status()

    def load_quick_text(
        self,
        text: str,
        title: str = "Texto pegado",
        start_offset: int = 0,
        prefetch: bool = False,
    ) -> dict:
        original = str(text or "")
        if not original.strip():
            return {"ok": False, "error": "empty_quick_text"}
        self._begin_document_lifecycle()
        self._laboratory_focus = {}
        digest = hashlib.sha256(f"{time.time_ns()}:{original}".encode("utf-8")).hexdigest()[:16]
        document = Document.from_text(
            f"quick-{digest}", str(title or "Texto pegado").strip() or "Texto pegado", original
        )
        status = self.session.load(document)
        selected_chunk = chunk_index_for_offset(original, document.chunks, start_offset)
        if document.chunks:
            self.session.cursor = selected_chunk
            self.session.updated_ts = time.time()
        self._main_source_path = ""
        self._main_source_type = "quick_text"
        self._reference_documents.pop(str(status.get("doc_id") or ""), None)
        # Persist preferences and references while deliberately writing no
        # recoverable copy of this transient document.
        self._persist_session_state(source_type="quick_text")
        if prefetch:
            self.prefetch_current()
        out = self.status()
        out["quick_text"] = {
            "transient": True,
            "characters": len(original),
            "start_offset": max(0, min(int(start_offset or 0), len(original))),
            "start_chunk": selected_chunk + 1 if document.chunks else 0,
        }
        return out

    def load_file(self, path: str | Path, prefetch: bool = True) -> dict:
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        return self.load_text(p.stem, p.name, text, prefetch=prefetch, source_path=str(p), source_type="file")

    def add_reference_text(
        self,
        doc_id: str,
        title: str,
        text: str,
        source_path: str = "",
        source_type: str = "",
    ) -> dict:
        main_doc = self.session.document
        clean_doc_id = str(doc_id or "").strip()
        if main_doc and clean_doc_id and clean_doc_id == main_doc.doc_id:
            return {"ok": False, "error": "reference_matches_main_document"}
        record = self._document_record(
            clean_doc_id or title, title, text, source_path=source_path, source_type=source_type
        )
        self._reference_documents[record["doc_id"]] = record
        self._persist_session_state()
        return {
            **self.status(),
            "reference_added": self._public_document_record(record),
            "message": f"{record['title']} agregado como documento de consulta.",
        }

    def add_reference_file(self, path: str | Path) -> dict:
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        return self.add_reference_text(p.stem, p.name, text, source_path=str(p), source_type="file")

    def list_reference_documents(self) -> dict:
        return {"ok": True, "items": self._reference_document_items()}

    def remove_reference_document(self, doc_id: str) -> dict:
        removed = self._reference_documents.pop(str(doc_id or ""), None)
        if not removed:
            return {"ok": False, "error": "reference_not_found"}
        if str(self._laboratory_focus.get("doc_id") or "") == str(removed.get("doc_id") or ""):
            self._laboratory_focus = {}
        self._persist_session_state()
        return {
            "ok": True,
            "removed": True,
            "reference": self._public_document_record(removed),
            "items": self._reference_document_items(),
        }

    def promote_reference_document(self, doc_id: str, prefetch: bool = True) -> dict:
        selected_id = str(doc_id or "")
        record = self._reference_documents.pop(selected_id, None)
        if not record:
            return {"ok": False, "error": "reference_not_found"}
        previous_main = self._main_document_record()
        self._begin_document_lifecycle()
        status = self.session.load(
            Document.from_text(
                str(record.get("doc_id") or ""), str(record.get("title") or ""), str(record.get("text") or "")
            )
        )
        self._main_source_path = str(record.get("source_path") or "")
        self._main_source_type = str(record.get("source_type") or "text")
        if previous_main and previous_main["doc_id"] != status.get("doc_id"):
            self._reference_documents[previous_main["doc_id"]] = previous_main
        self._laboratory_focus = {}
        self._persist_session_state()
        if prefetch:
            self.prefetch_current()
        return {
            **self.status(),
            "promoted_reference": self._public_document_record(record),
            "message": f"{record['title']} ahora es el documento principal.",
        }

    def _chat_health(self) -> dict:
        provider = getattr(self.conversation, "provider", None)
        if provider is None:
            return {"ok": False, "provider": "unknown", "detail": "chat_provider_missing"}
        health = (
            provider.health()
            if hasattr(provider, "health")
            else {"ok": False, "provider": getattr(provider, "name", "unknown"), "detail": "chat_health_missing"}
        )
        out = dict(health or {})
        out.setdefault("provider", getattr(provider, "name", "unknown"))
        out.setdefault("model", getattr(provider, "default_model", "") or "")
        return out

    def _active_chat_provider_name(self) -> str:
        provider = getattr(self.conversation, "provider", None)
        active = getattr(provider, "active", None)
        if active is not None:
            return str(getattr(active, "name", "") or getattr(provider, "selected", "") or "chat")
        # Preserve the historical trace contract for directly injected test and legacy providers.
        return "ollama"

    def _external_research_health(self) -> dict:
        return self._research_service.health()
        """Compatibility implementation retained below for stable blame history."""
        bridge = self.external_research
        out = {
            "provider": str(getattr(bridge, "name", "external_research") or "external_research"),
            "ok": False,
            "detail": "unavailable",
        }
        if hasattr(bridge, "available"):
            try:
                out["ok"] = bool(bridge.available())
            except Exception as exc:
                out["detail"] = str(exc)
                return out
        else:
            out["detail"] = "bridge_has_no_available_probe"
            return out
        if hasattr(bridge, "base_url"):
            out["url"] = str(getattr(bridge, "base_url") or "")
        if hasattr(bridge, "command"):
            out["command"] = str(getattr(bridge, "command") or "")
        if hasattr(bridge, "agent"):
            out["agent"] = str(getattr(bridge, "agent") or "")
        if hasattr(bridge, "searxng"):
            out["mode"] = "auto"
            searxng = getattr(bridge, "searxng")
            try:
                out["searxng"] = {
                    "provider": str(getattr(searxng, "name", "searxng") or "searxng"),
                    "ok": bool(searxng.available()),
                    "url": str(getattr(searxng, "base_url", "") or ""),
                }
            except Exception as exc:
                out["searxng"] = {"provider": "searxng", "ok": False, "detail": str(exc)}
            openclaw = getattr(bridge, "openclaw")
            try:
                out["openclaw"] = {
                    "provider": str(getattr(openclaw, "name", "openclaw_bridge") or "openclaw_bridge"),
                    "ok": bool(openclaw.available()),
                    "agent": str(getattr(openclaw, "agent", "") or ""),
                    "command": str(getattr(openclaw, "command", "") or ""),
                }
            except Exception as exc:
                out["openclaw"] = {"provider": "openclaw_bridge", "ok": False, "detail": str(exc)}
            out["detail"] = "ready" if out["ok"] else "all_external_bridges_unavailable"
            return out
        out["detail"] = "ready" if out["ok"] else "external_research_unavailable"
        return out

    def _dialogue_services_status(self) -> dict:
        tts = dict(self.tts.health() or {})
        stt = dict(self.stt.health() or {})
        stt.setdefault("requested_provider", getattr(self.stt, "requested_provider", self.stt.name))
        chat = self._chat_health()
        external = self._external_research_health()
        reasoning = self._effective_reasoning_mode(dialogue=True)
        return {
            "tts": {
                **tts,
                "ready": bool(tts.get("ok")),
                "owner_valid": bool(tts.get("ok")) or "tts_owner" not in str(tts.get("detail") or ""),
            },
            "stt": {
                **stt,
                "ready": bool(stt.get("ok")),
                "fallback_ready": bool(((stt.get("fallback") or {}).get("ok"))),
            },
            "chat": {
                **chat,
                "ready": bool(chat.get("ok")),
            },
            "external_research": {
                **external,
                "ready": bool(external.get("ok")),
            },
            "dialogue_reasoning": {
                "requested_mode": reasoning["requested"],
                "applied_mode": reasoning["applied"],
                "degraded": reasoning["degraded"],
                "degraded_reason": reasoning["reason"],
            },
        }

    def _human_dialogue_error(self, detail: str, *, stage: str, provider: str = "") -> str:
        clean_detail = str(detail or "").strip()
        if stage == "stt":
            if clean_detail in {"empty_transcript", "empty_audio"}:
                return "No alcancé a escuchar una frase completa. Repetímela un poco más cerca o un poco más lento."
            if clean_detail == "hallucinated_transcript":
                return ""
            return "No pude entender bien el audio que llegó a Dialogar. Probemos otra vez con una frase más clara."
        if stage == "chat":
            cloud = str(provider or "").strip().lower() in {"openai", "openclaw"}
            if cloud:
                if clean_detail == "empty_answer":
                    return (
                        "Me quedé sin respuesta útil de OpenAI mediante OpenClaw. "
                        "Repetímelo en una frase corta y vuelvo a intentarlo."
                    )
                if (
                    "http_" in clean_detail
                    or "Connection refused" in clean_detail
                    or "timed out" in clean_detail
                    or clean_detail == "openclaw_timeout"
                ):
                    return (
                        "OpenAI mediante OpenClaw no respondió a tiempo desde Fusion. "
                        "La lectura sigue sana; probemos otra vez en unos segundos."
                    )
                return (
                    "Se cayó el diálogo con OpenAI mediante OpenClaw justo cuando estaba respondiendo. "
                    "La lectura sigue sana; si querés, repetímelo y lo intento de nuevo."
                )
            if clean_detail == "empty_answer":
                return (
                    "Me quedé sin respuesta útil del modelo local. Repetímelo en una frase corta y vuelvo a intentarlo."
                )
            if "http_" in clean_detail or "Connection refused" in clean_detail or "timed out" in clean_detail:
                return "El modelo local de diálogo no respondió a tiempo desde Fusion. La lectura sigue sana; probemos otra vez en unos segundos."
            return "Se cayó el diálogo local justo cuando estaba respondiendo. La lectura sigue sana; si querés, repetímelo y lo intento de nuevo."
        if stage == "tts":
            return "Pude preparar la respuesta, pero la voz neural no salió esta vez. Te dejo el texto y la lectura sigue disponible."
        if stage == "external":
            return "Salí a buscar afuera, pero esta vez no pude cerrar bien la consulta externa. Te dejo lo que alcancé a recuperar sin romper Dialogar."
        return "Hubo un problema puntual en Dialogar, pero la lectura sigue sana."

    def _finalize_dialogue_failure(
        self,
        *,
        started: float,
        transcript: str,
        answer: str,
        detail: str,
        model: str,
        provider: str,
        stage: str,
        artifact: AudioArtifact | None = None,
        stt_provider: str = "",
        stt_ms: int = 0,
        chat_ms: int = 0,
        tts_ms: int = 0,
        reasoning: dict | None = None,
        trace_extra: dict | None = None,
    ) -> dict:
        reasoning = reasoning or self._effective_reasoning_mode(dialogue=True)
        chosen_answer = str(answer or self._human_dialogue_error(detail, stage=stage, provider=provider)).strip()
        chosen_artifact = artifact
        measured_tts_ms = int(tts_ms or 0)
        if chosen_answer and chosen_artifact is None:
            tts_started = time.perf_counter()
            chosen_artifact = self._synthesize_cached(chosen_answer)
            measured_tts_ms = chosen_artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
        if chosen_artifact is None:
            chosen_artifact = AudioArtifact(False, provider=provider or "null", detail="no_audio_attempt")
        return {
            "ok": True,
            "transcript": transcript,
            "answer": chosen_answer,
            "audio": str(chosen_artifact.path or ""),
            "cached": chosen_artifact.cached,
            "provider": chosen_artifact.provider or provider,
            "detail": detail,
            "model": model,
            "stt_provider": stt_provider,
            "stt_ms": int(stt_ms or 0),
            "chat_ms": int(chat_ms or 0),
            "tts_ms": measured_tts_ms,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "voice_ok": bool(chosen_artifact.ok),
            "audio_available": bool(chosen_artifact.ok and chosen_artifact.path),
            "human_error": chosen_answer,
            "failed_stage": stage,
            "reasoning_mode_requested": reasoning["requested"],
            "reasoning_mode_applied": reasoning["applied"],
            "reasoning_degraded": reasoning["degraded"],
            "trace": {
                "chat_ms": int(chat_ms or 0),
                "tts_ms": measured_tts_ms,
                "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                **(trace_extra or {}),
            },
        }

    def status(self) -> dict:
        out = self.session.status()
        out["voice"] = self.voice.voice
        out["language"] = self.voice.language
        out["tts"] = self.tts.health()
        out["services"] = self._dialogue_services_status()
        out["reasoning"] = self.reasoning_status()
        out["laboratory_mode"] = self.laboratory_mode_status()
        out["profile"] = self.profile_status()
        out["veil"] = self.veil_status()
        out["chat_provider"] = self.chat_provider_status()
        main_record = self._main_document_record()
        total = int(out.get("total") or 0)
        current = int(out.get("current") or 0)
        document_loaded = bool(main_record and total > 0)
        anchor_mode = str(self.laboratory_mode or "document").strip().lower() or "document"
        document_doc_id = str(out.get("doc_id") or "") if document_loaded else ""
        document_title = str(out.get("title") or "") if document_loaded else ""
        document_current = current if document_loaded else 0
        document_total = total if document_loaded else 0
        out["document"] = {
            "loaded": document_loaded,
            "doc_id": document_doc_id,
            "title": document_title,
            "current": document_current,
            "total": document_total,
            "source_type": self._main_source_type if document_loaded else "",
            "transient": bool(document_loaded and self._main_source_type == "quick_text"),
        }
        out["anchor"] = {
            "mode": "free" if anchor_mode == "free" else "document",
            "uses_document": bool(document_loaded and anchor_mode != "free"),
            "document_available": document_loaded,
        }
        out["main_document"] = self._public_document_record(main_record) if main_record else {}
        out["reference_documents"] = self._reference_document_items()
        out["laboratory_focus"] = self.laboratory_focus_status()
        with self._prefetch_lock:
            out["prefetch_index"] = self._prefetch_index
            out["prefetch_done"] = bool(self._prefetch_future and self._prefetch_future.done())
            out["prefetch_age_ms"] = (
                int((time.time() - self._prefetch_started_ts) * 1000) if self._prefetch_started_ts else 0
            )
            out["prefetch_indexes"] = sorted({self._prefetch_index_from_key(key) for key in self._prefetch_futures})
            out["prefetch_done_indexes"] = sorted(
                {self._prefetch_index_from_key(key) for key, future in self._prefetch_futures.items() if future.done()}
            )
            out["prefetch_ahead"] = self.prefetch_ahead
        out["document_generation"] = self._document_generation
        current_text = self.session.current_chunk()
        cached_audio = bool(current_text and self.cache.get(current_text, self.voice.voice, self.voice.language))
        out["audio_state"] = "cached" if cached_audio else ("needs_generation" if current_text else "empty")
        out["audio_ready"] = cached_audio
        out["audio_cached"] = cached_audio
        tts_health = dict(out.get("tts") or {})
        detail = str(tts_health.get("detail") or "").lower()
        out["tts_state"] = (
            "ready"
            if tts_health.get("ok")
            else ("starting" if "start" in detail or "loading" in detail else "temporarily_unavailable")
        )
        out["prepare"] = self.prepare_status()
        out["audio_export"] = self.audio_export_overview()
        out["notes"] = self.notes_summary()
        return out

    def _synthesize_cached(self, text: str) -> AudioArtifact:
        return self._synthesize_cached_with_settings(text, self.voice.voice, self.voice.language)

    def synthesize_for_export(self, text: str, voice: str = "", language: str = "es") -> AudioArtifact:
        """Generate cache-aware audio for an explicit, non-interactive export workflow."""
        return self._synthesize_cached_with_settings(
            text,
            str(voice or self.voice.voice),
            str(language or self.voice.language),
        )

    def _synthesize_cached_with_settings(
        self, text: str, voice: str, language: str, *, interactive: bool = False, prefetch_key: tuple | None = None
    ) -> AudioArtifact:
        if not self._begin_tts_operation():
            return AudioArtifact(False, provider=self.tts.name, detail="shutdown_in_progress")
        tts_locked = False
        try:
            cached = self.cache.get(text, voice, language)
            if cached:
                return cached
            if not interactive:
                while True:
                    if not self._background_work_is_open():
                        return AudioArtifact(False, provider=self.tts.name, detail="shutdown_in_progress")
                    with self._tts_gate:
                        should_wait = self._interactive_tts_pending and not self._prefetch_key_is_promoted_locked(
                            prefetch_key
                        )
                        if should_wait:
                            self._tts_gate.wait(timeout=0.1)
                    if should_wait:
                        continue
                    if not self._background_work_is_open():
                        return AudioArtifact(False, provider=self.tts.name, detail="shutdown_in_progress")
                    self._tts_lock.acquire()
                    tts_locked = True
                    with self._tts_gate:
                        should_retry = self._interactive_tts_pending and not self._prefetch_key_is_promoted_locked(
                            prefetch_key
                        )
                    if not should_retry:
                        break
                    self._tts_lock.release()
                    tts_locked = False
            else:
                self._tts_lock.acquire()
                tts_locked = True
            try:
                if not self._background_work_is_open():
                    return AudioArtifact(False, provider=self.tts.name, detail="shutdown_in_progress")
                cached = self.cache.get(text, voice, language)
                if cached:
                    return cached
                artifact = self.tts.synthesize(text, voice=voice, language=language)
                if not artifact.ok and artifact.detail == "http_400":
                    artifact = self._synthesize_segmented_with_settings(text, voice, language)
                return self.cache.put(text, voice, language, artifact)
            finally:
                if tts_locked:
                    self._tts_lock.release()
        finally:
            self._end_tts_operation()

    def _synthesize_segmented_with_settings(self, text: str, voice: str, language: str) -> AudioArtifact:
        segment_limit = getattr(self.tts, "max_input_chars", 0) or self.tts_segment_chars
        pieces = self._split_text_for_tts(text, max(80, int(segment_limit)))
        if len(pieces) <= 1:
            return AudioArtifact(False, provider=self.tts.name, detail="http_400")
        artifacts: list[AudioArtifact] = []
        paths: list[Path] = []
        started = time.perf_counter()
        try:
            for piece in pieces:
                artifact = self.tts.synthesize(piece, voice=voice, language=language)
                if not artifact.ok or not artifact.path:
                    return artifact
                artifacts.append(artifact)
                paths.append(Path(artifact.path))
            fd, name = tempfile.mkstemp(prefix="fusion_reader_v2_segmented_", suffix=".wav")
            os.close(fd)
            output_path = Path(name)
            method = concat_wav_files(paths, output_path)
            return AudioArtifact(
                True,
                path=output_path,
                provider=self.tts.name,
                detail=f"segmented:{method}",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        finally:
            for path in paths:
                try:
                    if path.exists():
                        path.unlink()
                except Exception:
                    continue

    def _split_text_for_tts(self, text: str, max_chars: int) -> list[str]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return []
        if len(cleaned) <= max_chars:
            return [cleaned]
        parts: list[str] = []
        paragraphs = [piece.strip() for piece in re.split(r"\n\s*\n+", cleaned) if piece.strip()]
        for paragraph in paragraphs or [cleaned]:
            if len(paragraph) <= max_chars:
                parts.append(paragraph)
                continue
            sentences = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+", paragraph) if piece.strip()]
            if not sentences:
                sentences = [paragraph]
            current = ""
            for sentence in sentences:
                if len(sentence) > max_chars:
                    if current:
                        parts.append(current)
                        current = ""
                    parts.extend(self._split_long_tts_unit(sentence, max_chars))
                    continue
                candidate = sentence if not current else f"{current} {sentence}"
                if current and len(candidate) > max_chars:
                    parts.append(current)
                    current = sentence
                else:
                    current = candidate
            if current:
                parts.append(current)
        return parts or [cleaned]

    def _split_long_tts_unit(self, text: str, max_chars: int) -> list[str]:
        words = text.split()
        if not words:
            return []
        parts: list[str] = []
        current = ""
        for word in words:
            if len(word) > max_chars:
                if current:
                    parts.append(current)
                    current = ""
                for offset in range(0, len(word), max_chars):
                    parts.append(word[offset : offset + max_chars])
                continue
            candidate = word if not current else f"{current} {word}"
            if current and len(candidate) > max_chars:
                parts.append(current)
                current = word
            else:
                current = candidate
        if current:
            parts.append(current)
        return parts

    def _prefetch_key(self, generation: int, index: int, text: str, voice: str, language: str) -> tuple:
        return (generation, index, voice, language, hashlib.sha256(text.encode("utf-8")).hexdigest())

    def _prefetch_index_from_key(self, key) -> int:
        return int(key[1]) if isinstance(key, tuple) else int(key)

    def _prefetch_key_is_promoted_locked(self, key: tuple | None) -> bool:
        return bool(key is not None and key in self._prefetch_promoted_keys)

    def _promote_prefetch_key(self, key: tuple) -> None:
        with self._tts_gate:
            self._prefetch_promoted_keys.add(key)
            self._tts_gate.notify_all()

    def _forget_prefetch_promotion(self, key: tuple) -> None:
        with self._tts_gate:
            if key in self._prefetch_promoted_keys:
                self._prefetch_promoted_keys.discard(key)
                self._tts_gate.notify_all()

    def _artifact_for_index(self, generation: int, index: int, text: str, voice: str, language: str) -> AudioArtifact:
        key = self._prefetch_key(generation, index, text, voice, language)
        with self._prefetch_lock:
            future = self._prefetch_futures.get(key)
        if future:
            self._promote_prefetch_key(key)
            try:
                artifact = future.result(timeout=self.prefetch_wait_seconds)
                self._forget_prefetch(key, future)
                return artifact
            except TimeoutError:
                self._forget_prefetch(key, future)
                self._reset_prefetch_queue(future)
                return AudioArtifact(False, provider=self.tts.name, detail="prefetch_timeout")
            finally:
                self._forget_prefetch_promotion(key)
        cached = self.cache.get(text, voice, language)
        if cached:
            return cached
        return self._synthesize_cached_with_settings(text, voice, language, interactive=True)

    def prefetch_current(self) -> None:
        self.prefetch_window(self.session.cursor)

    def prefetch_next(self) -> None:
        self.prefetch_window(self.session.cursor + 1)

    def prefetch_window(self, start_index: int) -> None:
        for offset in range(self.prefetch_ahead + 1):
            self._prefetch(start_index + offset)

    def _prefetch(self, index: int) -> None:
        document = self.session.document
        if not document or index < 0 or index >= len(document.chunks):
            return
        text = document.chunks[index]
        generation = self._document_generation
        voice = self.voice.voice
        language = self.voice.language
        key = self._prefetch_key(generation, index, text, voice, language)
        with self._background_work_condition:
            if not self._background_work_is_open_locked():
                return
            with self._prefetch_lock:
                if not self._background_work_is_open_locked():
                    return
                existing = self._prefetch_futures.get(key)
                if existing and not existing.done():
                    return
                future = self._executor.submit(
                    self._synthesize_cached_with_settings, text, voice, language, prefetch_key=key
                )
                self._prefetch_futures[key] = future
                self._prefetch_started[key] = time.time()
                self._set_primary_prefetch_locked()

    def _forget_prefetch(self, key, future: Future[AudioArtifact]) -> None:
        with self._prefetch_lock:
            if self._prefetch_futures.get(key) is future:
                self._prefetch_futures.pop(key, None)
                self._prefetch_started.pop(key, None)
            if self._prefetch_future is future:
                self._set_primary_prefetch_locked()

    def _set_primary_prefetch_locked(self) -> None:
        if not self._prefetch_futures:
            self._prefetch_future = None
            self._prefetch_index = None
            self._prefetch_started_ts = None
            return
        current = self.session.cursor
        key = min(
            self._prefetch_futures,
            key=lambda item: (abs(self._prefetch_index_from_key(item) - current), self._prefetch_index_from_key(item)),
        )
        self._prefetch_index = self._prefetch_index_from_key(key)
        self._prefetch_future = self._prefetch_futures[key]
        self._prefetch_started_ts = self._prefetch_started.get(key)

    def _reset_prefetch_queue(self, stale_future: Future[AudioArtifact]) -> None:
        self._lifecycle_service.reset_prefetch_queue(stale_future)

    def read_current(self, play: bool = True) -> dict:
        return self._reader_service.read_current(play)

    def next(self) -> dict:
        return self._reader_service.next()

    def previous(self) -> dict:
        return self._reader_service.previous()

    def jump(self, one_based_index: int) -> dict:
        return self._reader_service.jump(one_based_index)

    def prepare_document(self, start: str = "cursor") -> dict:
        return self._preparation_service.start(start)

    def cancel_prepare(self) -> dict:
        return self._preparation_service.cancel()

    def prepare_status(self) -> dict:
        return self._preparation_service.status()

    def _reset_prepare_for_new_document(self) -> None:
        self._preparation_service.reset()

    def _wait_for_interactive_tts(self, cancel_event: threading.Event | None = None) -> None:
        event = cancel_event or self._prepare_cancel
        while True:
            if event.is_set() or not self._background_work_is_open():
                return
            with self._tts_gate:
                if not self._interactive_tts_pending or event.is_set():
                    return
                self._tts_gate.wait(timeout=0.1)

    def _begin_document_lifecycle(self) -> None:
        self._document_generation += 1
        self._reset_prepare_for_new_document()
        self._clear_prefetch_queue()

    def _human_tts_error(self, detail: str, *, action: str = "read") -> str:
        clean = str(detail or "").strip()
        if clean.startswith("tts_owner_"):
            return "El servicio de voz no está disponible para Fusion. Iniciá el TTS de Fusion o seleccioná un motor válido."
        if clean.startswith("tts_foreign_doctora_lucy_port"):
            return "La voz disponible pertenece a otro proyecto. Fusion no va a usar ese puerto."
        if clean.startswith("tts_historic_unassigned_port"):
            return "El puerto histórico 7852 no es válido para la voz de Fusion."
        if clean in {"empty_tts_text", "no_current_chunk"}:
            return "No encontré texto legible para leer en este bloque."
        if "timed out" in clean or "timeout" in clean:
            return "La voz tardó demasiado en responder. Probemos otra vez en unos segundos."
        if clean.startswith("http_400"):
            return "La voz rechazó este bloque tal como llegó. Probá con otro bloque o con una voz distinta."
        if clean.startswith("http_") or "Connection refused" in clean or "refused" in clean:
            return "El servicio de voz no respondió desde Fusion. Iniciá TTS o seleccioná otro motor."
        if clean == "shutdown_in_progress":
            return "La lectura se detuvo porque el servicio se está cerrando."
        if action == "prepare":
            return "No pude preparar el audio porque la voz no está disponible en este momento."
        return "No pude leer este bloque porque la voz no está disponible en este momento."

    def audio_export_overview(self) -> dict:
        return self._audio_export_service.overview()

    def audio_export_status(self, job_id: str) -> dict:
        return self._audio_export_service.status(job_id)

    def start_audio_export(
        self, mode: str, block: int | None = None, start: int | None = None, end: int | None = None
    ) -> dict:
        return self._audio_export_service.start(mode, block, start, end)

    def cancel_audio_export(self, job_id: str) -> dict:
        return self._audio_export_service.cancel(job_id)

    def get_audio_export_download(self, job_id: str) -> dict:
        return self._audio_export_service.download(job_id)

    def _finish_audio_export_job(
        self,
        job_id: str,
        state: str,
        detail: str,
        *,
        output_path: Path | None = None,
        concat_method: str = "",
        error: str = "",
    ) -> None:
        self._audio_export_service.finish(
            job_id,
            state,
            detail,
            output_path=output_path,
            concat_method=concat_method,
            error=error,
        )

    def _audio_export_worker(self, job_id: str) -> None:
        self._audio_export_service.worker(job_id)

    def test_voice(self, text: str = "Prueba de voz neural del lector conversacional.", play: bool = True) -> dict:
        return self._audio_service.test(text, play=play)

    def voices(self) -> dict:
        return self._audio_service.catalog()

    def recent_voice_metrics(self, limit: int = 20) -> dict:
        return self._audio_service.recent(limit)

    def voice_metrics_summary(self, limit: int = 500) -> dict:
        return self._audio_service.summary(limit)

    def voice_metrics_by_document(self, limit: int = 1000) -> dict:
        return self._audio_service.by_document(limit)

    def voice_metrics_by_chunk(self, doc_id: str = "", limit: int = 1000) -> dict:
        return self._audio_service.by_chunk(doc_id, limit)

    def reader_snapshot(self) -> dict:
        document = self.session.document
        status = self.session.status()
        if not document:
            return {
                **status,
                "current_chunk": "",
                "previous_chunk": "",
                "next_chunk": "",
                "document_text": "",
                "notes": [],
                "main_document": {},
                "document_chunks": [],
                "reference_documents": [
                    self._snapshot_document_record(item) for item in self._reference_documents.values()
                ],
                "laboratory_focus": self.laboratory_focus_status(),
                "laboratory_mode": self.laboratory_mode_status(),
            }
        cursor = self.session.cursor
        chunks = document.chunks
        main_record = self._main_document_record()
        return {
            **status,
            "current_chunk": self.session.current_chunk(),
            "previous_chunk": chunks[cursor - 1] if cursor > 0 else "",
            "next_chunk": chunks[cursor + 1] if cursor + 1 < len(chunks) else "",
            "document_text": document.text,
            "notes": self.list_notes(doc_id=document.doc_id, chunk_index=None).get("items", []),
            "main_document": self._snapshot_document_record(main_record) if main_record else {},
            "document_chunks": [
                {
                    "chunk_number": index + 1,
                    "text": chunk,
                }
                for index, chunk in enumerate(chunks)
            ],
            "reference_documents": [
                self._snapshot_document_record(item) for item in self._reference_documents.values()
            ],
            "laboratory_focus": self.laboratory_focus_status(),
            "laboratory_mode": self.laboratory_mode_status(),
        }

    def _extract_navigation_plan(self, text: str) -> dict | None:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split())
        if not clean:
            return None
        plan: dict[str, object] = {}
        block_match = re.search(
            r"(?:^|\b)(?:and[aá]|anda|ir|ite|vamos|salt[aá]|salta|mostrame|mu[eé]strame|ll[eé]vame|llevame|abr[ií]|abre|quiero\s+ver|quiero\s+ir\s+a|ver)?"
            r".*?\b(?:bloque|chunk|parte|secci[oó]n)\s+(\d{1,4})(?:\s+(?:de|del|en)\s+(.+?))?(?=\s+y\s+(?:busca|busc[aá]|buscar|encuentra|encontr[aá]|encontrar|ubica|ubic[aá]|d[oó]nde|donde)\b|$)",
            clean,
            flags=re.IGNORECASE,
        )
        if block_match:
            plan["focus_chunk_number"] = int(block_match.group(1))
            plan["focus_selector"] = str(block_match.group(2) or "").strip(" .,:;!?")
        search_match = re.search(
            r"\b(?:busca|busc[aá]|buscar|encuentra|encontr[aá]|encontrar|ubica|ubic[aá])\b\s*(?:d[oó]nde\s+(?:habla|dice)\s+de\s+)?(.+)$",
            clean,
            flags=re.IGNORECASE,
        )
        if search_match:
            tail = str(search_match.group(1) or "").strip(" .,:;!?")
            query, selector = self._split_search_tail(tail)
            if query:
                plan["search_query"] = query
                plan["search_selector"] = selector
        where_match = re.search(
            r"\b(?:d[oó]nde\s+habla\s+de|d[oó]nde\s+dice|donde\s+habla\s+de|donde\s+dice)\s+(.+)$",
            clean,
            flags=re.IGNORECASE,
        )
        if where_match:
            tail = str(where_match.group(1) or "").strip(" .,:;!?")
            query, selector = self._split_search_tail(tail)
            if query:
                plan["search_query"] = query
                plan["search_selector"] = selector
        return plan or None

    def _split_search_tail(self, tail: str) -> tuple[str, str]:
        clean_tail = str(tail or "").strip(" .,:;!?")
        if not clean_tail:
            return "", ""
        quote_match = re.search(r"[\"“”']([^\"“”']{2,})[\"“”']", clean_tail)
        quoted = str(quote_match.group(1) or "").strip() if quote_match else ""
        selector = ""
        split_match = re.match(r"(.+?)\s+\ben\b\s+(.+)$", clean_tail, flags=re.IGNORECASE)
        if split_match:
            query = quoted or str(split_match.group(1) or "").strip(" .,:;!?")
            selector = str(split_match.group(2) or "").strip(" .,:;!?")
            return query, selector
        return quoted or clean_tail, selector

    def _extract_compare_plan(self, text: str) -> dict | None:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split())
        if not clean:
            return None
        if not re.search(r"\bcompar", clean, flags=re.IGNORECASE):
            return None
        lowered = clean.lower()
        if " con " not in lowered:
            return None
        left_raw, right_raw = re.split(r"\bcon\b", clean, maxsplit=1, flags=re.IGNORECASE)
        left_raw = re.sub(r"^.*?\bcompar[aá]\s+", "", left_raw, flags=re.IGNORECASE).strip(" .,:;!?")
        right_raw = str(right_raw or "").strip(" .,:;!?")
        if not left_raw or not right_raw:
            return None
        left = self._parse_compare_target(left_raw, prefer_focus=True)
        right = self._parse_compare_target(right_raw, prefer_focus=False)
        if not left or not right:
            return None
        return {"left": left, "right": right}

    def _parse_compare_target(self, text: str, prefer_focus: bool = False) -> dict | None:
        clean = str(text or "").strip(" .,:;!?")
        if not clean:
            return None
        if re.search(r"\b(?:este|ese|actual)\s+bloque\b", clean, flags=re.IGNORECASE):
            focus = self.laboratory_focus_status() if prefer_focus else {}
            if focus:
                return {
                    "source": "focus",
                    "doc_id": str(focus.get("doc_id") or ""),
                    "title": str(focus.get("title") or ""),
                    "chunk_number": int(focus.get("chunk_number") or 0),
                }
            status = self.session.status()
            if status.get("doc_id"):
                return {
                    "source": "main",
                    "doc_id": str(status.get("doc_id") or ""),
                    "title": str(status.get("title") or ""),
                    "chunk_number": int(status.get("current") or 0),
                }
        match = re.search(
            r"\b(?:bloque|chunk|parte|secci[oó]n)\s+(\d{1,4})(?:\s+(?:de|del|en)\s+(.+))?$",
            clean,
            flags=re.IGNORECASE,
        )
        if match:
            return {
                "source": "explicit",
                "doc_selector": str(match.group(2) or "").strip(" .,:;!?"),
                "chunk_number": int(match.group(1)),
            }
        # fall back to current block of selected document
        record = self._resolve_document_record(clean)
        if record:
            default_chunk = 1
            if record.get("role") == "main":
                default_chunk = int(self.session.status().get("current") or 1)
            return {
                "source": "document_only",
                "doc_selector": clean,
                "chunk_number": default_chunk,
            }
        return None

    def _resolve_compare_target(self, target: dict) -> dict | None:
        chunk_number = int(target.get("chunk_number") or 0)
        if chunk_number <= 0:
            return None
        record = None
        if target.get("source") == "focus":
            record = self._focus_record()
        if record is None and target.get("doc_id"):
            record = self._resolve_document_record(str(target.get("doc_id") or ""))
        if record is None:
            record = self._resolve_document_record(str(target.get("doc_selector") or ""))
        if record is None and self.session.document:
            record = self._resolve_document_record(str(self.session.document.doc_id))
        if not record:
            return None
        chunks = record.get("chunks")
        if not isinstance(chunks, list) or chunk_number > len(chunks):
            return None
        item = chunks[chunk_number - 1]
        return {
            "doc_id": str(record.get("doc_id") or ""),
            "title": str(record.get("title") or ""),
            "role": str(record.get("role") or "reference"),
            "source_type": str(record.get("source_type") or "text"),
            "total": int(record.get("total") or len(chunks)),
            "chunk_number": chunk_number,
            "chunk_index": chunk_number - 1,
            "text": str(item.get("text") or "").strip(),
        }

    def _compare_terms(self, text: str) -> list[str]:
        return self._meaningful_search_terms(text)

    def _compare_summary(self, left: dict, right: dict, dialogue: bool = False) -> str:
        left_terms = set(self._compare_terms(left.get("text") or ""))
        right_terms = set(self._compare_terms(right.get("text") or ""))
        overlap = [term for term in left_terms.intersection(right_terms)]
        overlap = sorted(overlap, key=len, reverse=True)[:6]
        left_only = sorted(left_terms - right_terms, key=len, reverse=True)[:4]
        right_only = sorted(right_terms - left_terms, key=len, reverse=True)[:4]
        left_excerpt = self._navigation_excerpt(str(left.get("text") or ""), max_chars=190 if dialogue else 240)
        right_excerpt = self._navigation_excerpt(str(right.get("text") or ""), max_chars=190 if dialogue else 240)
        if dialogue:
            parts = [
                f"Comparé {left['title']} bloque {left['chunk_number']} con {right['title']} bloque {right['chunk_number']}.",
                f"Coinciden en {', '.join(overlap[:3])}."
                if overlap
                else "No comparten vocabulario fuerte en esta muestra.",
                f"El primero dice: {left_excerpt}",
                f"El segundo dice: {right_excerpt}",
            ]
            return " ".join(parts)
        lines = [
            "Comparación:",
            f"- {left['title']} | bloque {left['chunk_number']} de {left['total']}: {left_excerpt}",
            f"- {right['title']} | bloque {right['chunk_number']} de {right['total']}: {right_excerpt}",
            f"Coincidencias: {', '.join(overlap) if overlap else 'no encontré coincidencias léxicas fuertes en esta muestra.'}",
            f"Rasgos del primero: {', '.join(left_only) if left_only else 'sin rasgos diferenciales claros.'}",
            f"Rasgos del segundo: {', '.join(right_only) if right_only else 'sin rasgos diferenciales claros.'}",
        ]
        return "\n".join(lines)

    def _handle_compare_intent(self, text: str, dialogue: bool = False) -> dict | None:
        plan = self._extract_compare_plan(text)
        if not plan:
            return None
        left = self._resolve_compare_target(plan["left"])
        right = self._resolve_compare_target(plan["right"])
        if not left or not right:
            return {
                "ok": False,
                "answer": "",
                "model": "reader_compare",
                "detail": "compare_target_not_found",
                "error": "compare_target_not_found",
            }
        summary = self._compare_summary(left, right, dialogue=dialogue)
        focus_record = self._resolve_document_record(str(right.get("doc_id") or right.get("title") or ""))
        if not focus_record:
            return {
                "ok": False,
                "answer": "",
                "model": "reader_compare",
                "detail": "document_not_found",
                "error": "document_not_found",
            }
        focus = self._set_laboratory_focus(focus_record, int(right["chunk_index"]), reason="compare")
        return {
            "ok": True,
            "answer": summary,
            "model": "reader_compare",
            "detail": "compare_blocks",
            "doc_id": focus.get("doc_id") or "",
            "title": focus.get("title") or "",
            "current": focus.get("chunk_number") or 0,
            "total": focus.get("total") or 0,
            "comparison": {"left": left, "right": right},
            "laboratory_focus": focus,
        }

    def _search_chunk_matches(self, query: str, selector: str = "", limit: int = 5) -> list[dict]:
        selected_record = self._resolve_document_record(selector) if selector else None
        records = [selected_record] if selected_record else self._all_document_records()
        normalized_query = self._normalize_search_text(query)
        terms = self._meaningful_search_terms(query)
        matches: list[tuple[int, int, dict]] = []
        for record in records:
            if not record:
                continue
            chunks = record.get("chunks")
            if not isinstance(chunks, list):
                continue
            for index, item in enumerate(chunks):
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                haystack = self._normalize_search_text(text)
                score = 0
                if normalized_query and normalized_query in haystack:
                    score += 30 + len(terms)
                score += sum(4 for term in terms if term and term in haystack)
                if score <= 0:
                    continue
                matches.append(
                    (
                        score,
                        -index,
                        {
                            "doc_id": str(record.get("doc_id") or ""),
                            "title": str(record.get("title") or ""),
                            "role": str(record.get("role") or "reference"),
                            "source_type": str(record.get("source_type") or "text"),
                            "total": int(record.get("total") or len(chunks)),
                            "chunk_index": index,
                            "chunk_number": int(item.get("chunk_number") or index + 1),
                            "text": text,
                        },
                    )
                )
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in matches[: max(1, int(limit or 1))]]

    def _navigation_excerpt(self, text: str, max_chars: int = 280) -> str:
        excerpt = " ".join(str(text or "").split())
        if len(excerpt) <= max_chars:
            return excerpt
        return excerpt[:max_chars].rstrip() + "..."

    def _search_no_matches_answer(self, query: str, dialogue: bool = False) -> str:
        clean_query = str(query or "").strip() or "eso"
        if dialogue:
            return f"No encontré coincidencias para {clean_query} en los documentos cargados. Si querés, probamos otra forma de nombrarlo o busco por una frase más larga."
        return (
            f"No encontré coincidencias para '{clean_query}' en los documentos cargados.\n\n"
            "Probá con otra forma de nombrarlo, sinónimos, o una frase más larga."
        )

    def _is_reflective_navigation_request(self, text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).lower()
        if not clean:
            return False
        if re.search(
            r"\b(?:exactamente|literal|textual(?:mente)?|cita|citame|cit[aá]melo|recita|repite|leer|le[eé]me|leelo|le[eé]lo|que\s+dice(?:\s+exactamente)?)\b",
            clean,
            flags=re.IGNORECASE,
        ):
            return False
        return bool(
            re.search(
                r"\b(?:pens(?:a|á|ar|emos)|filos[oó]fic|interpret|analiz|lectura|reflexion|reflexi[oó]n|opina|opin[aá]s|opinas|opin[ií]on|"
                r"qu[eé]\s+ves|qu[eé]\s+interpret|qu[eé]\s+te\s+parece|qu[eé]\s+plantea|qu[eé]\s+implica|qu[eé]\s+significa|"
                r"tension|problematiz|critic|latente|vuelo\s+propio|desarroll|pensamiento\s+cr[ií]tico)\b",
                clean,
                flags=re.IGNORECASE,
            )
        )

    def _handle_navigation_intent(self, text: str, dialogue: bool = False) -> dict | None:
        plan = self._extract_navigation_plan(text)
        if not plan:
            return None
        focused_record: dict | None = None
        if plan.get("focus_chunk_number"):
            selected = self._resolve_document_record(str(plan.get("focus_selector") or ""))
            if not selected:
                return {
                    "ok": False,
                    "answer": "",
                    "model": "reader_navigation",
                    "detail": "document_not_found",
                    "error": "document_not_found",
                }
            chunk_number = int(plan.get("focus_chunk_number") or 0)
            chunks_value = selected.get("chunks")
            chunks = chunks_value if isinstance(chunks_value, list) else []
            if chunk_number < 1 or chunk_number > len(chunks):
                return {
                    "ok": False,
                    "answer": "",
                    "model": "reader_navigation",
                    "detail": "chunk_out_of_bounds",
                    "error": "chunk_out_of_bounds",
                    "doc_id": selected.get("doc_id") or "",
                    "title": selected.get("title") or "",
                    "total": len(chunks),
                }
            if selected.get("role") == "main":
                self.jump(chunk_number)
                selected = self._resolve_document_record(selected.get("doc_id") or "") or selected
            focused_record = selected
            if not plan.get("search_query"):
                focus = self._set_laboratory_focus(selected, chunk_number - 1, reason="focus_block")
                if self._is_reflective_navigation_request(text):
                    return {
                        "ok": True,
                        "answer": "",
                        "model": "reader_navigation",
                        "detail": "focus_block_context",
                        "doc_id": focus.get("doc_id") or "",
                        "title": focus.get("title") or "",
                        "current": focus.get("chunk_number") or 0,
                        "total": focus.get("total") or 0,
                        "laboratory_focus": focus,
                        "continue_with_llm": True,
                    }
                excerpt = self._navigation_excerpt(focus["text"], max_chars=340 if dialogue else 460)
                answer = (
                    f"Quedé en {focus['title']}, bloque {focus['chunk_number']} de {focus['total']}. {excerpt}"
                    if dialogue
                    else f"Foco de laboratorio en {focus['title']}, bloque {focus['chunk_number']} de {focus['total']}.\n\n{excerpt}"
                )
                return {
                    "ok": True,
                    "answer": answer,
                    "model": "reader_navigation",
                    "detail": "focus_block",
                    "doc_id": focus.get("doc_id") or "",
                    "title": focus.get("title") or "",
                    "current": focus.get("chunk_number") or 0,
                    "total": focus.get("total") or 0,
                    "laboratory_focus": focus,
                }
        if plan.get("search_query"):
            selector = str(plan.get("search_selector") or "")
            if not selector and focused_record:
                selector = str(focused_record.get("doc_id") or focused_record.get("title") or "")
            matches = self._search_chunk_matches(str(plan.get("search_query") or ""), selector=selector, limit=5)
            if not matches:
                return {
                    "ok": True,
                    "answer": self._search_no_matches_answer(str(plan.get("search_query") or ""), dialogue=dialogue),
                    "model": "reader_navigation",
                    "detail": "search_no_matches",
                    "error": "",
                    "matches": [],
                }
            focus_record = self._resolve_document_record(
                matches[0].get("doc_id") or ""
            ) or self._resolve_document_record(matches[0].get("title") or "")
            if not focus_record:
                return {
                    "ok": False,
                    "answer": "",
                    "model": "reader_navigation",
                    "detail": "document_not_found",
                    "error": "document_not_found",
                }
            focus = self._set_laboratory_focus(
                focus_record,
                int(matches[0]["chunk_index"]),
                query=str(plan.get("search_query") or ""),
                reason="search",
            )
            if dialogue:
                answer = (
                    f"Encontré {plan.get('search_query')} en {focus['title']}, bloque {focus['chunk_number']}. "
                    f"{self._navigation_excerpt(focus['text'], max_chars=300)}"
                )
            else:
                lines = [f"Encontré coincidencias para '{plan.get('search_query')}'."]
                for item in matches[:3]:
                    lines.append(
                        f"- {item['title']} | bloque {item['chunk_number']} de {item['total']}: {self._navigation_excerpt(item['text'], max_chars=180)}"
                    )
                answer = "\n".join(lines)
            return {
                "ok": True,
                "answer": answer,
                "model": "reader_navigation",
                "detail": "search_matches",
                "doc_id": focus.get("doc_id") or "",
                "title": focus.get("title") or "",
                "current": focus.get("chunk_number") or 0,
                "total": focus.get("total") or 0,
                "laboratory_focus": focus,
                "matches": matches,
            }
        return None

    def chat(self, message: str, model: str = "", chunk_index: int | None = None) -> dict:
        started = time.perf_counter()
        note_text = self._extract_note_command(message)
        if note_text:
            if self._should_create_laboratory_note(message) or self._should_route_generic_note_to_laboratory(
                message, note_text
            ):
                created = self.create_laboratory_note(note_text)
            else:
                selected_chunk = self._resolve_note_chunk_index(chunk_index)
                created = self.create_note(note_text, chunk_index=selected_chunk)
            if not created.get("ok"):
                return {
                    "ok": False,
                    "answer": "",
                    "model": "reader_notes",
                    "detail": created.get("error") or "note_failed",
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "doc_id": self.session.status().get("doc_id") or "",
                    "title": self.session.status().get("title") or "",
                    "current": self.session.status().get("current") or 0,
                    "total": self.session.status().get("total") or 0,
                }
            note = created["note"]
            return {
                "ok": True,
                "answer": self._note_saved_answer(note),
                "model": "reader_notes",
                "detail": "",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "doc_id": note.get("doc_id") or "",
                "title": note.get("title") or "",
                "current": note.get("chunk_number") or 0,
                "total": self.session.status().get("total") or 0,
                "note": note,
            }
        if self._looks_like_note_request(message):
            snapshot = self.session.status()
            return {
                "ok": True,
                "answer": "Sí, puedo guardar notas. Decime: tomá nota de ...",
                "model": "reader_notes",
                "detail": "missing_note_text",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "doc_id": snapshot.get("doc_id") or "",
                "title": snapshot.get("title") or "",
                "current": snapshot.get("current") or 0,
                "total": snapshot.get("total") or 0,
            }
        comparison = self._handle_compare_intent(message, dialogue=False)
        if comparison is not None:
            comparison["duration_ms"] = int((time.perf_counter() - started) * 1000)
            if comparison.get("ok"):
                self._remember_chat_turn(message, comparison.get("answer") or "")
            return comparison
        external = self._external_research_chat_response(message, started)
        if external is not None:
            return external
        navigation = self._handle_navigation_intent(message, dialogue=False)
        if navigation is not None:
            navigation["duration_ms"] = int((time.perf_counter() - started) * 1000)
            if navigation.get("ok"):
                self._remember_chat_turn(message, navigation.get("answer") or "")
            return navigation
        snapshot = self.reader_snapshot()
        with self._chat_lock:
            history = list(self._chat_history)
        selected_model = model
        if not selected_model and self.profile == "bohemia":
            selected_model = environment_value("FUSION_READER_BOHEMIA_CHAT_MODEL") or ""
        result = self.conversation.ask(
            message,
            snapshot=snapshot,
            model=selected_model,
            history=history,
            reasoning_mode=self.reasoning_mode,
            profile=self.profile,
            veil=self.veil,
        )
        if result.ok:
            self._remember_chat_turn(message, result.answer)
        return {
            "ok": result.ok,
            "answer": result.answer,
            "model": result.model,
            "detail": result.detail,
            "duration_ms": result.duration_ms or int((time.perf_counter() - started) * 1000),
            "reasoning_mode": result.reasoning_mode or self.reasoning_mode,
            "reasoning_passes": result.reasoning_passes or 1,
            "doc_id": snapshot.get("doc_id") or "",
            "title": snapshot.get("title") or "",
            "current": snapshot.get("current") or 0,
            "total": snapshot.get("total") or 0,
        }

    def _remember_chat_turn(self, user_message: str, assistant_answer: str) -> None:
        self._dialogue_service.remember_chat_turn(user_message, assistant_answer)

    def clear_laboratory_history(self) -> dict:
        return self._dialogue_service.clear_history()

    def dialogue_status(self) -> dict:
        out = self._dialogue_service.status()
        out["chat_provider"] = self.chat_provider_status()
        return out

    def chat_provider_status(self) -> dict:
        provider = getattr(self.conversation, "provider", None)
        if provider is None:
            return {"id": "unknown", "label": "No disponible", "selected": True, "available": []}
        selected = str(getattr(provider, "selected", "local") or "local")
        catalog = provider.catalog() if hasattr(provider, "catalog") else []
        if not catalog:
            catalog = [
                {
                    "id": selected,
                    "label": "Local 14B" if selected == "local" else selected,
                    "model": str(getattr(provider, "default_model", "") or ""),
                    "cloud": selected == "openai",
                }
            ]
        active = next((item for item in catalog if str(item.get("id") or "") == selected), catalog[0])
        health = provider.health() if hasattr(provider, "health") else {}
        return {
            **active,
            "id": selected,
            "selected": True,
            "ready": bool((health or {}).get("ok")),
            "detail": str((health or {}).get("detail") or ""),
            "active_provider": str((health or {}).get("active_provider") or (health or {}).get("provider") or ""),
            "available": catalog,
        }

    def set_chat_provider(self, provider_id: str) -> dict:
        provider = getattr(self.conversation, "provider", None)
        if provider is None or not hasattr(provider, "select"):
            return {"ok": False, "error": "chat_provider_not_selectable"}
        try:
            selected = provider.select(provider_id)
        except ValueError:
            return {"ok": False, "error": "invalid_chat_provider"}
        self._persist_session_state()
        self._append_dialogue_trace(
            {"ts": time.time(), "event": "chat_provider_changed", "selected_provider": selected}
        )
        return {"ok": True, **self.chat_provider_status()}

    def reasoning_status(self) -> dict:
        return self._dialogue_service.reasoning_status()

    def get_voice_catalog(self) -> dict:
        return self._audio_service.catalog(fallback_current=True)

    def set_voice(self, voice: str) -> dict:
        return self._audio_service.set_voice(voice)

    def laboratory_mode_status(self) -> dict:
        return self._dialogue_service.laboratory_mode_status()

    def set_reasoning_mode(self, mode: str) -> dict:
        return self._dialogue_service.set_reasoning_mode(mode)

    def set_laboratory_mode(self, mode: str) -> dict:
        return self._dialogue_service.set_laboratory_mode(mode)

    def profile_status(self) -> dict:
        return self._dialogue_service.profile_status()

    def set_profile(self, mode: str) -> dict:
        return self._dialogue_service.set_profile(mode)

    def veil_catalog(self) -> list[dict]:
        return [
            {"mode": "lucy", "label": "Lucy", "description": ""},
            {
                "mode": "nocturna",
                "label": "Nocturna",
                "description": "Hablá como en una conversación de madrugada: más cerca, más lenta, con sombra, sin volverlo clase.",
            },
            {
                "mode": "critica",
                "label": "Crítica",
                "description": "No cuides demasiado al lector. Buscá la tensión real, el punto débil y lo que la idea intenta evitar.",
            },
            {
                "mode": "sombra",
                "label": "Sombra",
                "description": "Buscá el deseo, miedo o autoengaño íntimo que sostiene esta idea por debajo.",
            },
            {
                "mode": "confesional",
                "label": "Confesional",
                "description": "Permitite hablar desde vos como Lucy cuando eso aclare la conversación, pero no te vuelvas protagonista.",
            },
            {
                "mode": "taller",
                "label": "Taller",
                "description": "Pensá con el lector, no para él. Ayudalo a fabricar una idea mejor.",
            },
            {
                "mode": "debate",
                "label": "Debate",
                "description": "No des una respuesta complaciente. Discutí y objetá; si hace falta, cerrá con una pregunta real, no automática.",
            },
            {
                "mode": "evocadora",
                "label": "Evocadora",
                "description": "No hagas poesía decorativa. Usá una imagen precisa para pensar mejor, y volvé enseguida al nervio conceptual.",
            },
            {
                "mode": "directa",
                "label": "Directa",
                "description": "Respondé seco y frontal. Una idea central, sin adornos, sin rodeos y sin suavizar lo importante.",
            },
            {
                "mode": "incomoda",
                "label": "Incómoda",
                "description": "No busques consuelo. Mostrá lo que esta idea no quiere aceptar de sí misma.",
            },
            {
                "mode": "rigurosa",
                "label": "Rigurosa",
                "description": "Ordená el argumento, separá conceptos y marcá qué no está sostenido.",
            },
            {
                "mode": "intima",
                "label": "Íntima",
                "description": "Acercá la conversación. Respondé como alguien que piensa al lado del lector, sin convertirlo en clase ni confesión teatral.",
            },
            {
                "mode": "bar_filosofico",
                "label": "Bar filosófico",
                "description": "Hablalo como una discusión inteligente de madrugada: ironía lúcida, cercanía y filo. Cerrá con una frase que deje resonancia, no necesariamente con pregunta.",
            },
            {
                "mode": "desarme",
                "label": "Desarme",
                "description": "Desarmá la frase como mecanismo: qué afirma, qué oculta, qué seduce y qué no se sostiene.",
            },
            {
                "mode": "pregunta_viva",
                "label": "Pregunta viva",
                "description": "No termines en moraleja. Cerrá con una pregunta que deje la idea abierta.",
            },
        ]

    def clear_document(self) -> dict:
        self._begin_document_lifecycle()
        self.session.document = None
        self.session.cursor = 0
        self.session.state = "idle"
        self.session.updated_ts = time.time()
        self._main_source_path = ""
        self._main_source_type = ""
        self._persist_session_state()
        self._append_dialogue_trace(
            {
                "ts": time.time(),
                "event": "document_cleared",
            }
        )
        return self.status()

    def veil_status(self) -> dict:
        mode = str(getattr(self, "veil", "lucy") or "lucy").strip().lower()
        catalog = self.veil_catalog()
        item = next((v for v in catalog if v["mode"] == mode), catalog[0])
        return {
            "mode": item["mode"],
            "label": item["label"],
            "description": item["description"],
            "available": catalog,
        }

    def set_veil(self, mode: str) -> dict:
        mode = str(mode or "lucy").strip().lower()
        catalog = self.veil_catalog()
        item = next((v for v in catalog if v["mode"] == mode), catalog[0])
        self.veil = item["mode"]
        self._persist_session_state()
        self._append_dialogue_trace(
            {
                "ts": time.time(),
                "event": "veil_changed",
                "selected_mode": self.veil,
            }
        )
        return self.veil_status()

    def dialogue_reset(self) -> dict:
        return self._dialogue_service.reset()

    def dictation_turn_text(self, text: str, commands_enabled: bool = True) -> dict:
        clean = str(text or "").strip()
        if not clean:
            return {"ok": False, "error": "empty_dictation_text", "detail": "empty_dictation_text"}
        instruction = interpret_dictation_transcript(clean, commands_enabled=commands_enabled)
        return {
            "ok": True,
            "transcript": clean,
            "instruction": instruction.to_dict(),
            "stt_provider": "text",
            "stt_ms": 0,
        }

    def dictation_turn_audio(
        self,
        path: str | Path,
        mime: str = "",
        commands_enabled: bool = True,
    ) -> dict:
        self._prioritize_dialogue()
        started = time.perf_counter()
        transcript = self.stt.transcribe_file(path, mime=mime, language=self.voice.language)
        if not transcript.ok:
            return {
                "ok": False,
                "error": "dictation_transcription_failed",
                "detail": transcript.detail or "dictation_transcription_failed",
                "transcript": transcript.text,
                "stt_provider": transcript.provider,
                "stt_ms": transcript.duration_ms,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }
        instruction = interpret_dictation_transcript(
            transcript.text,
            commands_enabled=commands_enabled,
        )
        return {
            "ok": True,
            "transcript": transcript.text,
            "instruction": instruction.to_dict(),
            "stt_provider": transcript.provider,
            "stt_ms": transcript.duration_ms,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    def dialogue_turn_text(self, text: str, model: str = "", chunk_index: int | None = None) -> dict:
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "empty_dialogue_text"}
        self._prioritize_dialogue()
        started = time.perf_counter()
        reasoning = self._effective_reasoning_mode(dialogue=True)
        trace_event = {
            "ts": time.time(),
            "event": "dialogue_turn_text",
            "requested_mode": reasoning["requested"],
            "applied_mode": reasoning["applied"],
            "reasoning_degraded": reasoning["degraded"],
            "degraded_reason": reasoning["reason"],
            "chunk_index": chunk_index,
            "transcript": text,
            "text_preview": text[:220],
        }
        if self._is_stop_dialogue_command(text):
            out = {
                "ok": True,
                "transcript": text,
                "answer": "",
                "audio": "",
                "cached": False,
                "provider": "text_ack",
                "detail": "dialogue_stopped",
                "model": "reader_control",
                "stt_ms": 0,
                "chat_ms": 0,
                "tts_ms": 0,
                "trace": {
                    "intent_ms": int((time.perf_counter() - started) * 1000),
                    "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "voice_ok": True,
                "reasoning_mode_requested": reasoning["requested"],
                "reasoning_mode_applied": reasoning["applied"],
                "reasoning_degraded": reasoning["degraded"],
            }
            self._append_dialogue_trace(
                {**trace_event, "ok": True, "detail": "dialogue_stopped", "duration_ms": out["duration_ms"]}
            )
            return out
        note_text = self._extract_note_command(text)
        intent_ms = int((time.perf_counter() - started) * 1000)
        if note_text:
            note_started = time.perf_counter()
            if self._should_create_laboratory_note(text) or self._should_route_generic_note_to_laboratory(
                text, note_text
            ):
                created = self.create_laboratory_note(note_text)
            else:
                selected_chunk = self._resolve_note_chunk_index(chunk_index)
                created = self.create_note(note_text, chunk_index=selected_chunk)
            note_ms = int((time.perf_counter() - note_started) * 1000)
            if not created.get("ok"):
                out = {
                    "ok": False,
                    "transcript": text,
                    "answer": "",
                    "model": "reader_notes",
                    "detail": created.get("error") or "note_failed",
                    "chat_ms": 0,
                    "trace": {
                        "intent_ms": intent_ms,
                        "note_ms": note_ms,
                        "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                    },
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "reasoning_mode_requested": reasoning["requested"],
                    "reasoning_mode_applied": reasoning["applied"],
                    "reasoning_degraded": reasoning["degraded"],
                }
                self._append_dialogue_trace(
                    {**trace_event, "ok": False, "detail": out["detail"], "duration_ms": out["duration_ms"]}
                )
                return out
            note = created["note"]
            spoken_answer = self._note_saved_answer(note, spoken=True)
            if self.fast_note_ack:
                artifact = AudioArtifact(True, provider="text_ack", detail="fast_note_ack")
                tts_ms = 0
            else:
                tts_started = time.perf_counter()
                artifact = self._synthesize_cached(spoken_answer)
                tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
            with self._dialogue_lock:
                self._dialogue_history.append({"role": "user", "content": text})
                self._dialogue_history.append({"role": "assistant", "content": spoken_answer})
                self._dialogue_history = self._dialogue_history[-16:]
            out = {
                "ok": True,
                "transcript": text,
                "answer": spoken_answer,
                "audio": str(artifact.path or ""),
                "cached": artifact.cached,
                "provider": artifact.provider,
                "detail": artifact.detail,
                "model": "reader_notes",
                "stt_ms": 0,
                "chat_ms": 0,
                "tts_ms": tts_ms,
                "trace": {
                    "intent_ms": intent_ms,
                    "note_ms": note_ms,
                    "tts_ms": tts_ms,
                    "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "note": note,
                "voice_ok": artifact.ok,
                "reasoning_mode_requested": reasoning["requested"],
                "reasoning_mode_applied": reasoning["applied"],
                "reasoning_degraded": reasoning["degraded"],
            }
            self._append_dialogue_trace(
                {
                    **trace_event,
                    "ok": bool(out["ok"]),
                    "detail": str(out.get("detail") or ""),
                    "note": True,
                    "tts_ok": bool(artifact.ok),
                    "duration_ms": out["duration_ms"],
                }
            )
            return out
        if self._looks_like_note_request(text):
            spoken_answer = "Sí, puedo guardar notas. Decime: tomá nota de, y lo que querés guardar."
            if self.fast_dialogue_ack:
                artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
                tts_ms = 0
            else:
                tts_started = time.perf_counter()
                artifact = self._synthesize_cached(spoken_answer)
                tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
            out = {
                "ok": True,
                "transcript": text,
                "answer": spoken_answer,
                "audio": str(artifact.path or ""),
                "cached": artifact.cached,
                "provider": artifact.provider,
                "detail": "missing_note_text",
                "model": "reader_notes",
                "stt_ms": 0,
                "chat_ms": 0,
                "tts_ms": tts_ms,
                "trace": {
                    "intent_ms": intent_ms,
                    "tts_ms": tts_ms,
                    "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "voice_ok": artifact.ok,
                "reasoning_mode_requested": reasoning["requested"],
                "reasoning_mode_applied": reasoning["applied"],
                "reasoning_degraded": reasoning["degraded"],
            }
            self._append_dialogue_trace(
                {
                    **trace_event,
                    "ok": True,
                    "detail": "missing_note_text",
                    "tts_ok": bool(artifact.ok),
                    "duration_ms": out["duration_ms"],
                }
            )
            return out
        comparison = self._handle_compare_intent(text, dialogue=True)
        if comparison is not None:
            spoken_answer = str(comparison.get("answer") or "").strip()
            if not comparison.get("ok"):
                out = {
                    "ok": False,
                    "transcript": text,
                    "answer": "",
                    "model": comparison.get("model") or "reader_compare",
                    "detail": comparison.get("detail") or comparison.get("error") or "compare_failed",
                    "chat_ms": 0,
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "reasoning_mode_requested": reasoning["requested"],
                    "reasoning_mode_applied": reasoning["applied"],
                    "reasoning_degraded": reasoning["degraded"],
                }
                self._append_dialogue_trace(
                    {**trace_event, "ok": False, "detail": out["detail"], "duration_ms": out["duration_ms"]}
                )
                return out
            if self.fast_dialogue_ack:
                artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
                tts_ms = 0
            else:
                tts_started = time.perf_counter()
                artifact = self._synthesize_cached(spoken_answer)
                tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
            with self._dialogue_lock:
                self._dialogue_history.append({"role": "user", "content": text})
                self._dialogue_history.append({"role": "assistant", "content": spoken_answer})
                self._dialogue_history = self._dialogue_history[-16:]
            out = {
                "ok": True,
                "transcript": text,
                "answer": spoken_answer,
                "audio": str(artifact.path or ""),
                "cached": artifact.cached,
                "provider": artifact.provider,
                "detail": comparison.get("detail") or artifact.detail,
                "model": comparison.get("model") or "reader_compare",
                "stt_ms": 0,
                "chat_ms": 0,
                "tts_ms": tts_ms,
                "trace": {
                    "intent_ms": intent_ms,
                    "tts_ms": tts_ms,
                    "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "voice_ok": artifact.ok,
                "laboratory_focus": comparison.get("laboratory_focus") or {},
                "comparison": comparison.get("comparison") or {},
                "reasoning_mode_requested": reasoning["requested"],
                "reasoning_mode_applied": reasoning["applied"],
                "reasoning_degraded": reasoning["degraded"],
            }
            self._append_dialogue_trace(
                {
                    **trace_event,
                    "ok": True,
                    "detail": str(out.get("detail") or ""),
                    "tts_ok": bool(artifact.ok),
                    "duration_ms": out["duration_ms"],
                }
            )
            return out
        if self._looks_like_external_research_request(text):
            research_started = time.perf_counter()
            external_result = self._run_external_research(text)
            research_ms = external_result.duration_ms or int((time.perf_counter() - research_started) * 1000)
            spoken_answer = self._shorten_dialogue_answer(
                str(external_result.spoken_answer or external_result.answer or "").strip()
            )
            if self.fast_dialogue_ack:
                artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
                tts_ms = 0
            else:
                tts_started = time.perf_counter()
                artifact = self._synthesize_cached(spoken_answer)
                tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
            if external_result.ok and str(external_result.answer or "").strip():
                with self._dialogue_lock:
                    self._dialogue_history.append({"role": "user", "content": text})
                    self._dialogue_history.append(
                        {"role": "assistant", "content": str(external_result.answer or "").strip()}
                    )
                    self._dialogue_history = self._dialogue_history[-16:]
            out = {
                "ok": True,
                "transcript": text,
                "answer": str(external_result.answer or "").strip()
                or self._human_dialogue_error(
                    str(external_result.detail or ""),
                    stage="external",
                    provider=str(external_result.provider or ""),
                ),
                "audio": str(artifact.path or ""),
                "cached": artifact.cached,
                "provider": artifact.provider,
                "detail": external_result.detail or artifact.detail,
                "model": external_result.model or "openclaw_bridge",
                "stt_ms": 0,
                "chat_ms": research_ms,
                "tts_ms": tts_ms,
                "trace": {
                    "intent_ms": intent_ms,
                    "external_ms": research_ms,
                    "tts_ms": tts_ms,
                    "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                },
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "voice_ok": artifact.ok,
                "reasoning_mode": self.reasoning_mode,
                "reasoning_passes": 1,
                "reasoning_mode_requested": reasoning["requested"],
                "reasoning_mode_applied": reasoning["applied"],
                "reasoning_degraded": reasoning["degraded"],
                "external_research": True,
                "external_query": external_result.query or text,
                "external_summary": external_result.summary,
                "external_findings": list(external_result.findings),
                "external_sources": list(external_result.sources),
                "audio_available": bool(artifact.ok and artifact.path),
                "human_error": ""
                if external_result.ok
                else (
                    str(external_result.answer or "").strip()
                    or self._human_dialogue_error(
                        str(external_result.detail or ""),
                        stage="external",
                        provider=str(external_result.provider or ""),
                    )
                ),
            }
            self._append_dialogue_trace(
                {
                    **trace_event,
                    "ok": bool(external_result.ok),
                    "detail": str(out.get("detail") or ""),
                    "human_error": str(out.get("human_error") or ""),
                    "external_research": True,
                    "external_provider": str(external_result.provider or ""),
                    "external_model": str(external_result.model or ""),
                    "chat_provider": str(external_result.provider or ""),
                    "tts_provider": str(artifact.provider or ""),
                    "external_ms": research_ms,
                    "tts_ok": bool(artifact.ok),
                    "audio_available": bool(out.get("audio_available")),
                    "duration_ms": out["duration_ms"],
                }
            )
            return out
        navigation = self._handle_navigation_intent(text, dialogue=True)
        if navigation is not None:
            if navigation.get("continue_with_llm"):
                trace_event["navigation_detail"] = str(navigation.get("detail") or "")
            else:
                spoken_answer = str(navigation.get("answer") or "").strip()
                if not navigation.get("ok"):
                    out = {
                        "ok": False,
                        "transcript": text,
                        "answer": "",
                        "model": navigation.get("model") or "reader_navigation",
                        "detail": navigation.get("detail") or navigation.get("error") or "navigation_failed",
                        "chat_ms": 0,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "reasoning_mode_requested": reasoning["requested"],
                        "reasoning_mode_applied": reasoning["applied"],
                        "reasoning_degraded": reasoning["degraded"],
                    }
                    self._append_dialogue_trace(
                        {**trace_event, "ok": False, "detail": out["detail"], "duration_ms": out["duration_ms"]}
                    )
                    return out
                if self.fast_dialogue_ack:
                    artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
                    tts_ms = 0
                else:
                    tts_started = time.perf_counter()
                    artifact = self._synthesize_cached(spoken_answer)
                    tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
                with self._dialogue_lock:
                    self._dialogue_history.append({"role": "user", "content": text})
                    self._dialogue_history.append({"role": "assistant", "content": spoken_answer})
                    self._dialogue_history = self._dialogue_history[-16:]
                out = {
                    "ok": True,
                    "transcript": text,
                    "answer": spoken_answer,
                    "audio": str(artifact.path or ""),
                    "cached": artifact.cached,
                    "provider": artifact.provider,
                    "detail": navigation.get("detail") or artifact.detail,
                    "model": navigation.get("model") or "reader_navigation",
                    "stt_ms": 0,
                    "chat_ms": 0,
                    "tts_ms": tts_ms,
                    "trace": {
                        "intent_ms": intent_ms,
                        "tts_ms": tts_ms,
                        "server_text_total_ms": int((time.perf_counter() - started) * 1000),
                    },
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "voice_ok": artifact.ok,
                    "laboratory_focus": navigation.get("laboratory_focus") or {},
                    "matches": navigation.get("matches") or [],
                    "reasoning_mode_requested": reasoning["requested"],
                    "reasoning_mode_applied": reasoning["applied"],
                    "reasoning_degraded": reasoning["degraded"],
                }
                self._append_dialogue_trace(
                    {
                        **trace_event,
                        "ok": True,
                        "detail": str(out.get("detail") or ""),
                        "tts_ok": bool(artifact.ok),
                        "duration_ms": out["duration_ms"],
                    }
                )
                return out
        snapshot = self.reader_snapshot()
        with self._chat_lock:
            snapshot["laboratory_history"] = list(self._chat_history)
        with self._dialogue_lock:
            history = list(self._dialogue_history)
        chat_started = time.perf_counter()
        selected_model = model
        if not selected_model and self.profile == "bohemia":
            selected_model = environment_value("FUSION_READER_BOHEMIA_CHAT_MODEL") or ""
        chat_result = self.conversation.ask_dialogue(
            text,
            snapshot=snapshot,
            history=history,
            model=selected_model,
            reasoning_mode=reasoning["applied"],
            profile=self.profile,
            veil=self.veil,
        )
        chat_ms = chat_result.duration_ms or int((time.perf_counter() - chat_started) * 1000)
        if not chat_result.ok:
            out = self._finalize_dialogue_failure(
                started=started,
                transcript=text,
                answer="",
                detail=str(chat_result.detail or "dialogue_failed"),
                model=chat_result.model or self._active_chat_provider_name(),
                provider=self._active_chat_provider_name(),
                stage="chat",
                chat_ms=chat_ms,
                reasoning=reasoning,
                trace_extra={"intent_ms": intent_ms},
            )
            out["reasoning_mode"] = chat_result.reasoning_mode or reasoning["applied"]
            out["reasoning_passes"] = chat_result.reasoning_passes or 1
            self._append_dialogue_trace(
                {
                    **trace_event,
                    "ok": False,
                    "detail": out["detail"],
                    "human_error": str(out.get("human_error") or ""),
                    "chat_provider": self._active_chat_provider_name(),
                    "chat_model": str(out.get("model") or ""),
                    "chat_ms": chat_ms,
                    "tts_provider": str(out.get("provider") or ""),
                    "tts_ok": bool(out.get("voice_ok")),
                    "audio_available": bool(out.get("audio_available")),
                    "duration_ms": out["duration_ms"],
                }
            )
            return out
        spoken_answer = self._shorten_dialogue_answer(chat_result.answer)
        if self.fast_dialogue_ack:
            artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
            tts_ms = 0
        else:
            tts_started = time.perf_counter()
            artifact = self._synthesize_cached(spoken_answer)
            tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
        if str(spoken_answer or "").strip():
            with self._dialogue_lock:
                self._dialogue_history.append({"role": "user", "content": text})
                self._dialogue_history.append({"role": "assistant", "content": spoken_answer})
                self._dialogue_history = self._dialogue_history[-16:]
        out = {
            "ok": True,
            "transcript": text,
            "answer": spoken_answer,
            "audio": str(artifact.path or ""),
            "cached": artifact.cached,
            "provider": artifact.provider,
            "detail": artifact.detail or chat_result.detail,
            "model": chat_result.model,
            "stt_ms": 0,
            "chat_ms": chat_ms,
            "tts_ms": tts_ms,
            "reasoning_mode": chat_result.reasoning_mode or reasoning["applied"],
            "reasoning_passes": chat_result.reasoning_passes or 1,
            "reasoning_mode_requested": reasoning["requested"],
            "reasoning_mode_applied": reasoning["applied"],
            "reasoning_degraded": reasoning["degraded"],
            "trace": {
                "intent_ms": intent_ms,
                "chat_ms": chat_ms,
                "tts_ms": tts_ms,
                "server_text_total_ms": int((time.perf_counter() - started) * 1000),
            },
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "voice_ok": artifact.ok,
            "audio_available": bool(artifact.ok and artifact.path),
        }
        self._append_dialogue_trace(
            {
                **trace_event,
                "ok": True,
                "detail": str(out.get("detail") or ""),
                "chat_provider": self._active_chat_provider_name(),
                "chat_model": str(out.get("model") or ""),
                "chat_ms": chat_ms,
                "tts_ms": tts_ms,
                "reasoning_passes": out["reasoning_passes"],
                "duration_ms": out["duration_ms"],
                "tts_provider": str(artifact.provider or ""),
                "tts_ok": bool(artifact.ok),
                "audio_available": bool(out.get("audio_available")),
            }
        )
        return out

    def dialogue_turn_audio(
        self,
        path: str | Path,
        mime: str = "",
        model: str = "",
        chunk_index: int | None = None,
        audio_meta: dict | None = None,
    ) -> dict:
        self._prioritize_dialogue()
        started = time.perf_counter()
        audio_path = Path(path)
        audio_meta = audio_meta or {}

        def _meta_int(name: str, default: int = 0) -> int:
            try:
                return max(0, int(float(str(audio_meta.get(name, "") or default))))
            except Exception:
                return default

        def _meta_float(name: str, default: float = 0.0) -> float:
            try:
                return max(0.0, float(str(audio_meta.get(name, "") or default)))
            except Exception:
                return default

        def _meta_text(name: str, default: str = "") -> str:
            return str(audio_meta.get(name, "") or default)[:80]

        audio_trace = {
            "audio_size_bytes": _meta_int("audio_size_bytes", audio_path.stat().st_size if audio_path.exists() else 0),
            "audio_mime": str(mime or "")[:80],
            "capture_ms": _meta_int("capture_ms"),
            "mic_rms": round(_meta_float("mic_rms"), 6),
            "mic_peak": round(_meta_float("mic_peak"), 6),
            "voice_detected": _meta_text("voice_detected") in {"1", "true", "True", "yes", "si", "sí"},
            "cut_reason": _meta_text("cut_reason", "unknown"),
        }
        transcript = self.stt.transcribe_file(path, mime=mime, language=self.voice.language)
        stt_elapsed_ms = int((time.perf_counter() - started) * 1000)
        if not transcript.ok:
            if transcript.detail == "hallucinated_transcript":
                out = {
                    "ok": True,
                    "ignored": True,
                    "transcript": transcript.text,
                    "answer": "",
                    "audio": "",
                    "cached": False,
                    "provider": "text_ack",
                    "detail": transcript.detail,
                    "model": "reader_stt",
                    "stt_provider": transcript.provider,
                    "stt_ms": transcript.duration_ms,
                    "chat_ms": 0,
                    "tts_ms": 0,
                    "trace": {
                        **audio_trace,
                        "stt_ms": transcript.duration_ms,
                        "stt_wall_ms": stt_elapsed_ms,
                        "stt_detail": transcript.detail,
                        "stt_timings": transcript.timings or {},
                        "tts_ms": 0,
                        "server_total_ms": int((time.perf_counter() - started) * 1000),
                    },
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "voice_ok": True,
                }
                self._append_dialogue_trace(
                    {
                        "ts": time.time(),
                        "event": "dialogue_turn_audio",
                        "ok": True,
                        "ignored": True,
                        "detail": transcript.detail,
                        **audio_trace,
                        "stt_provider": transcript.provider,
                        "stt_ms": transcript.duration_ms,
                        "duration_ms": out["duration_ms"],
                    }
                )
                return out
            if transcript.detail in {"empty_transcript", "empty_audio"}:
                spoken_answer = (
                    "No alcancé a escuchar una frase completa. Repetímela un poco más cerca o un poco más lento."
                )
                if self.fast_dialogue_ack:
                    artifact = AudioArtifact(True, provider="text_ack", detail="fast_dialogue_ack")
                    tts_ms = 0
                else:
                    tts_started = time.perf_counter()
                    artifact = self._synthesize_cached(spoken_answer)
                    tts_ms = artifact.duration_ms or int((time.perf_counter() - tts_started) * 1000)
                out = {
                    "ok": True,
                    "transcript": transcript.text,
                    "answer": spoken_answer,
                    "audio": str(artifact.path or ""),
                    "cached": artifact.cached,
                    "provider": artifact.provider,
                    "detail": transcript.detail,
                    "model": "reader_stt",
                    "stt_provider": transcript.provider,
                    "stt_ms": transcript.duration_ms,
                    "chat_ms": 0,
                    "tts_ms": tts_ms,
                    "trace": {
                        **audio_trace,
                        "stt_ms": transcript.duration_ms,
                        "stt_wall_ms": stt_elapsed_ms,
                        "stt_detail": transcript.detail,
                        "stt_timings": transcript.timings or {},
                        "tts_ms": tts_ms,
                        "server_total_ms": int((time.perf_counter() - started) * 1000),
                    },
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "voice_ok": artifact.ok,
                }
                self._append_dialogue_trace(
                    {
                        "ts": time.time(),
                        "event": "dialogue_turn_audio",
                        "ok": True,
                        "detail": transcript.detail,
                        **audio_trace,
                        "stt_provider": transcript.provider,
                        "stt_ms": transcript.duration_ms,
                        "tts_ms": tts_ms,
                        "duration_ms": out["duration_ms"],
                        "tts_ok": bool(artifact.ok),
                    }
                )
                return out
            out = self._finalize_dialogue_failure(
                started=started,
                transcript=str(transcript.text or ""),
                answer="",
                detail=str(transcript.detail or "transcription_failed"),
                model="reader_stt",
                provider="reader_stt",
                stage="stt",
                stt_provider=str(transcript.provider or ""),
                stt_ms=transcript.duration_ms,
                trace_extra={
                    **audio_trace,
                    "stt_ms": transcript.duration_ms,
                    "stt_wall_ms": stt_elapsed_ms,
                    "stt_detail": transcript.detail,
                    "stt_timings": transcript.timings or {},
                    "server_total_ms": int((time.perf_counter() - started) * 1000),
                },
            )
            out["error"] = "transcription_failed"
            self._append_dialogue_trace(
                {
                    "ts": time.time(),
                    "event": "dialogue_turn_audio",
                    "ok": False,
                    "detail": transcript.detail,
                    "error": "transcription_failed",
                    **audio_trace,
                    "human_error": str(out.get("human_error") or ""),
                    "transcript": str(transcript.text or ""),
                    "stt_provider": transcript.provider,
                    "stt_ms": transcript.duration_ms,
                    "tts_provider": str(out.get("provider") or ""),
                    "tts_ok": bool(out.get("voice_ok")),
                    "audio_available": bool(out.get("audio_available")),
                    "duration_ms": out["duration_ms"],
                }
            )
            return out
        after_stt = time.perf_counter()
        out = self.dialogue_turn_text(transcript.text, model=model, chunk_index=chunk_index)
        text_turn_ms = int((time.perf_counter() - after_stt) * 1000)
        out["stt_provider"] = transcript.provider
        out["stt_ms"] = transcript.duration_ms
        trace_value = out.get("trace")
        existing_trace = trace_value if isinstance(trace_value, dict) else {}
        out["trace"] = {
            **existing_trace,
            **audio_trace,
            "stt_ms": transcript.duration_ms,
            "stt_wall_ms": stt_elapsed_ms,
            "stt_timings": transcript.timings or {},
            "text_turn_ms": text_turn_ms,
            "server_total_ms": int((time.perf_counter() - started) * 1000),
        }
        out["duration_ms"] = int((time.perf_counter() - started) * 1000)
        self._append_dialogue_trace(
            {
                "ts": time.time(),
                "event": "dialogue_turn_audio",
                "ok": bool(out.get("ok")),
                "detail": str(out.get("detail") or ""),
                **audio_trace,
                "transcript": str(out.get("transcript") or ""),
                "human_error": str(out.get("human_error") or ""),
                "stt_provider": transcript.provider,
                "stt_ms": transcript.duration_ms,
                "reasoning_mode_requested": str(out.get("reasoning_mode_requested") or ""),
                "reasoning_mode_applied": str(out.get("reasoning_mode_applied") or out.get("reasoning_mode") or ""),
                "reasoning_degraded": bool(out.get("reasoning_degraded")),
                "chat_provider": str(out.get("model") or ""),
                "tts_provider": str(out.get("provider") or ""),
                "tts_ok": bool(out.get("voice_ok", False)),
                "audio_available": bool(out.get("audio_available", False)),
                "duration_ms": out["duration_ms"],
            }
        )
        return out

    def _prioritize_dialogue(self) -> None:
        self._lifecycle_service.prioritize_dialogue()

    def _capture_prefetch_for_shutdown(self) -> tuple[list[ThreadPoolExecutor], list[Future[AudioArtifact]]]:
        with self._prefetch_lock:
            executors = list(dict.fromkeys(self._prefetch_executors + [self._executor]))
            futures = list(self._prefetch_futures.values())
            self._prefetch_futures = {}
            self._prefetch_started = {}
            self._prefetch_future = None
            self._prefetch_index = None
            self._prefetch_started_ts = None
        with self._tts_gate:
            self._prefetch_promoted_keys.clear()
            self._tts_gate.notify_all()
        return executors, futures

    def _clear_prefetch_queue_impl(self) -> None:
        with self._background_work_condition:
            background_open = self._background_work_is_open_locked()
            with self._prefetch_lock:
                tracked_futures = list(self._prefetch_futures.values())
                old_executor = self._executor
                if background_open:
                    self._executor = ThreadPoolExecutor(
                        max_workers=self.prefetch_workers,
                        thread_name_prefix="fusion-reader-v2-tts",
                    )
                    self._prefetch_executors.append(self._executor)
                self._prefetch_futures = {}
                self._prefetch_started = {}
                self._prefetch_future = None
                self._prefetch_index = None
                self._prefetch_started_ts = None
        for future in tracked_futures:
            future.cancel()
        with self._tts_gate:
            self._prefetch_promoted_keys.clear()
            self._tts_gate.notify_all()
        old_executor.shutdown(wait=False, cancel_futures=True)

    def _reset_prefetch_queue_impl(self, stale_future: Future[AudioArtifact]) -> None:
        old_executor: ThreadPoolExecutor | None = None
        stale_keys: list[tuple] = []
        stale_futures: list[Future[AudioArtifact]] = []
        with self._background_work_condition:
            background_open = self._background_work_is_open_locked()
            with self._prefetch_lock:
                stale_keys = [key for key, future in self._prefetch_futures.items() if future is stale_future]
                stale_futures = [future for future in self._prefetch_futures.values() if future is stale_future]
                if not background_open:
                    for key in stale_keys:
                        self._prefetch_futures.pop(key, None)
                        self._prefetch_started.pop(key, None)
                    if self._prefetch_future is stale_future:
                        self._prefetch_future = None
                        self._prefetch_index = None
                        self._prefetch_started_ts = None
                else:
                    if self._prefetch_future is not stale_future and not stale_keys:
                        return
                    old_executor = self._executor
                    self._executor = ThreadPoolExecutor(
                        max_workers=self.prefetch_workers,
                        thread_name_prefix="fusion-reader-v2-tts",
                    )
                    self._prefetch_executors.append(self._executor)
                    for key in stale_keys:
                        self._prefetch_futures.pop(key, None)
                        self._prefetch_started.pop(key, None)
                    self._prefetch_future = None
                    self._prefetch_index = None
                    self._prefetch_started_ts = None
                    self._set_primary_prefetch_locked()
        for future in stale_futures:
            future.cancel()
        if stale_keys:
            with self._tts_gate:
                removed = False
                for key in stale_keys:
                    if key in self._prefetch_promoted_keys:
                        self._prefetch_promoted_keys.discard(key)
                        removed = True
                if removed:
                    self._tts_gate.notify_all()
        if old_executor is not None:
            old_executor.shutdown(wait=False, cancel_futures=True)

    def _clear_prefetch_queue(self) -> None:
        self._lifecycle_service.clear_prefetch_queue()

    def _wait_for_thread(self, thread: threading.Thread | None, *, label: str, deadline: float) -> None:
        self._lifecycle_service.wait_for_thread(thread, label=label, deadline=deadline)

    def shutdown_background_work(self, timeout: float = 10.0) -> dict:
        return self._lifecycle_service.shutdown(timeout)

    def _shorten_dialogue_answer(self, answer: str) -> str:
        text = " ".join(str(answer or "").split()).strip()
        limit = max(80, self.dialogue_tts_max_chars)
        if len(text) <= limit:
            return text
        clipped = text[:limit].rstrip()
        sentence_end = max(clipped.rfind("."), clipped.rfind("?"), clipped.rfind("!"))
        if sentence_end >= 80:
            return clipped[: sentence_end + 1].strip()
        word_end = clipped.rfind(" ")
        if word_end >= 80:
            return clipped[:word_end].rstrip().rstrip(",;:") + "."
        return clipped.rstrip(",;:") + "."

    def notes_summary(self) -> dict:
        return self._notes_service.summary()

    def list_notes(self, doc_id: str = "", chunk_index: int | None = None, current_only: bool = False) -> dict:
        return self._notes_service.list(doc_id, chunk_index, current_only)

    def create_note(self, text: str, chunk_index: int | None = None) -> dict:
        return self._notes_service.create(text, chunk_index)

    def create_laboratory_note(self, text: str) -> dict:
        return self._notes_service.create_laboratory(text)

    def _resolve_note_chunk_index(self, chunk_index: int | None = None) -> int | None:
        return self._notes_service.resolve_chunk_index(chunk_index)

    def update_note(self, note_id: str, text: str, doc_id: str = "") -> dict:
        return self._notes_service.update(note_id, text, doc_id)

    def rename_note(self, note_id: str, label: str, doc_id: str = "") -> dict:
        return self._notes_service.rename(note_id, label, doc_id)

    def delete_note(self, note_id: str, doc_id: str = "") -> dict:
        return self._notes_service.delete(note_id, doc_id)

    def _note_reference(self, note: dict) -> str:
        return self._notes_service.reference(note)

    def _note_saved_answer(self, note: dict, spoken: bool = False) -> str:
        return self._notes_service.saved_answer(note, spoken)

    def _recent_laboratory_quote(self) -> str:
        return self._notes_service.recent_laboratory_quote()

    def _resolve_laboratory_note_text(self, text: str) -> str:
        return self._notes_service.resolve_laboratory_text(text)

    def _recent_laboratory_note_target(self) -> str:
        return self._notes_service.recent_laboratory_target()

    def _is_generic_laboratory_note_text(self, text: str) -> bool:
        return self._notes_service.is_generic_laboratory_text(text)

    def _should_create_laboratory_note(self, text: str) -> bool:
        return self._notes_service.should_create_laboratory(text)

    def _should_route_generic_note_to_laboratory(self, text: str, note_text: str) -> bool:
        return self._notes_service.should_route_generic_to_laboratory(text, note_text)

    def _looks_like_recent_speech_reference(self, text: str) -> bool:
        return self._notes_service.looks_like_recent_speech_reference(text)

    def _looks_like_immediate_speech_reference(self, text: str) -> bool:
        return self._notes_service.looks_like_immediate_speech_reference(text)

    def _is_generic_note_pointer(self, text: str) -> bool:
        return self._notes_service.is_generic_pointer(text)

    def _persist_session_state(self, text: str | None = None, source_path: str = "", source_type: str = "") -> None:
        self._persistence_service.persist(text, source_path, source_type)

    def _session_preferences(self) -> SessionPreferences:
        return SessionPreferences(
            reasoning_mode=self.reasoning_mode,
            laboratory_mode=self.laboratory_mode,
            profile=self.profile,
            veil=self.veil,
            chat_provider=str(getattr(getattr(self.conversation, "provider", None), "selected", "local") or "local"),
        )

    def _apply_session_preferences(self, preferences: SessionPreferences) -> None:
        self.reasoning_mode = preferences.reasoning_mode
        self.laboratory_mode = preferences.laboratory_mode
        self.profile = preferences.profile
        self.veil = preferences.veil
        provider = getattr(self.conversation, "provider", None)
        if provider is not None and hasattr(provider, "select"):
            try:
                provider.select(preferences.chat_provider)
            except ValueError:
                provider.select("local")

    def _set_main_source(self, source_path: str, source_type: str) -> None:
        self._main_source_path = source_path
        self._main_source_type = source_type

    def _read_session_state(self) -> dict:
        return self._persistence_service.read()

    def _restore_session_state(self) -> None:
        self._persistence_service.restore()

    def _extract_note_command(self, text: str) -> str:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split())
        if not clean:
            return ""
        recent_speech_note = self._extract_recent_speech_note(clean)
        if recent_speech_note:
            return recent_speech_note
        prefix = (
            r"(?:(?:hola|por\s+favor|che|ok|okay|bueno|bien|s[ií]|y|adem[áa]s|tambi[ée]n|est[áa]\s+bien)[,.]?\s+)*"
            r"(?:(?:necesito|necesitar[ií]a|quiero|quisiera|me\s+gustar[ií]a|te\s+pido)\s+que\s+)?"
            r"(?:(?:me\s+)?(?:pod[eé]s|podr[ií]as|puedes|puede|podrias|podr[ií]a)\s+)?"
        )
        save_verbs = r"(?:guarda|guard[áa]|guardar|guarde|guardes|gu[áa]rdame|guardame|gu[áa]rdalo|guardalo|gu[áa]rdala|guardala)"
        make_note_verbs = r"(?:hac[eé]|hac[ée]me|hace|hacer|haga|hagas|haz|hazme|crea|cre[áa]|crear|agrega|agreg[áa]|agregar|sum[áa]|suma|sumar|deja|dej[áa]|dejar|dejame|d[eé]jame)"
        note_noun = r"(?:(?:una|la|esta|esa)\s+)?notas?"
        suffix_target = r"(?:eso|esto|lo\s+anterior|esta\s+frase|esta\s+idea|lo\s+que\s+dije|lo\s+que\s+te\s+dije)"
        suffix_patterns = [
            rf"^(.{{8,}}?)\s+(?:{save_verbs}|anota|anot[áa]|anotar|anotame|an[óo]tame)\s+{suffix_target}\s+(?:en|como)\s+(?:una\s+)?notas?\s*[.!?]*$",
            r"^(.{8,}?)\s+(?:gu[áa]rdalo|guardalo|gu[áa]rdala|guardala|an[óo]talo|anotalo|an[óo]tala|anotala)\s+(?:en|como)\s+(?:una\s+)?notas?\s*[.!?]*$",
            rf"^(.{{8,}}?)\s+(?:{save_verbs}|anota|anot[áa]|anotar)\s+{suffix_target}\s*[.!?]*$",
        ]
        for pattern in suffix_patterns:
            match = re.search(pattern, clean, flags=re.IGNORECASE)
            if match:
                return self._clean_note_text(match.group(1))
        patterns = [
            rf"^{prefix}(?:lo\s+que\s+)?(?:quiero|necesito|quisiera|me\s+gustar[ií]a)\s+que\s+guardes\s+(?:es\s+)?(?:lo\s+siguiente\s*)?[:.,-]?\s*(.+)$",
            rf"^{prefix}{make_note_verbs}\s+(?:me\s+)?{note_noun}\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            rf"^{prefix}{make_note_verbs}\s+(?:otra\s+|una\s+)?notas?\s*[:,-]\s*(.+)$",
            rf"^{prefix}{save_verbs}\s+(?:esto\s+|eso\s+)?como\s+notas?\s*[:,-]?\s*(.+)$",
            rf"^{prefix}{save_verbs}\s+{note_noun}\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            rf"^{prefix}{save_verbs}\s+(?:esto|eso|esto\s+de|eso\s+de|la|lo|este|esta)\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            rf"^{prefix}{save_verbs}\s+(?:de\s+|del\s+|sobre\s+|con\s+)(.+)$",
            rf"^{prefix}{save_verbs}\s+(?:tambi[ée]n\s+|adem[áa]s\s+)?(.{{6,}})$",
            rf"^{prefix}(?:pon|pon[eé]|poneme|ponm[eé])\s+(?:esto\s+|eso\s+)?(?:en|como)\s+(?:una\s+)?notas?\s*[:,-]?\s*(.+)$",
            rf"^{prefix}(?:toma|tom[áa]|tomad|tomar|tome|tomes|tomame|t[óo]mame)\s+(?:una\s+)?notas?\s*(?:de\s+|del\s+|sobre\s+|acerca\s+de\s+|con\s+|en\s+notas?\s+del\s+documento\s+(?:que\s+)?(?:vamos\s+a\s+hablar\s+de\s+)?|[:,-]\s*)?(.+)$",
            rf"^{prefix}(?:anota|anot[áa]|anotar|anotame|an[óo]tame)\s*(?:esto\s+|eso\s+)?(?:de\s+|sobre\s+|[:,-]\s*)?(.+)$",
            rf"^{prefix}(?:notas?|apunte|apuntes)\s*[:,-]\s*(.+)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, clean, flags=re.IGNORECASE)
            if match:
                return self._clean_note_text(match.group(1))
        inline_patterns = [
            r"(?:^|[,.]\s*|\bas[ií]\s+que\s+)(?:toma|tom[áa]|tomad|tomar|tome|tomes|tomame|t[óo]mame)\s+(?:una\s+)?notas?\s*(?:de\s+|del\s+|sobre\s+|acerca\s+de\s+|con\s+|[:,-]\s*)?(.+)$",
            rf"(?:^|[,.]\s*|\bas[ií]\s+que\s+){save_verbs}\s+{note_noun}\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            rf"(?:^|[,.]\s*|\bas[ií]\s+que\s+){make_note_verbs}\s+(?:me\s+)?{note_noun}\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            rf"(?:^|[,.]\s*|\bas[ií]\s+que\s+){save_verbs}\s+(?:esto|eso|esto\s+de|eso\s+de|la|lo|este|esta)\s+(?:de\s+|del\s+|sobre\s+|con\s+)?(.+)$",
            r"(?:^|[,.]\s*|\bas[ií]\s+que\s+)(?:anota|anot[áa]|anotar|anotame|an[óo]tame)\s*(?:esto\s+|eso\s+)?(?:de\s+|sobre\s+|[:,-]\s*)?(.+)$",
        ]
        for pattern in inline_patterns:
            match = re.search(pattern, clean, flags=re.IGNORECASE)
            if match:
                return self._clean_note_text(match.group(1))
        if self._looks_like_note_request(clean):
            for pattern in (
                r"(?:vamos\s+a\s+hablar|hablemos|estamos\s+hablando)\s+de\s+(.+)$",
                r"notas?\s+(?:del?\s+|sobre\s+|acerca\s+de\s+|con\s+)(.+)$",
            ):
                match = re.search(pattern, clean, flags=re.IGNORECASE)
                if match:
                    return self._clean_note_text(match.group(1))
        return ""

    def _extract_recent_speech_note(self, text: str) -> str:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split())
        if not clean:
            return ""
        for pattern in (
            r"^\s*tomando\s+a\s+(.+)$",
            r"^\s*tom[ée]\s+nota\s+de\s+(.+)$",
            r"^\s*toma\s+de\s+(.+)$",
        ):
            match = re.match(pattern, clean, flags=re.IGNORECASE)
            if not match:
                continue
            candidate = str(match.group(1) or "").strip(" .,:;-¿?¡!")
            candidate = re.split(r"\.\s+", candidate)[0].strip(" .,:;-¿?¡!")
            if self._looks_like_immediate_speech_reference(candidate):
                return self._clean_note_text(candidate)
        if self._looks_like_immediate_speech_reference(clean):
            return self._clean_note_text(clean)
        return ""

    def _looks_like_note_request(self, text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split())
        if not clean:
            return False
        has_note_word = re.search(r"\bnotas?\b|\bapuntes?\b", clean, flags=re.IGNORECASE)
        has_note_action = re.search(
            r"\b(?:tomar|toma|tom[áa]|tomad|tome|tomes|guardar|guarda|guard[áa]|guarde|guardes|guardalo|guard[áa]lo|guardala|guard[áa]la|anotar|anota|anot[áa]|pon|pon[eé]|poneme|hac[eé]|haceme|haz|hazme|crea|cre[áa]|agrega|agreg[áa]|suma|sum[áa]|deja|dej[áa]|dejar|dejame|d[eé]jame)\b",
            clean,
            flags=re.IGNORECASE,
        )
        has_save_clause = re.search(
            r"\b(?:quiero|necesito|quisiera|me\s+gustar[ií]a)\s+que\s+guardes\b", clean, flags=re.IGNORECASE
        )
        has_suffix_reference = re.search(
            r"\b(?:guarda|guard[áa]|guardar|guarde|guardes|anota|anot[áa]|anotar)\s+(?:eso|esto|lo\s+anterior|esta\s+frase|esta\s+idea)\s+(?:en|como)\s+(?:una\s+)?notas?\b",
            clean,
            flags=re.IGNORECASE,
        )
        has_followup_save = re.search(
            r"^\s*(?:y\s+|adem[áa]s\s+|tambi[ée]n\s+)*(?:guarda|guard[áa]|guardar|guarde|guardes|gu[áa]rdame|guardame)\s+(?:tambi[ée]n\s+|adem[áa]s\s+)?.{6,}$",
            clean,
            flags=re.IGNORECASE,
        )
        return bool(
            (has_note_word and (has_note_action or has_save_clause)) or has_suffix_reference or has_followup_save
        )

    def _is_stop_dialogue_command(self, text: str) -> bool:
        clean = " ".join(str(text or "").strip().replace("¿", "").replace("¡", "").split()).strip(" .,:;-!?").lower()
        if not clean:
            return False
        return bool(
            re.fullmatch(
                r"(?:det[eé]nte|detente|par[áa]|para|stop|basta|callate|c[áa]llate|silencio|no\s+hables|esper[áa]|espera)(?:\s+por\s+favor)?",
                clean,
                flags=re.IGNORECASE,
            )
        )

    def _clean_note_text(self, text: str) -> str:
        note = str(text or "").strip(" .,:;-¿?¡!")
        cleanup_patterns = [
            r"^(?:en\s+)?notas?\s+del\s+documento\s+(?:que\s+)?(?:vamos\s+a\s+hablar\s+de\s+)?",
            r"^(?:que\s+)?(?:vamos\s+a\s+hablar|hablemos|estamos\s+hablando)\s+de\s+",
            r"^(?:de\s+)?que\s+",
            r"^(?:de\s+)?(?:lo\s+que\s+)?(?:vamos\s+a\s+hablar|hablemos|estamos\s+hablando)\s+",
            r"^(?:acerca|sobre)\s+de\s+",
            r"^(?:por\s+ejemplo|ejemplo)\s*[:,.-]?\s*",
        ]
        for pattern in cleanup_patterns:
            note = re.sub(pattern, "", note, flags=re.IGNORECASE).strip(" .,:;-¿?¡!")
        note = re.sub(r"\s+(?:en|del|para)\s+el\s+bloque\s+\d+\s*$", "", note, flags=re.IGNORECASE).strip(" .,:;-¿?¡!")
        note = re.sub(r"\s+", " ", note).strip()
        return note

    def _record_voice_metric(self, event: str, payload: dict, text: str) -> None:
        try:
            self.metrics.record(
                VoiceMetric(
                    event=event,
                    ok=bool(payload.get("ok")),
                    provider=str(payload.get("provider") or ""),
                    cached=bool(payload.get("cached")),
                    voice=self.voice.voice,
                    language=self.voice.language,
                    ready_ms=int(payload.get("ready_ms") or 0),
                    synthesis_ms=int(payload.get("synthesis_ms") or 0),
                    text_chars=len(text or ""),
                    doc_id=str(payload.get("doc_id") or ""),
                    title=str(payload.get("title") or ""),
                    current=int(payload.get("current") or 0),
                    total=int(payload.get("total") or 0),
                    detail=str(payload.get("detail") or ""),
                )
            )
        except Exception:
            return

    def _play(self, path: Path | None) -> None:
        if not path:
            return
        for cmd in (
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            ["paplay", str(path)],
            ["aplay", str(path)],
        ):
            try:
                run_owned(cmd, check=False, timeout=300)
                return
            except FileNotFoundError:
                continue
            except Exception:
                continue
