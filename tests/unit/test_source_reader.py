from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from fusion_reader_v2.services.source_reader import SourceError, _public_connection, read_source, source_action


class SourceReaderTests(unittest.TestCase):
    def test_news_section_keeps_dated_article_links_from_the_publisher(self) -> None:
        from datetime import date

        from fusion_reader_v2.services.source_reader import public_international_headlines

        section = "https://news.un.org/es/news/topic/peace-and-security"
        story = "/es/story/2026/11/1542201"
        html = f"""<article>
          <time datetime="{date.today().isoformat()}T12:00:00Z"></time>
          <a href="{story}">La noticia del día</a>
          <a href="https://example.com/es/story/2026/11/56">Ver después</a>
          </article><article>
          <a href="/es/story/2026/11/1542198">Una nota sin fecha</a></article>"""
        with patch(
            "fusion_reader_v2.services.source_reader.fetch_public_source",
            return_value=(section, "text/html; charset=utf-8", html.encode()),
        ):
            result = public_international_headlines("geopolítica", today=True)
        self.assertEqual(result.detail, "external_research_headlines")
        self.assertEqual([source["url"] for source in result.sources], ["https://news.un.org" + story])
        self.assertIn("La noticia del día", result.answer)
        self.assertIn(section, result.answer)
        self.assertEqual(source_action("Abrí el primer titular"), "read")

    def test_spoken_source_reference_matches_a_read_but_local_pdf_stays_in_the_reader(self) -> None:
        self.assertEqual(source_action("A ver el primer resultado."), "read")
        self.assertEqual(source_action("A ver, ¿podés entrar a la noticia?"), "read")
        self.assertEqual(source_action("Leé el PDF."), "")
        self.assertEqual(source_action("No descargues el documento de apoyo."), "")

    def test_article_has_body_and_date_even_when_built_without_article_tag(self) -> None:
        url = "https://publisher.example/news/story"
        body = "La noticia explica los hechos con datos verificables. " * 8
        html = f'<main><h1>Una noticia</h1><time datetime="2026-11-22">22 de noviembre</time><p>{body}</p></main>'
        with patch(
            "fusion_reader_v2.services.source_reader.fetch_public_source",
            return_value=(url, "text/html", html.encode()),
        ):
            result = read_source({"url": url})
        self.assertEqual(result.title, "Una noticia")
        self.assertEqual(result.date, "2026-11-22")
        self.assertIn(body.strip(), result.text)

    def test_news_listing_cannot_be_mistaken_for_one_article(self) -> None:
        url = "https://publisher.example/news"
        html = b'<meta property="og:type" content="article">' + b"<article><p>Noticia</p></article>" * 22
        with patch(
            "fusion_reader_v2.services.source_reader.fetch_public_source", return_value=(url, "text/html", html)
        ):
            with self.assertRaisesRegex(SourceError, "portada o un listado"):
                read_source({"url": url})

    def test_public_hostname_cannot_resolve_to_a_local_address(self) -> None:
        mixed_addresses = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with patch("socket.getaddrinfo", return_value=mixed_addresses):
            with self.assertRaisesRegex(SourceError, "direcciones públicas"):
                _public_connection("https://publisher.example/news", timeout=1)

    def test_pdf_metadata_cannot_make_html_into_a_download(self) -> None:
        url = "https://publisher.example/paper"
        html = b'<meta name="citation_pdf_url" content="/download">'
        responses = [(url, "text/html", html), (url + "/pdf", "application/pdf", b"<title>Login</title>")]
        with patch("fusion_reader_v2.services.source_reader.fetch_public_source", side_effect=responses):
            with self.assertRaisesRegex(SourceError, "PDF válido"):
                read_source({"url": url})
