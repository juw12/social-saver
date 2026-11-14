"""Social Saver web downloader: type a username or link, see the posts, download what you pick.

A small stateless Flask app made for free serverless hosting (Vercel, no card needed),
and it also runs on your own PC (`python -m saver.webdl --phone`, no hosting limits).
gallery-dl lists the media; each file
is then streamed from the site's CDN through this server, so the phone gets a normal
download (or a zip of everything it ticked).

Nothing is stored on the server. A download link carries the file address and the
request headers the site wants (TikTok's CDN refuses requests without its session
cookie), encrypted and signed with a key derived from SOCIAL_SAVER_PASSWORD or
SOCIAL_SAVER_SECRET, and it expires after a few hours.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import queue
import re
import secrets
import tempfile
import threading
import time
import zipfile
import zlib
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import quote

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from . import core

PAGE_SIZE = 24
MAX_PAGE_SIZE = 60
MAX_ZIP_FILES = 120
TOKEN_LIFETIME_SECONDS = 6 * 3600
CHUNK_BYTES = 256 * 1024
UPSTREAM_TIMEOUT = (15, 60)  # connect, read (seconds)
LISTING_TIMEOUT_SECONDS = 280  # Vercel stops a function at 300 s
WEB_PLATFORMS = ("instagram", "x", "tiktok")
# What the user can ask for per platform -> the type names core.plan_runs() understands.
KINDS = {
    "instagram": {"posts": ["posts"], "reels": ["reels"], "stories": ["stories"], "highlights": ["highlights"]},
    "x": {"media": ["posts", "videos"]},
    "tiktok": {"videos": ["posts", "videos"], "stories": ["stories"]},
}
NOT_FORWARDED_REQUEST_HEADERS = {"accept-encoding", "content-length", "host", "connection"}
FORWARDED_RESPONSE_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges",
                              "Last-Modified", "ETag")
CAPTION_KEYS = ("description", "content", "desc", "title", "caption")
UNSAFE_NAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")
ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
STATIC_DIR = ROOT / "static"
_FALLBACK_SECRET = "social-saver-web-default"

# A listing source: (url, file_filter, start, count, cookie_file, engine_category, should_stop)
# -> yields {"url", "meta", "headers", "thumb", "thumb_headers"} per file, or raises ListingError.
Lister = Callable[..., Iterator[dict]]


class ListingError(Exception):
    """The site refused the listing; the message is the engine's error line."""


class BadToken(Exception):
    """A download link that was altered, made by another server, or expired."""


# ---------------------------------------------------------------- download links

def _secret_key() -> bytes:
    material = (os.environ.get("SOCIAL_SAVER_SECRET")
                or os.environ.get("SOCIAL_SAVER_PASSWORD")
                or os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
                or _FALLBACK_SECRET)
    return hashlib.sha256(b"social-saver-web:" + material.encode("utf-8")).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = []
    for counter in range((length + 31) // 32):
        blocks.append(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
    return b"".join(blocks)[:length]


def seal(payload: dict, lifetime: int = TOKEN_LIFETIME_SECONDS) -> str:
    """Encrypt and sign a download link payload (encrypt-then-MAC, HMAC-SHA256)."""
    key = _secret_key()
    encryption_key = hashlib.sha256(key + b"encrypt").digest()
    signing_key = hashlib.sha256(key + b"sign").digest()
    body = zlib.compress(json.dumps({**payload, "exp": int(time.time()) + lifetime},
                                    separators=(",", ":")).encode("utf-8"), 9)
    nonce = secrets.token_bytes(12)
    stream = _keystream(encryption_key, nonce, len(body))
    cipher = bytes(left ^ right for left, right in zip(body, stream))
    tag = hmac.new(signing_key, nonce + cipher, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(nonce + cipher + tag).decode("ascii").rstrip("=")


def unseal(token: str) -> dict:
    key = _secret_key()
    encryption_key = hashlib.sha256(key + b"encrypt").digest()
    signing_key = hashlib.sha256(key + b"sign").digest()
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError) as error:
        raise BadToken("broken link") from error
    if len(raw) < 12 + 16 + 1:
        raise BadToken("broken link")
    nonce, cipher, tag = raw[:12], raw[12:-16], raw[-16:]
    expected = hmac.new(signing_key, nonce + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected):
        raise BadToken("This link was not made by this server (or the password changed). Search again.")
    stream = _keystream(encryption_key, nonce, len(cipher))
    payload = json.loads(zlib.decompress(bytes(left ^ right for left, right in zip(cipher, stream))))
    if payload.get("exp", 0) < time.time():
        raise BadToken("This link expired. Search again to get fresh links.")
    return payload


# ---------------------------------------------------------------- items

def _text(value) -> str:
    return value if isinstance(value, str) else ""


def account_name(metadata: dict) -> str:
    for key in ("username", "owner_username", "user", "author", "uploader"):
        value = metadata.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("unique_id") or value.get("uniqueId") or value.get("nick")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def post_id(metadata: dict) -> str:
    for key in ("post_shortcode", "shortcode", "tweet_id", "post_id", "id"):
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def post_link(platform: str, metadata: dict) -> str:
    if _text(metadata.get("post_url")).startswith("http"):
        return metadata["post_url"]
    identifier = post_id(metadata)
    if not identifier:
        return ""
    if platform == "instagram":
        return f"https://www.instagram.com/p/{identifier}/"
    if platform == "x":
        return f"https://x.com/i/status/{identifier}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{account_name(metadata)}/video/{identifier}"
    return ""


def file_extension(file_url: str, metadata: dict) -> str:
    extension = str(metadata.get("extension") or "").lower().lstrip(".")
    if not extension:
        extension = Path(file_url.split("?")[0]).suffix.lower().lstrip(".")
    return extension if re.fullmatch(r"[a-z0-9]{1,5}", extension or "") else "bin"


def file_name(platform: str, metadata: dict, extension: str, index: int) -> str:
    parts = [account_name(metadata) or platform, post_id(metadata) or str(index)]
    number = metadata.get("num")
    if isinstance(number, int) and number > 0:
        parts.append(str(number))
    stem = UNSAFE_NAME_CHARACTERS.sub("_", "_".join(parts)).strip("._")[:100] or f"{platform}_{index}"
    return f"{stem}.{extension}"


def describe(platform: str, entry: dict, index: int) -> dict:
    """One picker tile: what to show and the signed links to fetch it."""
    metadata = entry.get("meta") or {}
    extension = file_extension(entry["url"], metadata)
    name = file_name(platform, metadata, extension, index)
    caption = next((_text(metadata.get(key)) for key in CAPTION_KEYS if _text(metadata.get(key)).strip()), "")
    thumb = entry.get("thumb")
    return {
        "index": index,
        "name": name,
        "video": "." + extension in core.VIDEO_EXTENSIONS,
        "date": str(metadata.get("date") or "")[:10],
        "text": " ".join(caption.split())[:160],
        "link": post_link(platform, metadata),
        "file": seal({"u": entry["url"], "h": entry.get("headers") or {}, "n": name}),
        "thumb": seal({"u": thumb, "h": entry.get("thumb_headers") or {}, "n": name, "i": 1}) if thumb else None,
    }


def friendly_error(message: str, platform: str) -> str:
    label = core.PLATFORMS[platform]["label"]
    lowered = message.lower()
    if "authrequired" in lowered or "redirect to login" in lowered or "login required" in lowered \
            or "authenticated cookies" in lowered or "401" in lowered:
        return (f"{label} only shows this to signed-in users when the request comes from a server. "
                f"Add your {label} cookies under 'Sign-in cookies' and search again.")
    hint = core.explain_engine_message(message, label)
    if hint and "office network" in hint:
        hint = f"{label} refused the request (403). Try again later, or add your {label} cookies."
    return hint or f"{label}: {message}"


NESTED_COVER_KEYS = ("cover", "originCover", "thumbnail", "dynamicCover", "poster")


def find_thumbnail(metadata: dict, file_url: str) -> str | None:
    """A picture for the tile: the usual keys, else a cover inside the video details
    (TikTok keeps its covers in post["video"])."""
    thumb = core.thumbnail_for(metadata, file_url)
    if thumb:
        return thumb
    for nested_key in ("video", "media", "imagePost"):
        nested = metadata.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in NESTED_COVER_KEYS:
            value = nested.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
    return None


# ---------------------------------------------------------------- listing with gallery-dl

def _request_headers(session: requests.Session, url: str, extra: dict | None) -> dict:
    """The headers (cookies included) gallery-dl itself would send for this file."""
    prepared = session.prepare_request(requests.Request("GET", url, headers=extra or None))
    return {name: value for name, value in prepared.headers.items()
            if name.lower() not in NOT_FORWARDED_REQUEST_HEADERS}


def gallery_dl_lister(url: str, file_filter: str | None, start: int, count: int,
                      cookie_file: str | None, engine_category: str | None,
                      should_stop: Callable[[], bool] = lambda: False) -> Iterator[dict]:
    """Files start..start+count-1 of a profile or post, as gallery-dl finds them (streamed)."""
    from gallery_dl import config as engine_config
    from gallery_dl import exception, job

    from .engine import _ENGINE_LOCK

    found: queue.Queue = queue.Queue()
    finished = object()

    class StreamingJob(job.DataJob):
        def handle_url(self, file_url, kwdict):
            if should_stop():
                raise exception.StopExtraction()
            if file_url.startswith(("ytdl:", "text:")):
                return
            session = self.extractor.session
            thumb = find_thumbnail(kwdict, file_url)
            found.put({
                "url": file_url,
                "meta": json.loads(json.dumps(self.filter(kwdict), default=str)),
                "headers": _request_headers(session, file_url, kwdict.get("_http_headers")),
                "thumb": thumb,
                "thumb_headers": _request_headers(session, thumb, None) if thumb else None,
            })

    def run() -> None:
        try:
            with _ENGINE_LOCK:
                engine_config.clear()
                try:
                    engine_config.set(("extractor",), "image-range", f"{start}-{start + count - 1}")
                    if file_filter:
                        engine_config.set(("extractor",), "image-filter", file_filter)
                    engine_config.set(("cache",), "file", str(Path(tempfile.gettempdir()) / "gallery-dl-cache.sqlite3"))
                    if cookie_file and engine_category:
                        engine_config.set(("extractor", engine_category), "cookies", cookie_file)
                    listing = StreamingJob(url, file=None)
                    listing.run()
                    errors = [message for message in listing.data if message and message[0] == -1]
                    if errors:
                        details = errors[0][-1]
                        found.put(ListingError(f"{details.get('error', 'Error')}: {details.get('message', '')}"))
                finally:
                    engine_config.clear()
        except Exception as error:  # noqa: BLE001 - reported to the page instead of crashing the stream
            found.put(ListingError(f"{type(error).__name__}: {error}"))
        finally:
            found.put(finished)

    threading.Thread(target=run, daemon=True).start()
    while True:
        entry = found.get()
        if entry is finished:
            return
        if isinstance(entry, Exception):
            raise entry
        yield entry


def _cookie_file_for(platform: str, cookie_text: str) -> str | None:
    """Write the user's pasted cookies (this platform's only) to a temporary cookies.txt."""
    if not cookie_text.strip():
        return None
    cookies = [cookie for cookie in core.parse_cookie_export(cookie_text)
               if core.platform_for_host(cookie["domain"].lstrip(".")) == platform]
    if not cookies:
        label = core.PLATFORMS[platform]["label"]
        raise ValueError(f"Those cookies have nothing for {label}. Export them while you are on "
                         f"{core.PLATFORMS[platform]['domains'][0]} and signed in.")
    handle, path = tempfile.mkstemp(prefix=f"cookies-{platform}-", suffix=".txt")
    os.close(handle)
    core.write_cookie_file(Path(path), cookies)
    return path


# ---------------------------------------------------------------- streaming files

def _open_upstream(payload: dict, byte_range: str | None = None) -> requests.Response:
    url = payload.get("u") or ""
    if not url.startswith("https://"):
        raise BadToken("Only https files can be fetched.")
    headers = dict(payload.get("h") or {})
    if byte_range:
        headers["Range"] = byte_range
    return requests.get(url, headers=headers, stream=True, timeout=UPSTREAM_TIMEOUT)


def content_disposition(name: str, inline: bool) -> str:
    ascii_name = name.encode("ascii", "ignore").decode() or "download"
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


class _ZipStream(io.RawIOBase):
    """A write-only, unseekable sink: zipfile writes into it, the response drains it."""

    def __init__(self):
        super().__init__()
        self._chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def drain(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def zip_stream(payloads: list[dict], opener=None) -> Iterator[bytes]:
    """A zip of these files, produced while they download (never held in memory whole)."""
    opener = opener or _open_upstream
    sink = _ZipStream()
    used_names: set[str] = set()
    missing: list[str] = []
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for payload in payloads:
            name = payload.get("n") or "file"
            stem, dot, extension = name.rpartition(".")
            candidate, counter = name, 2
            while candidate in used_names:
                candidate = f"{stem}_{counter}.{extension}" if dot else f"{name}_{counter}"
                counter += 1
            used_names.add(candidate)
            try:
                upstream = opener(payload)
            except (requests.RequestException, BadToken) as error:
                missing.append(f"{name}: {error}")
                continue
            if upstream.status_code >= 400:
                missing.append(f"{name}: the site answered HTTP {upstream.status_code}")
                upstream.close()
                continue
            info = zipfile.ZipInfo(candidate, date_time=time.localtime()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            try:
                with archive.open(info, "w") as entry:
                    for chunk in upstream.iter_content(CHUNK_BYTES):
                        entry.write(chunk)
                        data = sink.drain()
                        if data:
                            yield data
            finally:
                upstream.close()
        if missing:
            archive.writestr("NOT-SAVED.txt", "These files could not be fetched:\n" + "\n".join(missing) + "\n")
    yield sink.drain()


# ---------------------------------------------------------------- the app

def create_app(lister: Lister | None = None, opener=None) -> Flask:
    lister = lister or gallery_dl_lister
    opener = opener or _open_upstream
    app = Flask(__name__, static_folder=None)

    def password() -> str:
        return os.environ.get("SOCIAL_SAVER_PASSWORD", "")

    def password_ok(given: str | None) -> bool:
        expected = password()
        return not expected or hmac.compare_digest((given or "").encode(), expected.encode())

    def refuse():
        return jsonify(error="Wrong or missing password."), 401

    @app.after_request
    def no_caching_of_pages(response):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/")
    def page():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(WEB_DIR, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/static/<path:name>")
    def static_file(name: str):
        response = send_from_directory(STATIC_DIR, name)
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    @app.get("/api/info")
    def info():
        return jsonify(
            password=bool(password()),
            platforms={platform: {"label": core.PLATFORMS[platform]["label"], "kinds": list(KINDS[platform])}
                       for platform in WEB_PLATFORMS},
            page_size=PAGE_SIZE,
        )

    @app.post("/api/login")
    def login():
        given = (request.get_json(silent=True) or {}).get("password")
        return jsonify(ok=True) if password_ok(given) else refuse()

    @app.post("/api/list")
    def list_items():
        if not password_ok(request.headers.get("X-Password")):
            return refuse()
        body = request.get_json(silent=True) or {}
        platform = body.get("platform") if body.get("platform") in WEB_PLATFORMS else "instagram"
        query = str(body.get("query") or "").strip()
        try:
            start = max(int(body.get("start") or 1), 1)
            count = min(max(int(body.get("count") or PAGE_SIZE), 1), MAX_PAGE_SIZE)
        except (TypeError, ValueError):
            return jsonify(error="start and count must be numbers"), 400
        try:
            target = core.parse_target(core.extract_link(query), platform)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        if target.platform not in WEB_PLATFORMS:
            return jsonify(error="Threads is not in the web version yet. Use the phone or PC app for Threads."), 400
        platform = target.platform
        if target.username:
            kind = body.get("kind") if body.get("kind") in KINDS[platform] else next(iter(KINDS[platform]))
            runs = core.plan_runs(target, KINDS[platform][kind])
        else:
            runs = core.plan_runs(target, [])
        run = runs[0]
        try:
            cookie_file = _cookie_file_for(platform, str(body.get("cookies") or ""))
        except ValueError as error:
            return jsonify(error=str(error)), 400

        stop = threading.Event()
        deadline = time.monotonic() + LISTING_TIMEOUT_SECONDS

        def lines() -> Iterator[str]:
            sent = 0
            try:
                yield json.dumps({"type": "start", "platform": platform, "target": target.display}) + "\n"
                entries = lister(run.url, run.file_filter, start, count + 1, cookie_file,
                                 core.PLATFORMS[platform]["engine_category"],
                                 lambda: stop.is_set() or time.monotonic() > deadline)
                for entry in entries:
                    if sent == count:
                        yield json.dumps({"type": "done", "more": True, "next": start + count}) + "\n"
                        return
                    yield json.dumps({"type": "item", "item": describe(platform, entry, start + sent)}) + "\n"
                    sent += 1
                yield json.dumps({"type": "done", "more": False, "next": start + sent}) + "\n"
            except ListingError as error:
                yield json.dumps({"type": "error", "message": friendly_error(str(error), platform),
                                  "detail": str(error)[:300], "next": start + sent}) + "\n"
            finally:
                stop.set()
                if cookie_file:
                    Path(cookie_file).unlink(missing_ok=True)

        return Response(stream_with_context(lines()), mimetype="application/x-ndjson",
                        headers={"X-Accel-Buffering": "no"})

    @app.get("/api/file")
    def file():
        try:
            payload = unseal(request.args.get("t", ""))
        except BadToken as error:
            return jsonify(error=str(error)), 403
        inline = bool(payload.get("i")) or request.args.get("inline") == "1"
        try:
            upstream = opener(payload, request.headers.get("Range"))
        except requests.RequestException as error:
            return jsonify(error=f"Could not reach the site: {type(error).__name__}"), 502
        if upstream.status_code >= 400:
            upstream.close()
            return jsonify(error=f"The site refused the file (HTTP {upstream.status_code}). "
                                 "Search again for fresh links."), 502
        headers = {name: upstream.headers[name] for name in FORWARDED_RESPONSE_HEADERS if name in upstream.headers}
        headers["Content-Disposition"] = content_disposition(payload.get("n") or "download", inline)
        headers["Cache-Control"] = "private, max-age=3600"

        def body() -> Iterator[bytes]:
            try:
                yield from upstream.iter_content(CHUNK_BYTES)
            finally:
                upstream.close()

        return Response(body(), status=upstream.status_code, headers=headers, direct_passthrough=True)

    @app.post("/api/zip")
    def zip_files():
        tokens = [token for token in request.form.getlist("t") if token]
        if not tokens:
            return jsonify(error="Nothing was picked."), 400
        if len(tokens) > MAX_ZIP_FILES:
            return jsonify(error=f"Pick at most {MAX_ZIP_FILES} files per zip."), 400
        try:
            payloads = [unseal(token) for token in tokens]
        except BadToken as error:
            return jsonify(error=str(error)), 403
        label = UNSAFE_NAME_CHARACTERS.sub("_", request.form.get("name") or "social-saver").strip("._")[:60]
        headers = {"Content-Disposition": content_disposition(f"{label or 'social-saver'}.zip", False)}
        return Response(stream_with_context(zip_stream(payloads, opener)), mimetype="application/zip",
                        headers=headers)

    return app


app = create_app()


# ---------------------------------------------------------------- on your own PC

def home_password() -> str:
    """The password phones type in phone mode: SOCIAL_SAVER_PASSWORD, or one made once and kept in settings.json."""
    given = os.environ.get("SOCIAL_SAVER_PASSWORD")
    if given:
        return given
    settings = core.load_settings()
    if not settings.get("web_password"):
        settings["web_password"] = secrets.token_hex(5)
        core.save_settings(settings)
    return settings["web_password"]


def start_phone_mode(port: int) -> str:
    """Lock the page with the home password and return the address a phone on the same Wi-Fi opens.

    Running on your own PC has no hosting limits (no monthly data cap, no bill) and the
    sites see a home connection instead of a cloud server.
    """
    from . import phone

    os.environ["SOCIAL_SAVER_PASSWORD"] = home_password()  # also the key that signs the download links
    return f"http://{phone.lan_address()}:{port}/"


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Social Saver web downloader")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--phone", action="store_true",
                        help="let phones on the same Wi-Fi in: prints the address, a QR code and the password")
    arguments = parser.parse_args(argv)
    host = arguments.host
    if arguments.phone:
        from . import phone

        host = "0.0.0.0"
        address = start_phone_mode(arguments.port)
        print(f"\nOn the phone (same Wi-Fi) open:  {address}")
        print(f"Password:                        {home_password()}")
        print("Keep this window open while you use it. Close it to stop.\n")
        try:
            phone.print_qr(address)
        except (OSError, UnicodeError):
            pass  # a console that cannot draw the QR code; the address above is enough
    app.run(host=host, port=arguments.port, threaded=True)


if __name__ == "__main__":
    main()
