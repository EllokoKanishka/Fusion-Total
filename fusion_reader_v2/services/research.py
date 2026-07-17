from __future__ import annotations

import unicodedata
from collections.abc import Callable

from fusion_reader_v2.openclaw_bridge import ExternalResearchBridge, ExternalResearchResult


class ResearchService:
    """Owns explicit external-research intent, provider health and execution."""

    def __init__(self, bridge: ExternalResearchBridge, snapshot: Callable[[], dict]) -> None:
        self.bridge = bridge
        self.snapshot = snapshot

    @staticmethod
    def normalized_key(text: str) -> str:
        lowered = str(text or "").lower()
        plain = "".join(char for char in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(char))
        return " ".join(plain.replace("¿", "").replace("¡", "").split())

    def is_explicit_request(self, text: str) -> bool:
        clean = self.normalized_key(text)
        if not clean:
            return False
        explicit = (
            "busca en internet",
            "buscar en internet",
            "busca en la red",
            "buscar en la red",
            "busca en web",
            "buscar en web",
            "busca afuera",
            "buscar afuera",
            "investiga en internet",
            "investigar en internet",
            "investiga afuera",
            "investigar afuera",
            "trae fuentes externas",
            "trae fuentes de internet",
            "busca fuentes externas",
            "googlea",
            "googlealo",
            "googleala",
            "busca online",
        )
        if any(marker in clean for marker in explicit):
            return True
        academic = (
            "tesis",
            "tesis doctoral",
            "tesis de doctorado",
            "paper",
            "papers",
            "articulo",
            "articulos",
            "fuente",
            "fuentes",
            "repositorio",
            "repositorios",
            "universidad",
            "universidades",
            "bibliografia",
            "journal",
            "revista academica",
            "revistas academicas",
        )
        verbs = ("busca", "buscar", "buscame", "investiga", "investigar", "trae", "traer", "revisa")
        return any(clean.startswith(verb + " ") for verb in verbs) and any(marker in clean for marker in academic)

    def research(self, message: str) -> ExternalResearchResult:
        return self.bridge.research(message, snapshot=self.snapshot())

    def health(self) -> dict:
        bridge = self.bridge
        out = {"provider": str(getattr(bridge, "name", "external_research")), "ok": False, "detail": "unavailable"}
        available = getattr(bridge, "available", None)
        if not callable(available):
            out["detail"] = "bridge_has_no_available_probe"
            return out
        try:
            out["ok"] = bool(available())
        except Exception as exc:
            out["detail"] = str(exc)
            return out
        for name in ("base_url", "command", "agent"):
            if hasattr(bridge, name):
                out["url" if name == "base_url" else name] = str(getattr(bridge, name) or "")
        if hasattr(bridge, "searxng"):
            out["mode"] = "auto"
            for name in ("searxng", "openclaw"):
                provider = getattr(bridge, name)
                try:
                    detail = {"provider": str(getattr(provider, "name", name)), "ok": bool(provider.available())}
                    if name == "searxng":
                        detail["url"] = str(getattr(provider, "base_url", "") or "")
                    else:
                        detail["agent"] = str(getattr(provider, "agent", "") or "")
                    out[name] = detail
                except Exception as exc:
                    out[name] = {"provider": name, "ok": False, "detail": str(exc)}
        if out["ok"]:
            out["detail"] = "ready"
        return out


__all__ = ["ResearchService"]
