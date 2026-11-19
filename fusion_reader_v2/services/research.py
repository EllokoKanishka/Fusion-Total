from __future__ import annotations

import re
import unicodedata

from fusion_reader_v2.openclaw_bridge import ExternalResearchBridge, ExternalResearchResult


class ResearchService:
    """Owns explicit external-research intent, provider health and execution."""

    def __init__(self, bridge: ExternalResearchBridge) -> None:
        self.bridge = bridge

    @staticmethod
    def normalized_key(text: str) -> str:
        lowered = str(text or "").lower()
        plain = "".join(char for char in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(char))
        return " ".join(plain.replace("¿", "").replace("¡", "").split())

    def is_explicit_request(self, text: str) -> bool:
        clean = self.normalized_key(text)
        if not clean:
            return False
        # Only a request clause can launch a provider. Quoted examples, questions
        # about searching and negative instructions are conversation, not actions.
        prefix = (
            r"(?:(?:por favor|che|bueno|bien|hola)[,.]?\s+)*"
            r"(?:(?:necesito|quiero|quisiera|te pido|me gustaria)\s+(?:que\s+)?)?"
            r"(?:(?:me\s+)?(?:podes|podrias|puedes|puede)\s+)?"
        )
        request = re.match(
            rf"^{prefix}(busca(?:r|me|s)?|busques|investiga(?:r|s)?|investigues|trae(?:r|me)?|traigas|revisa|googlea(?:lo|la)?)\b(.*)$",
            clean,
        )
        if not request:
            return False
        verb, target = request.groups()
        target = re.sub(r'«[^»]*»|“[^”]*”|"[^"]*"', " ", target)
        target = re.sub(r"(?<!\w)'[^']*'(?!\w)", " ", target)
        if re.search(
            r"\b(?:no|nunca|sin)\s+(?:(?:que|usar|uses|utilizar|utilices|consultar|consultes|buscar|busques|investigar|investigues|salgas|salir|acceder|accedas)\s+){0,3}"
            r"(?:en\s+|a\s+)?(?:internet|(?:la\s+)?web|la red|fuentes externas|afuera|fuera)\b",
            target,
        ):
            return False
        if verb.startswith("googlea"):
            return True
        local_scope = re.search(
            r"\b(?:en|dentro del?|sin salir del?|del|de)\s+(?:(?:el|la|los|las|este|esta|estos|estas|mi|mis)\s+)?"
            r"(?:texto|documento|bloque|capitulo|pdf|archivo|libro|parrafo|lectura|referencias|notas)\b",
            target,
        )
        if local_scope and not target[: local_scope.start()].strip():
            return False
        if re.search(r"\b(?:en (?:internet|la red|(?:la )?web|linea)|online|afuera)\b", target):
            return True
        if local_scope:
            return False
        return bool(
            re.search(
                r"\b(?:tesis|papers?|articulos?|fuentes?|repositorios?|universidad(?:es)?|bibliografia|"
                r"journals?|revistas? academicas?)\b",
                target,
            )
        )

    def research(self, message: str) -> ExternalResearchResult:
        return self.bridge.research(message, snapshot={})

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
