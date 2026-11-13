"""Phone mode (Android / iPhone over Wi-Fi), the no-window server and the Android "Share" command."""
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import requests

import app as social_saver
from saver import core, phone

FAKE_ENGINE = Path(__file__).with_name("fake_engine.py")
KEY = "phone-key-for-tests"
DESKTOP_TOKEN = "desktop-token"


@pytest.fixture
def output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(social_saver, "ENGINE_COMMAND", [sys.executable, str(FAKE_ENGINE)])
    core.save_settings({"output_dir": str(tmp_path / "out")})
    return tmp_path / "out"


@pytest.fixture
def phone_client(output_dir):
    runner = social_saver.JobRunner()
    return social_saver.create_app(runner, KEY, remote=True).test_client()


def test_phone_page_is_locked_without_the_key(phone_client):
    locked = phone_client.get("/")
    assert locked.status_code == 403
    assert "scan the QR code" in locked.get_data(as_text=True)
    assert phone_client.get("/?k=wrong").status_code == 403
    assert phone_client.get("/api/state").status_code == 403


def test_scanning_the_qr_link_unlocks_the_phone(phone_client):
    opened = phone_client.get(f"/?k={KEY}")
    assert opened.status_code == 200
    page = opened.get_data(as_text=True)
    assert 'const MODE = "phone"' in page
    assert f'const TOKEN = "{KEY}"' in page
    cookie = opened.headers["Set-Cookie"]
    assert f"{phone.KEY_COOKIE}={KEY}" in cookie and "HttpOnly" in cookie
    # later visits (home-screen icon, iPhone Shortcut) work with the cookie alone
    assert phone_client.get("/?url=https://www.instagram.com/p/abc/").status_code == 200
    state = phone_client.get("/api/state", headers={"X-Token": KEY}).get_json()
    assert {"instagram.com"} <= set(state["platforms"][0]["domains"])


def test_desktop_only_buttons_explain_themselves_on_the_phone(phone_client):
    for path, message in [("signin", "Sign in on the PC"), ("open-folder", "on the PC"),
                          ("choose-folder", "desktop app window")]:
        answer = phone_client.post(f"/api/{path}", json={"platform": "instagram"}, headers={"X-Token": KEY})
        assert answer.status_code == 400
        assert message in answer.get_json()["error"]
    # the phone cannot switch phone mode itself
    assert phone_client.get("/api/phone", headers={"X-Token": KEY}).status_code == 404


def test_phone_downloads_finished_zips_but_nothing_else(phone_client, output_dir):
    output_dir.mkdir(parents=True)
    zip_path = output_dir / "instagram_natgeo.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("posts/photo1.jpg", b"jpg")
    (output_dir / "notes.txt").write_text("private")

    assert phone_client.get("/files/instagram_natgeo.zip").status_code == 403
    phone_client.get(f"/?k={KEY}")  # sets the cookie
    answer = phone_client.get("/files/instagram_natgeo.zip")
    assert answer.status_code == 200
    assert answer.data == zip_path.read_bytes()
    assert "attachment" in answer.headers["Content-Disposition"]
    assert phone_client.get("/files/notes.txt").status_code == 404
    assert phone_client.get("/files/..%2Fsettings.zip").status_code == 404
    assert phone_client.get("/files/missing.zip").status_code == 404


def test_home_screen_icon_and_manifest(phone_client):
    manifest = phone_client.get("/manifest.webmanifest").get_json(force=True)
    assert manifest["display"] == "standalone"
    assert "start_url" not in manifest  # keeps the ?k= link, so a home-screen icon stays unlocked
    for icon in manifest["icons"]:
        answer = phone_client.get(icon["src"])
        assert answer.status_code == 200 and answer.data[:4] == b"\x89PNG"
    assert phone_client.get("/static/apple-touch-icon.png").status_code == 200
    assert phone_client.get("/static/settings.json").status_code == 404


def test_desktop_switches_the_phone_server_on_and_off(output_dir):
    runner = social_saver.JobRunner()
    phone_server = phone.PhoneServer(
        lambda key: social_saver.create_app(runner, key, remote=True), host="127.0.0.1",
    )
    core.save_settings({**core.load_settings(), "phone_port": 0})
    client = social_saver.create_app(runner, DESKTOP_TOKEN, phone_server=phone_server).test_client()
    headers = {"X-Token": DESKTOP_TOKEN}
    try:
        assert client.get("/api/phone", headers=headers).get_json()["enabled"] is False
        status = client.post("/api/phone", json={"enabled": True}, headers=headers).get_json()
        assert status["enabled"] is True
        assert status["qr"].startswith("<svg")
        key = phone.phone_settings()["phone_key"]
        assert status["url"].endswith(f":{phone_server.port}/?k={key}")
        assert phone.phone_settings()["phone_enabled"] is True

        # a real request over the network socket, like a phone would make
        base = f"http://127.0.0.1:{phone_server.port}"
        session = requests.Session()
        session.trust_env = False  # no proxy for localhost
        assert session.get(base + "/", timeout=5).status_code == 403
        assert session.get(f"{base}/?k={key}", timeout=5).status_code == 200
        assert session.get(base + "/", timeout=5).status_code == 200  # cookie kept

        # a new key locks out phones that scanned the old QR code
        status = client.post("/api/phone", json={"new_key": True}, headers=headers).get_json()
        new_key = phone.phone_settings()["phone_key"]
        assert new_key != key and new_key in status["url"]
        base = f"http://127.0.0.1:{phone_server.port}"
        assert session.get(base + "/", timeout=5).status_code == 403

        status = client.post("/api/phone", json={"enabled": False}, headers=headers).get_json()
        assert status["enabled"] is False and not phone_server.running
        assert phone.phone_settings()["phone_enabled"] is False
    finally:
        phone_server.stop()


def test_busy_port_is_explained(output_dir):
    blocker = phone.PhoneServer(lambda key: social_saver.create_app(social_saver.JobRunner(), key, remote=True),
                                host="127.0.0.1")
    blocker.start(0, KEY)
    try:
        second = phone.PhoneServer(lambda key: social_saver.create_app(social_saver.JobRunner(), key, remote=True),
                                   host="127.0.0.1")
        with pytest.raises(ValueError, match="busy or blocked"):
            second.start(blocker.port, KEY)
    finally:
        blocker.stop()


def test_lan_address_looks_like_an_ipv4_address():
    parts = phone.lan_address().split(".")
    assert len(parts) == 4 and all(part.isdigit() for part in parts)


def test_save_command_downloads_a_shared_link(output_dir, capsys):
    shared_text = "Look at this account! https://www.instagram.com/natgeo?igsh=abc123"
    assert social_saver.main(["--save", shared_text]) == 0
    names = zipfile.ZipFile(output_dir / "instagram_natgeo.zip").namelist()
    assert "posts/photo1.jpg" in names and "reels/reel1.mp4" in names
    printed = capsys.readouterr().out
    assert "Saved 7 new file(s)" in printed
    assert social_saver.main(["--save", "not a link!"]) == 2


@pytest.mark.parametrize("text, expected", [
    ("https://www.instagram.com/reel/abc/", "https://www.instagram.com/reel/abc/"),
    ("Check this out https://x.com/nasa/status/1 via @nasa", "https://x.com/nasa/status/1"),
    ("see (https://vt.tiktok.com/ZSabc123/).", "https://vt.tiktok.com/ZSabc123/"),
    ("natgeo", "natgeo"),
])
def test_links_are_found_in_shared_text(text, expected):
    assert core.extract_link(text) == expected


def test_tiktok_short_share_links_are_one_post_not_a_user():
    target = core.parse_target("https://vt.tiktok.com/ZSabc123/", "instagram")
    assert target.platform == "tiktok" and target.url and not target.username


def test_android_termux_saves_to_the_phone_downloads(monkeypatch, tmp_path):
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    monkeypatch.setattr(core.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "storage" / "downloads").mkdir(parents=True)
    assert core.default_output_dir() == tmp_path / "storage" / "downloads" / "SocialSaver"


def test_command_line_help_lists_the_phone_options():
    result = subprocess.run(
        [sys.executable, str(Path(social_saver.__file__)), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    for option in ("--serve", "--save", "--host"):
        assert option in result.stdout
