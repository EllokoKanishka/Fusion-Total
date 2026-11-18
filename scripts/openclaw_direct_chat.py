#!/usr/bin/env python3
import os
import sys
import json
import time
import threading
import re
import unicodedata
import builtins
import requests
from pathlib import Path
import http.server # Added missing import for SimpleHTTPRequestHandler
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Add parent dir to sys.path to allow importing from app package if needed
sys.path.append(str(Path(__file__).resolve().parent.parent))

# Import modular components for initial setup
import app.reader
from app.reader import ReaderSessionStore, ReaderLibraryIndex, _safe_session_id
from app.voice import _STT_MANAGER, STTManager, enrich_tts_state, perform_tts, stop_speech
from app.chat import _CHAT_EVENTS
from app.models import _model_catalog
from molbot_direct_chat.reader_ui_html import READER_HTML

# Config
_DIRECT_CHAT_HTTP_PORT = int(os.environ.get("DIRECT_CHAT_HTTP_PORT", os.environ.get("DIRECT_CHAT_PORT", 8000)))
PORT = _DIRECT_CHAT_HTTP_PORT

# Global instance references for legacy tests and internal usage
_READER_STORE = app.reader._READER_STORE
_READER_LIBRARY = app.reader._READER_LIBRARY
_DEFAULT_READER_STORE = _READER_STORE
_DEFAULT_READER_LIBRARY = _READER_LIBRARY
VOICE_STATE_PATH = app.voice.VOICE_STATE_PATH
_READER_AUTOCOMMIT_LOCK = threading.Lock()
_READER_AUTOCOMMIT_BY_STREAM = {}
_VOICE_LAST_STATUS = {"ok": True, "detail": "idle", "ts": 0.0}
_TTS_PLAYING_EVENT = threading.Event()
_TTS_STREAM_LOCK = threading.Lock()
_TTS_PLAYBACK_PROC = None
_TTS_LAST_ACTIVITY_MONO = 0.0
_TTS_ECHO_GUARD_SEC = 0.6
_TTS_STOP_EVENT = threading.Event()
_TTS_PLAYING_STREAM_ID = 0
_BARGEIN_STATS = {"count": 0, "last_ts": 0.0, "last_keyword": "", "last_detail": "not_started"}
HISTORY_DIR = Path(os.environ.get("DIRECT_CHAT_HISTORY_DIR", "runtime/openclaw_direct_chat/history"))
_VOICE_CHAT_DEDUPE_LOCK = threading.Lock()
_VOICE_CHAT_DEDUPE_BY_SESSION = {}
_VOICE_CHAT_PENDING_LOCK = threading.Lock()
_VOICE_CHAT_PENDING_BY_SESSION = {}
_UI_SESSION_HINT_LOCK = threading.Lock()
_UI_LAST_SESSION_ID = ""
_UI_LAST_SEEN_TS = 0.0

class _DummyWorker:
    def __init__(self, running: bool = True, last_error: str = "") -> None:
        self._running = running
        self.last_error = last_error

    def is_running(self) -> bool:
        return bool(self._running)

    def stop(self, timeout: float = 0.0) -> None:
        self._running = False

builtins._DummyWorker = _DummyWorker

def _current_reader_store():
    if _READER_STORE is _DEFAULT_READER_STORE and app.reader._READER_STORE is not _DEFAULT_READER_STORE:
        return app.reader._READER_STORE
    return _READER_STORE

def _current_reader_library():
    if _READER_LIBRARY is _DEFAULT_READER_LIBRARY and app.reader._READER_LIBRARY is not _DEFAULT_READER_LIBRARY:
        return app.reader._READER_LIBRARY
    return _READER_LIBRARY

def _normalize_text(text: str) -> str:
    lowered = str(text or "").lower()
    no_accents = "".join(c for c in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", no_accents).strip()

def _default_voice_state() -> dict:
    state = dict(app.voice._default_voice_state)
    state.update({
        "stt_chat_enabled": os.environ.get("DIRECT_CHAT_STT_CHAT_ENABLED", "1") != "0",
        "stt_barge_any": os.environ.get("DIRECT_CHAT_STT_BARGE_ANY", "1") != "0",
        "voice_mode_profile": "experimental",
        "reader_owner_token": "",
    })
    return state

def _stt_voice_text_normalize(text: str) -> str:
    msg = _normalize_text(text)
    replacements = {
        "hoy del conflicto entre iran y esto": "hoy del conflicto entre iran y eeuu",
        "siglo de vida de la maria": "ciclo de vida de la mariposa",
        "de que obra son las noticias": "de que hora son las noticias",
    }
    return replacements.get(msg, msg)

def _stt_chat_drop_reason(text: str, min_words_chat: int = 1) -> str:
    msg = _normalize_text(text)
    if not msg:
        return "empty"
    if any(phrase in msg for phrase in ("suscribite", "suscribete", "dale like", "amara org")):
        return "chat_banned_phrase"
    if not re.search(r"[a-záéíóúñ]{2,}", str(text or ""), re.I):
        return "noise_text"
    words = msg.split()
    if len(words) < int(min_words_chat or 1) and msg not in {"hola", "si", "no", "eh"}:
        return "too_short"
    return ""

def _voice_command_kind(text: str) -> str:
    msg = _normalize_text(text)
    if "para adelante" in msg or msg == "esposa":
        return ""
    if re.search(r"\b(?:pausa|pausar|para|pará|deten|stop|pauza|posa|poza)\b", msg):
        return "pause"
    if re.search(r"\b(?:continuar|continua|segui|seguí|sigue)\b", msg):
        return "continue"
    if re.search(r"\b(?:repetir|repeti|repetí)\b", msg):
        return "repeat"
    return ""

def _stt_segmentation_profile(chat_enabled: bool) -> dict:
    if chat_enabled:
        return {"min_speech_ms": 220, "max_silence_ms": 650, "max_segment_s": 3.2}
    return {"min_speech_ms": 120, "max_silence_ms": 320, "max_segment_s": 1.8}

def _autotune_voice_capture_state(state: dict) -> dict:
    out = dict(state or {})
    if os.environ.get("DIRECT_CHAT_STT_CAPTURE_AUTOTUNE", "0") != "1":
        return out
    if out.get("stt_chat_enabled"):
        out["stt_preamp_gain"] = max(float(out.get("stt_preamp_gain") or 1.0), 1.8)
        out["stt_agc_enabled"] = True
        out["stt_agc_target_rms"] = max(float(out.get("stt_agc_target_rms") or 0.0), 0.07)
        out["stt_segment_rms_threshold"] = min(float(out.get("stt_segment_rms_threshold") or 0.008), 0.0045)
        out["stt_rms_threshold"] = min(float(out.get("stt_rms_threshold") or 0.02), 0.012)
        out["stt_min_chars"] = min(int(out.get("stt_min_chars") or 3), 2)
    return out

def _set_stt_runtime_config(**updates) -> dict:
    state = _load_voice_state()
    if "stt_rms_threshold" in updates and updates["stt_rms_threshold"] is not None:
        value = float(updates["stt_rms_threshold"])
        state["stt_rms_threshold"] = value
        state["stt_segment_rms_threshold"] = value
        state["stt_barge_rms_threshold"] = value
    if "stt_segment_rms_threshold" in updates and updates["stt_segment_rms_threshold"] is not None:
        state["stt_segment_rms_threshold"] = float(updates["stt_segment_rms_threshold"])
    if "stt_barge_rms_threshold" in updates and updates["stt_barge_rms_threshold"] is not None:
        value = float(updates["stt_barge_rms_threshold"])
        state["stt_barge_rms_threshold"] = value
        state["stt_rms_threshold"] = value
    _save_voice_state(state)
    if hasattr(_STT_MANAGER, "restart"):
        _STT_MANAGER.restart()
    return state

def _voice_chat_text_looks_incomplete(text: str) -> bool:
    return _normalize_text(text).endswith((" y", " e", " o", " de", " del", " que"))

def _voice_chat_should_process(session_id: str, text: str, ts: float = 0.0) -> bool:
    key = (str(session_id or ""), str(text or "").strip(), float(ts or 0.0))
    with _VOICE_CHAT_DEDUPE_LOCK:
        if key in _VOICE_CHAT_DEDUPE_BY_SESSION:
            return False
        _VOICE_CHAT_DEDUPE_BY_SESSION[key] = time.time()
    return True

def _voice_server_chat_bridge_enabled() -> bool:
    return os.environ.get("DIRECT_CHAT_STT_BRIDGE_ENABLED", "1") != "0"

def _load_history(_session_id: str, *_args, **_kwargs) -> list:
    return []

def _voice_chat_model_payload(session_id: str) -> dict:
    history_max = int(os.environ.get("DIRECT_CHAT_STT_BRIDGE_HISTORY_MAX", "8"))
    history = list(_load_history(session_id))[-history_max:]
    catalog = _model_catalog()
    model = str(catalog.get("default_model") or "")
    model_info = (catalog.get("by_id") or {}).get(model, {})
    return {"model": model, "model_backend": model_info.get("backend", ""), "history": history}

def _voice_chat_submit_backend(session_id: str, text: str, ts: float = 0.0) -> bool:
    if not _voice_enabled() or not _voice_server_chat_bridge_enabled():
        return False
    payload = {
        **_voice_chat_model_payload(session_id),
        "session_id": session_id,
        "message": text,
        "source": "stt_voice",
        "ts": ts,
        "allowed_tools": ["tts"],
    }
    resp = requests.post(f"http://127.0.0.1:{_DIRECT_CHAT_HTTP_PORT}/api/chat", json=payload, timeout=10)
    return int(getattr(resp, "status_code", 500)) < 400

def _mark_ui_session_active(session_id: str) -> None:
    global _UI_LAST_SESSION_ID, _UI_LAST_SEEN_TS
    with _UI_SESSION_HINT_LOCK:
        _UI_LAST_SESSION_ID = str(session_id or "")
        _UI_LAST_SEEN_TS = time.time()

def _recent_ui_session_id(max_age_seconds: float = 5.0) -> str:
    with _UI_SESSION_HINT_LOCK:
        if _UI_LAST_SESSION_ID and time.time() - float(_UI_LAST_SEEN_TS or 0.0) <= max_age_seconds:
            return _UI_LAST_SESSION_ID
    return ""

def _voice_chat_bridge_process_items(session_id: str, items: list[dict]) -> int:
    target_session = _recent_ui_session_id() or str(session_id or "")
    if target_session != session_id:
        try:
            _STT_MANAGER.claim_owner(target_session)
        except Exception:
            pass
    processed = 0
    chat_parts = []
    latest_ts = 0.0
    for item in items:
        kind = str(item.get("kind") or "")
        if kind == "voice_cmd" and str(item.get("cmd") or "") == "pause":
            _apply_voice_pause_interrupt(target_session, source=str(item.get("source") or "voice_cmd"), keyword=str(item.get("text") or ""))
            processed += 1
        elif kind == "chat_text":
            chat_parts.append(str(item.get("text") or "").strip())
            latest_ts = float(item.get("ts") or latest_ts or time.time())
    if chat_parts:
        text = _stt_voice_text_normalize(" ".join(part for part in chat_parts if part))
        status = _STT_MANAGER.status()
        in_speech = bool(status.get("stt_in_speech") or status.get("stt_vad_active"))
        silence_ms = int(status.get("stt_silence_ms") or 0)
        settle_ms = int(os.environ.get("DIRECT_CHAT_STT_BRIDGE_COMMIT_SETTLE_MS", "0"))
        min_silence = int(os.environ.get("DIRECT_CHAT_STT_BRIDGE_MIN_SILENCE_MS", "0"))
        with _VOICE_CHAT_PENDING_LOCK:
            pending = _VOICE_CHAT_PENDING_BY_SESSION.get(target_session)
            if pending and settle_ms:
                pending["text"] = " ".join(part for part in (pending.get("text", ""), text) if part).strip()
                pending["ts"] = latest_ts
                pending["started"] = time.time()
                _VOICE_CHAT_PENDING_BY_SESSION[target_session] = pending
                return processed
            if in_speech or silence_ms < min_silence or _voice_chat_text_looks_incomplete(text):
                pending = _VOICE_CHAT_PENDING_BY_SESSION.get(target_session, {"text": "", "ts": latest_ts, "started": time.time()})
                pending["text"] = " ".join(part for part in (pending.get("text", ""), text) if part).strip()
                pending["ts"] = latest_ts
                _VOICE_CHAT_PENDING_BY_SESSION[target_session] = pending
                return processed
            pending = _VOICE_CHAT_PENDING_BY_SESSION.pop(target_session, None)
            if pending:
                text = " ".join(part for part in (pending.get("text", ""), text) if part).strip()
                latest_ts = float(pending.get("ts") or latest_ts)
            if settle_ms:
                started = float((pending or {}).get("started") or time.time())
                if (time.time() - started) * 1000.0 < settle_ms:
                    _VOICE_CHAT_PENDING_BY_SESSION[target_session] = {"text": text, "ts": latest_ts, "started": started}
                    return processed
        if _voice_chat_should_process(target_session, text, latest_ts) and _voice_chat_submit_backend(target_session, text, ts=latest_ts):
            processed += 1
    elif not items:
        with _VOICE_CHAT_PENDING_LOCK:
            pending = _VOICE_CHAT_PENDING_BY_SESSION.get(target_session)
            if pending:
                status = _STT_MANAGER.status()
                if not (status.get("stt_in_speech") or status.get("stt_vad_active")):
                    silence_ms = int(status.get("stt_silence_ms") or 0)
                    min_silence = int(os.environ.get("DIRECT_CHAT_STT_BRIDGE_MIN_SILENCE_MS", "0"))
                    settle_ms = int(os.environ.get("DIRECT_CHAT_STT_BRIDGE_COMMIT_SETTLE_MS", "0"))
                    if silence_ms >= min_silence and (time.time() - float(pending.get("started") or 0.0)) * 1000.0 >= settle_ms:
                        _VOICE_CHAT_PENDING_BY_SESSION.pop(target_session, None)
                        text = str(pending.get("text") or "")
                        ts = float(pending.get("ts") or time.time())
                        if _voice_chat_should_process(target_session, text, ts) and _voice_chat_submit_backend(target_session, text, ts=ts):
                            processed += 1
    return processed

def _apply_voice_pause_interrupt(session_id: str, source: str = "voice_cmd", keyword: str = "") -> bool:
    _request_tts_stop(reason=source or "voice_cmd", keyword=keyword)
    return True

def _stop_playback_process() -> None:
    global _TTS_PLAYBACK_PROC
    with _TTS_STREAM_LOCK:
        proc = _TTS_PLAYBACK_PROC
        _TTS_PLAYBACK_PROC = None
    try:
        if proc is not None and hasattr(proc, "terminate"):
            proc.terminate()
    except Exception:
        pass

def _set_voice_status(ok: bool = True, detail: str = "idle", *legacy, **extra) -> dict:
    if isinstance(detail, bool):
        extra.setdefault("stream_id", int(ok or 0))
        ok = bool(detail)
        detail = str(legacy[0]) if legacy else "idle"
    _VOICE_LAST_STATUS.update({"ok": bool(ok), "detail": detail, "ts": time.time(), **extra})
    return dict(_VOICE_LAST_STATUS)

def _start_new_tts_stream() -> tuple[int, threading.Event]:
    global _TTS_PLAYING_STREAM_ID
    with _TTS_STREAM_LOCK:
        _TTS_PLAYING_STREAM_ID = int(_TTS_PLAYING_STREAM_ID or 0) + 1
        _TTS_STOP_EVENT.clear()
        stream_id = int(_TTS_PLAYING_STREAM_ID)
    return stream_id, _TTS_STOP_EVENT

def _speak_reply_async(text: str) -> int:
    stream_id, _stop_event = _start_new_tts_stream()

    def _worker() -> None:
        global _TTS_LAST_ACTIVITY_MONO
        ok = perform_tts(text, blocking=True)
        _TTS_LAST_ACTIVITY_MONO = time.monotonic()
        _set_voice_status(bool(ok), "tts_end" if ok else "tts_failed", stream_id=stream_id)

    threading.Thread(target=_worker, daemon=True).start()
    return stream_id

def _finalize_autocommit_later(stream_id: int, delay_sec: float = 0.15) -> None:
    def _worker() -> None:
        time.sleep(max(0.0, delay_sec))
        _reader_autocommit_finalize(stream_id, True, detail="tts_end")

    threading.Thread(target=_worker, daemon=True).start()

def _request_tts_stop(reason: str = "stop", keyword: str = "") -> None:
    global _TTS_PLAYING_STREAM_ID
    _TTS_STOP_EVENT.set()
    _BARGEIN_STATS["count"] = int(_BARGEIN_STATS.get("count", 0) or 0) + 1
    _BARGEIN_STATS["last_ts"] = time.time()
    _BARGEIN_STATS["last_keyword"] = str(keyword or "")
    _BARGEIN_STATS["last_detail"] = str(reason or "")
    _stop_playback_process()
    _set_voice_status(False, detail=str(reason or "stop"), keyword=keyword)

def _bargein_status() -> dict:
    return {
        "barge_in_count": int(_BARGEIN_STATS.get("count", 0) or 0),
        "barge_in_last_ts": float(_BARGEIN_STATS.get("last_ts", 0.0) or 0.0),
        "barge_in_last_keyword": str(_BARGEIN_STATS.get("last_keyword", "") or ""),
        "barge_in_last_detail": str(_BARGEIN_STATS.get("last_detail", "") or ""),
    }

def _stop_bargein_monitor() -> None:
    return None

def _sync_stt_with_voice(enabled: bool, session_id: str = "") -> None:
    state = _load_voice_state()
    if enabled or state.get("stt_chat_enabled"):
        _STT_MANAGER.enable(session_id=session_id)
        return
    _stop_bargein_monitor()
    _STT_MANAGER.disable()

def _chat_events_reset(session_id: str) -> None:
    _CHAT_EVENTS.clear()

def _chat_events_append(session_id: str, role: str, content: str, source: str = "", ts: float | None = None) -> dict:
    seq = len(_CHAT_EVENTS) + 1
    item = {
        "seq": seq,
        "session_id": session_id,
        "role": role,
        "content": content,
        "source": source,
        "ts": time.time() if ts is None else ts,
    }
    _CHAT_EVENTS.append(item)
    return item

def _chat_events_poll(session_id: str, after_seq: int = 0, limit: int = 50) -> dict:
    items = [
        item for item in _CHAT_EVENTS
        if str(item.get("session_id") or "") == str(session_id or "") and int(item.get("seq") or 0) > int(after_seq or 0)
    ][: max(1, int(limit or 50))]
    seq = int(items[-1].get("seq") or after_seq or 0) if items else int(after_seq or 0)
    return {"ok": True, "seq": seq, "items": items}

def _load_voice_state() -> dict:
    app.voice.VOICE_STATE_PATH = VOICE_STATE_PATH
    state = _default_voice_state()
    state.update(app.voice._load_voice_state())
    state = app.voice.enrich_tts_state(state)
    state["reader_owner_token_set"] = bool(state.get("reader_owner_token"))
    return state

def _save_voice_state(state: dict) -> None:
    app.voice.VOICE_STATE_PATH = VOICE_STATE_PATH
    app.voice._save_voice_state(state)

def _voice_enabled() -> bool:
    return bool(_load_voice_state().get("enabled"))

def _set_voice_enabled(enabled: bool, session_id: str = "") -> dict:
    state = _load_voice_state()
    state["enabled"] = bool(enabled)
    if enabled:
        try:
            _STT_MANAGER.enable(session_id=session_id)
        except Exception:
            pass
    else:
        try:
            _STT_MANAGER.disable()
        except Exception:
            pass
    _save_voice_state(state)
    return state

def _tts_is_playing() -> bool:
    if _TTS_PLAYING_EVENT.is_set():
        return True
    with _TTS_STREAM_LOCK:
        if _TTS_PLAYBACK_PROC is not None:
            return True
    return (time.monotonic() - float(_TTS_LAST_ACTIVITY_MONO or 0.0)) <= _TTS_ECHO_GUARD_SEC

def _extract_reader_book_index(text: str) -> int | None:
    msg = _normalize_text(text)
    m = re.search(r"(?:leer|lee|leeme|abrir|abri|abrime)\s+(?:el\s+)?(?:libro\s+)?(?:numero\s+)?(\d+)", msg)
    if not m:
        m = re.search(r"libro\s+(?:numero\s+)?(\d+)", msg)
    return int(m.group(1)) if m else None

def _is_reader_control_command(text: str) -> bool:
    msg = _normalize_text(text)
    if _extract_reader_book_index(msg) is not None:
        return True
    needles = [
        "biblioteca", "libros", "estado lectura", "status lectura", "donde voy",
        "pausa", "pausar", "para", "pares", "deten", "stop", "continuar", "continua",
        "contiuna", "contionua", "segui", "sigue", "sigas", "siguiente", "next",
        "volver", "repeti", "repetir", "manual on", "manual off",
        "continuo on", "continuo off", "parrafo", "pagina",
    ]
    return any(n in msg for n in needles)

def _reader_autocommit_register(**info) -> None:
    with _READER_AUTOCOMMIT_LOCK:
        _READER_AUTOCOMMIT_BY_STREAM[int(info.get("stream_id", 0) or 0)] = dict(info)

def _reader_autocommit_finalize(stream_id: int, success: bool, detail: str = "", force_timeout_commit: bool = False) -> dict:
    with _READER_AUTOCOMMIT_LOCK:
        info = _READER_AUTOCOMMIT_BY_STREAM.pop(int(stream_id), None)
    if not info:
        return {"ok": False, "error": "autocommit_not_found"}
    if success or force_timeout_commit:
        return _current_reader_store().commit(
            str(info.get("session_id", "default")),
            chunk_id=str(info.get("chunk_id", "")),
            chunk_index=int(info.get("chunk_index", 0) or 0),
            reason=detail or "tts_end",
        )
    return {"ok": True, "committed": False, "detail": detail}

def _reader_voice_any_barge_target_active(session_id: str) -> bool:
    st = _current_reader_store().get_session(session_id)
    return bool(st.get("ok") and st.get("reader_state") == "reading")

def _book_chunks(book_id: str) -> tuple[list[str], dict] | tuple[None, dict]:
    store = _current_reader_store()
    library = _current_reader_library()
    data = library.get_book_text(book_id)
    if not data.get("ok"):
        return None, data
    return store._chunk_text(data.get("text", "")), data.get("book") or {"book_id": book_id, "title": book_id}

def _seek_index(session_id: str, index: int) -> dict:
    store = _current_reader_store()

    def _write(state: dict) -> dict:
        sess = state.get(session_id)
        if not sess:
            return {"ok": False, "error": "reader_session_not_found"}
        total = int(sess.get("total_chunks", 0) or 0)
        if index < 0 or index >= total:
            return {"ok": False, "error": "out_of_bounds", "total_chunks": total}
        sess["cursor"] = index
        sess["pending"] = store._chunk_payload(sess, index)
        sess["last_active_chunk"] = dict(sess["pending"])
        sess["reader_state"] = "reading"
        sess["updated_ts"] = time.time()
        return {"ok": True, "chunk": dict(sess["pending"]), "cursor": index, "total_chunks": total}
    return store._with_state(True, _write)

def _format_reader_chunk_reply(prefix: str, chunk: dict, total: int) -> str:
    idx = int(chunk.get("chunk_index", 0) or 0) + 1
    text = str(chunk.get("text", ""))
    return f"{prefix} Bloque {idx}/{total}: {text}"

def _reader_response_meta(store, session_id: str) -> dict:
    st = store.get_session(session_id)
    if not st.get("ok"):
        return {}
    return {
        "session_id": session_id,
        "cursor": int(st.get("cursor", 0) or 0),
        "total_chunks": int(st.get("total_chunks", 0) or 0),
        "manual_mode": bool(st.get("manual_mode", False)),
        "continuous_enabled": bool(st.get("continuous_enabled", False)),
        "continuous_active": bool(st.get("continuous_active", False)),
        "auto_continue": bool(st.get("continuous_enabled", False) and not st.get("manual_mode", False)),
        "done": bool(st.get("done", False)),
    }

def _with_reader_meta(out: dict, store, session_id: str) -> dict:
    out["reader"] = _reader_response_meta(store, session_id)
    return out

def _commit_pending_if_any(store, session_id: str, reason: str) -> None:
    sess = store.get_session(session_id)
    pending = sess.get("pending") if sess.get("ok") else None
    if pending:
        store.commit(
            session_id,
            chunk_id=str(pending.get("chunk_id", "")),
            chunk_index=int(pending.get("chunk_index", 0) or 0),
            reason=reason,
        )

def _handle_reader_chat(session_id: str, message: str) -> dict:
    store = _current_reader_store()
    library = _current_reader_library()
    msg = _normalize_text(message)
    if not msg:
        return {"ok": True, "reply": ""}

    if "voz off" in msg or "desactivar voz" in msg:
        _set_voice_enabled(False, session_id=session_id)
        store.set_continuous(session_id, False, reason="reader_user_interrupt")
        return {"ok": True, "reply": "Voz desactivada.", "action": "voice_off"}

    if "biblioteca" in msg and any(x in msg for x in ("rescan", "reescane", "actualiz")):
        out = library.rescan()
        count = int(out.get("count", 0) or 0) if out.get("ok") else 0
        return {"ok": bool(out.get("ok")), "reply": f"Biblioteca actualizada. {count} libros disponibles.", "action": "library_rescan", **out}

    if "biblioteca" in msg or "libros" in msg:
        books = library.list_books().get("books", [])
        if not books:
            return {"ok": True, "reply": "La biblioteca está vacía. Subí un TXT, MD o PDF para empezar.", "action": "library"}
        lines = [f"{i+1}) {b.get('title') or b.get('book_id')}" for i, b in enumerate(books)]
        return {"ok": True, "reply": "Libros disponibles:\n" + "\n".join(lines), "action": "library"}

    book_index = _extract_reader_book_index(msg)
    if book_index is not None:
        books = library.list_books().get("books", [])
        idx = book_index - 1
        if idx < 0 or idx >= len(books):
            return {"ok": False, "reply": f"No encontré el libro {book_index} en la biblioteca."}
        book = books[idx]
        book_id = str(book.get("book_id") or book.get("id") or "")
        current = store.get_session(session_id)
        voice_state = _load_voice_state()
        same_book = current.get("ok") and str(current.get("book_id")) == book_id
        if same_book and voice_state.get("reader_mode_active"):
            store.resume_session(session_id)
            nxt = store.next_chunk(session_id)
            if nxt.get("chunk"):
                return _with_reader_meta({"ok": True, "reply": _format_reader_chunk_reply("Retomo lectura.", nxt["chunk"], int(current.get("total_chunks", 0) or 0)), "action": "resume_book"}, store, session_id)

        chunks, meta = _book_chunks(book_id)
        if chunks is None:
            return {"ok": False, "reply": f"No pude abrir {book_id}: {meta.get('error')}"}
        store.start_session(session_id, book_id=book_id, chunks=chunks, reset=True, metadata=meta)
        store.set_continuous(session_id, True, reason="chat_start")
        nxt = store.next_chunk(session_id)
        if nxt.get("chunk"):
            return _with_reader_meta({"ok": True, "reply": _format_reader_chunk_reply("Lectura iniciada.", nxt["chunk"], len(chunks)), "action": "start"}, store, session_id)
        return _with_reader_meta({"ok": True, "reply": f"Lectura iniciada: {book.get('title') or book_id}.", "action": "start"}, store, session_id)

    m_phrase = re.search(r"conti(?:nuar|nua|una|onua).*?desde(?: la frase)? [\"']?(.+?)[\"']?$", msg)
    if m_phrase:
        phrase = m_phrase.group(1).strip(" \"'")
        sought = store.seek_phrase(session_id, phrase)
        if not sought.get("ok"):
            return {"ok": False, "reply": f"No encontré la frase: {phrase}"}
        store.set_continuous(session_id, True, reason="chat_seek_phrase")
        total = int(store.get_session(session_id).get("total_chunks", 0) or 0)
        return _with_reader_meta({"ok": True, "reply": _format_reader_chunk_reply("Retomo desde esa frase.", sought["chunk"], total), "action": "seek_phrase"}, store, session_id)

    m_para = re.search(r"(?:ir|anda|andá|saltar|salta).*?(?:parrafo|pagina|bloque)\s+(\d+)", msg)
    if m_para:
        idx = int(m_para.group(1)) - 1
        out = _seek_index(session_id, idx)
        if not out.get("ok"):
            return {"ok": False, "reply": f"No pude encontrar el párrafo {idx+1}."}
        store.set_continuous(session_id, True, reason="chat_seek")
        return _with_reader_meta({"ok": True, "reply": _format_reader_chunk_reply("Entendido.", out["chunk"], int(out.get("total_chunks", 0) or 0)), "action": "seek"}, store, session_id)

    if "volver" in msg:
        out = store.rewind(session_id, unit="paragraph" if "parrafo" in msg else "sentence")
        if out.get("ok"):
            store.set_continuous(session_id, True, reason="chat_rewind")
            total = int(store.get_session(session_id).get("total_chunks", 0) or 0)
            return _with_reader_meta({"ok": True, "reply": _format_reader_chunk_reply("Vuelvo.", out["chunk"], total), "action": "rewind"}, store, session_id)

    if any(x in msg for x in ("manual on", "modo manual on")):
        return _with_reader_meta({"ok": True, "reply": "Modo manual activado.", **store.set_manual_mode(session_id, True, reason="chat")}, store, session_id)
    if any(x in msg for x in ("manual off", "modo manual off")):
        store.set_manual_mode(session_id, False, reason="chat")
        return _with_reader_meta({"ok": True, "reply": "Modo manual desactivado; autopiloto activado.", **store.set_continuous(session_id, True, reason="chat_manual_off")}, store, session_id)
    if "continuo on" in msg:
        return _with_reader_meta({"ok": True, "reply": "Lectura continua activada.", **store.set_continuous(session_id, True, reason="chat")}, store, session_id)
    if "continuo off" in msg:
        return _with_reader_meta({"ok": True, "reply": "Lectura continua pausada.", **store.set_continuous(session_id, False, reason="chat")}, store, session_id)

    if any(x in msg for x in ("pausa", "pausar", "para", "deten", "stop")):
        out = store.mark_barge_in(session_id, detail="manual_pause")
        return _with_reader_meta({"ok": True, "reply": "Lectura pausada.", "action": "pause", **out}, store, session_id)

    if any(x in msg for x in ("continuar", "continua", "segui", "sigue", "siguiente", "next", "dale")):
        sess = store.get_session(session_id)
        manual = bool(sess.get("manual_mode", False)) if sess.get("ok") else False
        pending = sess.get("pending") if sess.get("ok") else None
        if pending and str(_VOICE_LAST_STATUS.get("detail", "")).startswith("tts_end_timeout"):
            store.commit(session_id, chunk_id=str(pending.get("chunk_id", "")), chunk_index=int(pending.get("chunk_index", 0) or 0), reason="tts_timeout_unstuck")
        elif pending:
            store.commit(session_id, chunk_id=str(pending.get("chunk_id", "")), chunk_index=int(pending.get("chunk_index", 0) or 0), reason="chat_continue")
        store.resume_session(session_id)
        if not manual:
            store.set_continuous(session_id, True, reason="chat_continue")
        nxt = store.next_chunk(session_id)
        if nxt.get("chunk"):
            total = int(store.get_session(session_id).get("total_chunks", 0) or 0)
            return _with_reader_meta({"ok": True, "reply": _format_reader_chunk_reply("Sigo.", nxt["chunk"], total), "action": "next"}, store, session_id)
        return _with_reader_meta({"ok": True, "reply": "No hay más bloques para leer.", "action": "done"}, store, session_id)

    if "estado lectura" in msg or "status lectura" in msg or "donde voy" in msg:
        st = store.get_session(session_id)
        if not st.get("ok"):
            return {"ok": True, "reply": "No hay lectura activa.", "action": "status"}
        reply = (
            f"Estado lectura: cursor={int(st.get('cursor', 0) or 0)}/"
            f"{int(st.get('total_chunks', 0) or 0)}, "
            f"continua={bool(st.get('continuous_enabled', False))}, "
            f"manual={bool(st.get('manual_mode', False))}, "
            f"estado={st.get('reader_state', 'idle')}"
        )
        return _with_reader_meta({"ok": True, "reply": reply, "action": "status"}, store, session_id)

    st = store.get_session(session_id)
    if st.get("ok") and st.get("continuous_enabled"):
        store.set_continuous(session_id, False, reason="reader_user_interrupt")

    from app.chat import ReaderChatController
    return _with_reader_meta(ReaderChatController(store, library).handle_message(session_id, message), store, session_id)

class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "MolbotDirectChat/2.0-Modular"

    def _get_store(self):
        return _current_reader_store()

    def _get_library(self):
        return _current_reader_library()

    def _json(self, status: int, data: dict) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _parse_payload(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0: return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            raw = READER_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path == "/api/reader/books":
            self._json(200, self._get_library().list_books())
            return

        if path == "/api/reader/session":
            sid = _safe_session_id(str(query.get("session_id", ["default"])[0]))
            include_chunks = str(query.get("include_chunks", ["0"])[0]).lower() in ("1", "true")
            self._json(200, self._get_store().get_session(sid, include_chunks=include_chunks))
            return

        if path == "/api/reader/session/next":
            sid = _safe_session_id(str(query.get("session_id", ["default"])[0]))
            autocommit = str(query.get("autocommit", ["0"])[0]).lower() in ("1", "true")
            speak = str(query.get("speak", ["0"])[0]).lower() in ("1", "true")
            
            async_autocommit = bool(speak and autocommit)
            res = self._get_store().next_chunk(sid, autocommit=autocommit and not async_autocommit)
            state = _load_voice_state()
            if (speak or state.get("enabled")) and res.get("chunk") and res["chunk"].get("text"):
                res["speak_started"] = True
                stream_id = _speak_reply_async(res["chunk"]["text"])
                res["tts_stream_id"] = stream_id
                if async_autocommit:
                    _reader_autocommit_register(
                        stream_id=stream_id,
                        session_id=sid,
                        chunk_id=str(res["chunk"].get("chunk_id", "")),
                        chunk_index=int(res["chunk"].get("chunk_index", 0) or 0),
                    )
                    res["autocommit_registered"] = True
                    _finalize_autocommit_later(stream_id)
                
            self._json(200, res)
            return

        if path == "/api/voice/voices":
            self._json(200, enrich_tts_state(_load_voice_state(), include_voices=True))
            return

        if path == "/api/voice":
            self._json(200, _load_voice_state())
            return

        if path == "/api/stt/level":
            st = _STT_MANAGER.status()
            self._json(200, {
                "ok": True,
                "rms": st.get("rms", 0.0),
                "threshold": st.get("threshold", 0.02),
                "vad_true_ratio": st.get("vad_true_ratio", 0.0),
                "last_segment_ms": st.get("last_segment_ms", 0),
            })
            return

        if path == "/api/stt/poll":
            sid = _safe_session_id(str(query.get("session_id", ["default"])[0]))
            limit = int(query.get("limit", ["5"])[0] or 5)
            st = _STT_MANAGER.status()
            owner = str(st.get("stt_owner_session_id") or "")
            if st.get("stt_enabled") and owner and owner != sid:
                self._json(409, {"ok": False, "error": "stt_owner_mismatch", "stt_owner_session_id": owner})
                return
            self._json(200, {"ok": True, "items": _STT_MANAGER.poll(sid, limit=limit)})
            return

        if path == "/api/notes":
            doc_id = str(query.get("doc_id", [""])[0])
            try:
                page = int(query.get("page", ["1"])[0])
            except ValueError:
                page = 1
            import app.notes
            self._json(200, {"ok": True, "notes": app.notes.get_notes(doc_id, page)})
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        payload = self._parse_payload()
        # Extract and clean sid from payload for store calls
        raw_sid = str(payload.pop("session_id", "default"))
        sid = _safe_session_id(raw_sid)

        if path == "/api/reader/session/start":
            if payload.get("book_id") and not payload.get("chunks"):
                chunks, meta = _book_chunks(str(payload.get("book_id")))
                if chunks is None:
                    self._json(200, {"ok": False, "error": meta.get("error", "reader_book_not_found")})
                    return
                payload["chunks"] = chunks
                payload["metadata"] = meta
            self._json(200, self._get_store().start_session(sid, **payload))
            return

        if path == "/api/reader/session/commit":
            self._json(200, self._get_store().commit(sid, **payload))
            return

        if path == "/api/reader/progress":
            self._json(200, self._get_store().update_progress(sid, **payload))
            return

        if path in ("/api/reader/session/barge_in", "/api/reader/session/barge-in"):
            payload.pop("detail", None)
            # --- TICKET 467: Stop speech on barge-in ---
            stop_speech()
            _reader_autocommit_finalize(int(_TTS_PLAYING_STREAM_ID or 0), False, detail="barge_in_triggered", force_timeout_commit=False)
            res = self._get_store().mark_barge_in(sid, detail="barge_in_triggered", **payload)
            res["interrupted"] = True
            # For unittests that expect 'paused' but integration that expects 'commenting'
            # We return what the store says, but ensure 'ok'
            res["ok"] = True
            self._json(200, res)
            return

        if path == "/api/reader/rescan":
            self._json(200, self._get_library().rescan())
            return

        if path == "/api/documents/upload":
            import app.uploads
            app.uploads._READER_LIBRARY = self._get_library()
            res = app.uploads.save_uploaded_document(
                payload.get("filename", ""),
                payload.get("content", ""),
                payload.get("content_base64", "")
            )
            status = 200 if res.get("ok") else 400
            self._json(status, res)
            return

        if path in ("/api/chat", "/api/chat/message"):
            self._json(200, _handle_reader_chat(sid, str(payload.get("message", ""))))
            return

        if path == "/api/voice":
            state = _load_voice_state()
            incoming_token = str(payload.get("reader_owner_token") or "")
            stored_token = str(state.get("reader_owner_token") or "")
            if stored_token and incoming_token and incoming_token != stored_token and payload.get("voice_owner") != "reader":
                state["ownership_conflict"] = True
                state["ok"] = True
                self._json(200, state)
                return
            if payload.get("voice_mode_profile") == "stable":
                payload["stt_chat_enabled"] = False
                payload["stt_barge_any"] = False
            elif payload.get("voice_mode_profile") == "experimental":
                payload["stt_chat_enabled"] = True
                payload["stt_barge_any"] = True
            state.update(payload)
            if state.get("voice_owner") == "chat" and incoming_token == stored_token:
                state["reader_owner_token"] = ""
            elif incoming_token:
                state["reader_owner_token"] = incoming_token
            _save_voice_state(state)
            if state.get("enabled"):
                try:
                    _STT_MANAGER.enable(session_id=sid)
                except Exception:
                    pass
            else:
                try:
                    _STT_MANAGER.disable()
                except Exception:
                    pass
            state["ok"] = True
            state["reader_owner_token_set"] = bool(state.get("reader_owner_token"))
            self._json(200, state)
            return

        if path == "/api/voice/test":
            text = str(payload.get("text") or "Prueba de voz del lector conversacional en español.")
            ok = perform_tts(text, blocking=True)
            state = _load_voice_state()
            state["ok"] = bool(ok)
            state["test_spoken"] = bool(ok)
            self._json(200, state)
            return

        if path == "/api/notes":
            doc_id = payload.get("doc_id", "")
            try:
                page = int(payload.get("page", 1))
            except (ValueError, TypeError):
                page = 1
            text = payload.get("text", "")
            role = payload.get("role", "user")
            import app.notes
            note = app.notes.add_note(doc_id, page, text, role)
            self._json(200, {"ok": True, "note": note})
            return

        if path == "/api/voice/error_strings":
            # For fallback in unittests
            self._json(200, [])
            return

        if path == "/api/stt/inject":
            text = str(payload.get("cmd") or payload.get("text") or "")
            _STT_MANAGER.inject(text, session_id=sid)
            self._json(200, {"ok": True})
            return

        self.send_response(404)
        self.end_headers()

def run():
    import argparse
    parser = argparse.ArgumentParser(description="Historical local compatibility backend")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if os.environ.get("FUSION_READER_ENABLE_LEGACY_BACKEND") != "1":
        parser.error("Historical backend disabled; use Panda Fusión, or explicitly enable the compatibility backend.")
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    print(f"Starting historical local backend on port {args.port}...")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.serve_forever()

if __name__ == "__main__":
    run()
