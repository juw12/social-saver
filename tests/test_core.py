import zipfile
from pathlib import Path

import pytest

from saver import core, threads


# ---------------------------------------------------------------- targets

@pytest.mark.parametrize("text, expected", [
    ("natgeo", core.Target("instagram", username="natgeo")),
    ("  @nat.geo_1 ", core.Target("instagram", username="nat.geo_1")),
    ("https://www.instagram.com/natgeo/", core.Target("instagram", username="natgeo")),
    ("x.com/NASA", core.Target("x", username="NASA")),
    ("https://twitter.com/NASA", core.Target("x", username="NASA")),
    ("https://www.tiktok.com/@nasa?lang=en", core.Target("tiktok", username="nasa")),
    ("https://www.threads.net/@zuck", core.Target("threads", username="zuck")),
    (
        "https://www.instagram.com/reel/Cabc123/",
        core.Target("instagram", url="https://www.instagram.com/reel/Cabc123/"),
    ),
    (
        "https://x.com/NASA/status/123?s=20",
        core.Target("x", url="https://x.com/NASA/status/123?s=20"),
    ),
])
def test_parse_target(text, expected):
    assert core.parse_target(text, "instagram") == expected


@pytest.mark.parametrize("text", ["bad name!", "https://youtube.com/watch?v=1", ""])
def test_parse_target_rejects_what_it_cannot_download(text):
    with pytest.raises(ValueError):
        core.parse_target(text, "instagram")


# ---------------------------------------------------------------- download plans

def test_instagram_plan_has_one_run_per_ticked_type():
    runs = core.plan_runs(core.Target("instagram", username="natgeo"),
                          ["posts", "reels", "stories", "highlights"])
    assert [run.url for run in runs] == [
        "https://www.instagram.com/natgeo/reels/",
        "https://www.instagram.com/natgeo/posts/",
        "https://www.instagram.com/stories/natgeo/",
        "https://www.instagram.com/natgeo/highlights/",
    ]
    assert all(run.file_filter is None for run in runs)


def test_instagram_posts_only_keeps_reels_out():
    runs = core.plan_runs(core.Target("instagram", username="natgeo"), ["posts"])
    assert runs == [core.EngineRun("https://www.instagram.com/natgeo/posts/", "type == 'post'")]


@pytest.mark.parametrize("types, expected_filter", [
    (["posts", "videos"], None),
    (["videos"], core.VIDEO_FILTER),
    (["posts"], core.PHOTO_FILTER),
])
def test_x_plan_splits_photos_and_videos_with_a_filter(types, expected_filter):
    runs = core.plan_runs(core.Target("x", username="NASA"), types)
    assert runs == [core.EngineRun("https://x.com/NASA/media", expected_filter)]


def test_tiktok_plan():
    runs = core.plan_runs(core.Target("tiktok", username="nasa"), ["videos", "stories"])
    assert runs == [
        core.EngineRun("https://www.tiktok.com/@nasa/posts", core.VIDEO_FILTER),
        core.EngineRun("https://www.tiktok.com/@nasa/stories"),
    ]


def test_single_link_is_downloaded_as_is():
    target = core.Target("instagram", url="https://www.instagram.com/p/Cabc/")
    assert core.plan_runs(target, []) == [core.EngineRun("https://www.instagram.com/p/Cabc/")]


# ------------------------------------------- engine config, checked against real gallery-dl

def staged_folder(tmp_path, url, metadata):
    """Folder real gallery-dl would stage a file in, relative to the staging dir."""
    from gallery_dl import config, extractor, path

    staging_dir = tmp_path / "staging"
    engine_config = core.build_engine_config(staging_dir, tmp_path / "history.sqlite3")
    config.clear()
    config.set((), "extractor", engine_config["extractor"])
    try:
        found = extractor.find(url)
        assert found is not None, f"gallery-dl has no extractor for {url}"
        path_format = path.PathFormat(found)
        path_format.set_directory({"category": found.category,
                                   "subcategory": found.subcategory, **metadata})
        return Path(path_format.directory).relative_to(staging_dir).as_posix()
    finally:
        config.clear()


@pytest.mark.parametrize("url, metadata, expected_folder, zip_folder", [
    ("https://www.instagram.com/natgeo/reels/", {"username": "natgeo", "type": "reel"},
     "instagram/natgeo/reel", "reels"),
    ("https://www.instagram.com/natgeo/posts/", {"username": "natgeo", "type": "post"},
     "instagram/natgeo/post", "posts"),
    ("https://www.instagram.com/stories/natgeo/", {"username": "natgeo", "type": "story"},
     "instagram/natgeo/story", "stories"),
    ("https://www.instagram.com/natgeo/highlights/",
     {"username": "natgeo", "type": "highlight", "highlight_title": "Trip", "post_id": "17"},
     "instagram/natgeo/highlights/Trip", "highlights/Trip"),
    ("https://www.instagram.com/natgeo/highlights/",
     {"username": "natgeo", "type": "highlight", "post_id": "17"},
     "instagram/natgeo/highlights/17", "highlights/17"),
    ("https://x.com/NASA/media", {"user": {"name": "NASA"}}, "x/NASA/media", "videos"),
    ("https://www.tiktok.com/@nasa/posts", {"user": "nasa"}, "tiktok/nasa/media", "videos"),
    ("https://www.tiktok.com/@nasa/stories", {"user": "nasa"}, "tiktok/nasa/stories", "stories"),
])
def test_real_engine_stages_files_where_the_zip_sorter_expects(
        tmp_path, url, metadata, expected_folder, zip_folder):
    folder = staged_folder(tmp_path, url, metadata)
    assert folder == expected_folder
    kind_path = Path(*Path(folder).parts[2:]) / "clip.mp4"
    assert core.archive_name(kind_path) == f"{zip_folder}/clip.mp4"


def test_engine_config_uses_cookies_only_when_signed_in(tmp_path):
    signed_out = core.build_engine_config(tmp_path / "staging", tmp_path / "history.sqlite3")
    assert "cookies" not in signed_out["extractor"]["instagram"]

    core.write_cookie_file(core.cookie_path("instagram"), [
        {"name": "sessionid", "value": "abc", "domain": ".instagram.com", "secure": True},
    ])
    signed_in = core.build_engine_config(tmp_path / "staging", tmp_path / "history.sqlite3")
    assert signed_in["extractor"]["instagram"]["cookies"] == str(core.cookie_path("instagram"))
    assert "cookies" not in signed_in["extractor"]["twitter"]


# ---------------------------------------------------------------- sign-in cookies

def test_cookie_file_round_trip_and_sign_out():
    from http.cookiejar import MozillaCookieJar

    assert not core.is_signed_in("x")
    core.write_cookie_file(core.cookie_path("x"), [
        {"name": "auth_token", "value": "secret", "domain": ".x.com", "path": "/",
         "secure": True, "expires": "Wed, 01 Jan 2031 00:00:00 GMT"},
        {"name": "ct0", "value": "csrf", "domain": "x.com", "expires": ""},
    ])
    assert core.is_signed_in("x")

    jar = MozillaCookieJar(str(core.cookie_path("x")))
    jar.load(ignore_discard=True, ignore_expires=True)
    assert {cookie.name: cookie.value for cookie in jar} == {"auth_token": "secret", "ct0": "csrf"}

    core.sign_out("x")
    assert not core.is_signed_in("x")


def test_cookies_without_the_login_cookie_do_not_count_as_signed_in():
    core.write_cookie_file(core.cookie_path("instagram"), [
        {"name": "csrftoken", "value": "x", "domain": ".instagram.com"},
    ])
    assert not core.is_signed_in("instagram")


# ---------------------------------------------------------------- zips

@pytest.mark.parametrize("staged, expected", [
    ("reel/a.mp4", "reels/a.mp4"),
    ("post/a.jpg", "posts/a.jpg"),
    ("story/a.jpg", "stories/a.jpg"),
    ("highlights/Trip/a.jpg", "highlights/Trip/a.jpg"),
    ("media/a.MP4", "videos/a.MP4"),
    ("media/a.jpg", "posts/a.jpg"),
    ("stories/a.mp4", "stories/a.mp4"),
])
def test_archive_name(staged, expected):
    assert core.archive_name(Path(staged)) == expected


def stage(staging_dir, relative_path, content=b"data"):
    staged_file = staging_dir / relative_path
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    staged_file.write_bytes(content)


def test_sorting_makes_type_folders_and_never_adds_a_file_twice(tmp_path):
    staging_dir, output_dir = tmp_path / "staging", tmp_path / "out"
    stage(staging_dir, "instagram/natgeo/reel/r1.mp4")
    stage(staging_dir, "instagram/natgeo/post/p1.jpg")
    stage(staging_dir, "instagram/natgeo/highlights/Trip/h1.jpg")
    stage(staging_dir, "x/NASA/media/v1.mp4")
    stage(staging_dir, "x/NASA/media/i1.jpg")

    added = core.sort_staging_into_zips(staging_dir, output_dir)
    natgeo_zip, nasa_zip = output_dir / "instagram_natgeo.zip", output_dir / "x_NASA.zip"
    assert added == {natgeo_zip: 3, nasa_zip: 2}
    assert sorted(zipfile.ZipFile(natgeo_zip).namelist()) == [
        "highlights/Trip/h1.jpg", "posts/p1.jpg", "reels/r1.mp4",
    ]
    assert sorted(zipfile.ZipFile(nasa_zip).namelist()) == ["posts/i1.jpg", "videos/v1.mp4"]
    assert not any(staging_dir.rglob("*.*")), "staged files are removed once zipped"

    # Same reel staged again plus one new one: only the new one is added.
    stage(staging_dir, "instagram/natgeo/reel/r1.mp4")
    stage(staging_dir, "instagram/natgeo/reel/r2.mp4")
    assert core.sort_staging_into_zips(staging_dir, output_dir) == {natgeo_zip: 1}
    names = zipfile.ZipFile(natgeo_zip).namelist()
    assert len(names) == len(set(names)) == 4
    assert zipfile.ZipFile(natgeo_zip).testzip() is None


# ---------------------------------------------------------------- messages

def test_known_engine_errors_get_a_plain_hint():
    line = "[twitter][error] AuthRequired: authenticated cookies needed"
    assert "Sign in" in core.explain_engine_message(line, "X")
    assert "blocked" in core.explain_engine_message("403 Client Error: Forbidden", "TikTok")
    assert core.explain_engine_message("[download][info] something normal", "X") is None


# ---------------------------------------------------------------- Threads (beta)

def threads_page(*posts):
    import json

    data = {"require": [{"deep": {"thread_items": [{"post": post} for post in posts]}}]}
    return f'<html><script type="application/json" data-sjs>{json.dumps(data)}</script></html>'


PHOTO_POST = {
    "code": "AAA",
    "user": {"username": "zuck"},
    "image_versions2": {"candidates": [{"url": "https://cdn.example/a_big.jpg"},
                                       {"url": "https://cdn.example/a_small.jpg"}]},
}
CAROUSEL_POST = {
    "code": "BBB",
    "user": {"username": "zuck"},
    "carousel_media": [
        {"video_versions": [{"url": "https://cdn.example/b1.mp4"}],
         "image_versions2": {"candidates": [{"url": "https://cdn.example/b1_cover.jpg"}]}},
        {"image_versions2": {"candidates": [{"url": "https://cdn.example/b2.jpg"}]}},
    ],
}
TEXT_POST = {"code": "CCC", "user": {"username": "zuck"}, "image_versions2": {"candidates": []}}


def test_threads_posts_are_found_once_and_media_listed():
    posts = threads.find_posts(threads_page(PHOTO_POST, CAROUSEL_POST, PHOTO_POST, TEXT_POST))
    assert [post["code"] for post in posts] == ["AAA", "BBB", "CCC"]
    assert threads.media_files(posts[0]) == [("AAA_01.jpg", "https://cdn.example/a_big.jpg")]
    assert threads.media_files(posts[1]) == [
        ("BBB_01.mp4", "https://cdn.example/b1.mp4"),
        ("BBB_02.jpg", "https://cdn.example/b2.jpg"),
    ]
    assert threads.media_files(posts[2]) == []


def test_threads_download_respects_types_and_skips_saved_files(tmp_path, monkeypatch):
    class FakeResponse:
        def __init__(self, text="", content=b""):
            self.text, self.content = text, content

        def raise_for_status(self):
            pass

    requested = []

    def fake_get(self, url, timeout):
        requested.append(url)
        if "threads.com" in url:
            return FakeResponse(text=threads_page(PHOTO_POST, CAROUSEL_POST))
        return FakeResponse(content=b"media")

    monkeypatch.setattr(threads.requests.Session, "get", fake_get)
    staging_dir, output_dir = tmp_path / "staging", tmp_path / "out"
    target = core.Target("threads", username="zuck")

    def run(types):
        count = threads.download(target, types, staging_dir, output_dir, None,
                                 lambda message: None, lambda: False)
        core.sort_staging_into_zips(staging_dir, output_dir)
        return count

    assert run(["videos"]) == 1
    assert run(["posts", "videos"]) == 2          # the video is already in the zip
    assert run(["posts", "videos"]) == 0
    assert sorted(zipfile.ZipFile(output_dir / "threads_zuck.zip").namelist()) == [
        "posts/AAA_01.jpg", "posts/BBB_02.jpg", "videos/BBB_01.mp4",
    ]
    assert requested.count("https://cdn.example/b1.mp4") == 1
