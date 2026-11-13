"""Threads downloader (beta).

gallery-dl and yt-dlp have no Threads support, so this reads the post data that the
Threads web page embeds in its HTML. That covers the newest posts shown on first load
of a profile (or one post link); it does not scroll further back.
"""
from __future__ import annotations

import json
import re
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Callable

import requests

from . import core

JSON_SCRIPT = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S)
POST_CODE_IN_URL = re.compile(r"/post/([A-Za-z0-9_-]+)")
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


def _values_for_key(node, key: str):
    """Yield every value stored under `key` anywhere inside nested JSON."""
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key:
                yield value
            else:
                yield from _values_for_key(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _values_for_key(item, key)


def find_posts(page_html: str) -> list[dict]:
    """Post objects embedded in a Threads page, in page order, without repeats."""
    posts: list[dict] = []
    seen_codes: set[str] = set()
    for block in JSON_SCRIPT.findall(page_html):
        if '"thread_items"' not in block:
            continue
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for thread_items in _values_for_key(data, "thread_items"):
            for item in thread_items or []:
                post = item.get("post") if isinstance(item, dict) else None
                if post and post.get("code") and post["code"] not in seen_codes:
                    seen_codes.add(post["code"])
                    posts.append(post)
    return posts


def media_files(post: dict) -> list[tuple[str, str]]:
    """(file name, url) for every photo/video in one post."""
    nodes = post.get("carousel_media") or [post]
    files = []
    for index, node in enumerate(nodes, start=1):
        videos = node.get("video_versions") or []
        photos = (node.get("image_versions2") or {}).get("candidates") or []
        if videos:
            files.append((f"{post['code']}_{index:02d}.mp4", videos[0]["url"]))
        elif photos:
            files.append((f"{post['code']}_{index:02d}.jpg", photos[0]["url"]))
    return files


def download(
    target: core.Target,
    types: list[str],
    staging_dir: Path,
    output_dir: Path,
    limit: int | None,
    log: Callable[[str], None],
    cancelled: Callable[[], bool],
) -> int:
    """Stage new Threads media for one target; returns the number of files downloaded."""
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    if core.is_signed_in("threads"):
        jar = MozillaCookieJar(str(core.cookie_path("threads")))
        jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies.update(jar)

    page_url = target.url or f"https://www.threads.com/@{target.username}"
    response = session.get(page_url, timeout=30)
    response.raise_for_status()
    posts = find_posts(response.text)

    if target.url:
        code_match = POST_CODE_IN_URL.search(target.url)
        if code_match:
            posts = [post for post in posts if post["code"] == code_match.group(1)]
    else:
        posts = [
            post for post in posts
            if (post.get("user") or {}).get("username", "").lower() == target.username.lower()
        ]
    if not posts:
        log("Threads: no posts found on that page (private account, or sign-in needed).")
        return 0
    if limit:
        posts = posts[:limit]

    wanted_videos = target.url is not None or "videos" in types
    wanted_photos = target.url is not None or "posts" in types
    downloaded = 0
    for post in posts:
        account = (post.get("user") or {}).get("username") or target.username or "unknown"
        if not core.USERNAME_PATTERN.match(account):
            continue
        already_saved = core.saved_file_names(core.zip_path_for(output_dir, "threads", account))
        account_dir = staging_dir / "threads" / account / core.MIXED_MEDIA_KIND
        for file_name, url in media_files(post):
            if cancelled():
                return downloaded
            is_video = file_name.endswith(".mp4")
            if (is_video and not wanted_videos) or (not is_video and not wanted_photos):
                continue
            if file_name in already_saved:
                continue
            account_dir.mkdir(parents=True, exist_ok=True)
            media = session.get(url, timeout=120)
            media.raise_for_status()
            (account_dir / file_name).write_bytes(media.content)
            downloaded += 1
            log(f"saved {file_name}")
    return downloaded
