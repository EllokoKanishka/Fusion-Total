from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class VoiceMetric:
    event: str
    ok: bool
    provider: str
    cached: bool
    voice: str
    language: str
    ready_ms: int
    synthesis_ms: int
    text_chars: int
    doc_id: str = ""
    title: str = ""
    current: int = 0
    total: int = 0
    detail: str = ""
    created_ts: float = 0.0

    def to_dict(self) -> dict:
        out = asdict(self)
        out["schema_version"] = 1
        if not out["created_ts"]:
            out["created_ts"] = time.time()
        return out


class VoiceMetricsStore:
    def __init__(
        self,
        path: Path | str = "runtime/fusion_reader_v2/voice_metrics.jsonl",
        *,
        max_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max(1024, int(max_bytes))
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, metric: VoiceMetric) -> None:
        raw = (json.dumps(metric.to_dict(), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if len(raw) > self.max_bytes:
            raise ValueError("metric_record_too_large")
        with self._lock:
            current_size = self.path.stat().st_size if self.path.exists() else 0
            if current_size + len(raw) > self.max_bytes:
                self._compact_locked(target_bytes=max(0, self.max_bytes // 2))
            with self.path.open("ab") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())

    def recent(self, limit: int = 20) -> list[dict]:
        wanted = max(0, int(limit))
        if wanted == 0:
            return []
        with self._lock:
            if not self.path.exists():
                return []
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = deque(handle, maxlen=wanted)
        out: list[dict] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def _compact_locked(self, *, target_bytes: int) -> None:
        if not self.path.exists():
            return
        kept: deque[bytes] = deque()
        kept_bytes = 0
        with self.path.open("rb") as source:
            for line in source:
                kept.append(line)
                kept_bytes += len(line)
                while kept and kept_bytes > target_bytes:
                    kept_bytes -= len(kept.popleft())
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                for line in kept:
                    handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def summary(self, limit: int = 500) -> list[dict]:
        rows = self.recent(limit=limit)
        groups: dict[tuple[str, str, str, bool], dict] = {}
        for row in rows:
            key = (
                str(row.get("event") or ""),
                str(row.get("provider") or ""),
                str(row.get("voice") or ""),
                bool(row.get("cached")),
            )
            item = groups.setdefault(
                key,
                {
                    "event": key[0],
                    "provider": key[1],
                    "voice": key[2],
                    "cached": key[3],
                    "count": 0,
                    "ok_count": 0,
                    "ready_ms_total": 0,
                    "synthesis_ms_total": 0,
                    "ready_ms_max": 0,
                    "synthesis_ms_max": 0,
                },
            )
            ready_ms = int(row.get("ready_ms") or 0)
            synthesis_ms = int(row.get("synthesis_ms") or 0)
            item["count"] += 1
            item["ok_count"] += 1 if row.get("ok") else 0
            item["ready_ms_total"] += ready_ms
            item["synthesis_ms_total"] += synthesis_ms
            item["ready_ms_max"] = max(item["ready_ms_max"], ready_ms)
            item["synthesis_ms_max"] = max(item["synthesis_ms_max"], synthesis_ms)

        out = []
        for item in groups.values():
            count = max(1, int(item["count"]))
            out.append({
                "event": item["event"],
                "provider": item["provider"],
                "voice": item["voice"],
                "cached": item["cached"],
                "count": item["count"],
                "ok_count": item["ok_count"],
                "ready_ms_avg": int(item["ready_ms_total"] / count),
                "synthesis_ms_avg": int(item["synthesis_ms_total"] / count),
                "ready_ms_max": item["ready_ms_max"],
                "synthesis_ms_max": item["synthesis_ms_max"],
            })
        return sorted(out, key=lambda row: (str(row["event"]), str(row["provider"]), str(row["voice"]), bool(row["cached"])))

    def document_summary(self, limit: int = 1000) -> list[dict]:
        rows = [row for row in self.recent(limit=limit) if str(row.get("event") or "") == "read"]
        groups: dict[str, dict] = {}
        for row in rows:
            doc_id = str(row.get("doc_id") or "")
            if not doc_id:
                continue
            item = groups.setdefault(
                doc_id,
                {
                    "doc_id": doc_id,
                    "title": str(row.get("title") or ""),
                    "count": 0,
                    "ok_count": 0,
                    "cache_count": 0,
                    "ready_ms_total": 0,
                    "synthesis_ms_total": 0,
                    "ready_ms_max": 0,
                    "synthesis_ms_max": 0,
                    "text_chars_total": 0,
                    "total_chunks": int(row.get("total") or 0),
                    "last_current": int(row.get("current") or 0),
                    "last_ts": float(row.get("created_ts") or 0),
                },
            )
            ready_ms = int(row.get("ready_ms") or 0)
            synthesis_ms = int(row.get("synthesis_ms") or 0)
            item["title"] = str(row.get("title") or item["title"])
            item["count"] += 1
            item["ok_count"] += 1 if row.get("ok") else 0
            item["cache_count"] += 1 if row.get("cached") else 0
            item["ready_ms_total"] += ready_ms
            item["synthesis_ms_total"] += synthesis_ms
            item["ready_ms_max"] = max(item["ready_ms_max"], ready_ms)
            item["synthesis_ms_max"] = max(item["synthesis_ms_max"], synthesis_ms)
            item["text_chars_total"] += int(row.get("text_chars") or 0)
            item["total_chunks"] = max(item["total_chunks"], int(row.get("total") or 0))
            item["last_current"] = int(row.get("current") or item["last_current"])
            item["last_ts"] = max(item["last_ts"], float(row.get("created_ts") or 0))

        out = []
        for item in groups.values():
            count = max(1, int(item["count"]))
            out.append({
                "doc_id": item["doc_id"],
                "title": item["title"],
                "count": item["count"],
                "ok_count": item["ok_count"],
                "cache_count": item["cache_count"],
                "cache_ratio": round(item["cache_count"] / count, 3),
                "ready_ms_avg": int(item["ready_ms_total"] / count),
                "synthesis_ms_avg": int(item["synthesis_ms_total"] / count),
                "ready_ms_max": item["ready_ms_max"],
                "synthesis_ms_max": item["synthesis_ms_max"],
                "text_chars_avg": int(item["text_chars_total"] / count),
                "total_chunks": item["total_chunks"],
                "last_current": item["last_current"],
                "last_ts": item["last_ts"],
            })
        return sorted(out, key=lambda row: float(row["last_ts"]), reverse=True)

    def chunk_summary(self, doc_id: str = "", limit: int = 1000, top: int = 20) -> list[dict]:
        rows = [row for row in self.recent(limit=limit) if str(row.get("event") or "") == "read"]
        wanted_doc = str(doc_id or "")
        if wanted_doc:
            rows = [row for row in rows if str(row.get("doc_id") or "") == wanted_doc]
        groups: dict[tuple[str, int], dict] = {}
        for row in rows:
            row_doc_id = str(row.get("doc_id") or "")
            current = int(row.get("current") or 0)
            if not row_doc_id or current <= 0:
                continue
            key = (row_doc_id, current)
            item = groups.setdefault(
                key,
                {
                    "doc_id": row_doc_id,
                    "title": str(row.get("title") or ""),
                    "current": current,
                    "total": int(row.get("total") or 0),
                    "count": 0,
                    "ok_count": 0,
                    "cache_count": 0,
                    "ready_ms_total": 0,
                    "synthesis_ms_total": 0,
                    "ready_ms_max": 0,
                    "synthesis_ms_max": 0,
                    "text_chars": int(row.get("text_chars") or 0),
                    "last_ts": float(row.get("created_ts") or 0),
                },
            )
            ready_ms = int(row.get("ready_ms") or 0)
            synthesis_ms = int(row.get("synthesis_ms") or 0)
            item["count"] += 1
            item["ok_count"] += 1 if row.get("ok") else 0
            item["cache_count"] += 1 if row.get("cached") else 0
            item["ready_ms_total"] += ready_ms
            item["synthesis_ms_total"] += synthesis_ms
            item["ready_ms_max"] = max(item["ready_ms_max"], ready_ms)
            item["synthesis_ms_max"] = max(item["synthesis_ms_max"], synthesis_ms)
            item["text_chars"] = max(item["text_chars"], int(row.get("text_chars") or 0))
            item["last_ts"] = max(item["last_ts"], float(row.get("created_ts") or 0))

        out = []
        for item in groups.values():
            count = max(1, int(item["count"]))
            out.append({
                "doc_id": item["doc_id"],
                "title": item["title"],
                "current": item["current"],
                "total": item["total"],
                "count": item["count"],
                "ok_count": item["ok_count"],
                "cache_count": item["cache_count"],
                "ready_ms_avg": int(item["ready_ms_total"] / count),
                "synthesis_ms_avg": int(item["synthesis_ms_total"] / count),
                "ready_ms_max": item["ready_ms_max"],
                "synthesis_ms_max": item["synthesis_ms_max"],
                "text_chars": item["text_chars"],
                "last_ts": item["last_ts"],
            })
        return sorted(out, key=lambda row: (int(row["ready_ms_max"]), int(row["synthesis_ms_max"])), reverse=True)[: max(0, int(top))]
