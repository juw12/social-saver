"""Phone builds without a PC: the in-process engine (Android app), the password web app (cloud) and Android mode."""
import functools
import http.server
import threading
from pathlib import Path

import pytest
import requests

import app as social_saver
from saver import core, engine

PASSWORD = "correct horse battery"


@pytest.fixture
def file_server(tmp_path):
    """A tiny web server with one photo and one clip, for the real engine's direct-link support."""
    served = tmp_path / "served"
    served.mkdir()
    (served / "photo.jpg").write_bytes(b"\xff\xd8fake jpeg")
    (served / "clip.mp4").write_bytes(b"fake mp4")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    handler.log_message = lambda *args: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def in_process(monkeypatch):
    monkeypatch.setenv("SOCIAL_SAVER_IN_PROCESS", "1")


def local_session():
    session = requests.Session()
    session.trust_env = False  # never send localhost through a proxy
    return session


# ---------------------------------------------------------------- in-process engine (Android)

def test_real_engine_runs_in_process_twice_without_leftovers(file_server, in_process, capsys):
    assert engine.in_process()
    assert social_saver.self_test(f"{file_server}/photo.jpg") == 1
    assert social_saver.self_test(f"{file_server}/clip.mp4") == 1
    printed = capsys.readouterr().out
    assert printed.count("SELFTEST OK 1 file(s), in-process engine") == 2
    # nothing from the self-test is left to end up in a zip
    assert not (core.data_home() / "staging" / "directlink").exists()


def test_self_test_also_works_with_the_engine_as_a_child_process(file_server, monkeypatch, capsys):
    monkeypatch.delenv("SOCIAL_SAVER_IN_PROCESS", raising=False)
    assert social_saver.main(["--self-test", f"{file_server}/photo.jpg"]) == 0
    assert "SELFTEST OK 1 file(s), child-process engine" in capsys.readouterr().out
    assert social_saver.main(["--self-test", f"{file_server}/missing.jpg"]) == 1


def test_lines_that_are_not_saved_files_are_not_counted(file_server, in_process):
    """Other threads can print while the engine runs in-process; only real file paths count."""
    runner = social_saver.JobRunner()
    original = engine.run_in_process

    def noisy(args, on_stdout, **kwargs):
        on_stdout('127.0.0.1 - - "GET /api/state HTTP/1.1" 200 -')
        return original(args, on_stdout, **kwargs)

    engine.run_in_process = noisy
    try:
        social_saver.run_engine(runner, core.EngineRun(url=f"{file_server}/photo.jpg"),
                                social_saver.write_engine_config(), None, "Test")
    finally:
        engine.run_in_process = original
    assert runner.downloaded == 1


def test_stop_button_stops_the_in_process_engine(file_server):
    lines = []
    exit_code = engine.run_in_process(
        ["--config-ignore", "-o", "base-directory=" + str(core.data_home()), f"{file_server}/photo.jpg"],
        lines.append,
        should_stop=lambda: True,
    )
    assert exit_code != 0
    assert lines == []


def test_other_threads_output_is_not_taken_as_engine_output():
    seen = []
    passthrough = []
    writer = engine.LineWriter(seen.append, should_stop=lambda: True, passthrough=type(
        "Sink", (), {"write": lambda self, text: passthrough.append(text)})())
    other = threading.Thread(target=lambda: writer.write("server log line\n"))
    other.start()
    other.join()
    assert seen == [] and passthrough == ["server log line\n"]  # and no Stop raised in that thread
    with pytest.raises(engine.Stopped):
        writer.write("engine line\n")


def test_picker_preview_works_in_process(file_server, in_process):
    messages = engine.dump_json_in_process(str(social_saver.write_engine_config()), f"{file_server}/photo.jpg")
    urls = [message[1] for message in messages if message[0] == 3]
    assert urls == [f"{file_server}/photo.jpg"]
    items = core.preview_items_from_engine(messages)
    assert items[0]["index"] == 1 and not items[0]["video"]


def test_in_process_engine_does_not_try_pip(in_process):
    runner = social_saver.JobRunner()
    social_saver.update_engines(runner)
    assert "install the newest Social Saver APK" in runner.log_lines[-1]


def test_output_folder_can_be_set_by_the_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SOCIAL_SAVER_OUTPUT_DIR", str(tmp_path / "zips"))
    assert core.default_output_dir() == tmp_path / "zips"


# ---------------------------------------------------------------- password web app (cloud, iPhone + Android)

@pytest.fixture
def web_client():
    token = social_saver.web_token(PASSWORD)
    app = social_saver.create_app(social_saver.JobRunner(), token, remote=True, kind="web", password=PASSWORD)
    return app.test_client(), token


def test_web_app_asks_for_the_password_and_keeps_shared_links(web_client):
    client, token = web_client
    first = client.get("/?text=look+https://www.instagram.com/p/abc/")
    assert first.status_code == 401
    page = first.get_data(as_text=True)
    assert 'type="password"' in page and "text=look" in page

    wrong = client.post("/login", data={"password": "nope", "next": ""})
    assert wrong.status_code == 401 and "Wrong password" in wrong.get_data(as_text=True)

    right = client.post("/login", data={"password": PASSWORD, "next": "text=hello"},
                        headers={"X-Forwarded-Proto": "https"})
    assert right.status_code == 302
    assert right.headers["Location"].endswith("/?text=hello")
    cookie = right.headers["Set-Cookie"]
    assert token in cookie and "HttpOnly" in cookie and "Secure" in cookie

    opened = client.get("/")
    assert opened.status_code == 200
    assert 'const MODE = "web"' in opened.get_data(as_text=True)
    assert client.get("/api/state").status_code == 403
    state = client.get("/api/state", headers={"X-Token": token}).get_json()
    assert state["platforms"][0]["login_url"].startswith("https://")


def test_web_token_survives_restarts_and_depends_on_the_password():
    assert social_saver.web_token(PASSWORD) == social_saver.web_token(PASSWORD)
    assert social_saver.web_token(PASSWORD) != social_saver.web_token(PASSWORD + "!")


def test_web_app_can_be_installed_and_take_shared_links(web_client):
    client, _token = web_client
    manifest = client.get("/manifest.webmanifest").get_json(force=True)
    assert manifest["start_url"] == "/"
    assert manifest["share_target"]["params"] == {"title": "title", "text": "text", "url": "url"}
    worker = client.get("/sw.js")
    assert worker.status_code == 200 and "fetch" in worker.get_data(as_text=True)
    assert client.get("/healthz").get_data(as_text=True) == "ok"


def test_login_is_not_offered_without_a_password():
    app = social_saver.create_app(social_saver.JobRunner(), "key", remote=True)
    client = app.test_client()
    assert client.post("/login", data={"password": "x"}).status_code == 404
    assert client.get("/").status_code == 403
    assert "share_target" not in client.get("/manifest.webmanifest").get_json(force=True)


# ---------------------------------------------------------------- Android app screen

def test_android_screen_starts_on_this_device_only():
    url = social_saver.start_embedded()
    assert url.startswith("http://127.0.0.1:") and "/?k=" in url
    session = local_session()
    page = session.get(url, timeout=10)
    assert page.status_code == 200
    assert 'const MODE = "android"' in page.text
    assert session.get(url.split("?")[0], timeout=10).status_code == 200  # cookie kept
    assert local_session().get(url.split("?")[0], timeout=10).status_code == 403


def test_screen_has_the_android_sign_in_bridge():
    ui = Path(social_saver.__file__).with_name("ui.html").read_text(encoding="utf-8")
    assert "window.SocialSaverAndroid.signIn(" in ui
    assert "window.socialSaverCookiesFromApp" in ui
