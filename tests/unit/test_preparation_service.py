from __future__ import annotations

import threading
import time
import unittest

from fusion_reader_v2.reader import Document, ReaderSession
from fusion_reader_v2.services.preparation import PreparationService
from fusion_reader_v2.tts import AudioArtifact


class Voice:
    voice = "female.wav"
    language = "es"


class Cache:
    def get(self, text: str, voice: str, language: str):
        return None


class TTS:
    def health(self) -> dict:
        return {"ok": True}


def make_service(
    session: ReaderSession,
    synthesize,
    *,
    generation=lambda: 1,
) -> PreparationService:
    return PreparationService(
        session=session,
        voice=Voice(),
        cache=Cache(),
        tts=TTS(),
        background_condition=threading.Condition(threading.RLock()),
        background_is_open_locked=lambda: True,
        document_generation=generation,
        before_registration=lambda: None,
        synthesize=synthesize,
        human_error=lambda detail: detail,
    )


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not predicate():
        raise AssertionError("condition was not reached before timeout")


class PreparationServiceTests(unittest.TestCase):
    def test_service_prepares_document_without_facade(self) -> None:
        session = ReaderSession()
        session.load(Document("book", "Book", "one two", ["one", "two"]))
        service = make_service(session, lambda text, voice, language: AudioArtifact(True))
        service.start()
        wait_until(lambda: service.status()["status"] != "running")
        self.assertEqual(service.status()["status"], "done")
        self.assertEqual(service.status()["generated"], 2)
        self.assertEqual(service.active_threads(), ())

    def test_reset_retains_old_worker_until_shutdown_joins_every_generation(self) -> None:
        session = ReaderSession()
        generation = 1
        session.load(Document("old", "Old", "old", ["old"]))
        old_entered = threading.Event()
        old_release = threading.Event()
        new_entered = threading.Event()
        new_release = threading.Event()

        def synthesize(text: str, voice: str, language: str) -> AudioArtifact:
            if text == "old":
                old_entered.set()
                old_release.wait(timeout=2)
            else:
                new_entered.set()
                new_release.wait(timeout=2)
            return AudioArtifact(True)

        service = make_service(session, synthesize, generation=lambda: generation)
        service.start()
        self.assertTrue(old_entered.wait(timeout=1))
        old_worker = service.active_threads()[0]

        service.reset()
        generation = 2
        session.load(Document("new", "New", "new", ["new"]))
        service.start()
        self.assertTrue(new_entered.wait(timeout=1))
        workers = service.active_threads()
        self.assertEqual(len(workers), 2)
        self.assertIn(old_worker, workers)
        self.assertFalse(any(worker.daemon for worker in workers))

        shutdown_waiter = service.begin_shutdown()
        self.assertIsNotNone(shutdown_waiter)
        self.assertTrue(shutdown_waiter.is_alive())
        old_release.set()
        new_release.set()
        shutdown_waiter.join(timeout=2)
        self.assertFalse(shutdown_waiter.is_alive())
        wait_until(lambda: not service.active_threads())
        self.assertEqual(service.status()["doc_id"], "new")
        self.assertEqual(service.status()["status"], "canceled")
        self.assertEqual(service.status()["generated"], 0)

    def test_provider_exception_releases_worker_and_reports_error(self) -> None:
        session = ReaderSession()
        session.load(Document("book", "Book", "one", ["one"]))

        def synthesize(text: str, voice: str, language: str) -> AudioArtifact:
            raise RuntimeError("provider exploded")

        service = make_service(session, synthesize)
        service.start()
        wait_until(lambda: not service.active_threads())
        self.assertEqual(service.status()["status"], "error")
        self.assertEqual(service.status()["message"], "RuntimeError")
        self.assertEqual(service.status()["failed"], 1)

    def test_repeated_reset_cancels_retired_workers_without_losing_references(self) -> None:
        session = ReaderSession()
        generation = 1
        release = threading.Event()
        entered = threading.Event()
        session.load(Document("one", "One", "one", ["one"]))

        def synthesize(text: str, voice: str, language: str) -> AudioArtifact:
            entered.set()
            release.wait(timeout=2)
            return AudioArtifact(True)

        service = make_service(session, synthesize, generation=lambda: generation)
        service.start()
        self.assertTrue(entered.wait(timeout=1))
        worker = service.active_threads()[0]
        service.reset()
        service.reset()
        self.assertIn(worker, service.active_threads())
        waiter = service.begin_shutdown()
        self.assertIsNotNone(waiter)
        release.set()
        waiter.join(timeout=2)
        wait_until(lambda: not service.active_threads())
        self.assertEqual(service.status()["status"], "idle")


if __name__ == "__main__":
    unittest.main()
