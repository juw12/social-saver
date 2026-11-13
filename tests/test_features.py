"""Cookie import, the item picker and Threads paging - all without network."""
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest

import app as social_saver
from saver import core, threads

FAKE_ENGINE = Path(__file__).with_name("fake_engine.py")
TOKEN = "test-token"


# ---------------------------------------------------------------- cookie import

NETSCAPE_EXPORT = "\n".join([
    "# Netscape HTTP Cookie File",
    "# exported by a browser extension",
    "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\tsecret-session",
    ".instagram.com\tTRUE\t/\tTRUE\t1893456000\tcsrftoken\tcsrf",
    ".google.com\tTRUE\t/\tTRUE\t1893456000\tNID\tnot-for-us",
    "",
])

JSON_EXPORT = json.dumps([
    {"domain": ".x.com", "name": "auth_token", "value": "tok", "path": "/",
     "secure": True, "expirationDate": 1893456000.5},
    {"domain": ".x.com", "name": "ct0", "value": "csrf", "session": True},
    {"domain": ".example.com", "name": "other", "value": "1"},
])


def cookie_names(platform):
    from http.cookiejar import MozillaCookieJar

    jar = MozillaCookieJar(str(core.cookie_path(platform)))
    jar.load(ignore_discard=True, ignore_expires=True)
    return {cookie.name: cookie.value for cookie in jar}


def test_netscape_cookie_export_is_imported_with_httponly_lines():
    assert core.import_cookies("instagram", NETSCAPE_EXPORT) == 2
    assert core.is_signed_in("instagram")
    assert cookie_names("instagram") == {"sessionid": "secret-session", "csrftoken": "csrf"}


def test_json_cookie_export_is_imported():
    assert core.import_cookies("x", "\ufeff" + JSON_EXPORT) == 2
    assert core.is_signed_in("x")
    assert cookie_names("x") == {"auth_token": "tok", "ct0": "csrf"}


def test_wrong_or_logged_out_exports_are_refused_and_keep_the_old_sign_in():
    core.import_cookies("instagram", NETSCAPE_EXPORT)
    logged_out = ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tcsrf\n"
    with pytest.raises(ValueError, match="not the login one"):
        core.import_cookies("instagram", logged_out)
    with pytest.raises(ValueError, match="No TikTok cookies"):
        core.import_cookies("tiktok", NETSCAPE_EXPORT)
    with pytest.raises(ValueError, match="not valid JSON"):
        core.import_cookies("x", "[{broken")
    assert cookie_names("instagram")["sessionid"] == "secret-session"


# ---------------------------------------------------------------- picker helpers

def test_preview_items_number_files_like_range_does():
    messages = [
        [2, {"username": "a"}],
        [3, "https://cdn.example/1.jpg", {"extension": "jpg"}],
        [3, "https://cdn.example/2.mp4", {"extension": "mp4", "display_url": "https://cdn.example/2.jpg"}],
        [3, "https://cdn.example/3.mp4?x=1", {}],
    ]
    assert core.preview_items_from_engine(messages) == [
        {"index": 1, "video": False, "thumb": "https://cdn.example/1.jpg"},
        {"index": 2, "video": True, "thumb": "https://cdn.example/2.jpg"},
        {"index": 3, "video": True, "thumb": None},
    ]
    assert core.range_for_pick([3, 1, 3]) == "1,3"


def test_preview_engine_errors_are_raised():
    with pytest.raises(ValueError, match="AuthRequired"):
        core.preview_items_from_engine([[-1, {"error": "AuthRequired", "message": "login"}]])


# ---------------------------------------------------------------- Threads paging

def post(code, username="zuck", user_id="314"):
    return {
        "code": code,
        "user": {"username": username, "pk": user_id},
        "image_versions2": {"candidates": [{"url": f"https://cdn.example/{code}.jpg"},
                                           {"url": f"https://cdn.example/{code}_small.jpg"}]},
    }


def edge_list(posts, cursor):
    return {
        "edges": [{"node": {"thread_items": [{"post": item}]}} for item in posts],
        "page_info": {"has_next_page": cursor is not None, "end_cursor": cursor},
    }


def profile_page(posts, cursor, with_bundle=True):
    data = {"require": [{"mediaData": edge_list(posts, cursor)}]}
    bundle = ('<script src="https://static.cdninstagram.com/rsrc.php/v4/yX/r/abc.js" async></script>'
              if with_bundle else "")
    return (
        '<html><script>["LSD",[],{"token":"lsd-token"},323]</script>' + bundle
        + f'<script type="application/json" data-sjs>{json.dumps(data)}</script></html>'
    )


BUNDLE_JS = (
    '__d("BarcelonaProfileThreadsTabQuery_threadsRelayOperation",[],'
    '(function(a,b,c,d,e,f){e.exports="1111"}),null);'
    '__d("BarcelonaProfileThreadsTabRefetchableDirectQuery_threadsRelayOperation",[],'
    '(function(a,b,c,d,e,f){e.exports="9876"}),null);'
)


class FakeResponse:
    def __init__(self, text="", content=b"", headers=None):
        self.text = text
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        pass


class FakeThreadsSession:
    """Profile page (page 1) + /api/graphql pages 2 and 3."""

    def __init__(self, with_bundle=True):
        self.with_bundle = with_bundle
        self.forms = []
        self.media_requests = []
        self.graphql_pages = {
            "c1": "for (;;);" + json.dumps({"data": {"mediaData": edge_list([post("BBB"), post("CCC")], "c2")}}),
            "c2": json.dumps({"data": {"mediaData": edge_list([post("DDD")], None)}}),
        }

    def get(self, url, timeout=None):
        if url.startswith("https://www.threads.com/@"):
            return FakeResponse(text=profile_page([post("AAA")], "c1", self.with_bundle))
        if url.endswith(".js"):
            return FakeResponse(text=BUNDLE_JS)
        self.media_requests.append(url)
        return FakeResponse(content=b"media")

    def post(self, url, data=None, headers=None, timeout=None):
        assert url == threads.GRAPHQL_URL
        assert headers["X-FB-LSD"] == "lsd-token"
        self.forms.append(data)
        cursor = json.loads(data["variables"])["after"]
        return FakeResponse(text=self.graphql_pages[cursor])


def run_threads(tmp_path, session, limit=None, pick=None, url=None):
    log = []
    target = core.Target("threads", url=url) if url else core.Target("threads", username="zuck")
    count = threads.download(
        target, ["posts", "videos"], tmp_path / "staging", tmp_path / "out", limit,
        log.append, lambda: False, pick=pick, session=session, pause=0,
    )
    core.sort_staging_into_zips(tmp_path / "staging", tmp_path / "out")
    return count, log


def test_threads_profile_pages_back_through_older_posts(tmp_path):
    session = FakeThreadsSession()
    count, log = run_threads(tmp_path, session)

    assert count == 4
    assert sorted(zipfile.ZipFile(tmp_path / "out" / "threads_zuck.zip").namelist()) == [
        "posts/AAA_01.jpg", "posts/BBB_01.jpg", "posts/CCC_01.jpg", "posts/DDD_01.jpg",
    ]
    assert [form["doc_id"] for form in session.forms] == ["9876", "9876"]  # the Refetchable query
    assert [json.loads(form["variables"]) for form in session.forms] == [
        {"userID": "314", "after": "c1", "first": 25},
        {"userID": "314", "after": "c2", "first": 25},
    ]
    assert any("loaded 2 more page(s), 4 posts" in line for line in log)


def test_threads_paging_stops_at_the_newest_limit(tmp_path):
    session = FakeThreadsSession()
    count, _log = run_threads(tmp_path, session, limit=2)
    assert count == 2
    assert len(session.forms) == 1


def test_threads_without_paging_details_still_saves_the_newest_posts(tmp_path):
    session = FakeThreadsSession(with_bundle=False)
    count, log = run_threads(tmp_path, session)
    assert count == 1
    assert session.forms == []
    assert any("newest posts only" in line for line in log)


def test_threads_query_id_can_be_pinned_in_settings(tmp_path):
    core.save_settings({**core.load_settings(), "threads_doc_id": "5555"})
    session = FakeThreadsSession(with_bundle=False)
    count, _log = run_threads(tmp_path, session)
    assert count == 4
    assert {form["doc_id"] for form in session.forms} == {"5555"}


CAROUSEL = {
    "code": "CAR",
    "user": {"username": "zuck"},
    "carousel_media": [
        {"image_versions2": {"candidates": [{"url": "https://cdn.example/c1.jpg"}]}},
        {"video_versions": [{"url": "https://cdn.example/c2.mp4"}],
         "image_versions2": {"candidates": [{"url": "https://cdn.example/c2_cover.jpg"}]}},
        {"image_versions2": {"candidates": [{"url": "https://cdn.example/c3.jpg"}]}},
    ],
}


class FakePostSession(FakeThreadsSession):
    def get(self, url, timeout=None):
        if "/post/" in url:
            data = {"require": [{"thread_items": [{"post": CAROUSEL}]}]}
            return FakeResponse(text=f'<script type="application/json">{json.dumps(data)}</script>')
        return super().get(url, timeout)


def test_threads_picker_lists_items_and_saves_only_the_picked_ones(tmp_path):
    link = "https://www.threads.com/@zuck/post/CAR"
    target = core.Target("threads", url=link)
    assert threads.preview(target, session=FakePostSession()) == [
        {"index": 1, "video": False, "thumb": "https://cdn.example/c1.jpg"},
        {"index": 2, "video": True, "thumb": "https://cdn.example/c2_cover.jpg"},
        {"index": 3, "video": False, "thumb": "https://cdn.example/c3.jpg"},
    ]
    session = FakePostSession()
    count, _log = run_threads(tmp_path, session, pick=[2, 3], url=link)
    assert count == 2
    assert session.media_requests == ["https://cdn.example/c2.mp4", "https://cdn.example/c3.jpg"]
    assert sorted(zipfile.ZipFile(tmp_path / "out" / "threads_zuck.zip").namelist()) == [
        "posts/CAR_03.jpg", "videos/CAR_02.mp4",
    ]


# ---------------------------------------------------------------- picker + cookies through the API

@pytest.fixture
def saver_app(tmp_path, monkeypatch):
    monkeypatch.setattr(social_saver, "ENGINE_COMMAND", [sys.executable, str(FAKE_ENGINE)])
    core.save_settings({"output_dir": str(tmp_path / "out")})
    runner = social_saver.JobRunner()
    client = social_saver.create_app(runner, TOKEN).test_client()
    return client, runner, tmp_path / "out" / "instagram_natgeo.zip"


def api(client, path, body):
    return client.post(f"/api/{path}", json=body, headers={"X-Token": TOKEN})


def wait(runner):
    deadline = time.time() + 30
    while runner.running and time.time() < deadline:
        time.sleep(0.05)
    assert not runner.running


LINK = "https://www.instagram.com/p/CAROUSEL1/"


def test_picker_shows_the_post_items_and_saves_only_ticked_ones(saver_app, monkeypatch):
    client, runner, zip_path = saver_app
    preview = api(client, "preview", {"platform": "instagram", "targets": LINK})
    assert preview.status_code == 200, preview.get_json()
    body = preview.get_json()
    assert body["items"] == [
        {"index": 1, "video": False, "has_thumb": True},
        {"index": 2, "video": True, "has_thumb": True},
        {"index": 3, "video": False, "has_thumb": True},
    ]

    fetched = []

    def fake_get(self, url, timeout=None):
        fetched.append(url)
        return FakeResponse(content=b"\xff\xd8picture", headers={"Content-Type": "image/jpeg"})

    monkeypatch.setattr(social_saver.requests.Session, "get", fake_get)
    picture = client.get(f"/thumb/{body['id']}/2?t={TOKEN}")
    assert picture.status_code == 200
    assert picture.data == b"\xff\xd8picture"
    assert fetched == ["https://cdn.example/car2.mp4.preview.jpg"]
    assert client.get(f"/thumb/{body['id']}/2?t=wrong").status_code == 403
    assert client.get(f"/thumb/{body['id']}/9?t={TOKEN}").status_code == 404

    started = api(client, "start", {"platform": "instagram", "targets": LINK,
                                    "types": ["posts"], "pick": [1, 3]})
    assert started.status_code == 200, started.get_json()
    wait(runner)
    assert sorted(zipfile.ZipFile(zip_path).namelist()) == ["posts/car1.jpg", "posts/car3.jpg"]


@pytest.mark.parametrize("body, message", [
    ({"targets": "natgeo"}, "not a whole account"),
    ({"targets": LINK + "\n" + LINK}, "one post link at a time"),
])
def test_picker_needs_one_post_link(saver_app, body, message):
    client, _runner, _zip = saver_app
    response = api(client, "preview", {"platform": "instagram", **body})
    assert response.status_code == 400
    assert message in response.get_json()["error"]


def test_picked_download_must_match_the_previewed_link(saver_app):
    client, _runner, _zip = saver_app
    api(client, "preview", {"platform": "instagram", "targets": LINK})
    other = api(client, "start", {"platform": "instagram", "targets": LINK.replace("1", "2"),
                                  "types": ["posts"], "pick": [1]})
    assert other.status_code == 400
    assert "changed" in other.get_json()["error"]
    empty = api(client, "start", {"platform": "instagram", "targets": LINK,
                                  "types": ["posts"], "pick": []})
    assert "at least one item" in empty.get_json()["error"]


def test_cookie_import_through_the_api_signs_in(saver_app):
    client, runner, _zip = saver_app
    response = api(client, "import-cookies", {"platform": "instagram", "text": NETSCAPE_EXPORT})
    assert response.status_code == 200
    assert response.get_json()["count"] == 2
    state = client.get("/api/state", headers={"X-Token": TOKEN}).get_json()
    instagram = next(item for item in state["platforms"] if item["id"] == "instagram")
    assert instagram["signed_in"]
    assert any("Imported 2 Instagram cookies" in line for line in runner.log_lines)

    refused = api(client, "import-cookies", {"platform": "x", "text": NETSCAPE_EXPORT})
    assert refused.status_code == 400
