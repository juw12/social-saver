"""Social Saver: one desktop app to save Instagram, Threads, X and TikTok media into zips.

Run with:  python app.py
A small local web server drives the downloads; a pywebview window shows the UI.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

import requests
from flask import Flask, Response, abort, jsonify, request
from werkzeug.serving import make_server

from saver import core, threads

APP_DIR = Path(__file__).resolve().parent
ENGINE_COMMAND = [sys.executable, "-m", "gallery_dl"]
ENGINE_PROBLEM_LINE = re.compile(r"^\[[\w.-]+\]\[(warning|error)\]")
ENGINE_INFO_LINE = re.compile(r"^\[[\w.-]+\]\[(debug|info)\]")
NO_CONSOLE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PREVIEW_TIMEOUT_SECONDS = 180
MAX_PICKER_ITEMS = 200


class JobRunner:
    """Runs one background job at a time and keeps its progress for the UI."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._process: subprocess.Popen | None = None
        self.running = False
        self.current = ""
        self.downloaded = 0
        self.log_lines: deque[str] = deque(maxlen=400)
        self.preview: dict = {"id": 0, "target": None, "items": []}
        self.thumb_cache: dict[tuple[int, int], tuple[bytes, str]] = {}

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def log(self, message: str) -> None:
        self.log_lines.append(f"{time.strftime('%H:%M:%S')}  {message}")

    def start(self, work: Callable[[], None]) -> bool:
        with self._lock:
            if self.running:
                return False
            self.running = True
        self._cancel.clear()
        self.downloaded = 0
        threading.Thread(target=self._run, args=(work,), daemon=True).start()
        return True

    def _run(self, work: Callable[[], None]) -> None:
        try:
            work()
        except Exception as error:  # keep the app alive and tell the user what stopped
            self.log(f"Stopped: {error}")
        finally:
            self.current = ""
            self.running = False

    def cancel(self) -> None:
        self._cancel.set()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def run_process(self, command: list[str], on_line: Callable[[str], None]) -> int:
        """Run a command, feeding each output line to on_line; returns the exit code."""
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,  # never wait for typed input
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            creationflags=NO_CONSOLE_WINDOW,
        )
        for line in self._process.stdout:
            if line.strip():
                on_line(line.strip())
        return self._process.wait()


def run_engine(runner: JobRunner, run: core.EngineRun, config_path: Path,
               limit: int | None, platform_label: str, pick: list[int] | None = None) -> None:
    """One gallery-dl run; counts saved files and explains known errors."""
    command = [*ENGINE_COMMAND, "--config-ignore", "--config", str(config_path)]
    if run.file_filter:
        command += ["--filter", run.file_filter]
    if pick:
        command += ["--range", core.range_for_pick(pick)]
    elif limit:
        command += ["--range", f"1-{limit}"]
    command.append(run.url)
    skipped = 0

    def on_line(line: str) -> None:
        nonlocal skipped
        if ENGINE_PROBLEM_LINE.match(line):
            runner.log(line)
            hint = core.explain_engine_message(line, platform_label)
            if hint:
                runner.log("-> " + hint)
        elif ENGINE_INFO_LINE.match(line):
            return
        elif line.startswith("#"):
            skipped += 1
        else:
            runner.downloaded += 1
            runner.log(f"saved {Path(line).name}")

    runner.run_process(command, on_line)
    if skipped:
        runner.log(f"{skipped} file(s) were already downloaded before - skipped.")


def write_engine_config() -> Path:
    home = core.data_home()
    staging_dir = home / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    config_path = home / "engine-config.json"
    config_path.write_text(
        json.dumps(core.build_engine_config(staging_dir, home / "history.sqlite3"), indent=2),
        encoding="utf-8",
    )
    return config_path


def download_targets(runner: JobRunner, targets: list[core.Target],
                     chosen_types: dict[str, list[str]], limit: int | None,
                     pick: list[int] | None = None) -> None:
    staging_dir = core.data_home() / "staging"
    output_dir = Path(core.load_settings()["output_dir"])
    config_path = write_engine_config()

    for target in targets:
        if runner.cancelled:
            break
        spec = core.PLATFORMS[target.platform]
        types = chosen_types.get(target.platform) or list(spec["types"])
        runner.current = target.display
        runner.log(f"Starting {target.display}")
        try:
            if target.platform == "threads":
                runner.downloaded += threads.download(
                    target, types, staging_dir, output_dir, limit,
                    runner.log, lambda: runner.cancelled, pick=pick,
                )
            else:
                for run in core.plan_runs(target, types):
                    if runner.cancelled:
                        break
                    run_engine(runner, run, config_path, limit, spec["label"], pick)
        except requests.RequestException as error:
            runner.log(f"{spec['label']}: {error}")
            hint = core.explain_engine_message(str(error), spec["label"])
            if hint:
                runner.log("-> " + hint)

        try:
            zip_updates = core.sort_staging_into_zips(staging_dir, output_dir)
        except OSError as error:
            runner.log(f"Could not update a zip ({error}). Close it if it is open; "
                       "the files are kept and will be added on the next run.")
            continue
        for zip_path, new_count in zip_updates.items():
            runner.log(f"{zip_path.name}: {new_count} new file(s) added")
        if not zip_updates:
            runner.log(f"Nothing new for {target.display}.")

    runner.log("Cancelled." if runner.cancelled else "All done.")


def preview_target(target: core.Target) -> list[dict]:
    """Picker items (with picture urls) for one post link."""
    if target.platform == "threads":
        return threads.preview(target)
    command = [
        *ENGINE_COMMAND, "--config-ignore", "--config", str(write_engine_config()),
        "--dump-json", target.url,
    ]
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=PREVIEW_TIMEOUT_SECONDS,
        creationflags=NO_CONSOLE_WINDOW,
    )
    label = core.PLATFORMS[target.platform]["label"]
    try:
        messages = json.loads(result.stdout or "[]")
    except ValueError:
        messages = []
    try:
        items = core.preview_items_from_engine(messages)
    except ValueError as error:
        hint = core.explain_engine_message(str(error), label)
        raise ValueError(hint or f"{label} could not list that post ({error}).") from error
    if not items:
        problem = next(
            (line for line in result.stderr.splitlines() if ENGINE_PROBLEM_LINE.match(line)), "",
        )
        hint = core.explain_engine_message(problem, label) if problem else None
        raise ValueError(hint or f"Nothing to pick in that {label} link.")
    return items


def single_link_target(body: dict) -> core.Target:
    """The one post link that the picker works on."""
    platform = body.get("platform")
    if platform not in core.PLATFORMS:
        raise ValueError("Pick a platform first.")
    lines = [line for line in str(body.get("targets", "")).splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("Choosing items works on one post link at a time.")
    target = core.parse_target(lines[0], platform)
    if not target.url:
        raise ValueError("Choosing items needs a link to one post, not a whole account.")
    return target


def update_engines(runner: JobRunner) -> None:
    runner.current = "Updating download engines"
    exit_code = runner.run_process(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "gallery-dl", "yt-dlp"],
        runner.log,
    )
    runner.log("Download engines are up to date." if exit_code == 0 else "Update failed.")


def create_app(runner: JobRunner, token: str, desktop=None) -> Flask:
    """The local API. `desktop` supplies the native bits (sign-in window, folder picker)."""
    app = Flask(__name__)
    ui_html = (APP_DIR / "ui.html").read_text(encoding="utf-8")

    @app.before_request
    def require_token():
        if request.path.startswith("/api/") and request.headers.get("X-Token") != token:
            abort(403)
        if request.path.startswith("/thumb/") and request.args.get("t") != token:
            abort(403)

    @app.errorhandler(ValueError)
    def bad_request(error):
        return jsonify(error=str(error)), 400

    @app.get("/")
    def index():
        return ui_html.replace("__TOKEN__", token)

    @app.get("/api/state")
    def state():
        output_dir = Path(core.load_settings()["output_dir"])
        zips = [
            {"name": path.name, "megabytes": round(path.stat().st_size / 1_000_000, 1)}
            for path in sorted(output_dir.glob("*.zip"))
        ] if output_dir.exists() else []
        return jsonify(
            platforms=[
                {
                    "id": platform,
                    "label": spec["label"],
                    "types": spec["types"],
                    "beta": spec["beta"],
                    "signed_in": core.is_signed_in(platform),
                }
                for platform, spec in core.PLATFORMS.items()
            ],
            output_dir=str(output_dir),
            zips=zips,
            job={
                "running": runner.running,
                "current": runner.current,
                "downloaded": runner.downloaded,
                "log": list(runner.log_lines),
            },
        )

    @app.post("/api/start")
    def start():
        body = request.get_json(force=True)
        platform = body.get("platform")
        if platform not in core.PLATFORMS:
            raise ValueError("Pick a platform first.")
        lines = [line for line in str(body.get("targets", "")).splitlines() if line.strip()]
        if not lines:
            raise ValueError("Type at least one username or link.")
        targets = [core.parse_target(line, platform) for line in lines]
        types = [name for name in body.get("types", []) if name in core.PLATFORMS[platform]["types"]]
        if not types and any(target.username and target.platform == platform for target in targets):
            raise ValueError("Tick at least one thing to download.")
        limit = int(body.get("limit") or 0) or None
        if limit is not None and limit < 1:
            raise ValueError("The limit must be 1 or more.")
        pick = body.get("pick")
        if pick is not None:
            if runner.preview["target"] != single_link_target(body):
                raise ValueError("The link changed. Press 'Choose items' again.")
            if not isinstance(pick, list) or not pick \
                    or not all(isinstance(index, int) and index >= 1 for index in pick):
                raise ValueError("Tick at least one item to save.")
            limit = None
        job = lambda: download_targets(runner, targets, {platform: types}, limit, pick)  # noqa: E731
        if not runner.start(job):
            raise ValueError("A download is already running.")
        return jsonify(ok=True)

    @app.post("/api/preview")
    def preview():
        target = single_link_target(request.get_json(force=True))
        try:
            items = preview_target(target)[:MAX_PICKER_ITEMS]
        except subprocess.TimeoutExpired as error:
            raise ValueError("The platform took too long to answer. Try again.") from error
        except requests.RequestException as error:
            label = core.PLATFORMS[target.platform]["label"]
            raise ValueError(core.explain_engine_message(str(error), label) or str(error)) from error
        runner.preview = {"id": runner.preview["id"] + 1, "target": target, "items": items}
        runner.thumb_cache.clear()
        return jsonify(
            id=runner.preview["id"],
            items=[
                {"index": item["index"], "video": item["video"], "has_thumb": bool(item["thumb"])}
                for item in items
            ],
        )

    @app.get("/thumb/<int:preview_id>/<int:index>")
    def thumbnail(preview_id: int, index: int):
        """Picture of one picker item, fetched here so the platform's CDN rules don't block it."""
        if preview_id != runner.preview["id"] or not 1 <= index <= len(runner.preview["items"]):
            abort(404)
        key = (preview_id, index)
        if key not in runner.thumb_cache:
            picture_url = runner.preview["items"][index - 1]["thumb"]
            if not picture_url:
                abort(404)
            platform = runner.preview["target"].platform
            session = threads.new_session() if platform == "threads" else requests.Session()
            session.headers.setdefault("User-Agent", threads.BROWSER_HEADERS["User-Agent"])
            try:
                answer = session.get(picture_url, timeout=30)
                answer.raise_for_status()
            except requests.RequestException:
                abort(502)
            content_type = answer.headers.get("Content-Type", "image/jpeg")
            if not content_type.startswith("image/"):
                abort(502)
            runner.thumb_cache[key] = (answer.content, content_type)
        picture, content_type = runner.thumb_cache[key]
        return Response(picture, mimetype=content_type, headers={"Cache-Control": "no-store"})

    @app.post("/api/import-cookies")
    def import_cookies():
        body = request.get_json(force=True)
        platform = body.get("platform")
        if platform not in core.PLATFORMS:
            raise ValueError("Unknown platform.")
        text = str(body.get("text", ""))
        if not text.strip():
            raise ValueError("That file is empty.")
        count = core.import_cookies(platform, text)
        runner.log(f"Imported {count} {core.PLATFORMS[platform]['label']} cookies - signed in.")
        return jsonify(ok=True, count=count)

    @app.post("/api/cancel")
    def cancel():
        runner.cancel()
        return jsonify(ok=True)

    @app.post("/api/signin")
    def signin():
        platform = request.get_json(force=True).get("platform")
        if platform not in core.PLATFORMS:
            raise ValueError("Unknown platform.")
        if desktop is None:
            raise ValueError("Sign-in needs the desktop app window.")
        desktop.open_signin(platform, runner.log)
        return jsonify(ok=True)

    @app.post("/api/signout")
    def signout():
        platform = request.get_json(force=True).get("platform")
        if platform not in core.PLATFORMS:
            raise ValueError("Unknown platform.")
        core.sign_out(platform)
        return jsonify(ok=True)

    @app.post("/api/choose-folder")
    def choose_folder():
        if desktop is None:
            raise ValueError("Choosing a folder needs the desktop app window.")
        folder = desktop.choose_folder()
        if folder:
            core.save_settings({**core.load_settings(), "output_dir": folder})
        return jsonify(ok=True)

    @app.post("/api/open-folder")
    def open_folder():
        output_dir = Path(core.load_settings()["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(output_dir)  # noqa: S606 - opens the user's own download folder
        return jsonify(ok=True)

    @app.post("/api/forget-history")
    def forget_history():
        if runner.running:
            raise ValueError("Wait for the current download to finish first.")
        (core.data_home() / "history.sqlite3").unlink(missing_ok=True)
        runner.log("Download history cleared. Files already inside a zip are still never added twice.")
        return jsonify(ok=True)

    @app.post("/api/update-engines")
    def start_engine_update():
        if not runner.start(lambda: update_engines(runner)):
            raise ValueError("Wait for the current download to finish first.")
        return jsonify(ok=True)

    return app


class Desktop:
    """Native windows via pywebview: the main window, sign-in windows, folder picker."""

    def __init__(self, webview_module) -> None:
        self.webview = webview_module
        self.main_window = None

    def choose_folder(self) -> str | None:
        chosen = self.main_window.create_file_dialog(self.webview.FOLDER_DIALOG)
        return chosen[0] if chosen else None

    def open_signin(self, platform: str, log: Callable[[str], None]) -> None:
        """Open the platform's own login page and keep the session once the user is in.

        The password is typed into the real site; this app only stores the resulting
        session cookies on this PC (see core.cookie_path).
        """
        spec = core.PLATFORMS[platform]
        window = self.webview.create_window(
            f"Sign in to {spec['label']} - this window closes by itself when you are in",
            html="<p style='font-family:sans-serif;padding:24px'>Loading...</p>",
            width=520,
            height=820,
        )
        closed = threading.Event()
        window.events.closed += closed.set

        def read_cookies() -> list[dict]:
            cookies = []
            for jar in window.get_cookies() or []:
                for name, morsel in jar.items():
                    cookies.append({
                        "name": name,
                        "value": morsel.value,
                        "domain": morsel["domain"],
                        "path": morsel["path"],
                        "secure": bool(morsel["secure"]),
                        "expires": morsel["expires"],
                    })
            return cookies

        def is_logged_in(cookies: list[dict]) -> bool:
            return any(
                cookie["name"] == spec["auth_cookie"] and cookie["value"]
                and core.platform_for_host(cookie["domain"].lstrip(".")) == platform
                for cookie in cookies
            )

        def watch() -> None:
            window.events.shown.wait(20)
            window.clear_cookies()  # always start from a clean login page
            window.load_url(spec["login_url"])
            deadline = time.time() + 15 * 60
            while not closed.is_set() and time.time() < deadline:
                time.sleep(2)
                try:
                    if not is_logged_in(read_cookies()):
                        continue
                    time.sleep(3)  # let the site finish setting its cookies
                    core.write_cookie_file(core.cookie_path(platform), read_cookies())
                except Exception as error:
                    if closed.is_set():
                        return
                    log(f"Sign-in check failed: {error}")
                    continue
                log(f"Signed in to {spec['label']}.")
                window.destroy()
                return

        threading.Thread(target=watch, daemon=True).start()


def main() -> None:
    import logging

    import webview

    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # no console when run windowless
    core.data_home().mkdir(parents=True, exist_ok=True)
    runner = JobRunner()
    desktop = Desktop(webview)
    token = secrets.token_urlsafe(24)
    server = make_server("127.0.0.1", 0, create_app(runner, token, desktop), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    desktop.main_window = webview.create_window(
        "Social Saver",
        f"http://127.0.0.1:{server.server_port}/",
        width=1040,
        height=860,
        min_size=(760, 600),
    )
    webview.start()
    runner.cancel()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        core.data_home().mkdir(parents=True, exist_ok=True)
        (core.data_home() / "crash.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
