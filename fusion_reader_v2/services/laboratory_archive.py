"""Durable Laboratory transcripts and scoped conversation context."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock

from fusion_reader_v2.openclaw_bridge import ExternalResearchResult
from fusion_reader_v2.services.dialogue import LaboratoryResearch, LaboratoryResearchRequest
from fusion_reader_v2.services.research import ResearchService, WebIntent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS retired (id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources TEXT NOT NULL,
    truncated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS turns_conversation ON turns(conversation_id, id);
CREATE TABLE IF NOT EXISTS contexts (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    document_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY (conversation_id, provider, document_id, mode)
);
"""


@dataclass(frozen=True)
class ArchiveScope:
    session: str
    provider: str
    document_id: str
    mode: str

    def values(self) -> tuple[str, str, str, str]:
        return self.session, self.provider, self.document_id, self.mode


class LaboratoryArchive:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._schema_lock = Lock()
        self._initialized = False

    def _connect(self, *, write: bool = False) -> sqlite3.Connection | None:
        if self.path is None:
            return None
        with self._schema_lock:
            if not self._initialized:
                if write:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    fd = os.open(self.path, os.O_CREAT | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
                    os.close(fd)
                elif not self.path.is_file():
                    return None
                with closing(sqlite3.connect(self.path, timeout=5)) as setup:
                    setup.executescript(_SCHEMA)
                    with setup:
                        setup.execute("BEGIN IMMEDIATE")
                        if "truncated" not in {row[1] for row in setup.execute("PRAGMA table_info(turns)")}:
                            setup.execute("ALTER TABLE turns ADD COLUMN truncated INTEGER NOT NULL DEFAULT 0")
                self._initialized = True
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def record(
        self,
        scope: ArchiveScope,
        question: str,
        answer: str,
        sources: list[dict],
        history: list[dict] | None,
        research: LaboratoryResearch | None,
        *,
        truncated: bool = False,
    ) -> None:
        connection = self._connect(write=True)
        if connection is None:
            return
        title = " ".join(question.split())[:85] or "Conversación"
        with closing(connection), connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT id FROM retired WHERE id=?", (scope.session,)).fetchone() is not None:
                return
            connection.execute(
                """INSERT INTO conversations (id, title, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET updated_at=excluded.updated_at""",
                (scope.session, title, time.time()),
            )
            connection.execute(
                "INSERT INTO turns (conversation_id, question, answer, sources, truncated) VALUES (?, ?, ?, ?, ?)",
                (scope.session, question[:16000], answer[:32000], json.dumps(ResearchService.web_sources(sources)),
                 int(truncated)),
            )
            if history is not None and research is not None:
                state = json.dumps(
                    {
                        "history": history,
                        "pending": asdict(research.pending) if research.pending is not None else None,
                        "previous_request": asdict(research.previous_request) if research.previous_request is not None else None,
                        "last_search": asdict(research.last_search) if research.last_search is not None else None,
                        "opened_url": research.opened_url,
                        "opened_context": research.opened_context,
                        "opened_link": research.opened_link,
                        "opened_digest": research.opened_digest,
                    }
                )
                connection.execute(
                    """INSERT INTO contexts (conversation_id, provider, document_id, mode, state) VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT (conversation_id, provider, document_id, mode)
                       DO UPDATE SET state=excluded.state""",
                    (*scope.values(), state),
                )

    def conversations(self) -> list[dict]:
        connection = self._connect()
        if connection is None:
            return []
        with closing(connection):
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id, title, updated_at FROM conversations ORDER BY updated_at DESC, id"
                )
            ]

    @staticmethod
    def _turn(row: sqlite3.Row) -> dict:
        result = {"question": row["question"], "answer": row["answer"], "sources": json.loads(row["sources"])}
        if row["truncated"]:
            result["truncated"] = True
        return result

    def turns(self, session: str) -> list[dict]:
        connection = self._connect()
        if connection is None:
            return []
        with closing(connection):
            return [
                self._turn(row) for row in connection.execute(
                    "SELECT question, answer, sources, truncated FROM turns WHERE conversation_id=? ORDER BY id",
                    (session,),
                )
            ]

    def turn_page(self, session: str, before: int | None = None, *, limit: int = 30) -> dict:
        connection = self._connect()
        if connection is None:
            return {"turns": [], "next_before": None}
        with closing(connection):
            sql = "SELECT id, question, answer, sources, truncated FROM turns WHERE conversation_id=?"
            args = (session,)
            if before is not None:
                sql += " AND id < ?"
                args += (before,)
            rows = connection.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit + 1)).fetchall()
        page = rows[:limit]
        return {
            "turns": [self._turn(row) for row in reversed(page)],
            "next_before": page[-1]["id"] if len(rows) > limit else None,
        }

    def restore(self, scope: ArchiveScope) -> tuple[list[dict], LaboratoryResearch]:
        empty: tuple[list[dict], LaboratoryResearch] = ([], LaboratoryResearch())
        connection = self._connect()
        if connection is None:
            return empty
        with closing(connection):
            row = connection.execute(
                "SELECT state FROM contexts WHERE conversation_id=? AND provider=? AND document_id=? AND mode=?",
                scope.values(),
            ).fetchone()
        if row is None:
            return empty
        state = json.loads(row["state"])
        last = state.get("last_search")
        return state["history"], LaboratoryResearch(
            pending=self._restore_request(state.get("pending")),
            previous_request=self._restore_request(state.get("previous_request")),
            last_search=ExternalResearchResult(**last) if isinstance(last, dict) else None,
            opened_url=state.get("opened_url", ""),
            opened_context=state.get("opened_context", ""),
            opened_link=state.get("opened_link", {}),
            opened_digest=state.get("opened_digest", ""),
        )

    @staticmethod
    def _restore_request(payload: dict | None) -> LaboratoryResearchRequest | None:
        if payload is None:
            return None
        restored = dict(payload)
        intent = restored.get("web_intent")
        if isinstance(intent, dict):
            restored["web_intent"] = WebIntent(**intent)
        return LaboratoryResearchRequest(**restored)

    def remove(self, session: str) -> None:
        connection = self._connect(write=True)
        if connection is not None:
            with closing(connection), connection:
                connection.execute("INSERT OR IGNORE INTO retired (id) VALUES (?)", (session,))
                connection.execute("DELETE FROM conversations WHERE id=?", (session,))
