from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from fusion_reader_v2.openclaw_bridge import ExternalResearchBridge, ExternalResearchResult
from fusion_reader_v2.turn_provider import external_research_permission


_RESEARCH_VERB = (
    r"busca(?:r)?(?:me|nos|te|les?|l[oa]s?)?|buscas|busques|"
    r"investiga(?:r)?(?:me|nos|te|les?|l[oa]s?)?|investigas|investigues|"
    r"consulta(?:r)?(?:me|nos|te|les?|l[oa]s?)?|consultas|consultes|"
    r"trae(?:r)?(?:me|nos|te|les?|l[oa]s?)?|traigas|revisa|googlea(?:lo|la)?"
)
_TOPIC_INTRO = r"sobre|acerca\s+del?|(?:con\s+)?respecto\s+(?:al?|del?)"
_SPOKEN_PREFIX = r"(?:(?:por favor|che|bueno|bien|hola|escucha(?:me)?|mira|ok(?:ay)?|a ver|entonces|esta (?:muy )?bien)[,.:;!?]?\s+)*"
_REQUEST = re.compile(
    rf"^{_SPOKEN_PREFIX}"
    r"(?:(?:pero\s+)?ahora(?:\s+de)?[,.:;]?\s+)?"
    r"(?:(?:no[,.:;]\s+)?lo que te (?:pedi|pido|dije|decia) (?:es|era) que\s+|"
    r"(?:necesito|quiero|quisiera|te pido|me gustaria)\s+(?:que\s+)?)?"
    r"(?:(?:me\s+)?(?:podes|podrias|puedes|puede)\s+)?"
    rf"(?:(?P<topic>(?:{_TOPIC_INTRO})\s+(?:[^.!?;]|\.(?=\w)){{1,180}}?)[,;]?\s+)?"
    rf"(?P<verb>{_RESEARCH_VERB})\b(?P<target>.*)$"
)
_QUOTED = re.compile(r"""«[^»]*»|“[^”]*”|"[^"]*"|(?<!\w)'[^']*'(?!\w)""")
_WEB_LOCATION = r"en (?:internet|la red|(?:la )?web|linea)|online|afuera"
_CLAUSE_BOUNDARY = re.compile(r"(?<=[.!?;])\s+")
_NON_REQUEST_CONTEXT = re.compile(
    r"\b(?:"
    r"(?:te (?:leo|dicto)|(?:voy a|quiero) (?:leer|dictar)(?:te)?)\s+(?:una?\s+|la\s+|el\s+)?"
    r"(?:consigna|ejemplo|frase|cita|orden|instruccion|texto)"
    r"|(?:esto|eso)\s+(?:es|era)\s+(?:solo\s+)?(?:una?\s+)?(?:ejemplo|cita|consigna)"
    r"|(?:la (?:consigna|frase|orden|instruccion)|el (?:ejemplo|texto))\s+(?:dice|decia|reza)"
    r"|si (?:yo )?te (?:dijera|pidiera)\b"
    r"|(?:que (?:significa|quiere decir)|(?:analiza|explica)(?:me)?)\s+"
    r"(?:esta|esa|la)\s+(?:consigna|frase|orden|instruccion)"
    r")\b"
)
_UNCERTAIN_WEB_REQUEST = re.compile(
    rf"\b(?:me pregunto si|estaba pensando si|seria posible|capaz podrias|tal vez puedas)\b"
    rf"[^.!?;]{{0,100}}\b(?:{_RESEARCH_VERB})\b[^.!?;]{{0,50}}\b(?:{_WEB_LOCATION})\b"
)
_WHY_NO_SEARCH = re.compile(
    rf"^{_SPOKEN_PREFIX}(?:(?:y|pero)\s+)?(?:por que|por que motivo)\s+"
    rf"(?:no\s+(?:(?:podes|puedes|pudiste|quisiste)\s+)?(?:buscar|buscaste|investigaste|investigar)"
    r"|no\s+(?:hiciste|haces|harias)\s+(?:la|esa)\s+busqueda)\b"
)
_NEWS_TOPIC = re.compile(
    rf"^\s*(?:(?:{_WEB_LOCATION})\s+)?(?:(?:las|los|algunas|algunos|ultimas|ultimos|principales)\s+)*"
    r"(?:noticias|titulares|novedades)\b"
)
_NEWS_SUBJECT_FIRST = re.compile(
    r"\b(?:sobre|acerca de)\s+(.+?)\s+(?:(?:las|los|[uú]ltimas|[uú]ltimos|principales)\s+)*"
    r"(?:noticias|titulares|novedades)\b(.*)$",
    re.I,
)
_NEWS_QUERY = re.compile(
    r".*?\b(?:noticias|titulares|novedades)\b(?:\s+recientes)?(?:\s+(?:de|sobre|acerca\s+de)\s+)?",
    re.IGNORECASE,
)
_TODAY = re.compile(r"\b(?:de\s+)?hoy\b", re.IGNORECASE)
_GENERIC_WEB_TARGET = re.compile(
    rf"\s*(?:(?:informacion|fuentes|datos|referencias)\s+)?(?:{_WEB_LOCATION})"
    r"(?:\s+(?:informacion|fuentes|datos|referencias))?"
)
_ORDINALS = "primero segundo tercero cuarto quinto sexto septimo octavo noveno decimo".split()
_CARDINALS = "uno dos tres cuatro cinco seis siete ocho nueve diez".split()
_NUMBERED_BLOCK = re.compile(
    rf"\b(?:(?P<ordinal>{'|'.join((*_ORDINALS, 'primer', 'tercer'))})\s+bloque"
    rf"|bloque\s+(?:numero\s+)?(?P<number>\d{{1,4}}|{'|'.join(_CARDINALS)}))\b"
)
_BLOCK_DOCUMENT = re.compile(rf"^\s+(?:de|del)\s+(.+?)(?=\s+(?:{_RESEARCH_VERB}|{_WEB_LOCATION}|por favor)\b|[,;]|$)")
_PREVIOUS_ANSWER_REQUEST = re.compile(
    r"\b(?:sobre|acerca de|respecto a)\s+"
    r"(?:(?:esto|eso|lo)\s+que\s+(?:me\s+)?(?:acabas?\s+de\s+decir|dijiste(?:\s+recien)?|mencionaste)"
    r"|esto|eso|lo anterior|tu (?:ultima )?respuesta"
    r"|(?:estos|esos|aquellos) (?:puntos|temas|ejes|conceptos)"
    r"|(?:estas|esas|aquellas) ideas)$"
)
_INTEGRATES = r"englob\w*|integr\w*|reun\w*|abarc\w*|articul\w*|relacion\w*|conect\w*|combin\w*|vincul\w*"
_SOURCE_NOUN = r"articulo|paper|publicacion|trabajo|estudio|fuente"
_REFERENCED_THEMES = re.compile(
    rf"\b(?:sobre|acerca de|respecto a|{_INTEGRATES})\s+"
    r"(?:(?:todos|todas)\s+)?(?:estos|esos|aquellos|estas|esas|aquellas)\s+"
    r"(?:titulos|temas|ideas|ejes|puntos|conceptos)\b"
)
_INTEGRATIVE_SOURCE_REQUEST = re.compile(
    rf"\b(?:(?:un solo|una sola)\s+(?:{_SOURCE_NOUN})\b|"
    rf"(?:un|una|algun|alguna)\s+(?:{_SOURCE_NOUN})\b[^.!?;]{{0,80}}\bque\s+(?:{_INTEGRATES})\b)"
)
_ACADEMIC = re.compile(r"\b(?:academi\w*|cientifi\w*|bibliograf\w*|papers?|tesis|journals?)\b")
_RESEARCH_COMPOSITE = re.compile(
    r"\b(?:quiero decir[,:;]?|quiero|queria|pedi|necesito|me gustaria|podes|podrias) (?:es )?(?:que )?"
    r"(?:interpret|identifi|analiz|detect|extra|revis|separ|veas|mires)\w*\b[^!?]{1,300}?"
    rf"\by (?:despues |luego )?(?:que )?(?P<search>(?:{_RESEARCH_VERB})\b.*)$"
)
_RESEARCH_AFTER_CONTEXT = re.compile(
    rf"^{_SPOKEN_PREFIX}(?:(?:me )?(?:podes|podrias|puedes) )?"
    r"(?:toma(?:r)?|ten(?:e|er)? en cuenta|usa(?:r)?|lee(?:r)?|mira(?:r)?|"
    r"parti(?:r)? de|analiza(?:r)?|separa(?:r)?|identifica(?:r)?|resum(?:e|i|ir)|explica(?:r)?)\s+[^!?]{1,380}?"
    rf"(?:\s+y\s+|[.;:]\s*)(?:despues |luego )?"
    rf"(?:(?:de|sobre) cada (?:una? de )?(?:esas |esos |las |los )?(?:ideas|temas|ejes|puntos) )?"
    rf"(?P<search>(?:{_RESEARCH_VERB})\b.*)$"
)
_RESEARCH_PROHIBITION = re.compile(
    r"\b(?:no|nunca|jamas|sin|evita)\s+(?:(?:quiero|quisiera|te|pido|que)\s+){0,4}"
    r"(?:bus(?:c|qu)\w*|investig\w*|consult\w*|usar|uses|utiliz\w*)"
    r"\b[^.!?;]{0,65}\b(?:internet|la red|(?:la )?web|afuera|fuentes externas)\b"
)
_CONTEXT_REFERENCE = re.compile(
    r"\b(?:referid[oa]s? a|en relacion con|en relacion a|con relacion a|aplicad[oa]s? a)"
    r"\s+(?:esto|eso|lo anterior|(?:este|ese|el) (?:texto|bloque|tema|documento))\b"
)
_INFORMATION_REQUEST = re.compile(r"^\s*(?:informacion|datos)\s+(?:sobre|acerca del?|respecto al?)\s+\S")
_GENERIC_ACADEMIC = re.compile(
    r"^\s*(?:informacion (?:academica|cientifica)|fuentes(?: academicas)?|bibliografia|referencias academicas)"
    r"(?:[ ,.]+(?:(?:y )?(?:deja|dejame|dejarme|pasame|pasa|trae|mostra)(?:\s+me)? (?:los )?(?:links|enlaces)"
    r"|(?:busc(?:a|ar)la|buscarlos|investigala)?\s*en (?:internet|la red|la web)))*[ ,.]*$"
)
_WEB_REPAIR = re.compile(
    rf"^{_SPOKEN_PREFIX}(?:ahora )?(?:(?:me )?(?:podes|podrias|puedes) )?"
    rf"(?:hacerl[oa]|hacel[oa]|intenta(?:lo)?)\b[^.!?]{{0,80}}\b(?:{_RESEARCH_VERB})\s+(?:{_WEB_LOCATION})\b"
)
_HOW_RESEARCH = re.compile(
    rf"^{_SPOKEN_PREFIX}(?:(?:y|pero)\s+)?"
    r"(?:"
    r"como\s+(?:hiciste|realizaste|ejecutaste|armaste)\s+(?:la|esa|esta)\s+(?:busqueda|consulta)"
    r"|(?:como|(?:de )?donde)\s+(?:(?:lo|la|los|las)\s+)?"
    r"(?:buscaste|encontraste|sacaste|bajaste|descargaste|obtuviste|conseguiste)"
    r"|(?:por que|como)\s+(?:(?:lo|la|los|las)\s+)?"
    r"(?:seleccionaste|elegiste|recomendaste)(?=\s+(?:est[ea]s?|es[ea]s?|l[ao]s?)\s+"
    r"(?:fuentes?|articulos?|papers?|enlaces?|referencias?|textos?|pdfs?)\b|[.?!]*$)"
    r"|(?:que|cual(?:es)?)\s+(?:consulta(?:s)?|buscador|motor|fuentes?)\s+(?:usaste|utilizaste|consultaste)"
    r"|(?:realmente\s+)?(?:buscaste|encontraste|bajaste|descargaste)\s+(?:realmente\s+)?"
    r"(?:en (?:internet|la web|la red)|(?:est[ea]s?|es[ea]s?|l[ao]s?)\s+(?:fuentes?|articulos?|pdfs?))"
    r")\b"
)
_WEB_RETRY = re.compile(
    rf"^{_SPOKEN_PREFIX}(?:(?:y|pero)\s+)?(?:me\s+)?(?:podrias|podes|puedes)\s+"
    rf"(?:(?:recuperar|reparar|demostrar|comprobar|verificar)\b[^!?;.]{{1,65}}?\s+)?"
    rf"buscando\s+(?:(?:realmente|de verdad|ahora)\s+)?{_WEB_LOCATION}\s*[.?!]*$"
)
_DOCUMENT_TOPIC = re.compile(
    r"\b(?:documento|texto|bloque|pasaje|fragmento|lectura|pdf|pantalla)\b"
    r"|\b(?:ejes?|temas?) (?:que|del?|en)\b"
)
_RESEARCH_CONTINUATION = re.compile(
    r"^(?:(?:si|dale|bueno|bien|ok|a ver|ahora si)[,.]?\s+)*"
    r"(?:(?:ahi |ya )?(?:te )?(?:habilite|active|marque)\b.{0,70}\b(?:busqueda|internet|web)\b.*|"
    r"(?:ahi |ya )?deje (?:prendida|activada|habilitada)\b.{0,70}\b(?:busqueda|internet|web)\b"
    r".{0,35}\b(?:podes buscar|fijate|busca ahora|hacela)\b.*|"
    r"(?:(?:me )?(?:podes|podrias|puedes)\s+)?"
    r"(?:hacerla|realizarla|ejecutarla|hace(?:la|lo)|realiza(?:la|lo)|busca(?:lo|los|las)|"
    r"buscal[oa]|hace esa busqueda|realiza esa busqueda|busca(?:r| ahora)?|adelante|dale))$"
)
_RESEARCH_RETRY = re.compile(
    rf"^{_SPOKEN_PREFIX}(?:(?:podes|podrias|puedes) )?"
    r"(?:(?:intenta(?:r|lo)?|proba(?:r|lo)?) (?:ahora )?(?:de nuevo|otra vez)|"
    r"(?:reintenta(?:r|lo)?|repeti(?:r)?) (?:la |esa )?busqueda|volve a buscar|"
    r"(?:(?:repara|arregla|corregi|corri)(?:lo|gelo)? (?:ahora )?y )?busca(?:los|las))$"
)
_LINKS_REQUEST = re.compile(
    r"\b(?:dej|pas|mostr|mand|dame|pon|copi|compart)\w*\b[^.!?;]{0,105}"
    r"\b(?:links?|enlaces?|urls?)\b"
)
_QUERY_STOP_WORDS = frozenset(
    "ahi algo ambas ambos ante aquel aquella aquello aqui bien cada como con cual cuanto debe deben deberia "
    "deberian decir del desde dice dos ella ellas ellos esas esos esta estan estas este esto estos fue hace "
    "hacen habia hacia hasta hay justo las les los mas muy nos otra otras otro otros para pero pueden puede por que "
    "pagina queda quien sea segun ser sin sobre son suyo tal tambien tiene tienen todas todo todos tras una unos".split()
)


_BROAD_SOURCE_TERMS = frozenset(
    word[:6]
    for word in (
        "educacion educativa educativo evaluation evaluacion educational education superior higher "
        "limitaciones limitacion limitations problems problemas analysis analisis estudio studies study "
        "texto textos text enfoque metodo revision review resultados results"
    ).split()
)


@dataclass(frozen=True)
class WebIntent:
    operation: str
    topic: str = ""
    today: bool = False


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
        clean = _QUOTED.sub(" ", self.normalized_key(text))
        if self.cancels_pending(clean) or _NON_REQUEST_CONTEXT.search(clean):
            return False
        if _WEB_REPAIR.match(clean):
            return True
        # A spoken turn can establish the context before asking for the search.
        request = self._search_clause(clean)
        if not request:
            return False
        return not self._denies_web_search(clean)

    def may_request_web_action(self, text: str) -> bool:
        clean = _QUOTED.sub(" ", self.normalized_key(text))
        if self.cancels_pending(clean) or self._denies_web_search(clean) or _NON_REQUEST_CONTEXT.search(clean):
            return False
        return bool(
            re.search(r"\b(?:internet|web|la red|portales?|titulares|noticias|fuentes externas|busqueda externa)\b", clean)
            and re.search(
                r"\b(?:acced\w*|entr\w*|abr\w*|mir\w*|fij\w*|rev[ií]s\w*|"
                r"consult\w*|bus[cq]\w*|investig\w*|mostr\w*|tra\w*|encontr\w*)\b",
                clean,
            )
        )

    def grants_web_permission(self, text: str) -> bool:
        clean = self.normalized_key(text)
        return self.may_request_web_action(text) and bool(
            re.search(
                rf"^{_SPOKEN_PREFIX}(?:yo )?te (?:autorizo|habilito|doy permiso)\b[^.!?;]{{0,80}}"
                r"\b(?:buscar|busqueda|internet|la red|web)\b",
                clean,
            )
        )

    @classmethod
    def parse_web_intent(cls, raw: str) -> WebIntent | None:
        payload = cls._json_object(raw)
        operation = payload.get("operation")
        topic = payload.get("topic")
        if operation not in ("search", "news", "headlines") or not isinstance(topic, str):
            return None
        topic = " ".join(topic.split())
        if not 3 <= len(topic) <= 160 or not re.fullmatch(r"[\w ,.'’()-]+", topic):
            return None
        return WebIntent(operation, topic, payload.get("today") is True)

    @classmethod
    def keep_news_date(cls, intent: WebIntent, message: str, previous_topic: str, previous_request: str) -> WebIntent:
        if intent.today or intent.operation not in {"news", "headlines"} or not _TODAY.search(previous_request):
            return intent
        previous = cls.normalized_key(previous_topic)
        topic = cls.normalized_key(intent.topic)
        clean = cls.normalized_key(message)
        if previous and (previous in topic or topic in previous) and not re.search(
            r"\b(?:ayer|anteayer|semana|mes|ano pasado|recientes|ultimos dias|cualquier fecha)\b", clean
        ):
            return replace(intent, today=True)
        return intent

    @staticmethod
    def _denies_web_search(clean: str) -> bool:
        external_scope = r"(?:internet|(?:la\s+)?web|la red|fuentes externas|afuera|fuera|online)"
        # A restriction in an adversative clause can refer to the earlier
        # search indirectly, including verbs unrelated to searching.
        if re.search(
            rf"\b(?:pero|aunque)\s+(?:por favor,?\s+)?(?:no|nunca|sin|evita|evitemos|prohibido)\b"
            rf"[^.;!?]{{0,100}}\b{external_scope}\b",
            clean,
        ):
            return True
        if re.search(
            r"\b(?:no|nunca|sin)\s+(?:(?:quiero|quisiera|permito|autorizo|te|pido)\s+){0,3}"
            r"(?:(?:que|lo|la|hagas|hacer|usar|uses|utilizar|utilices|consultar|consultes|buscar|busques|"
            r"investigar|investigues|salgas|salir|acceder|accedas)\s+){0,5}"
            rf"(?:(?:en|por|a)\s+)?{external_scope}\b",
            clean,
        ):
            return True
        return False

    @staticmethod
    def _is_web_clause(request: re.Match[str]) -> bool:
        topic, verb, target = request.groups()
        if topic and re.search(
            r"\b(?:no|nunca|jamas|sin|evita|como|donde|cuando|por que|para que|sabes|saber)\b", topic
        ):
            return False
        if verb.startswith("googlea"):
            return True
        local_scope = re.search(
            r"\b(?:en|dentro del?|sin salir del?|del|de)\s+(?:(?:el|la|los|las|este|esta|estos|estas|mi|mis)\s+)?"
            rf"(?:(?:primer|tercer|{'|'.join(_ORDINALS)})\s+)?"
            r"(?:texto|documento|bloque|capitulo|pdf|archivo|libro|parrafo|lectura|referencias|notas)\b",
            target,
        )
        if local_scope and re.fullmatch(r"\s*(?:(?:solo|solamente|por favor)[,\s]+)*", target[: local_scope.start()]):
            return False
        if re.search(rf"\b(?:{_WEB_LOCATION})\b", target):
            return True
        if local_scope:
            return False
        return bool(
            _ACADEMIC.search(target)
            or _NEWS_TOPIC.match(target)
            or _NEWS_SUBJECT_FIRST.match(target)
            or _INFORMATION_REQUEST.match(target)
            or re.search(
                r"\b(?:tesis|papers?|articulos?|fuentes?|repositorios?|universidad(?:es)?|bibliografia|"
                r"journals?|revistas? academicas?)\b",
                target,
            )
        )

    def _composite_request(self, clean: str) -> re.Match[str] | None:
        return _RESEARCH_AFTER_CONTEXT.match(clean) or _RESEARCH_COMPOSITE.search(clean)

    def _search_clause(self, clean: str) -> re.Match[str] | None:
        composite = self._composite_request(clean)
        if composite:
            request = _REQUEST.match(composite["search"])
            if request and self._is_web_clause(request):
                return request
        for clause in _CLAUSE_BOUNDARY.split(clean):
            clause = clause.lstrip()
            composite = self._composite_request(clause)
            request = _REQUEST.match(composite["search"] if composite else clause)
            if request and self._is_web_clause(request):
                return request
        return None

    def needs_search_clarification(self, text: str) -> bool:
        clean = _QUOTED.sub(" ", self.normalized_key(text))
        return not (
            self.cancels_pending(clean) or self._denies_web_search(clean) or _NON_REQUEST_CONTEXT.search(clean)
        ) and bool(_UNCERTAIN_WEB_REQUEST.search(clean))

    def asks_why_no_search(self, text: str) -> bool:
        clean = _QUOTED.sub(" ", self.normalized_key(text)).strip(" .?!")
        return bool(
            _WHY_NO_SEARCH.search(clean)
            and (re.search(r"\b(?:internet|web|red|busqueda|fuentes)\b", clean) or _WHY_NO_SEARCH.fullmatch(clean))
        )

    @staticmethod
    def explain_search_routing(*, permitted: bool, result: ExternalResearchResult | None) -> dict:
        if not permitted:
            answer = "La casilla «Buscar en internet» está desmarcada en este turno."
        elif result is not None:
            return ResearchService.describe_last_search(result)
        else:
            answer = (
                "La búsqueda web está habilitada. Panda aún no registró una consulta en esta conversación. "
                "Decime qué querés que busque."
            )
        return {"ok": True, "answer": answer, "model": "reader_research", "detail": "external_research_routing"}

    def asks_how_researched(self, text: str) -> bool:
        clean = _QUOTED.sub(" ", self.normalized_key(text))
        match = _HOW_RESEARCH.search(clean)
        return bool(
            match
            and not re.match(
                r"\s+(?:(?:el|la|los|las)\s+)?(?:volumen|velocidad|bloques?|documentos?|pasajes?)\b",
                clean[match.end() :],
            )
        )

    @staticmethod
    def describe_last_search(result: ExternalResearchResult | None) -> dict:
        response = {"ok": True, "model": "reader_research", "detail": "external_research_provenance"}
        if result is None or not result.provider or not result.query:
            return {**response, "answer": "Todavía no hay una búsqueda web registrada en esta conversación."}
        providers = {"searxng": "SearXNG", "openclaw_bridge": "OpenClaw", "un_news": "Noticias ONU"}
        provider = providers.get(result.provider, result.provider)
        sources = result.sources
        outcome = (
            f"Mostré {len(sources)} enlace{'s' if len(sources) != 1 else ''} del buscador."
            if sources
            else "Ese intento terminó sin fuentes verificables."
        )
        summary = f"El último intento registrado usó {provider}. {outcome}"
        lines = [summary, "Consultas registradas:", *(f"- {query}" for query in result.query.split(" | "))]
        for source in sources:
            origin = providers.get(source.get("provider"), source.get("provider")) or provider
            engine = f" mediante {source['engine']}" if source.get("engine") else ""
            query = f" para «{source['query']}»" if source.get("query") else ""
            lines.append(f"- {source['title']} · Resultado de {origin}{engine}{query}.")
            if source.get("topic"):
                lines.append(f"  Seleccionado por su relación con «{source['topic']}», según el título o extracto.")
            lines.append(f"  {source['url']}")
        if sources:
            lines.append(
                "El turno de búsqueda recuperó los enlaces y sus extractos. La descarga o lectura del archivo completo no forma parte de ese turno."
            )
        spoken = f"{summary} Te dejo las consultas en el chat."
        if sources:
            spoken += " El buscador devolvió enlaces y extractos. El archivo completo no se descargó en ese turno."
        return {
            **response,
            "answer": "\n".join(lines),
            "spoken_answer": spoken,
            "external_provider": result.provider,
            "external_query": result.query,
            "external_sources": sources,
        }

    def asks_to_repeat_web_search(self, text: str) -> bool:
        return bool(_WEB_RETRY.fullmatch(self.normalized_key(text)))

    def is_incomplete_document_request(self, text: str) -> bool:
        clean = self.normalized_key(text).strip(" .,:;!?")
        composite = self._composite_request(_QUOTED.sub(" ", clean))
        if not composite or self.cancels_pending(clean):
            return False
        requested = composite["search"]
        return bool(
            _DOCUMENT_TOPIC.search(composite[0][: composite.start("search") - composite.start()])
            and re.fullmatch(rf"(?:{_RESEARCH_VERB})(?:[ ,]+(?:algo|alguna?s?|algun)){{0,4}}", requested)
        )

    def resumes_pending(self, text: str) -> bool:
        clean = self.normalized_key(text).strip(" .,:;!?")
        return bool(
            not self.cancels_pending(clean) and (_RESEARCH_CONTINUATION.fullmatch(clean) or self.requests_retry(clean))
        )

    def requests_retry(self, text: str) -> bool:
        clean = self.normalized_key(text).strip(" .,:;!?")
        return bool(_RESEARCH_RETRY.fullmatch(clean) or self.asks_to_repeat_web_search(clean))

    def explicitly_repeats_search(self, text: str) -> bool:
        clean = self.normalized_key(text).strip(" .,:;!?")
        return self.asks_to_repeat_web_search(clean) or (
            self.requests_retry(clean) and bool(re.search(r"\b(?:busca(?:los|las)|busqueda|volve a buscar)$", clean))
        )

    def cancels_pending(self, text: str) -> bool:
        clean = _QUOTED.sub(" ", self.normalized_key(text))
        return bool(
            _RESEARCH_PROHIBITION.search(clean)
            or re.match(r"^(?:cancela|detene|olvida|deja) (?:la|esa) busqueda\b", clean)
            or re.search(r"\bno (?:busques|investigues|hagas la busqueda)\s*[.!?]*$", clean)
        )

    def wants_source_links(self, text: str) -> bool:
        clean = self.normalized_key(text)
        return bool(re.match(r"^(?:los )?(?:links|enlaces|urls)[.? ]*$", clean) or _LINKS_REQUEST.search(clean))

    def research(
        self,
        message: str,
        *,
        previous_answer: Callable[[], str] | None = None,
        previous_question: str = "",
        block_text: Callable[[int, str], str] | None = None,
        visible_text: Callable[[], str] | None = None,
        plan_topics: Callable[[str, str], str] | None = None,
        review_sources: Callable[[list[dict]], str] | None = None,
        news_queries: Callable[[str], str] | None = None,
        review_news_sources: Callable[[str, list[dict]], str] | None = None,
        web_intent: WebIntent | None = None,
    ) -> ExternalResearchResult:
        if external_research_permission.get() is False:
            message = "Activá «Buscar en internet» para esta consulta."
            return ExternalResearchResult(
                False, answer=message, spoken_answer=message, detail="external_research_permission_required"
            )
        if web_intent is not None:
            query = web_intent.topic
            if web_intent.operation in {"news", "headlines"}:
                result = self.bridge.research_news(query, today=web_intent.today)
                if web_intent.today and news_queries and review_news_sources and (
                    result.sources or result.detail == "searxng_no_results"
                ):
                    return self._review_daily_news(query, result, news_queries, review_news_sources)
            else:
                result = self.bridge.research(query, snapshot={})
            return self._with_public_sources(result, query)

        clean = self.normalized_key(message).rstrip(" .,:;!?")
        unquoted = _QUOTED.sub(lambda match: " " * len(match[0]), clean)
        block = self._block_reference(clean, unquoted)
        prior_reference = bool(_PREVIOUS_ANSWER_REQUEST.search(unquoted) or _REFERENCED_THEMES.search(unquoted))
        integrative = bool(_INTEGRATIVE_SOURCE_REQUEST.search(unquoted))
        if block:
            block_number, selector = block
            text = block_text(block_number, selector) if block_text else ""
            if not text:
                answer = (
                    f"No encontré el bloque {block_number} en el documento indicado. Decime qué tema querés buscar."
                )
                return ExternalResearchResult(
                    False, answer=answer, spoken_answer=answer, detail="external_research_block_unavailable"
                )
            if plan_topics is not None:
                earlier = previous_answer() if previous_answer and prior_reference else ""
                direction = f"{message}\nEjes mencionados antes: {earlier[:1800]}" if earlier else message
                return self._research_document(direction, text, plan_topics, review_sources, integrative=integrative)
            query = self._query_from_text(text)
        elif _CONTEXT_REFERENCE.search(unquoted) or _WEB_REPAIR.match(unquoted):
            passage = visible_text() if visible_text else ""
            passage = passage or (previous_answer() if previous_answer else "")
            if passage and plan_topics is not None:
                return self._research_document(message, passage, plan_topics, review_sources, integrative=integrative)
            query = self._query_from_text(passage)
        elif prior_reference:
            earlier = previous_answer() if previous_answer else ""
            if earlier and plan_topics is not None and (integrative or _ACADEMIC.search(unquoted)):
                support = ""
                if integrative and block_text and previous_question:
                    prior = self.normalized_key(previous_question).rstrip(" .,:;!?")
                    origin = self._block_reference(prior, _QUOTED.sub(lambda match: " " * len(match[0]), prior))
                    if origin:
                        support = block_text(*origin)
                return self._research_document(
                    message, earlier, plan_topics, review_sources, integrative=integrative, supporting_passage=support
                )
            query = self._query_from_text(earlier)
        elif (
            visible_text is not None
            and plan_topics is not None
            and (
                self._composite_request(unquoted)
                or (
                    _ACADEMIC.search(unquoted)
                    and re.search(
                        r"\b(?:ejes|temas|ideas|conceptos)\s+(?:que (?:trata|presenta|desarrolla) |del? )"
                        r"(?:(?:el|este|ese) )?(?:documento|texto|bloque|pasaje)\b",
                        unquoted,
                    )
                )
                or integrative
                or (request := self._search_clause(unquoted))
                and _GENERIC_ACADEMIC.fullmatch(request["target"])
                or re.search(
                    r"\b(?:sobre|respecto al?|acerca de) (?:este|ese|el) (?:bloque|texto|documento)\b", unquoted
                )
            )
        ):
            text = visible_text() or (previous_answer() if previous_answer else "")
            if text:
                return self._research_document(message, text, plan_topics, review_sources, integrative=integrative)
            return self._failure("external_research_block_unavailable", "Cargá el texto que querés que investigue.")
        else:
            query = self._standalone_query(clean, unquoted, message)

        request = self._search_clause(self.normalized_key(query))
        target = request["target"] if request else ""
        subject_first = _NEWS_SUBJECT_FIRST.search(target)
        is_news = bool(_NEWS_TOPIC.match(target) or subject_first)
        today = is_news and bool(_TODAY.search(target))
        if is_news:
            if subject_first:
                original_subject = _NEWS_SUBJECT_FIRST.search(query)
                news_topic = (original_subject or subject_first)[1]
            elif request["topic"]:
                news_topic = " ".join((request["topic"], _NEWS_QUERY.sub("", target, count=1)))
            else:
                news_topic = _NEWS_QUERY.sub("", query, count=1)
            query = re.sub(rf"\b(?:{_WEB_LOCATION})\b", "", _TODAY.sub("", news_topic), flags=re.I)
            query = query.strip(" .,:;!?") or "noticias"
        if not query:
            answer = "Decime qué tema querés que busque en internet."
            return ExternalResearchResult(
                False, answer=answer, spoken_answer=answer, detail="external_research_topic_required"
            )
        if is_news:
            result = self.bridge.research_news(query, today=today)
            if today and news_queries and review_news_sources and (
                result.sources or result.detail == "searxng_no_results"
            ):
                return self._review_daily_news(query, result, news_queries, review_news_sources)
        elif _ACADEMIC.search(target):
            if re.search(rf"\b(?:{_TOPIC_INTRO})\s+.+", target):
                academic_subject = re.search(rf"\b(?:{_TOPIC_INTRO})\s+(.+)", query, re.I)
                if academic_subject:
                    query = academic_subject[1].strip(" .,:;!?")
            result = self.bridge.research_academic(query)
        else:
            result = self.bridge.research(query, snapshot={})
        return self._with_public_sources(result, query)

    def _with_public_sources(self, result: ExternalResearchResult, query: str) -> ExternalResearchResult:
        return replace(
            result,
            query=result.query or query,
            sources=[
                {**source, "query": result.query or query, "provider": result.provider}
                for source in self.web_sources(result.sources)
            ],
        )

    def _review_daily_news(
        self,
        topic: str,
        original: ExternalResearchResult,
        plan: Callable[[str], str],
        review: Callable[[str, list[dict]], str],
    ) -> ExternalResearchResult:
        queries = [topic]
        for query in self._json_list(plan(topic), "queries") or []:
            if isinstance(query, str):
                query = " ".join(query.split())
                if 2 <= len(query.split()) <= 8 and len(query) <= 100 and re.fullmatch(r"[\w -]+", query):
                    if all(self.normalized_key(query) != self.normalized_key(item) for item in queries):
                        queries.append(query)
            if len(queries) == 3:
                break
        results = [original]
        if len(queries) > 1:
            with ThreadPoolExecutor(max_workers=len(queries) - 1) as pool:
                searches = [
                    pool.submit(copy_context().run, self.bridge.research_news, query, today=True)
                    for query in queries[1:]
                ]
                results.extend(search.result() for search in searches)
        sources = {}
        for result, query in zip(results, queries, strict=True):
            for source in self.web_sources(result.sources):
                if source.get("published_date"):
                    sources.setdefault(source["url"], {**source, "query": result.query or query, "provider": result.provider})
        entries = list(sources.values())[:15]
        recorded_query = " | ".join(queries)
        if not entries:
            return replace(original, query=recorded_query)
        evidence = [
            {"id": i, "title": source["title"], "excerpt": source["note"]}
            for i, source in enumerate(entries, 1)
        ]
        choice = self._json_list(review(topic, evidence), "relevant")
        if choice is None:
            return replace(original, query=recorded_query)
        indexes = dict.fromkeys(i - 1 for i in choice if type(i) is int and 1 <= i <= len(entries))
        selected = [{**entries[i], "topic": topic} for i in list(indexes)[:5]]
        if not selected:
            answer = f"Busqué noticias de hoy sobre {topic}. Los resultados con fecha no eran suficientemente pertinentes."
            return replace(
                original, ok=False, answer=answer, spoken_answer=answer, query=recorded_query,
                detail="external_research_news_no_relevant_results", sources=[],
            )
        headlines = [f"- {source['title']} ({source['published_date']})" for source in selected]
        summary = f"Encontré {len(selected)} noticia{'s' if len(selected) != 1 else ''} con fecha de hoy sobre {topic}."
        return replace(
            original,
            ok=True,
            detail="external_research_ok",
            query=recorded_query,
            answer="\n".join((summary, *headlines, "Son resultados del buscador. Podés pedirme que abra una noticia.")),
            spoken_answer=" ".join((summary, *(source["title"] for source in selected[:3]))),
            summary=summary,
            findings=headlines,
            sources=selected,
        )

    @staticmethod
    def _block_reference(clean: str, unquoted: str) -> tuple[int, str] | None:
        block = _NUMBERED_BLOCK.search(unquoted)
        if not block:
            return None
        number = block["number"]
        if number is None:
            ordinal = {"primer": "primero", "tercer": "tercero"}.get(block["ordinal"], block["ordinal"])
            block_number = _ORDINALS.index(ordinal) + 1
        else:
            block_number = int(number) if number.isdecimal() else _CARDINALS.index(number) + 1
        document = _BLOCK_DOCUMENT.match(clean[block.end() :])
        selector = document[1].strip(" .,:;!?\"'«»“”") if document else ""
        return block_number, selector

    def _standalone_query(self, clean: str, unquoted: str, message: str) -> str:
        request = self._search_clause(unquoted)
        if request and not request["topic"] and _GENERIC_WEB_TARGET.fullmatch(request["target"]):
            return ""
        if re.search(
            rf"\b(?:{_TOPIC_INTRO})\s+(?:lo que (?:dice|explica|plantea)(?: en)?|el contenido de)\s+"
            r"(?:(?:el|la|este|esta|ese|esa)\s+)?(?:(?:primer[ao]?|segunda|tercera|ultima)\s+)?"
            r"(?:pagina|bloque|documento|archivo|pdf|texto)\b",
            unquoted,
        ):
            return ""
        # Inspect a masked copy to find the clause, then keep accents and quoted
        # search terms from that span in the original transcription.
        masked = _QUOTED.sub(lambda match: " " * len(match[0]), message)
        start = 0
        for boundary in (*_CLAUSE_BOUNDARY.finditer(masked), None):
            end = boundary.start() if boundary is not None else len(message)
            clause = _REQUEST.match(self.normalized_key(masked[start:end]))
            if clause and self._is_web_clause(clause):
                return message[start:end].strip()
            if boundary is not None:
                start = boundary.end()
        return message

    @staticmethod
    def _failure(detail: str, answer: str) -> ExternalResearchResult:
        return ExternalResearchResult(False, answer=answer, spoken_answer=answer, detail=detail)

    def _topic_words(self, text: str) -> set[str]:
        return {
            word[:6]
            for word in re.findall(r"[^\W\d_]{4,}", self.normalized_key(text))
            if word not in _QUERY_STOP_WORDS and not _ACADEMIC.fullmatch(word)
        }

    @staticmethod
    def _json_object(response: str) -> dict:
        response = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip(), flags=re.IGNORECASE)
        try:
            payload = json.loads(response)
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _json_list(cls, response: str, key: str) -> list | None:
        items = cls._json_object(response).get(key)
        return items if isinstance(items, list) else None

    def _research_document(
        self,
        message: str,
        passage: str,
        planner: Callable[[str, str], str],
        review: Callable[[list[dict]], str] | None,
        *,
        integrative: bool = False,
        supporting_passage: str = "",
    ) -> ExternalResearchResult:
        started = time.perf_counter()
        passage = passage[:8000] if supporting_passage else passage[:12000]
        support = supporting_passage[:4000]
        request = message[:1500]
        if integrative:
            request += "\nUna sola publicación debe vincular los ejes. Prepará variantes que crucen esos ejes y devolvé también los criterios de cobertura."
        material = f"{passage}\n\nPasaje de origen de la lista:\n{support}" if support else passage
        plan = self._json_object(planner(request, material))
        candidates = plan.get("topics")
        topics = []
        passage_words = self._topic_words(passage)
        criteria = []
        if integrative:
            raw_criteria = plan.get("criteria")
            for item in raw_criteria[:5] if isinstance(raw_criteria, list) else []:
                if not isinstance(item, str):
                    continue
                label = " ".join(item.split())
                if 3 <= len(label) <= 120 and label not in criteria and self._topic_words(label) & passage_words:
                    criteria.append(label)
            if len(criteria) < 2:
                return self._failure(
                    "external_research_plan_unavailable",
                    "No pude identificar con suficiente claridad los ejes que el artículo tendría que reunir.",
                )
        requested_years = set(re.findall(r"\b(?:19|20)\d{2}\b", message.split("\n", 1)[0]))
        if isinstance(candidates, list):
            for candidate in candidates[:3]:
                if not isinstance(candidate, dict):
                    continue
                query = candidate.get("query")
                label = candidate.get("topic")
                if not isinstance(query, str) or not isinstance(label, str):
                    continue
                query = " ".join(query.split())
                missing_years = requested_years.difference(query.split())
                if missing_years:
                    query = " ".join((query, *sorted(missing_years)))
                label = " ".join(label.split())
                if (
                    5 <= len(query) <= 140
                    and 3 <= len(label) <= 100
                    and 2 <= len(query.split()) <= 12
                    and re.fullmatch(r"[\w -]+", query)
                    and self._topic_words(query) & passage_words
                    and all(old_query != query and old_label != label for old_label, old_query in topics)
                ):
                    topics.append((label, query))
        if not topics:
            return self._failure(
                "external_research_plan_unavailable",
                "No pude identificar términos confiables para buscar a partir del pasaje. Decime qué concepto querés investigar.",
            )
        with ThreadPoolExecutor(max_workers=len(topics)) as pool:
            searches = [pool.submit(copy_context().run, self.bridge.research_academic, query) for _, query in topics]
            results = [search.result() for search in searches]

        candidates = []
        evidence = []
        for (topic, query), result in zip(topics, results, strict=True):
            terms = self._topic_words(query) - _BROAD_SOURCE_TERMS
            if not result.ok or not terms:
                continue
            for source in self.web_sources(result.sources)[:5]:
                matched = terms & self._topic_words(f"{source['title']} {source['note']}")
                if not matched:
                    continue
                candidate = {**source, "topic": topic, "query": result.query or query, "provider": result.provider}
                if criteria:
                    candidate["model"] = result.model
                candidates.append(candidate)
                evidence.append(
                    {
                        "id": len(candidates),
                        "topic": topic,
                        "query": query,
                        "title": source["title"],
                        "excerpt": source["note"],
                    }
                )

        if criteria:
            return self._review_integrative_sources(topics, results, candidates, evidence, criteria, review, started)

        selected = self._json_list(review(evidence), "relevant") if evidence and review is not None else None
        primary = next((result for result in results if result.ok), results[0])
        recorded = {
            "provider": primary.provider,
            "model": primary.model,
            "query": " | ".join(query for _, query in topics),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        if evidence and selected is None:
            return replace(
                self._failure(
                    "external_research_review_unavailable",
                    "El buscador devolvió resultados, pero no pude comprobar su relación con el texto.",
                ),
                **recorded,
            )
        source_ids = {item for item in selected or [] if type(item) is int and 1 <= item <= len(candidates)}
        sources = []
        counts: dict[str, int] = {}
        seen_urls = set()
        seen_titles = set()
        for index, source in enumerate(candidates, 1):
            topic = source["topic"]
            title = self.normalized_key(source["title"]).rstrip(".")
            if (
                index not in source_ids
                or source["url"] in seen_urls
                or title in seen_titles
                or counts.get(topic, 0) >= 2
            ):
                continue
            sources.append(source)
            seen_urls.add(source["url"])
            if title != "fuente":
                seen_titles.add(title)
            counts[topic] = counts.get(topic, 0) + 1

        findings = []
        for (topic, _), result in zip(topics, results, strict=True):
            count = counts.get(topic, 0)
            if count:
                findings.append(f"{topic}: {count} fuente{'s' if count != 1 else ''}")
            elif result.ok:
                findings.append(f"{topic}: sin coincidencias suficientes")
            else:
                findings.append(f"{topic}: el buscador no devolvió resultados")
        if not sources:
            if all(not result.ok and result.detail != "searxng_no_results" for result in results):
                return replace(primary, **recorded)
            return replace(
                self._failure(
                    "external_research_no_relevant_sources",
                    "Busqué por los ejes del pasaje, pero no encontré fuentes académicas con una relación suficientemente clara.",
                ),
                **recorded,
            )

        summary = (
            "Encontré una fuente académica candidata."
            if len(sources) == 1
            else f"Encontré {len(sources)} fuentes académicas candidatas."
        ) + " La relación se evaluó por sus títulos y extractos."
        lines = [summary, *findings]
        for source in sources:
            lines.append(f"- {source['topic']} · {source['title']}\n  {source['note']}\n  {source['url']}")
        return ExternalResearchResult(
            True,
            answer="\n".join(lines),
            spoken_answer=f"{summary} Dejo los enlaces en pantalla.",
            detail="external_research_ok",
            summary=summary,
            findings=findings,
            sources=sources,
            **recorded,
        )

    def _review_integrative_sources(
        self,
        topics: list[tuple[str, str]],
        results: list[ExternalResearchResult],
        candidates: list[dict],
        evidence: list[dict],
        criteria: list[str],
        review: Callable[[list[dict]], str] | None,
        started: float,
    ) -> ExternalResearchResult:
        primary = next((result for result in results if result.ok), results[0])
        query = " | ".join(query for _, query in topics)
        reviewed = None
        if evidence and review is not None:
            evidence[0]["criteria"] = criteria
            reviewed = self._json_list(review(evidence), "coverage")
        recorded = {
            "provider": primary.provider,
            "model": primary.model,
            "query": query,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        if evidence and reviewed is None:
            return replace(
                self._failure(
                    "external_research_review_unavailable",
                    "Encontré publicaciones, pero no pude comprobar qué ejes relaciona cada una.",
                ),
                **recorded,
            )

        best: tuple[dict, set[int]] | None = None
        for entry in reviewed or []:
            if not isinstance(entry, dict):
                continue
            index, covered = entry.get("id"), entry.get("criteria")
            if type(index) is not int or not 1 <= index <= len(candidates) or not isinstance(covered, list):
                continue
            source = candidates[index - 1]
            source_text = self.normalized_key(f"{source['title']} {source['note']}")
            valid = set()
            for match in covered:
                if not isinstance(match, dict):
                    continue
                criterion, quotation = match.get("index"), match.get("evidence")
                if type(criterion) is not int or not 1 <= criterion <= len(criteria) or not isinstance(quotation, str):
                    continue
                literal = self.normalized_key(quotation).strip(" .…")
                terms = self._topic_words(criteria[criterion - 1]) - _BROAD_SOURCE_TERMS
                if 5 <= len(literal) <= 200 and literal in source_text and terms & self._topic_words(literal):
                    valid.add(criterion)
            if valid and (best is None or len(valid) > len(best[1])):
                best = source, valid

        if best is None:
            if all(not result.ok and result.detail != "searxng_no_results" for result in results):
                return replace(primary, **recorded)
            return replace(
                self._failure(
                    "external_research_no_integrative_source",
                    "Busqué un artículo que reuniera estos ejes. Los títulos y extractos recuperados no alcanzan para confirmar uno.",
                ),
                **recorded,
            )

        source, covered = best
        recorded["provider"] = source.get("provider") or primary.provider
        recorded["model"] = source.get("model") or primary.model
        confirmed = [criterion for i, criterion in enumerate(criteria, 1) if i in covered]
        missing = [criterion for i, criterion in enumerate(criteria, 1) if i not in covered]
        summary = (
            "Encontré una publicación cercana que cubre parte de lo pedido."
            if missing
            else "Encontré una publicación candidata que vincula los ejes pedidos."
        )
        findings = [f"Ejes respaldados por el título o extracto: {', '.join(confirmed)}."]
        if missing:
            findings.append(f"Cobertura pendiente de confirmar: {', '.join(missing)}.")
        return ExternalResearchResult(
            True,
            answer="\n".join((summary, source["title"], *findings, source["note"], source["url"])),
            spoken_answer=f"{summary} {findings[0]} Dejo el enlace en pantalla.",
            detail="external_research_partial_coverage" if missing else "external_research_ok",
            summary=summary,
            findings=findings,
            sources=[
                {**source, "topic": "Artículo con cobertura parcial" if missing else "Artículo que vincula los ejes"}
            ],
            **recorded,
        )

    @staticmethod
    def web_sources(sources: list[dict]) -> list[dict]:
        links = []
        for source in sources[:10]:
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or "").strip()
            try:
                parsed = urlsplit(url)
                safe = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
            except ValueError:
                safe = False
            if not safe or any(char.isspace() or ord(char) < 32 for char in url) or len(url) > 4096:
                continue
            links.append(
                {
                    "title": " ".join(str(source.get("title") or "Fuente").split())[:200],
                    "url": url,
                    "note": " ".join(str(source.get("note") or "").split())[:480],
                    "topic": " ".join(str(source.get("topic") or "").split())[:100],
                    **{
                        key: " ".join(source[key].split())[:240]
                        for key in ("query", "provider", "engine", "published_date")
                        if isinstance(source.get(key), str) and source[key].strip()
                    },
                }
            )
        return links

    def _query_from_text(self, source: str) -> str:
        excerpt = source[:1500]
        if source[1500:1501] and not source[1500].isspace():
            excerpt = re.sub(r"\S+$", "", excerpt)
        text = re.sub(r"https?://\S+|www\.\S+|\S+@\S+", " ", excerpt, flags=re.IGNORECASE)
        terms: dict[str, str] = {}
        for word in re.findall(r"[^\W\d_]+", text):
            key = self.normalized_key(word)
            if not (3 <= len(word) <= 40 or (len(word) == 2 and word.isupper())) or key in _QUERY_STOP_WORDS:
                continue
            terms.setdefault(key, word)
            if len(terms) == 12:
                break
        return " ".join(terms.values())

    @staticmethod
    def public_source_listing(sources: list[dict]) -> str:
        lines = []
        for source in ResearchService.web_sources(sources)[:5]:
            topic = f"{source['topic']} · " if source["topic"] else ""
            lines.append(f"- {topic}{source['title']} ({source['url']})")
        return "Fuentes públicas recuperadas (títulos y enlaces):\n" + "\n".join(lines) if lines else ""

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
