"""Cancellation shared by the Laboratory's HTTP, provider and publication stages."""

from __future__ import annotations

import http.client
import socket
from contextlib import contextmanager
from contextvars import ContextVar
from select import POLLERR, POLLHUP, POLLIN, poll
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.parse import urlsplit


class LaboratoryTurnCancelled(Exception):
    pass


class ClientDisconnect:
    def __init__(self, connection: socket.socket) -> None:
        self.connection = connection
        self.disconnected = False

    def is_set(self) -> bool:
        if not self.disconnected:
            try:
                ready = poll()
                ready.register(self.connection, POLLIN | POLLHUP | POLLERR)
                # Python applies a socket timeout before recv even with MSG_DONTWAIT.
                # Poll first so an idle, connected client never looks disconnected.
                if ready.poll(0) and not self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT):
                    self.disconnected = True
            except (BlockingIOError, TimeoutError):
                pass
            except OSError:
                self.disconnected = True
        return self.disconnected


turn_cancellation: ContextVar[ClientDisconnect | None] = ContextVar("laboratory_turn_cancellation", default=None)


def check_laboratory_turn() -> None:
    signal = turn_cancellation.get()
    if signal is not None and signal.is_set():
        raise LaboratoryTurnCancelled()


@contextmanager
def laboratory_cancellation(connection: socket.socket):
    token = turn_cancellation.set(ClientDisconnect(connection))
    try:
        yield turn_cancellation.get()
    finally:
        turn_cancellation.reset(token)


def read_turn_response(request, *, timeout, urlopen):
    """Interrupt a private HTTP provider read when the initiating client disconnects."""
    signal = turn_cancellation.get()
    endpoint = urlsplit(request.full_url)
    check_laboratory_turn()
    if signal is None or endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "::1", "localhost"}:
        with urlopen(request, timeout=timeout) as response:
            result = response.read()
        check_laboratory_turn()
        return result
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=timeout)
    finished = Event()
    try:
        connection.connect()
        with connection.sock.dup() as upstream:
            def wait_for_disconnect():
                while not finished.wait(0.05):
                    if signal.is_set():
                        try:
                            upstream.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        return

            watcher = Thread(target=wait_for_disconnect, name="panda-laboratory-disconnect", daemon=True)
            watcher.start()
            try:
                connection.request(request.get_method(), request.selector, request.data, dict(request.header_items()))
                with connection.getresponse() as response:
                    if response.status >= 400:
                        raise HTTPError(request.full_url, response.status, response.reason, response.headers, None)
                    result = response.read()
                check_laboratory_turn()
                return result
            except (OSError, http.client.HTTPException):
                check_laboratory_turn()
                raise
            finally:
                finished.set()
                watcher.join()
    finally:
        connection.close()
