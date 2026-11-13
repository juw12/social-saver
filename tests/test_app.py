"""The whole download flow through the local API, with a fake engine instead of gallery-dl."""
import sys
import time
import zipfile
from pathlib import Path

import pytest

import app as social_saver
from saver import core

FAKE_ENGINE = Path(__file__).with_name("fake_engine.py")
TOKEN = "test-token"


@pytest.fixture
def saver_app(tmp_path, monkeypatch):
    monkeypatch.setattr(social_saver, "ENGINE_COMMAND", [sys.executable, str(FAKE_ENGINE)])
    core.save_settings({"output_dir": str(tmp_path / "out")})
    runner = social_saver.JobRunner()
    client = social_saver.create_app(runner, TOKEN).test_client()
    return client, runner, tmp_path / "out" / "instagram_natgeo.zip"


def post(client, path, body=None):
    return client.post(f"/api/{path}", json=body or {}, headers={"X-Token": TOKEN})


def download(client, runner, **body):
    response = post(client, "start", {"platform": "instagram", "targets": "natgeo", **body})
    assert response.status_code == 200, response.get_json()
    deadline = time.time() + 30
    while runner.running and time.time() < deadline:
        time.sleep(0.05)
    assert not runner.running
    return runner.downloaded


def zip_names(zip_path):
    return sorted(zipfile.ZipFile(zip_path).namelist())


def test_api_needs_the_token(saver_app):
    client, _runner, _zip = saver_app
    assert client.get("/api/state").status_code == 403
    assert client.get("/api/state", headers={"X-Token": TOKEN}).status_code == 200
    assert "Social Saver" in client.get("/").get_data(as_text=True)


def test_chosen_types_land_in_their_folders_and_reruns_add_nothing(saver_app):
    client, runner, zip_path = saver_app

    assert download(client, runner, types=["posts"]) == 2
    assert zip_names(zip_path) == ["posts/photo1.jpg", "posts/photo2.jpg"]

    everything = ["posts", "reels", "stories", "highlights"]
    assert download(client, runner, types=everything) == 5
    assert zip_names(zip_path) == [
        "highlights/Trip/trip1.jpg", "highlights/Trip/trip2.mp4",
        "posts/photo1.jpg", "posts/photo2.jpg",
        "reels/reel1.mp4", "reels/reel2.mp4",
        "stories/story1.jpg",
    ]

    size_before = zip_path.stat().st_size
    assert download(client, runner, types=everything) == 0
    assert zip_path.stat().st_size == size_before
    assert any("already downloaded" in line for line in runner.log_lines)

    # Even with the history forgotten, files already in the zip are not added again.
    assert post(client, "forget-history").status_code == 200
    download(client, runner, types=everything)
    assert len(zip_names(zip_path)) == 7
    assert zip_path.stat().st_size == size_before

    state = client.get("/api/state", headers={"X-Token": TOKEN}).get_json()
    assert [entry["name"] for entry in state["zips"]] == ["instagram_natgeo.zip"]


def test_newest_limit_is_passed_to_the_engine(saver_app):
    client, runner, zip_path = saver_app
    assert download(client, runner, types=["reels"], limit="1") == 1
    assert zip_names(zip_path) == ["reels/reel1.mp4"]


@pytest.mark.parametrize("body, message", [
    ({"platform": "instagram", "targets": "", "types": ["posts"]}, "at least one username"),
    ({"platform": "instagram", "targets": "natgeo", "types": []}, "Tick at least one"),
    ({"platform": "instagram", "targets": "not a name!", "types": ["posts"]}, "not a valid"),
    ({"platform": "myspace", "targets": "natgeo", "types": ["posts"]}, "Pick a platform"),
])
def test_bad_requests_are_explained(saver_app, body, message):
    client, _runner, _zip = saver_app
    response = post(client, "start", body)
    assert response.status_code == 400
    assert message in response.get_json()["error"]
