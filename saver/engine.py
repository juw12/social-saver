"""Runs the download engine (gallery-dl).

On a PC it runs as a child process. Inside the Android app (Chaquopy) there are no
child processes, so it runs in this process instead: same arguments, same output lines.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
from typing import Callable

_ENGINE_LOCK = threading.Lock()  # gallery-dl keeps global state; one run at a time


def in_process() -> bool:
    """True inside the Android app (it sets SOCIAL_SAVER_IN_PROCESS=1)."""
    return os.environ.get("SOCIAL_SAVER_IN_PROCESS") == "1"


class Stopped(KeyboardInterrupt):
    """Raised from inside the engine when the user presses Stop (gallery-dl stops on KeyboardInterrupt)."""


class LineWriter(io.TextIOBase):
    """A text stream that hands every finished line from the engine's thread to a callback.

    While the engine runs in-process, sys.stdout/sys.stderr point here for the whole app.
    Writes from other threads (the web server, logging) pass straight through to the real
    stream, so they are neither mistaken for engine output nor hit by the Stop request.
    """

    def __init__(self, on_line: Callable[[str], None], should_stop: Callable[[], bool] = lambda: False,
                 passthrough=None):
        super().__init__()
        self._on_line = on_line
        self._should_stop = should_stop
        self._passthrough = passthrough
        self._owner = threading.get_ident()
        self._pending = ""

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:
        return "utf-8"

    def write(self, text: str) -> int:
        if threading.get_ident() != self._owner:
            if self._passthrough is not None:
                self._passthrough.write(text)
            return len(text)
        if self._should_stop():
            raise Stopped()
        self._pending += text
        *lines, self._pending = self._pending.split("\n")
        for line in lines:
            if line.strip():
                self._on_line(line.strip())
        return len(text)

    def flush(self) -> None:
        pass

    def finish(self) -> None:
        if self._pending.strip():
            self._on_line(self._pending.strip())
        self._pending = ""


def run_in_process(args: list[str], on_stdout: Callable[[str], None],
                   on_stderr: Callable[[str], None] | None = None,
                   should_stop: Callable[[], bool] = lambda: False) -> int:
    """Run gallery-dl's main() here with these arguments; returns its exit code."""
    import gallery_dl
    from gallery_dl import config as engine_config

    stdout = LineWriter(on_stdout, should_stop, passthrough=sys.stdout)
    stderr = LineWriter(on_stderr or on_stdout, should_stop, passthrough=sys.stderr)
    root_logger = logging.getLogger()
    with _ENGINE_LOCK:
        handlers_before = list(root_logger.handlers)
        saved = sys.argv, sys.stdout, sys.stderr
        engine_config.clear()  # options from the last run (range, filter) must not leak into this one
        sys.argv = ["gallery-dl", *args]
        sys.stdout, sys.stderr = stdout, stderr
        try:
            exit_code = gallery_dl.main()
        except SystemExit as stop:
            exit_code = stop.code if isinstance(stop.code, int) else 1
        except KeyboardInterrupt:
            exit_code = 130
        finally:
            stdout.finish()
            stderr.finish()
            sys.argv, sys.stdout, sys.stderr = saved
            for handler in root_logger.handlers[:]:
                if handler not in handlers_before:  # gallery-dl adds one log handler per run
                    root_logger.removeHandler(handler)
            engine_config.clear()
    return exit_code or 0


def dump_json_in_process(config_path: str, url: str) -> list:
    """What `gallery-dl --dump-json URL` prints, as data (for the item picker).

    gallery-dl binds its JSON output to the real stdout when it is imported, so this
    runs its DataJob directly with its own buffer instead of capturing printed text.
    """
    from gallery_dl import config as engine_config
    from gallery_dl import job

    with _ENGINE_LOCK:
        engine_config.clear()
        try:
            engine_config.load([config_path], strict=True)
            data_job = job.DataJob(url, file=io.StringIO(), ensure_ascii=False)
            data_job.run()
            messages = data_job.data
        finally:
            engine_config.clear()
    return json.loads(json.dumps(messages, default=str))
