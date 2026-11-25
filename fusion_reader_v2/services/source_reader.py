"""Retrieve public sources for the Laboratory, without browser or host access."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urljoin, urlsplit

from fusion_reader_v2.documents import document_conversion, import_document_bytes, normalize_raw_text, safe_filename
from fusion_reader_v2.laboratory_turn import check_laboratory_turn, turn_cancellation
from fusion_reader_v2.openclaw_bridge import ExternalResearchResult
from fusion_reader_v2.output_reservation import reserve_output_path
from fusion_reader_v2.services.research import ResearchService

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_CHARS = 240_000
_REDIRECTS = {301, 302, 303, 307, 308}

_UN_NEWS_SECTION = "https://news.un.org/es/news/topic/peace-and-security"


class SourceError(ValueError):
    """A user-facing failure to read or save a source."""


def _public_connection(url: str, *, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").encode("idna").decode("ascii")
        default_port = {"http": 80, "https": 443}[parsed.scheme]
        port = parsed.port or default_port
    except (KeyError, ValueError, UnicodeError):
        raise SourceError("El enlace tiene que ser una página pública HTTP o HTTPS.") from None
    if not host or parsed.username is not None or port != default_port or "%" in host:
        raise SourceError("El enlace tiene que ser una página pública HTTP o HTTPS.")
    if any(ord(char) < 33 for char in url) or "\\" in url or len(url) > 4096:
        raise SourceError("El enlace contiene caracteres que Panda no puede usar.")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if isinstance(address, ipaddress.IPv6Address):
            if address.sixtofour or address.teredo or address.is_site_local:
                raise SourceError("Panda solo puede abrir direcciones públicas.")
            address = address.ipv4_mapped or address
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise SourceError("Panda solo puede abrir direcciones públicas.")
    if not addresses:
        raise SourceError("No pude resolver el sitio de la fuente.")

    def connect(*_args, **_kwargs):
        # Dial the vetted numeric addresses, while the HTTP Host and TLS
        # certificate stay bound to the original hostname.
        last_error: OSError | None = None
        deadline = time.monotonic() + timeout
        for family, kind, protocol, _, endpoint in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                with socket.socket(family, kind, protocol) as sock:
                    sock.settimeout(remaining)
                    sock.connect(endpoint)
                    return sock.dup()
            except OSError as exc:
                last_error = exc
        raise last_error or TimeoutError("source_connection_timeout")

    connection = (
        http.client.HTTPSConnection(host, timeout=timeout, context=ssl.create_default_context())
        if parsed.scheme == "https"
        else http.client.HTTPConnection(host, timeout=timeout)
    )
    connection._create_connection = connect
    target = quote(parsed.path or "/", safe="/:@!$&'()*+,;=-._~%")
    if parsed.query:
        target += "?" + quote(parsed.query, safe="/?:@!$&'()*+,;=-._~%")
    return connection, target


def fetch_public_source(url: str) -> tuple[str, str, bytes]:
    deadline = time.monotonic() + 18
    try:
        for _ in range(5):
            check_laboratory_turn()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("source_deadline")
            connection, target = _public_connection(url, timeout=min(remaining, 8))
            try:
                connection.request(
                    "GET",
                    target,
                    headers={
                        "User-Agent": "Panda-Fusion/2.0",
                        "Accept": "text/html,application/pdf",
                        "Accept-Encoding": "identity",
                    },
                )
                with connection.getresponse() as response:
                    if response.status in _REDIRECTS:
                        location = response.getheader("Location")
                        if not location:
                            break
                        url = urljoin(url, location)
                        continue
                    if response.status in {401, 402, 403}:
                        raise SourceError(
                            "El sitio restringió el acceso. Puedo trabajar con una versión pública o con un PDF que cargues."
                        )
                    if response.status != 200:
                        raise SourceError(f"El sitio respondió con un error HTTP {response.status}.")
                    if response.getheader("Content-Encoding", "identity").lower() != "identity":
                        raise SourceError("El sitio devolvió un formato de transferencia que Panda no puede leer.")
                    if response.length is not None and response.length > MAX_SOURCE_BYTES:
                        raise SourceError("La fuente supera el tamaño que admite el Laboratorio.")
                    chunks = bytearray()
                    while True:
                        check_laboratory_turn()
                        if time.monotonic() >= deadline:
                            raise TimeoutError("source_deadline")
                        data = response.read1(64 * 1024)
                        if not data:
                            break
                        chunks.extend(data)
                        if len(chunks) > MAX_SOURCE_BYTES:
                            raise SourceError("La fuente supera el tamaño que admite el Laboratorio.")
                    return url, response.getheader("Content-Type", ""), bytes(chunks)
            finally:
                connection.close()
    except (OSError, http.client.HTTPException) as exc:
        raise SourceError("No pude recuperar el contenido de la fuente desde su servidor.") from exc
    raise SourceError("El enlace redirigió demasiadas veces o no indicó una página final.")


@dataclass(frozen=True)
class _ConversionDeadline:
    end: float

    def is_set(self) -> bool:
        cancel = turn_cancellation.get()
        return time.monotonic() >= self.end or (cancel is not None and cancel.is_set())


class _Article(HTMLParser):
    _BODY_TAGS = {"p", "h1", "h2", "h3", "blockquote", "li", "pre"}
    _IGNORE_TAGS = {"script", "style", "template", "noscript", "nav", "aside", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.body: list[str] = []
        self.main: list[str] = []
        self.article: list[str] = []
        self.article_count = 0
        self.article_times: set[str] = set()
        self.main_times: set[str] = set()
        self.heading: list[str] = []
        self._regions: list[str] = []
        self._ignore: list[str] = []
        self._part: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._IGNORE_TAGS:
            self._ignore.append(tag)
        if self._ignore:
            return
        if tag == "time" and dict(attrs).get("datetime"):
            value = str(dict(attrs)["datetime"])
            if self._regions:
                self.main_times.add(value)
            if "article" in self._regions:
                self.article_times.add(value)
        if tag == "meta":
            fields = dict(attrs)
            key = fields.get("name") or fields.get("property")
            if key and fields.get("content"):
                self.meta.setdefault(key.lower(), str(fields["content"]))
        if tag == "article" and "article" not in self._regions:
            self.article_count += 1
        if tag in {"main", "article"}:
            self._regions.append(tag)
        if tag in self._BODY_TAGS:
            self._finish_part()
            self._part = tag

    def handle_endtag(self, tag: str) -> None:
        if self._ignore:
            if self._ignore[-1] == tag:
                self._ignore.pop()
            return
        if tag == self._part:
            self._finish_part()
        if self._regions and self._regions[-1] == tag:
            self._finish_part()
            self._regions.pop()

    def handle_data(self, data: str) -> None:
        if self._part is not None and not self._ignore:
            self._parts.append(data)

    def _finish_part(self) -> None:
        text = " ".join("".join(self._parts).split())
        if text:
            self.body.append(text)
            if self._regions:
                self.main.append(text)
            if "article" in self._regions:
                self.article.append(text)
            if self._part == "h1":
                self.heading.append(text)
        self._part = None
        self._parts.clear()


class _NewsSection(HTMLParser):
    def __init__(self, url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.url = url
        self.items: list[dict] = []
        self.depth = 0
        self.headline: tuple[str, str] | None = None
        self.published = ""
        self.anchor = ""
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag == "article":
            self.depth += 1
        if self.depth != 1:
            return
        if tag == "time" and fields.get("datetime"):
            try:
                self.published = date.fromisoformat(fields["datetime"][:10]).isoformat()
            except ValueError:
                pass
        if tag == "a" and not self.headline:
            url = urljoin(self.url, fields.get("href") or "")
            parsed = urlsplit(url)
            if parsed.scheme == "https" and parsed.netloc == "news.un.org" and re.fullmatch(
                r"/es/story/\d{4}/\d{2}/\d+", parsed.path
            ):
                self.anchor = f"https://news.un.org{parsed.path}"

    def handle_data(self, data: str) -> None:
        if self.anchor:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor:
            title = " ".join("".join(self.parts).split())
            if title:
                self.headline = self.anchor, title
            self.anchor = ""
            self.parts.clear()
        if tag == "article" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                if self.headline:
                    url, title = self.headline
                    self.items.append({"url": url, "title": title, "published_date": self.published})
                self.headline = None
                self.published = ""


def public_international_headlines(topic: str, *, today: bool) -> ExternalResearchResult | None:
    if not re.search(
        r"\b(?:geopolitic\w*|relaciones internacionales|noticias internacionales|noticias del mundo|paz y seguridad)\b",
        ResearchService.normalized_key(topic),
    ):
        return None
    try:
        url, mime, content = fetch_public_source(_UN_NEWS_SECTION)
        if urlsplit(url).netloc != "news.un.org" or "text/html" not in mime.lower():
            return None
        parser = _NewsSection(url)
        parser.feed(content.decode("utf-8", errors="replace"))
        parser.close()
    except (SourceError, ValueError):
        return None
    headlines = {item["url"]: item for item in parser.items}
    sources = [
        {**item, "provider": "un_news", "engine": "Noticias ONU · Paz y seguridad", "query": topic}
        for item in headlines.values()
        if not today or item["published_date"] == date.today().isoformat()
    ][:5]
    if not sources:
        return None
    summary = f"Encontré {len(sources)} titular{'es' if len(sources) != 1 else ''} en la sección Paz y seguridad de Noticias ONU."
    lines = [
        f"- {source['title']}" + (f" ({source['published_date']})" if source["published_date"] else "")
        for source in sources
    ]
    return ExternalResearchResult(
        True,
        answer="\n".join((summary, *lines, f"Sección consultada: {url}")),
        spoken_answer=" ".join((summary, *(source["title"] for source in sources[:3]))),
        detail="external_research_headlines",
        provider="un_news",
        model="reader_source",
        query=topic,
        summary=summary,
        sources=sources,
    )


@dataclass(frozen=True)
class OpenedSource:
    requested_url: str
    url: str
    title: str
    text: str
    kind: str
    date: str = ""
    extraction: str = ""
    truncated: bool = False
    pdf: bytes = field(default=b"", repr=False)
    digest: str = ""

    def link(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "published_date": self.date}


def split_search_and_read(text: str) -> tuple[str, str]:
    # The second clause only authorizes reading from the resulting shortlist.
    for match in re.finditer(r"[,;]?\s+y\s+(?:(?:luego|después|despues)\s+)?", text, re.I):
        follow_up = text[match.end() :].strip()
        if source_action(follow_up) == "read":
            return text[: match.start()].rstrip(), follow_up
    return text, ""


def source_action(text: str) -> str:
    clean = ResearchService.normalized_key(text)
    if re.search(
        r"\b(?:no|nunca|jamas|sin)\s+(?:(?:lo|la|el|quiero|que|te)\s+){0,4}(?:abr\w+|lee\w*|baj\w+|descarg\w+|guard\w+|incorpor\w+|agreg\w+)",
        clean,
    ):
        return ""
    prefix = (
        r"^(?:(?:ok|bien|bueno|dale|ahora|escuchame|mira|por favor|perfecto|esta bien|a ver|si)[,;:.!?]*\s+)*"
        r"(?:(?:podes|podrias|puedes|quiero que|necesito que|te pido que|vamos a)\s+)?"
    )
    save = r"(?:descarg|baj|guard|incorpor|agreg|sum)"
    reference = re.search(
        r"\b(?:documentos?\s+(?:de\s+)?(?:apoyo|consulta|referencia)|como (?:apoyo|consulta|referencia))\b", clean
    )
    download = re.search(r"\b(?:descarg\w*|baj\w*|guard\w*\s+(?:el pdf\s+)?(?:en|a)\s+descargas)\b", clean)
    if re.match(prefix + save + r"\w*\b", clean) and (reference or download):
        return "save_reference" if reference and download else ("reference" if reference else "save")
    external_object = r"\b(?:fuente|enlace|url|link|resultado|titular|noticia|paper|articulo|pdf|publicacion|doi|pagina)\b"
    if re.match(
        prefix + r"(?:a ver|mostra(?:me)?|veamos) (?:el|la) "
        r"(?:(?:primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|quint[oa]|ultim[oa]) )?"
        r"(?:resultado|titular|noticia|fuente|enlace)\b",
        clean,
    ):
        return "read"

    if re.match(prefix + r"(?:abri\w*|abr[ea]\w*|entra\w*|lee\w*|leas?|consulta\w*)\b", clean):
        if (
            re.search(r"https?://", clean)
            or re.search(external_object, clean)
            or re.match(prefix + r"(?:abrilo|abrila|leelo|leela)\b", clean)
            or re.search(r"\b(?:el|la) (?:primer[oa]|segund[oa]|tercer[oa]|cuart[oa]|quint[oa]|ultim[oa])\W*$", clean)
        ):
            if not re.search(
                r"\b(?:el|este|nuestro) (?:pdf|documento|bloque)(?: (?:actual|cargado|del lector)\b|\W*$)",
                clean,
            ):
                return "read"
    if re.match(prefix + r"(?:de cuando|de que fecha|cuando se publico|de que trata|que dice)\b", clean):
        if re.search(external_object, clean):
            return "read"
    return ""


def source_candidates(message: str, sources: list[dict], last_url: str = "") -> list[dict]:
    clean = ResearchService.normalized_key(message)
    urls = re.findall(r"https?://[^\s<>]+", message, re.I)
    if urls:
        return [{"title": "", "url": url.rstrip(".,;)!?")} for url in dict.fromkeys(urls)]
    combined = re.search(
        r"\b(?:resultado|titular|enlace|fuente|noticia|articulo|paper|pdf)\s*(?:numero|nro|#)?\s*(\d{1,2})\b", clean
    )
    ordinal = re.search(
        r"\b(primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|quint[oa]|ultim[oa])"
        r"(?=\s+(?:resultado|titular|enlace|fuente|noticia|articulo|paper|pdf)\b|\s+de (?:la lista|los resultados)\b|\W*$)",
        clean,
    )
    if combined or ordinal:
        index = (
            int(combined[1]) - 1
            if combined
            else (
                len(sources) - 1
                if ordinal[1].startswith("ultim")
                else next(
                    i
                    for i, value in enumerate(("primer", "segund", "tercer", "cuart", "quint"))
                    if ordinal[1].startswith(value)
                )
            )
        )
        return sources[index : index + 1] if 0 <= index < len(sources) else []
    matched_titles = [
        source
        for source in sources
        if len(key := ResearchService.normalized_key(source.get("title", ""))) > 8 and key in clean
    ]
    if matched_titles:
        return matched_titles
    if last_url and (
        not sources
        or re.search(
            r"\b(?:abri(?:lo|la)|lee(?:lo|la)|descarga(?:lo|la)|baja(?:lo|la)|guarda(?:lo|la)|incorpora(?:lo|la)|agrega(?:lo|la)|suma(?:lo|la)|esta|ese|esa|este)\b",
            clean,
        )
    ):
        return [next((source for source in sources if source.get("url") == last_url), {"title": "", "url": last_url})]
    return sources


def _publication_date(page: _Article) -> str:
    keys = ["article:published_time", "citation_publication_date", "citation_date", "datepublished", "dc.date"]
    values = [page.meta.get(key, "") for key in keys]
    times = page.article_times or page.main_times
    if len(times) == 1 and page.article_count <= 1 and len(page.heading) == 1:
        values.extend(times)
    for value in values:
        try:
            return date.fromisoformat(value[:10].replace("/", "-")).isoformat()
        except ValueError:
            continue
    return ""


def read_source(source: dict) -> OpenedSource:
    requested_url = source["url"]
    url, mime, data = fetch_public_source(requested_url)
    title = str(source.get("title") or "")[:240]
    display_date = ""
    if not data.startswith(b"%PDF-"):
        if "html" not in mime.lower():
            raise SourceError("La fuente no devolvió un artículo HTML ni un PDF válido.")
        charset = re.search(r"charset=([\w.-]+)", mime, re.I)
        parser = _Article()
        try:
            page = data.decode(charset[1] if charset else "utf-8", errors="replace")
        except LookupError:
            page = data.decode("utf-8", errors="replace")
        parser.feed(page)
        parser.close()
        display_date = _publication_date(parser)
        pdf_url = parser.meta.get("citation_pdf_url")
        if pdf_url:
            url, _mime, data = fetch_public_source(urljoin(url, pdf_url))
            if not data.startswith(b"%PDF-"):
                raise SourceError("La ficha enlaza un archivo que no llegó como PDF válido.")
            title = parser.meta.get("citation_title", title)[:240]
        else:
            title = (parser.meta.get("og:title") or (parser.heading or [title or urlsplit(url).hostname])[0])[:240]
            body = "\n\n".join(parser.article or parser.main or parser.body)
            if parser.article_count > 1 or (
                not display_date
                and parser.article_count != 1
                and not parser.meta.get("citation_title")
                and parser.meta.get("og:type") != "article"
            ):
                raise SourceError(
                    "El enlace parece una portada o un listado. Necesito el enlace de un artículo específico."
                )
            if len(body) < 250:
                raise SourceError("Pude abrir el sitio, pero no recuperar el cuerpo del artículo.")
            return OpenedSource(
                requested_url,
                url,
                title,
                body[:MAX_SOURCE_CHARS],
                "html",
                display_date,
                truncated=len(body) > MAX_SOURCE_CHARS,
            )
    title = title or Path(urlsplit(url).path).stem or "Publicación"
    deadline = _ConversionDeadline(time.monotonic() + 45)
    with document_conversion(deadline):
        imported = import_document_bytes("fuente.pdf", data, mime="application/pdf")
    if deadline.is_set():
        raise SourceError("El PDF tardó demasiado en convertirse. Podés cargarlo manualmente desde el lector.")
    full_text = normalize_raw_text(imported.text)
    if not full_text.strip():
        raise SourceError("Pude recuperar el PDF, pero no extraer texto legible.")
    return OpenedSource(
        requested_url,
        url,
        title,
        full_text,
        "pdf",
        date=display_date,
        extraction=imported.detail,
        truncated=False,
        pdf=data,
        digest=hashlib.sha256(data).hexdigest(),
    )


class SourceReader:
    def __init__(self) -> None:
        self._cache: OrderedDict[tuple[str, str], OpenedSource] = OrderedDict()
        self._lock = Lock()

    def open(self, scope: str, source: dict) -> OpenedSource:
        key = scope, source["url"]
        with self._lock:
            opened = self._cache.get(key)
            if opened is not None:
                self._cache.move_to_end(key)
                return opened
        try:
            opened = read_source(source)
        except (ValueError, OSError) as exc:
            if isinstance(exc, SourceError):
                raise
            raise SourceError("Pude recuperar la fuente, pero falló su extracción de texto.") from exc
        with self._lock:
            self._cache[key] = opened
            while len(self._cache) > 4:
                self._cache.popitem(last=False)
        return opened

    @staticmethod
    def save_pdf(opened: OpenedSource, root: Path) -> Path:
        if not opened.pdf:
            raise SourceError(
                "La fuente abierta es una página. Para guardarla como PDF necesito el archivo de la publicación."
            )
        base = safe_filename(opened.title).removesuffix(".pdf")
        filename = base.encode("utf-8")[:150].decode("utf-8", "ignore").strip() or "Publicación"
        reservation = reserve_output_path(root, filename + ".pdf", default_suffix=".pdf")
        staged: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=root, prefix=".panda-source-", delete=False) as temporary:
                staged = Path(temporary.name)
                temporary.write(opened.pdf)
            return reservation.publish(staged)
        finally:
            reservation.cleanup()
            if staged is not None:
                staged.unlink(missing_ok=True)
