from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import environment_copy, environment_value


@dataclass(frozen=True)
class ExternalResearchResult:
    ok: bool
    answer: str = ""
    spoken_answer: str = ""
    detail: str = ""
    provider: str = ""
    model: str = ""
    duration_ms: int = 0
    query: str = ""
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    raw_text: str = ""


class ExternalResearchBridge:
    name = "external_research"

    def research(self, request: str, snapshot: dict | None = None) -> ExternalResearchResult:
        return ExternalResearchResult(
            False, detail="not_implemented", provider=self.name, query=str(request or "").strip()
        )


class NullExternalResearchBridge(ExternalResearchBridge):
    name = "null_external_research"

    def __init__(self, result: ExternalResearchResult | None = None) -> None:
        self.result = result or ExternalResearchResult(
            True,
            answer="Investigacion externa simulada.",
            spoken_answer="Investigacion externa simulada.",
            provider=self.name,
            model="null",
            summary="Investigacion externa simulada.",
        )
        self.calls: list[tuple[str, dict]] = []

    def research(self, request: str, snapshot: dict | None = None) -> ExternalResearchResult:
        self.calls.append((str(request or ""), dict(snapshot or {})))
        return self.result


class OpenClawResearchBridge(ExternalResearchBridge):
    name = "openclaw_bridge"

    def __init__(
        self,
        command: str = "",
        agent: str = "",
        timeout_seconds: float | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.command = (
            command
            or environment_value("FUSION_READER_OPENCLAW_BIN")
            or str(Path.home() / ".openclaw" / "bin" / "openclaw")
        )
        self.agent = agent or environment_value("FUSION_READER_OPENCLAW_AGENT") or "fusion-research"
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else environment_value("FUSION_READER_OPENCLAW_TIMEOUT", "90") or "90"
        )
        self.retry_attempts = max(1, int(environment_value("FUSION_READER_OPENCLAW_RETRIES", "2") or "2"))
        if enabled is None:
            raw_enabled = (environment_value("FUSION_READER_OPENCLAW_ENABLED", "1") or "1").strip().lower()
            self.enabled = raw_enabled not in {"0", "false", "no", "off"}
        else:
            self.enabled = bool(enabled)

    def available(self) -> bool:
        if not self.enabled:
            return False
        if os.path.isabs(self.command):
            return Path(self.command).exists()
        return shutil.which(self.command) is not None

    def research(self, request: str, snapshot: dict | None = None) -> ExternalResearchResult:
        query = str(request or "").strip()
        started = time.perf_counter()
        if not query:
            return ExternalResearchResult(False, detail="empty_query", provider=self.name)
        if not self.enabled:
            return ExternalResearchResult(
                False,
                answer="La investigacion externa esta desactivada en Fusion.",
                spoken_answer="La investigacion externa esta desactivada en Fusion.",
                detail="bridge_disabled",
                provider=self.name,
                query=query,
            )
        if not self.available():
            return ExternalResearchResult(
                False,
                answer="No encuentro OpenClaw listo para hacer investigacion externa desde Fusion.",
                spoken_answer="No encuentro OpenClaw listo para investigar afuera desde Fusion.",
                detail="bridge_unavailable",
                provider=self.name,
                query=query,
            )
        prompt = self._build_prompt(query, snapshot or {})
        env = environment_copy()
        env["PATH"] = f"{Path.home() / '.openclaw' / 'bin'}:{env.get('PATH', '')}"
        last_error: ExternalResearchResult | None = None
        for attempt in range(1, self.retry_attempts + 1):
            cmd = [
                self.command,
                "agent",
                "--agent",
                self.agent,
                "--json",
                "--timeout",
                str(int(max(10, self.timeout_seconds))),
                "--message",
                prompt,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds + 5.0,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return ExternalResearchResult(
                    False,
                    answer="Sali a investigar afuera, pero OpenClaw tardo demasiado y corte la salida para no dejar colgado el laboratorio.",
                    spoken_answer="Sali a investigar afuera, pero OpenClaw tardo demasiado y corte la salida para no dejar colgado el laboratorio.",
                    detail="bridge_timeout",
                    provider=self.name,
                    query=query,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            raw = (proc.stdout or proc.stderr or "").strip()
            parsed = self._extract_json_payload(raw)
            if not parsed:
                detail = f"bridge_exit_{proc.returncode}" if proc.returncode else "bridge_invalid_json"
                last_error = ExternalResearchResult(
                    False,
                    answer="Sali a investigar afuera, pero OpenClaw devolvio algo que Fusion no pudo interpretar con seguridad.",
                    spoken_answer="Sali a investigar afuera, pero OpenClaw devolvio algo que no pude interpretar con seguridad.",
                    detail=detail,
                    provider=self.name,
                    query=query,
                    raw_text=raw,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            else:
                last_error = self._result_from_payload(
                    parsed,
                    query=query,
                    raw_text=raw,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                if last_error.ok or not self._should_retry(last_error):
                    return last_error
            if attempt < self.retry_attempts:
                time.sleep(0.8)
        return last_error or ExternalResearchResult(
            False,
            answer="Salí a investigar afuera, pero no pude completar la consulta externa.",
            spoken_answer="Sali a investigar afuera, pero no pude completar la consulta externa.",
            detail="bridge_failed",
            provider=self.name,
            query=query,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _build_prompt(self, request: str, snapshot: dict) -> str:
        context_lines = []
        main_document_value = snapshot.get("main_document")
        main_document = main_document_value if isinstance(main_document_value, dict) else {}
        if main_document:
            title = str(main_document.get("title") or "").strip()
            if title:
                context_lines.append(f"Documento principal: {title}")
        current_chunk = self._clip(snapshot.get("current_chunk") or "", 420)
        if current_chunk:
            context_lines.append(f"Bloque actual: {current_chunk}")
        focus_value = snapshot.get("laboratory_focus")
        focus = focus_value if isinstance(focus_value, dict) else {}
        if focus:
            focus_title = str(focus.get("title") or "").strip()
            focus_text = self._clip(focus.get("text") or "", 340)
            if focus_title:
                context_lines.append(
                    f"Foco de laboratorio: {focus_title} bloque {int(focus.get('chunk_number') or 0)} de {int(focus.get('total') or 0)}."
                )
            if focus_text:
                context_lines.append(f"Texto focal: {focus_text}")
        laboratory_mode_value = snapshot.get("laboratory_mode")
        laboratory_mode = laboratory_mode_value if isinstance(laboratory_mode_value, dict) else {}
        context_lines.append(f"Modo de laboratorio: {laboratory_mode.get('mode') or 'document'}")
        context_block = "\n".join(f"- {line}" for line in context_lines if line)
        return (
            "Sos el exoesqueleto externo de investigacion de Fusion Reader v2.\n"
            "Se te llama solo cuando el usuario pidio explicitamente buscar afuera del lector.\n"
            "Investiga de forma puntual y util. Si el pedido sugiere academia, prioriza tesis, papers, repositorios universitarios y fuentes institucionales.\n"
            "No inventes fuentes ni enlaces. Si no podes investigar, decilo con claridad.\n"
            "Devolve SOLO JSON puro, sin markdown ni comentarios, con este esquema:\n"
            '{"ok":true,"query":"...","summary":"...","findings":["..."],"sources":[{"title":"...","url":"...","note":"..."}],"suggested_followup":"...","error":""}\n'
            "Si falla algo o hay rate limit, usa ok=false y explica el problema en summary y error.\n\n"
            f"Contexto de Fusion:\n{context_block or '- Sin contexto adicional.'}\n\n"
            f"Pedido del usuario:\n{request}"
        )

    def _result_from_payload(
        self, payload: dict, query: str, raw_text: str, duration_ms: int
    ) -> ExternalResearchResult:
        result_value = payload.get("result")
        result = result_value if isinstance(result_value, dict) else {}
        meta_value = result.get("meta")
        meta = meta_value if isinstance(meta_value, dict) else {}
        agent_meta_value = meta.get("agentMeta")
        agent_meta = agent_meta_value if isinstance(agent_meta_value, dict) else {}
        provider = str(agent_meta.get("provider") or self.name)
        model = str(agent_meta.get("model") or self.agent)
        payloads_value = result.get("payloads")
        payloads = payloads_value if isinstance(payloads_value, list) else []
        text_parts = [
            str(item.get("text") or "").strip()
            for item in payloads
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        combined_text = "\n".join(text_parts).strip()
        inner = self._extract_json_payload(combined_text)
        parsed = inner if isinstance(inner, dict) else {}
        summary = str(parsed.get("summary") or "").strip()
        findings = self._sanitize_findings(parsed.get("findings"))
        sources = self._sanitize_sources(parsed.get("sources"))
        suggested = str(parsed.get("suggested_followup") or "").strip()
        stop_reason = str(result.get("stopReason") or "").strip().lower()
        detail = str(parsed.get("error") or "").strip()
        ok = bool(parsed.get("ok")) if parsed else bool(payload.get("status") == "ok")
        combined_lower = combined_text.lower()
        if stop_reason == "error" or "rate limit" in combined_lower or "try again later" in combined_lower:
            ok = False
            if not detail:
                detail = "bridge_rate_limit" if "rate limit" in combined_lower else "bridge_error"
        if not summary:
            summary = combined_text or (
                "Investigacion externa completada." if ok else "No pude completar la investigacion externa."
            )
        if not ok:
            summary, detail = self._humanize_failure(summary, detail, combined_text or raw_text)
        answer = self._format_answer(query, summary, findings, sources, suggested, ok=ok)
        spoken_answer = self._format_spoken_answer(summary, findings, ok=ok)
        return ExternalResearchResult(
            ok=ok,
            answer=answer,
            spoken_answer=spoken_answer,
            detail=detail or ("external_research_ok" if ok else "external_research_failed"),
            provider=provider,
            model=model,
            duration_ms=duration_ms or int(meta.get("durationMs") or 0),
            query=str(parsed.get("query") or query).strip(),
            summary=summary,
            findings=findings,
            sources=sources,
            raw_text=raw_text,
        )

    def _should_retry(self, result: ExternalResearchResult) -> bool:
        detail = str(result.detail or "").strip().lower()
        if detail in {
            "bridge_gateway_restart",
            "bridge_invalid_json",
            "bridge_workspace_busy",
            "bridge_exit_1",
            "bridge_exit_2",
        }:
            return True
        raw = str(result.raw_text or "").lower()
        transient_markers = (
            "gateway closed",
            "service restart",
            "workspace state",
            "enoent",
            "lock",
            "temporarily unavailable",
        )
        return any(marker in raw for marker in transient_markers)

    def _humanize_failure(self, summary: str, detail: str, raw_text: str) -> tuple[str, str]:
        clean_detail = str(detail or "").strip().lower()
        raw_lower = str(raw_text or "").lower()
        if (
            clean_detail == "bridge_rate_limit"
            or "rate limit" in raw_lower
            or "quota" in raw_lower
            or "429" in raw_lower
        ):
            return (
                "Salí a investigar afuera, pero OpenClaw/Gemini está temporalmente limitado por cuota o rate limit. La integración quedó activa; probá de nuevo en un rato.",
                "bridge_rate_limit",
            )
        if "gateway closed" in raw_lower or "service restart" in raw_lower:
            return (
                "Salí a investigar afuera, pero el gateway de OpenClaw se estaba reiniciando y no terminó la búsqueda. Probá otra vez en unos segundos.",
                "bridge_gateway_restart",
            )
        if "workspace state" in raw_lower or "enoent" in raw_lower or "lock" in raw_lower:
            return (
                "Salí a investigar afuera, pero OpenClaw tenía su workspace ocupado o inconsistente y no pudo terminar esta misión.",
                "bridge_workspace_busy",
            )
        if summary:
            return summary, detail or "external_research_failed"
        return (
            "Salí a investigar afuera, pero hubo un problema en OpenClaw y no pude completar la búsqueda externa.",
            detail or "external_research_failed",
        )

    def _format_answer(
        self, query: str, summary: str, findings: list[str], sources: list[dict], suggested: str, *, ok: bool
    ) -> str:
        lines = []
        if ok:
            lines.append(f"Sali a investigar afuera sobre: {query}.")
        else:
            lines.append("Intente salir a investigar afuera, pero hubo un problema.")
        if summary:
            lines.append(summary)
        if findings:
            lines.append("Hallazgos:")
            for finding in findings[:5]:
                lines.append(f"- {finding}")
        if sources:
            lines.append("Fuentes:")
            for source in sources[:5]:
                title = str(source.get("title") or "Fuente").strip()
                url = str(source.get("url") or "").strip()
                note = str(source.get("note") or "").strip()
                chunk = title
                if note:
                    chunk += f" | {note}"
                if url:
                    chunk += f" | {url}"
                lines.append(f"- {chunk}")
        if suggested and ok:
            lines.append(f"Seguimiento sugerido: {suggested}")
        return "\n".join(line for line in lines if line).strip()

    def _format_spoken_answer(self, summary: str, findings: list[str], *, ok: bool) -> str:
        pieces = []
        pieces.append(
            summary if summary else ("Investigacion externa lista." if ok else "La investigacion externa fallo.")
        )
        if findings:
            pieces.append(" ".join(findings[:2]))
        text = " ".join(piece.strip() for piece in pieces if piece.strip())
        text = re.sub(r"https?://\S+", "", text).strip()
        return text or ("Investigacion externa lista." if ok else "La investigacion externa fallo.")

    def _sanitize_findings(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            text = " ".join(str(item or "").split()).strip()
            if text:
                out.append(text)
        return out

    def _sanitize_sources(self, value: object) -> list[dict]:
        if not isinstance(value, list):
            return []
        out: list[dict] = []
        for item in value:
            if isinstance(item, dict):
                title = " ".join(str(item.get("title") or "").split()).strip()
                url = " ".join(str(item.get("url") or "").split()).strip()
                note = " ".join(str(item.get("note") or "").split()).strip()
            else:
                title = " ".join(str(item or "").split()).strip()
                url = ""
                note = ""
            if title or url or note:
                out.append({"title": title, "url": url, "note": note})
        return out

    def _extract_json_payload(self, text: str) -> dict | None:
        clean = str(text or "").strip()
        if not clean:
            return None
        if clean.startswith("```"):
            clean = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", clean)
            clean = re.sub(r"\s*```$", "", clean).strip()
        try:
            data = json.loads(clean)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
        decoder = json.JSONDecoder()
        for index, char in enumerate(clean):
            if char != "{":
                continue
            try:
                data, _ = decoder.raw_decode(clean[index:])
            except Exception:
                continue
            if isinstance(data, dict):
                return data
        return None

    def _clip(self, text: str, max_chars: int) -> str:
        clean = " ".join(str(text or "").split()).strip()
        if len(clean) <= max_chars:
            return clean
        return clean[:max_chars].rstrip() + "..."
