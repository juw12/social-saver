"""The web downloader (saver/webdl.py): links, listing stream, file proxy, zip, password."""
from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from saver import webdl

TIKTOK_ENTRY = {
    "url": "https://v16.tiktokcdn.example/video/abc.mp4?sig=1",
    "meta": {"user": "scout2015", "id": "6718335390845095173", "extension": "mp4", "num": 0,
             "desc": "Scramble up ur name & I'll try to guess it", "date": "2019-07-31 12:00:00"},
    "headers": {"User-Agent": "UA", "Cookie": "tt_chain_token=abc"},
    "thumb": "https://p16.tiktokcdn.example/cover.jpeg",
    "thumb_headers": {"User-Agent": "UA"},
}


class FakeUpstream:
    def __init__(self, status=200, body=b"data", headers=None):
        self.status_code = status
        self.body = body
        self.headers = {"Content-Type": "video/mp4", "Content-Length": str(len(body)), **(headers or {})}
        self.closed = False

    def iter_content(self, size):
        for start in range(0, len(self.body), 3):
            yield self.body[start:start + 3]

    def close(self):
        self.closed = True


def make_client(entries=None, error=None, opener=None, calls=None, threads_source=None):
    def lister(url, file_filter, start, count, cookie_file, category, should_stop):
        if calls is not None:
            calls.append({"url": url, "filter": file_filter, "start": start, "count": count,
                          "cookie_file": cookie_file, "category": category,
                          "cookie_text": Path(cookie_file).read_text() if cookie_file else None})
        for entry in (entries or [])[:count]:
            yield entry
        if error:
            raise webdl.ListingError(error)

    app = webdl.create_app(lister=lister, opener=opener or (lambda payload, byte_range=None: FakeUpstream()),
                           threads_source=threads_source)
    return app.test_client()


def read_stream(response):
    return [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line.strip()]


# ---------------------------------------------------------------- download links

def test_sealed_link_round_trips_and_hides_the_cookie():
    token = webdl.seal({"u": "https://cdn.example/a.mp4", "h": {"Cookie": "secret=1"}, "n": "a.mp4"})
    assert "secret" not in token and "cdn.example" not in token
    payload = webdl.unseal(token)
    assert payload["u"] == "https://cdn.example/a.mp4"
    assert payload["h"] == {"Cookie": "secret=1"}


def test_altered_link_is_refused():
    token = webdl.seal({"u": "https://cdn.example/a.mp4"})
    altered = token[:20] + ("A" if token[20] != "A" else "B") + token[21:]
    with pytest.raises(webdl.BadToken):
        webdl.unseal(altered)


def test_link_from_a_server_with_another_password_is_refused(monkeypatch):
    monkeypatch.setenv("SOCIAL_SAVER_PASSWORD", "one")
    token = webdl.seal({"u": "https://cdn.example/a.mp4"})
    monkeypatch.setenv("SOCIAL_SAVER_PASSWORD", "two")
    with pytest.raises(webdl.BadToken):
        webdl.unseal(token)


def test_expired_link_is_refused():
    token = webdl.seal({"u": "https://cdn.example/a.mp4"}, lifetime=-1)
    with pytest.raises(webdl.BadToken, match="expired"):
        webdl.unseal(token)


# ---------------------------------------------------------------- items

def test_tiktok_item_gets_a_readable_name_link_and_caption():
    item = webdl.describe("tiktok", TIKTOK_ENTRY, 1)
    assert item["name"] == "scout2015_6718335390845095173.mp4"
    assert item["video"] is True
    assert item["link"] == "https://www.tiktok.com/@scout2015/video/6718335390845095173"
    assert item["date"] == "2019-07-31"
    assert item["text"].startswith("Scramble")
    assert webdl.unseal(item["file"])["h"]["Cookie"] == "tt_chain_token=abc"
    assert webdl.unseal(item["thumb"])["i"] == 1


def test_tiktok_video_cover_is_used_as_the_thumbnail():
    metadata = {"extension": "mp4", "video": {"cover": "https://p16.tiktokcdn.example/c.jpeg", "duration": 9}}
    assert webdl.find_thumbnail(metadata, "https://v16.example/v.mp4") == "https://p16.tiktokcdn.example/c.jpeg"
    assert webdl.find_thumbnail({"extension": "mp4"}, "https://v16.example/v.mp4") is None
    assert webdl.find_thumbnail({"extension": "jpg"}, "https://cdn.example/p.jpg") == "https://cdn.example/p.jpg"


def test_instagram_carousel_and_x_names():
    instagram = {"url": "https://scontent.cdninstagram.com/v/p.jpg?x=1",
                 "meta": {"username": "natgeo", "post_shortcode": "DAqzT8PNTmA", "num": 2, "extension": "jpg"}}
    assert webdl.describe("instagram", instagram, 5)["name"] == "natgeo_DAqzT8PNTmA_2.jpg"
    x_post = {"url": "https://pbs.twimg.com/media/F.jpg?format=jpg&name=orig",
              "meta": {"user": {"name": "NASA"}, "tweet_id": 17, "num": 1, "extension": "jpg"}}
    item = webdl.describe("x", x_post, 1)
    assert item["name"] == "NASA_17_1.jpg"
    assert item["link"] == "https://x.com/i/status/17"


def test_strange_characters_never_reach_the_file_name():
    entry = {"url": "https://cdn.example/a", "meta": {"username": "../../evil name", "id": "1/2"}}
    name = webdl.describe("instagram", entry, 3)["name"]
    assert "/" not in name and ".." not in name and " " not in name


# ---------------------------------------------------------------- listing

def test_listing_streams_items_and_says_when_there_are_more():
    calls = []
    entries = [dict(TIKTOK_ENTRY, meta=dict(TIKTOK_ENTRY["meta"], id=str(n))) for n in range(5)]
    client = make_client(entries, calls=calls)
    response = client.post("/api/list", json={"query": "@scout2015", "platform": "tiktok", "count": 3})
    assert response.status_code == 200
    messages = read_stream(response)
    assert messages[0]["type"] == "start"
    items = [message["item"] for message in messages if message["type"] == "item"]
    assert [item["index"] for item in items] == [1, 2, 3]
    assert messages[-1] == {"type": "done", "more": True, "next": 4}
    assert calls[0]["url"] == "https://www.tiktok.com/@scout2015/posts"
    assert calls[0]["filter"] is None  # photos and videos together
    assert calls[0]["count"] == 4  # one extra to know whether there is a next page


def test_last_page_has_no_more():
    client = make_client([TIKTOK_ENTRY])
    messages = read_stream(client.post("/api/list", json={"query": "scout2015", "platform": "tiktok", "start": 25}))
    assert messages[-1] == {"type": "done", "more": False, "next": 26}
    assert messages[1]["item"]["index"] == 25


def test_a_post_link_switches_platform_by_itself():
    calls = []
    client = make_client([TIKTOK_ENTRY], calls=calls)
    link = "Look! https://www.tiktok.com/@scout2015/video/6718335390845095173?lang=en"
    messages = read_stream(client.post("/api/list", json={"query": link, "platform": "instagram"}))
    assert messages[0]["platform"] == "tiktok"
    assert calls[0]["url"] == "https://www.tiktok.com/@scout2015/video/6718335390845095173?lang=en"


def test_instagram_kinds_map_to_profile_pages():
    calls = []
    client = make_client([], calls=calls)
    read_stream(client.post("/api/list", json={"query": "natgeo", "platform": "instagram", "kind": "reels"}))
    read_stream(client.post("/api/list", json={"query": "natgeo", "platform": "instagram"}))
    assert calls[0]["url"] == "https://www.instagram.com/natgeo/reels/"
    assert calls[1]["url"] == "https://www.instagram.com/natgeo/posts/"


def test_login_wall_becomes_a_plain_cookie_hint():
    client = make_client(error="AuthRequired: HTTP redirect to login page")
    messages = read_stream(client.post("/api/list", json={"query": "natgeo", "platform": "instagram"}))
    assert messages[-1]["type"] == "error"
    assert "cookies" in messages[-1]["message"]


def test_pasted_cookies_reach_the_engine_and_are_deleted_after():
    calls = []
    client = make_client([TIKTOK_ENTRY], calls=calls)
    cookies = json.dumps([
        {"name": "sessionid", "value": "s1", "domain": ".instagram.com", "path": "/", "secure": True},
        {"name": "other", "value": "o", "domain": ".example.com"},
    ])
    read_stream(client.post("/api/list", json={"query": "natgeo", "platform": "instagram", "cookies": cookies}))
    assert calls[0]["category"] == "instagram"
    assert "sessionid\ts1" in calls[0]["cookie_text"]
    assert "example.com" not in calls[0]["cookie_text"]
    assert not Path(calls[0]["cookie_file"]).exists()


def test_cookies_for_another_site_are_refused():
    client = make_client()
    cookies = json.dumps([{"name": "a", "value": "b", "domain": ".example.com"}])
    response = client.post("/api/list", json={"query": "natgeo", "platform": "instagram", "cookies": cookies})
    assert response.status_code == 400
    assert "nothing for Instagram" in response.get_json()["error"]


def test_bad_names_and_other_sites_are_refused():
    client = make_client()
    assert client.post("/api/list", json={"query": "not a name!", "platform": "x"}).status_code == 400
    assert client.post("/api/list", json={"query": "https://example.com/someone", "platform": "x"}).status_code == 400


# ---------------------------------------------------------------- Threads

def threads_post(code, taken_at=1_700_000_000, caption=None, video=False, carousel=0):
    """A post the way the Threads page embeds it (biggest picture first, smallest last)."""
    def media(name):
        node = {"image_versions2": {"candidates": [{"url": f"https://cdn.example/{name}.jpg"},
                                                   {"url": f"https://cdn.example/{name}_small.jpg"}]}}
        if video:
            node["video_versions"] = [{"url": f"https://cdn.example/{name}.mp4"}]
        return node

    post = {"code": code, "user": {"username": "zuck", "pk": "314"}, "taken_at": taken_at,
            "caption": {"text": caption} if caption else None}
    if carousel:
        post["carousel_media"] = [media(f"{code}_{number}") for number in range(1, carousel + 1)]
    else:
        post.update(media(code))
    return post


TEXT_ONLY_POST = {"code": "TXT", "user": {"username": "zuck", "pk": "314"}, "caption": {"text": "words only"}}


def threads_edges(posts, cursor):
    return {"edges": [{"node": {"thread_items": [{"post": post}]}} for post in posts],
            "page_info": {"has_next_page": cursor is not None, "end_cursor": cursor}}


class FakeThreadsResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class FakeThreadsSession:
    """A profile page with two posts, then two older pages behind /api/graphql."""

    def __init__(self, first_page=None):
        self.first_page = first_page if first_page is not None else [
            threads_post("AAA", caption="First  post\nhere"), TEXT_ONLY_POST]
        self.older_pages = {"c1": (threads_edges([threads_post("BBB", video=True),
                                                  threads_post("CCC", carousel=2)], "c2")),
                            "c2": threads_edges([threads_post("DDD")], None)}
        self.pages_asked = []
        self.page_urls = []

    def get(self, url, timeout=None):
        self.page_urls.append(url)
        data = {"require": [{"mediaData": threads_edges(self.first_page, "c1")}]}
        return FakeThreadsResponse(
            '<html><script>["LSD",[],{"token":"lsd-token"},323]</script>'
            '<script>__d("BarcelonaProfileThreadsTabRefetchableDirectQuery_threadsRelayOperation",[],'
            '(function(a,b,c,d,e,f){e.exports="9876"}),null);</script>'
            f'<script type="application/json" data-sjs>{json.dumps(data)}</script></html>')

    def post(self, url, data=None, headers=None, timeout=None):
        cursor = json.loads(data["variables"])["after"]
        self.pages_asked.append(cursor)
        return FakeThreadsResponse(json.dumps({"data": {"mediaData": self.older_pages[cursor]}}))


def threads_client(session, calls=None):
    def source(target, start, count, cookie_file, should_stop):
        if calls is not None:
            calls.append({"target": target, "start": start, "count": count, "cookie_file": cookie_file,
                          "cookie_text": Path(cookie_file).read_text() if cookie_file else None})
        return webdl.threads_lister(target, start, count, cookie_file, should_stop, session=session, pause=0)

    return make_client(threads_source=source)


def test_threads_is_a_platform_on_the_page():
    platforms = make_client().get("/api/info").get_json()["platforms"]
    assert set(platforms) == {"instagram", "threads", "x", "tiktok"}
    assert platforms["threads"] == {"label": "Threads", "kinds": ["media"]}


@pytest.mark.parametrize("query, tab", [
    ("zuck", "threads"),
    ("@zuck", "threads"),
    ("https://www.threads.com/@zuck", "instagram"),            # a link picks the platform by itself
    ("https://www.threads.net/@zuck?igshid=abc123", "x"),      # the old address, with a tracking tail
    ("Look at this https://www.threads.com/@zuck", "tiktok"),  # text shared from the Threads app
])
def test_threads_username_or_account_link_lists_photos_and_videos(query, tab):
    session, calls = FakeThreadsSession(), []
    response = threads_client(session, calls).post("/api/list", json={"query": query, "platform": tab})
    assert response.status_code == 200
    messages = read_stream(response)
    assert messages[0] == {"type": "start", "platform": "threads", "target": "Threads @zuck"}
    assert calls[0]["target"] == webdl.core.Target("threads", username="zuck")
    assert session.page_urls == ["https://www.threads.com/@zuck"]
    items = [message["item"] for message in messages if message["type"] == "item"]
    assert [item["name"] for item in items] == [
        "zuck_AAA.jpg", "zuck_BBB.mp4", "zuck_CCC_1.jpg", "zuck_CCC_2.jpg", "zuck_DDD.jpg"]
    assert [item["video"] for item in items] == [False, True, False, False, False]
    assert messages[-1] == {"type": "done", "more": False, "next": 6}
    first = items[0]
    assert first["link"] == "https://www.threads.com/@zuck/post/AAA"
    assert first["text"] == "First post here"
    assert first["date"] == "2023-11-14"
    assert webdl.unseal(first["file"])["u"] == "https://cdn.example/AAA.jpg"      # the biggest picture
    assert webdl.unseal(first["thumb"])["u"] == "https://cdn.example/AAA_small.jpg"
    assert webdl.unseal(items[1]["file"])["u"] == "https://cdn.example/BBB.mp4"
    assert "Cookie" not in webdl.unseal(first["file"])["h"]


def test_threads_loads_older_posts_only_until_the_page_is_full():
    session = FakeThreadsSession()
    messages = read_stream(threads_client(session).post(
        "/api/list", json={"query": "zuck", "platform": "threads", "count": 2}))
    assert [message["item"]["name"] for message in messages[1:-1]] == ["zuck_AAA.jpg", "zuck_BBB.mp4"]
    assert messages[-1] == {"type": "done", "more": True, "next": 3}
    assert session.pages_asked == ["c1"]  # 4 photos/videos found: enough for 2 and "is there more?"

    next_page = FakeThreadsSession()
    messages = read_stream(threads_client(next_page).post(
        "/api/list", json={"query": "zuck", "platform": "threads", "count": 2, "start": 3}))
    assert [message["item"]["index"] for message in messages[1:-1]] == [3, 4]
    assert [message["item"]["name"] for message in messages[1:-1]] == ["zuck_CCC_1.jpg", "zuck_CCC_2.jpg"]
    assert messages[-1] == {"type": "done", "more": True, "next": 5}
    assert next_page.pages_asked == ["c1", "c2"]


def test_a_threads_post_link_lists_only_that_post():
    session = FakeThreadsSession(first_page=[threads_post("AAA"), threads_post("CCC", carousel=2)])
    link = "https://www.threads.com/@zuck/post/CCC?xmt=1"
    messages = read_stream(threads_client(session).post("/api/list", json={"query": link, "platform": "tiktok"}))
    assert messages[0]["platform"] == "threads"
    assert [message["item"]["name"] for message in messages[1:-1]] == ["zuck_CCC_1.jpg", "zuck_CCC_2.jpg"]
    assert session.pages_asked == []


def test_pasted_threads_cookies_sign_the_search_in_and_are_deleted_after():
    calls = []
    cookies = json.dumps([
        {"name": "sessionid", "value": "t1", "domain": ".threads.com", "path": "/", "secure": True},
        {"name": "sessionid", "value": "i1", "domain": ".instagram.com", "path": "/", "secure": True},
    ])
    read_stream(threads_client(FakeThreadsSession(), calls).post(
        "/api/list", json={"query": "zuck", "platform": "threads", "cookies": cookies}))
    assert "sessionid\tt1" in calls[0]["cookie_text"]
    assert "instagram.com" not in calls[0]["cookie_text"]
    assert not Path(calls[0]["cookie_file"]).exists()


def test_threads_cookie_file_is_loaded_into_the_session(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    webdl.core.write_cookie_file(cookie_file, [
        {"name": "sessionid", "value": "t1", "domain": ".threads.com", "path": "/", "secure": True}])
    session = webdl._threads_session(str(cookie_file))
    assert session.cookies.get("sessionid", domain=".threads.com") == "t1"
    assert "Chrome" in session.headers["User-Agent"]
    assert not webdl._threads_session(None).cookies


def test_threads_with_no_posts_says_what_to_try():
    session = FakeThreadsSession(first_page=[])
    messages = read_stream(threads_client(session).post("/api/list", json={"query": "zuck", "platform": "threads"}))
    assert messages[-1]["type"] == "error"
    assert messages[-1]["message"].startswith("Threads: no posts found")
    assert "cookies" in messages[-1]["message"]


def test_threads_that_cannot_be_reached_is_reported_not_crashed():
    class BlockedSession:
        def get(self, url, timeout=None):
            raise webdl.requests.ConnectionError("blocked")

    messages = read_stream(threads_client(BlockedSession()).post(
        "/api/list", json={"query": "zuck", "platform": "threads"}))
    assert messages[-1]["type"] == "error"
    assert "ConnectionError" in messages[-1]["detail"]


# ---------------------------------------------------------------- password

def test_password_guards_listing_but_signed_links_still_work(monkeypatch):
    monkeypatch.setenv("SOCIAL_SAVER_PASSWORD", "hunter2")
    client = make_client([TIKTOK_ENTRY])
    assert client.get("/api/info").get_json()["password"] is True
    assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"password": "hunter2"}).status_code == 200
    assert client.post("/api/list", json={"query": "a", "platform": "tiktok"}).status_code == 401
    response = client.post("/api/list", json={"query": "a", "platform": "tiktok"}, headers={"X-Password": "hunter2"})
    item = read_stream(response)[1]["item"]
    assert client.get("/api/file?t=" + item["file"]).status_code == 200


# ---------------------------------------------------------------- on your own PC

def test_phone_mode_makes_a_password_once_and_locks_the_page(monkeypatch):
    monkeypatch.delenv("SOCIAL_SAVER_PASSWORD", raising=False)
    address = webdl.start_phone_mode(8080)
    made = webdl.home_password()
    assert address.startswith("http://") and address.endswith(":8080/")
    assert len(made) == 10
    assert webdl.core.load_settings()["web_password"] == made  # kept, so the phone need not retype it
    client = make_client([TIKTOK_ENTRY])
    assert client.get("/api/info").get_json()["password"] is True
    assert client.post("/api/list", json={"query": "a", "platform": "tiktok"}).status_code == 401
    assert client.post("/api/login", json={"password": made}).status_code == 200


def test_phone_mode_keeps_a_password_you_set_yourself(monkeypatch):
    monkeypatch.setenv("SOCIAL_SAVER_PASSWORD", "hunter2")
    webdl.start_phone_mode(8080)
    assert webdl.home_password() == "hunter2"
    assert "web_password" not in webdl.core.load_settings()


# ---------------------------------------------------------------- files

def test_file_is_streamed_as_a_download_with_the_sites_headers():
    seen = {}

    def opener(payload, byte_range=None):
        seen.update(payload=payload, byte_range=byte_range)
        return FakeUpstream(body=b"0123456789")

    client = make_client(opener=opener)
    item = webdl.describe("tiktok", TIKTOK_ENTRY, 1)
    response = client.get("/api/file?t=" + item["file"])
    assert response.status_code == 200
    assert response.data == b"0123456789"
    assert response.headers["Content-Disposition"].startswith('attachment; filename="scout2015_')
    assert response.headers["Content-Type"] == "video/mp4"
    assert seen["payload"]["h"]["Cookie"] == "tt_chain_token=abc"


def test_preview_passes_the_byte_range_through():
    def opener(payload, byte_range=None):
        assert byte_range == "bytes=0-1"
        return FakeUpstream(status=206, body=b"01", headers={"Content-Range": "bytes 0-1/10"})

    client = make_client(opener=opener)
    item = webdl.describe("tiktok", TIKTOK_ENTRY, 1)
    response = client.get("/api/file?inline=1&t=" + item["file"], headers={"Range": "bytes=0-1"})
    assert response.status_code == 206
    assert response.headers["Content-Range"] == "bytes 0-1/10"
    assert response.headers["Content-Disposition"].startswith("inline")


def test_refused_file_and_bad_link_give_clear_errors():
    client = make_client(opener=lambda payload, byte_range=None: FakeUpstream(status=403))
    item = webdl.describe("tiktok", TIKTOK_ENTRY, 1)
    refused = client.get("/api/file?t=" + item["file"])
    assert refused.status_code == 502 and "403" in refused.get_json()["error"]
    assert client.get("/api/file?t=garbage").status_code == 403


def test_zip_holds_every_picked_file_and_lists_the_ones_that_failed():
    def opener(payload, byte_range=None):
        if "bad" in payload["u"]:
            return FakeUpstream(status=404)
        return FakeUpstream(body=payload["u"].encode() * 50)

    tokens = [webdl.seal({"u": f"https://cdn.example/{name}", "n": "same.jpg"}) for name in ("one", "two", "bad")]
    client = make_client(opener=opener)
    response = client.post("/api/zip", data={"t": tokens, "name": "tiktok_scout2015"})
    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith('attachment; filename="tiktok_scout2015.zip"')
    archive = zipfile.ZipFile(io.BytesIO(response.data))
    assert sorted(archive.namelist()) == ["NOT-SAVED.txt", "same.jpg", "same_2.jpg"]
    assert archive.read("same.jpg") == b"https://cdn.example/one" * 50
    assert archive.testzip() is None


def test_zip_without_picks_is_refused():
    assert make_client().post("/api/zip", data={}).status_code == 400


def test_page_and_manifest_are_served():
    client = make_client()
    page = client.get("/")
    assert page.status_code == 200 and b"Social Saver" in page.data
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/static/icon-192.png").status_code == 200


# ---------------------------------------------------------------- the real engine, against a local server

class _PictureServer(BaseHTTPRequestHandler):
    seen_headers: list = []

    def do_GET(self):  # noqa: N802 - http.server API
        _PictureServer.seen_headers.append(dict(self.headers))
        body = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_gallery_dl_lister_streams_files_with_the_headers_it_would_use():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PictureServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/pictures/cat.png"
        started = time.time()
        entries = list(webdl.gallery_dl_lister(url, None, 1, 5, None, None))
        assert time.time() - started < 30
    finally:
        server.shutdown()
    assert len(entries) == 1
    assert entries[0]["url"] == url
    assert entries[0]["meta"]["extension"] == "png"
    assert "User-Agent" in entries[0]["headers"]
    assert entries[0]["thumb"] == url


def test_gallery_dl_lister_reports_unsupported_links():
    with pytest.raises(webdl.ListingError):
        list(webdl.gallery_dl_lister("https://unsupported.example/nothing", None, 1, 5, None, None))
