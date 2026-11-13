"""Use Social Saver from a phone (Android or iPhone) on the same Wi-Fi.

The PC keeps doing the downloads; the phone opens the same screen in its browser.
The phone server listens on the home network but only answers someone who has the
phone key, which travels inside the QR code shown on the PC.
"""
from __future__ import annotations

import secrets
import socket
import threading
from typing import Callable

from werkzeug.serving import make_server

from saver import core

DEFAULT_PORT = 8765
KEY_COOKIE = "ss_key"
KEY_COOKIE_DAYS = 400  # the longest a browser keeps a cookie


def lan_address() -> str:
    """The PC's address on the home network, the one a phone can reach."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))  # picks the outgoing interface; nothing is sent
        return probe.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        probe.close()


def phone_settings() -> dict:
    """Settings with a phone key and port, created on first use and kept in settings.json."""
    settings = core.load_settings()
    if not settings.get("phone_key"):
        settings["phone_key"] = secrets.token_urlsafe(18)
        core.save_settings(settings)
    settings.setdefault("phone_port", DEFAULT_PORT)
    settings.setdefault("phone_enabled", False)
    return settings


def update_phone_settings(**changes) -> dict:
    settings = {**phone_settings(), **changes}
    core.save_settings(settings)
    return settings


def ensure_port_free(host: str, port: int) -> None:
    """Raise OSError if another program already listens on the port.

    Python servers set SO_REUSEADDR, which on Windows would let two programs share a port
    silently, so check with an exclusive bind first.
    """
    if port == 0:
        return
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    finally:
        probe.close()


def phone_url(host: str, port: int, key: str) -> str:
    return f"http://{host}:{port}/?k={key}"


def qr_svg(text: str) -> str | None:
    """The QR code as inline SVG, or None when segno is not installed."""
    try:
        import segno
    except ImportError:
        return None
    return segno.make(text, error="m").svg_inline(scale=5, border=2, dark="#1d1c1a", light="#ffffff")


def print_qr(text: str) -> None:
    """Show the QR code in a terminal (for --serve on a PC without a window)."""
    try:
        import segno
    except ImportError:
        return
    segno.make(text, error="m").terminal(compact=True)


class PhoneServer:
    """Starts and stops the network-facing server that phones talk to."""

    def __init__(self, make_app: Callable[[str], object], host: str = "0.0.0.0") -> None:
        self._make_app = make_app  # phone key -> WSGI app
        self._host = host
        self._server = None
        self._lock = threading.Lock()
        self.error = ""

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int | None:
        return self._server.server_port if self._server else None

    def start(self, port: int, key: str) -> None:
        with self._lock:
            if self._server:
                return
            try:
                ensure_port_free(self._host, port)
                server = make_server(self._host, port, self._make_app(key), threaded=True)
            except OSError as error:
                self.error = (f"Port {port} is busy or blocked ({error}). Close the other program "
                              "or set another \"phone_port\" in settings.json.")
                raise ValueError(self.error) from error
            self.error = ""
            self._server = server
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            server, self._server = self._server, None
        if server:
            server.shutdown()
            server.server_close()

    def restart(self, port: int, key: str) -> None:
        self.stop()
        self.start(port, key)

    def status(self) -> dict:
        settings = phone_settings()
        if not self.running:
            return {"enabled": False, "url": "", "qr": None, "error": self.error}
        url = phone_url(lan_address(), self.port, settings["phone_key"])
        return {"enabled": True, "url": url, "qr": qr_svg(url), "error": ""}
