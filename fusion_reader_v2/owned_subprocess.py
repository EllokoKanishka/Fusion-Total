from __future__ import annotations

import logging
import os
import signal
import subprocess
import selectors
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import BinaryIO, cast, Any, Protocol

LOG = logging.getLogger(__name__)
DEFAULT_OUTPUT_LIMIT = 1024 * 1024


class OwnedProcessError(RuntimeError):
    """Stable failure raised when an owned subprocess cannot be reaped."""


class CancelSignal(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True)
class ProcessPolicy:
    timeout: float
    terminate_grace: float = 1.0
    output_limit: int = DEFAULT_OUTPUT_LIMIT
    poll_interval: float = 0.05


def sanitized_command(command: Sequence[os.PathLike[str] | str]) -> str:
    safe: list[str] = []
    redact_next = False
    for raw in command:
        value = os.fspath(raw)
        if redact_next:
            safe.append("<redacted>")
            redact_next = False
        elif value.lower() in {"--token", "--api-key", "--password", "--secret"}:
            safe.append(value)
            redact_next = True
        elif len(value) > 240:
            safe.append(f"<arg:{len(value)} chars>")
        else:
            safe.append(value)
    return " ".join(safe)


def _signal_process(process: subprocess.Popen[Any], sig: int) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        try:
            process.send_signal(sig)
        except ProcessLookupError:
            pass


def terminate_owned_process(process: subprocess.Popen[Any], *, grace: float = 1.0) -> None:
    _signal_process(process, signal.SIGTERM)
    try:
        process.wait(timeout=max(0.01, grace))
        return
    except subprocess.TimeoutExpired:
        _signal_process(process, signal.SIGKILL)
    try:
        process.wait(timeout=max(0.1, grace))
    except subprocess.TimeoutExpired as exc:
        raise OwnedProcessError("owned_process_unreaped") from exc


def run_owned(
    command: Sequence[os.PathLike[str] | str],
    *,
    timeout: float,
    cancel_event: CancelSignal | None = None,
    cwd: os.PathLike[str] | str | None = None,
    env: Mapping[str, str] | None = None,
    input: str | bytes | None = None,
    text: bool = False,
    check: bool = False,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
    terminate_grace: float = 1.0,
    on_start=None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run and always reap a child owned by Fusion, including timeout/cancel paths."""
    if kwargs.pop("shell", False):
        raise ValueError("shell_not_supported")
    kwargs.pop("capture_output", None)
    kwargs.pop("stdout", None)
    kwargs.pop("stderr", None)
    LOG.info("owned subprocess: %s", sanitized_command(command))
    if output_limit < 0:
        raise ValueError("negative_output_limit")
    if cancel_event is not None and cancel_event.is_set():
        raise OwnedProcessError("owned_process_cancelled")
    deadline = time.monotonic() + max(0.01, timeout)
    raw_input = memoryview(input.encode() if isinstance(input, str) else (input or b""))
    process = subprocess.Popen(
        [os.fspath(part) for part in command],
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        **kwargs,
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    cancelled = timed_out = False
    try:
        if on_start is not None:
            on_start(process)
        with selectors.DefaultSelector() as selector:
            for name in ("stdout", "stderr"):
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                if raw_input:
                    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                else:
                    process.stdin.close()
            offset = 0
            while selector.get_map() or process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(0.05, remaining)):
                    stream = cast(BinaryIO, key.fileobj)
                    if key.data == "stdin":
                        try:
                            offset += os.write(stream.fileno(), raw_input[offset : offset + 65536])
                        except BrokenPipeError:
                            offset = len(raw_input)
                        if offset >= len(raw_input):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        data = os.read(stream.fileno(), 65536)
                        if not data:
                            selector.unregister(stream)
                            stream.close()
                        else:
                            buffer = buffers[key.data]
                            # Keep only the requested prefix; drain/discard overflow
                            # while running, with no unbounded temporary on disk.
                            buffer.extend(data[: max(0, output_limit - len(buffer))])
            if cancelled or timed_out:
                terminate_owned_process(process, grace=terminate_grace)
            else:
                process.wait()
    finally:
        try:
            if process.poll() is None:
                terminate_owned_process(process, grace=terminate_grace)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except OSError:
                        LOG.exception("owned subprocess pipe close failed")
    stdout_raw, stderr_raw = bytes(buffers["stdout"]), bytes(buffers["stderr"])
    stdout = stdout_raw.decode(errors="replace") if text else stdout_raw
    stderr = stderr_raw.decode(errors="replace") if text else stderr_raw
    if cancelled:
        raise OwnedProcessError("owned_process_cancelled")
    if timed_out:
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
    result = subprocess.CompletedProcess(command, int(process.returncode or 0), stdout, stderr)
    if check:
        result.check_returncode()
    return result


__all__ = [
    "DEFAULT_OUTPUT_LIMIT",
    "OwnedProcessError",
    "ProcessPolicy",
    "run_owned",
    "sanitized_command",
    "terminate_owned_process",
]
