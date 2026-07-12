from __future__ import annotations

import json
import os
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .openclaw_bridge import ExternalResearchBridge, ExternalResearchResult, OpenClawResearchBridge


class SearxngResearchBridge(ExternalResearchBridge):
    name = "searxng"

    def __init__(
        self,
        base_url: str = "",
        timeout_seconds: float | None = None,
        max_results: int = 5,
        enabled: bool | None = None,
    ) -> None:
        self.base_url = str(base_url or os.environ.get("FUSION_READER_SEARXNG_URL") or "http://127.0.0.1:8080").strip()
        self.timeout_seconds = float(
            timeout_seconds if timeout_seconds is not None else os.environ.get("FUSION_READER_SEARXNG_TIMEOUT", "12")
        )
        self.max_results = max(1, int(max_results))
        if enabled is None:
            raw_enabled = os.environ.get("FUSION_READER_SEARXNG_ENABLED", "1").strip().lower()
            self.enabled = raw_enabled not in {"0", "false", "no", "off"}
        else:
            self.enabled = bool(enabled)

    def available(self) -> bool:
        if not self.enabled or not self.base_url:
            return False
        request = Request(self.base_url, headers={"User-Agent": "FusionReaderV2/1.0"})
        try:
            with urlopen(request, timeout=min(self.timeout_seconds, 2.0)) as response:
                return int(getattr(response, "status", 200) or 200) < 500
        except (HTTPError, URLError, OSError, TimeoutError, socket.timeout):
            return False

    def research(self, request: str, snapshot: dict | None = None) -> ExternalResearchResult:
        del snapshot
        query = str(request or "").strip()
        started = time.perf_counter()
        if not query:
            return ExternalResearchResult(False, detail="empty_query", provider=self.name)
        if not self.enabled:
            return ExternalResearchResult(
                False,
                answer="La investigacion web local esta desactivada en Fusion.",
                spoken_answer="La investigacion web local esta desactivada en Fusion.",
                detail="searxng_disabled",
                provider=self.name,
                model="searxng-local",
                query=query,
            )
        params = urlencode(
            {
                "q": query,
                "format": "json",
                "language": "es-ES",
            }
        )
        endpoint = self._search_endpoint()
        search_url = f"{endpoint}?{params}"
        request_obj = Request(search_url, headers={"User-Agent": "FusionReaderV2/1.0"})
        try:
            with urlopen(request_obj, timeout=self.timeout_seconds) as response:
                raw_text = response.read().decode("utf-8", errors="replace")
        except HTTPError:
            return self._failure(
                query,
                started,
                detail="searxng_http_error",
                answer="Salí a buscar afuera con SearXNG local, pero respondió con un error HTTP y no pude recuperar fuentes confiables.",
                spoken="Salí a buscar afuera, pero SearXNG local devolvió un error y no pude recuperar fuentes confiables.",
            )
        except (TimeoutError, socket.timeout):
            return self._failure(
                query,
                started,
                detail="searxng_timeout",
                answer="Salí a buscar afuera con SearXNG local, pero tardó demasiado y corté la consulta para no colgar el laboratorio.",
                spoken="Salí a buscar afuera, pero SearXNG local tardó demasiado y corté la consulta para no colgar el laboratorio.",
            )
        except (URLError, OSError) as exc:
            return self._failure(
                query,
                started,
                detail="searxng_unavailable",
                answer="Salí a buscar afuera con SearXNG local, pero ahora no responde desde Fusion.",
                spoken="Salí a buscar afuera, pero SearXNG local no responde desde Fusion.",
                raw_text=str(exc),
            )
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            return self._failure(
                query,
                started,
                detail="searxng_invalid_json",
                answer="Salí a buscar afuera con SearXNG local, pero devolvió una respuesta que Fusion no pudo interpretar con seguridad.",
                spoken="Salí a buscar afuera, pero SearXNG local devolvió algo que no pude interpretar con seguridad.",
                raw_text=raw_text,
            )
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            results = []
        sources = self._sanitize_sources(results[: self.max_results])
        if not sources:
            summary = f"No encontré resultados útiles en SearXNG local para: {query}."
            return ExternalResearchResult(
                False,
                answer=f"Sali a buscar afuera sobre: {query}.\n{summary}",
                spoken_answer=summary,
                detail="searxng_no_results",
                provider=self.name,
                model="searxng-local",
                duration_ms=int((time.perf_counter() - started) * 1000),
                query=query,
                summary=summary,
                findings=[],
                sources=[],
                raw_text=raw_text,
            )
        findings = self._sanitize_findings(sources)
        summary = self._build_summary(sources)
        answer = self._format_answer(query, summary, findings, sources)
        spoken_answer = self._format_spoken_answer(summary, sources)
        return ExternalResearchResult(
            True,
            answer=answer,
            spoken_answer=spoken_answer,
            detail="external_research_ok",
            provider=self.name,
            model="searxng-local",
            duration_ms=int((time.perf_counter() - started) * 1000),
            query=query,
            summary=summary,
            findings=findings,
            sources=sources,
            raw_text=raw_text,
        )

    def _failure(
        self, query: str, started: float, *, detail: str, answer: str, spoken: str, raw_text: str = ""
    ) -> ExternalResearchResult:
        return ExternalResearchResult(
            False,
            answer=answer,
            spoken_answer=spoken,
            detail=detail,
            provider=self.name,
            model="searxng-local",
            duration_ms=int((time.perf_counter() - started) * 1000),
            query=query,
            summary=spoken,
            raw_text=raw_text,
        )

    def _search_endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/search"):
            return base
        return f"{base}/search"

    def _sanitize_sources(self, results: list[object]) -> list[dict]:
        out: list[dict] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = self._clean_text(item.get("title") or "")
            url = self._clean_text(item.get("url") or item.get("pretty_url") or "")
            note = self._clean_text(item.get("content") or item.get("snippet") or "")
            if title or url or note:
                out.append({"title": title, "url": url, "note": self._clip(note, 220)})
        return out

    def _sanitize_findings(self, sources: list[dict]) -> list[str]:
        findings: list[str] = []
        for item in sources[:3]:
            title = self._clean_text(item.get("title") or "Fuente")
            note = self._clean_text(item.get("note") or "")
            if note:
                findings.append(f"{title}: {note}")
            else:
                findings.append(title)
        return findings

    def _build_summary(self, sources: list[dict]) -> str:
        count = len(sources)
        first = sources[0]
        intro = f"Encontré {count} fuente{'s' if count != 1 else ''} en SearXNG local."
        note = self._clean_text(first.get("note") or "")
        if note:
            return f"{intro} A primera vista, la primera apunta a: {self._clip(note, 180)}"
        title = self._clean_text(first.get("title") or "una fuente relevante")
        return f"{intro} La primera es: {title}."

    def _format_answer(self, query: str, summary: str, findings: list[str], sources: list[dict]) -> str:
        lines = [f"Sali a buscar afuera sobre: {query}.", summary]
        if findings:
            lines.append("Hallazgos rápidos:")
            for finding in findings[:3]:
                lines.append(f"- {finding}")
        if sources:
            lines.append("Fuentes:")
            for source in sources[:5]:
                title = self._clean_text(source.get("title") or "Fuente")
                note = self._clean_text(source.get("note") or "")
                url = self._clean_text(source.get("url") or "")
                chunk = title
                if note:
                    chunk += f" | {note}"
                if url:
                    chunk += f" | {url}"
                lines.append(f"- {chunk}")
        return "\n".join(line for line in lines if line).strip()

    def _format_spoken_answer(self, summary: str, sources: list[dict]) -> str:
        pieces = [summary]
        if sources:
            title = self._clean_text(sources[0].get("title") or "")
            note = self._clean_text(sources[0].get("note") or "")
            if title:
                pieces.append(f"La primera fuente es {title}.")
            if note:
                pieces.append(self._clip(note, 180))
        text = " ".join(piece.strip() for piece in pieces if piece.strip())
        text = re.sub(r"https?://\S+", "", text).strip()
        return text or "Encontré fuentes en SearXNG local."

    def _clean_text(self, value: object) -> str:
        text = re.sub(r"<[^>]+>", " ", str(value or ""))
        return " ".join(text.split()).strip()

    def _clip(self, text: str, max_chars: int) -> str:
        clean = self._clean_text(text)
        if len(clean) <= max_chars:
            return clean
        return clean[:max_chars].rstrip() + "..."


class AutoExternalResearchBridge(ExternalResearchBridge):
    name = "auto_external_research"

    def __init__(
        self,
        searxng: ExternalResearchBridge | None = None,
        openclaw: OpenClawResearchBridge | None = None,
    ) -> None:
        self.searxng = searxng or SearxngResearchBridge()
        self.openclaw = openclaw or OpenClawResearchBridge()

    def available(self) -> bool:
        return bool(self._bridge_available(self.searxng) or self._bridge_available(self.openclaw))

    def research(self, request: str, snapshot: dict | None = None) -> ExternalResearchResult:
        local_result = self.searxng.research(request, snapshot=snapshot)
        if local_result.ok or local_result.detail not in {
            "searxng_unavailable",
            "searxng_timeout",
            "searxng_http_error",
            "searxng_invalid_json",
        }:
            return local_result
        if self._bridge_available(self.openclaw):
            return self.openclaw.research(request, snapshot=snapshot)
        return local_result

    def _bridge_available(self, bridge: ExternalResearchBridge) -> bool:
        available = getattr(bridge, "available", None)
        if callable(available):
            try:
                return bool(available())
            except Exception:
                return False
        return True


def default_external_research_bridge() -> ExternalResearchBridge:
    provider = str(os.environ.get("FUSION_READER_EXTERNAL_RESEARCH_PROVIDER") or "auto").strip().lower()
    if provider == "searxng":
        return SearxngResearchBridge()
    if provider == "openclaw":
        return OpenClawResearchBridge()
    return AutoExternalResearchBridge()
