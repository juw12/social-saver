"""Threads downloader (beta).

gallery-dl and yt-dlp have no Threads support, so this reads the post data that the
Threads web page embeds in its HTML. A profile page holds only the newest posts; older
ones are loaded the way the Threads web app does it: a POST to /api/graphql with the
page's `lsd` token, the profile query's `doc_id` (found at run time in the page's script
bundles, because Meta changes it often) and the `end_cursor` of the previous page.
If any of those cannot be found, the newest posts are still saved.
"""
from __future__ import annotations

import json
import re
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Callable

import requests

from . import core

JSON_SCRIPT = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S)
POST_CODE_IN_URL = re.compile(r"/post/([A-Za-z0-9_-]+)")
LSD_TOKEN = re.compile(r'"LSD",\[\],\{"token":"([^"]+)"')
USER_ID_IN_PAGE = re.compile(r'"user_id":"(\d+)"')
SCRIPT_BUNDLE = re.compile(r'<script[^>]+src="(https://[^"]+?\.js[^"]*)"')
# Relay operation modules look like:
#   __d("BarcelonaProfileThreadsTabRefetchableDirectQuery_threadsRelayOperation",[],
#       (function(a,b,c,d,e,f){e.exports="1234567890"}),null);
PROFILE_QUERY = re.compile(
    r'__d\("(BarcelonaProfileThreads\w*?Query)_\w*?RelayOperation",\[\],'
    r'\(function\([^)]*\)\{\w+\.exports="(\d+)"'
)
GRAPHQL_URL = "https://www.threads.com/api/graphql"
IG_APP_ID = "238260118697367"
MAX_PAGES = 100
MAX_BUNDLES_SCANNED = 60
PAGE_PAUSE_SECONDS = 1.5
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


def _page_json_blocks(page_html: str) -> list:
    """Parsed JSON script blocks of a Threads page that carry post data."""
    blocks = []
    for block in JSON_SCRIPT.findall(page_html):
        if '"thread_items"' not in block:
            continue
        try:
            blocks.append(json.loads(block))
        except ValueError:
            continue
    return blocks


def _collect_posts(data, posts: list[dict], seen_codes: set[str]) -> None:
    for thread_items in _values_for_key(data, "thread_items"):
        for item in thread_items or []:
            post = item.get("post") if isinstance(item, dict) else None
            if post and post.get("code") and post["code"] not in seen_codes:
                seen_codes.add(post["code"])
                posts.append(post)


def find_posts(page_html: str) -> list[dict]:
    """Post objects embedded in a Threads page, in page order, without repeats."""
    posts: list[dict] = []
    seen_codes: set[str] = set()
    for data in _page_json_blocks(page_html):
        _collect_posts(data, posts, seen_codes)
    return posts


def find_page_info(data) -> dict:
    """The {has_next_page, end_cursor} of a post list, or {} when there is none."""
    for page_info in _values_for_key(data, "page_info"):
        if isinstance(page_info, dict) and "end_cursor" in page_info:
            return page_info
    return {}


def parse_graphql(text: str):
    """JSON of a /api/graphql answer (Meta prefixes some answers with 'for (;;);')."""
    text = text.strip()
    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):]
    # Streamed answers can hold several JSON documents, one per line.
    documents = []
    for line in text.splitlines() or [text]:
        line = line.strip()
        if line:
            try:
                documents.append(json.loads(line))
            except ValueError:
                pass
    if not documents:
        raise ValueError("Threads sent an answer that is not JSON")
    return documents[0] if len(documents) == 1 else documents


def find_profile_query_id(page_html: str, fetch_text: Callable[[str], str]) -> str | None:
    """doc_id of the query that loads older profile posts.

    Prefers the 'Refetchable' (load more) query. Looks in the page first, then in the
    script bundles the page loads.
    """
    def best(text: str) -> str | None:
        found = PROFILE_QUERY.findall(text)
        for name, query_id in found:
            if "Refetchable" in name:
                return query_id
        return found[0][1] if found else None

    query_id = best(page_html)
    if query_id:
        return query_id
    for bundle_url in SCRIPT_BUNDLE.findall(page_html)[:MAX_BUNDLES_SCANNED]:
        try:
            query_id = best(fetch_text(bundle_url.replace("&amp;", "&")))
        except requests.RequestException:
            continue
        if query_id:
            return query_id
    return None


def media_items(post: dict) -> list[dict]:
    """Every photo/video in one post: file name, download url, picker picture."""
    nodes = post.get("carousel_media") or [post]
    items = []
    for index, node in enumerate(nodes, start=1):
        videos = node.get("video_versions") or []
        photos = (node.get("image_versions2") or {}).get("candidates") or []
        picture = photos[-1]["url"] if photos else None  # smallest candidate is last
        if videos:
            items.append({
                "name": f"{post['code']}_{index:02d}.mp4",
                "url": videos[0]["url"],
                "video": True,
                "thumb": picture,
            })
        elif photos:
            items.append({
                "name": f"{post['code']}_{index:02d}.jpg",
                "url": photos[0]["url"],
                "video": False,
                "thumb": picture,
            })
    return items


def media_files(post: dict) -> list[tuple[str, str]]:
    """(file name, url) for every photo/video in one post."""
    return [(item["name"], item["url"]) for item in media_items(post)]


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    if core.is_signed_in("threads"):
        jar = MozillaCookieJar(str(core.cookie_path("threads")))
        jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies.update(jar)
    return session


def _post_owner(post: dict) -> str:
    return (post.get("user") or {}).get("username", "")


def _profile_user_id(posts: list[dict], page_html: str) -> str | None:
    for post in posts:
        user = post.get("user") or {}
        user_id = user.get("pk") or user.get("id")
        if user_id:
            return str(user_id)
    match = USER_ID_IN_PAGE.search(page_html)
    return match.group(1) if match else None


def load_older_posts(
    session,
    page_url: str,
    page_html: str,
    username: str,
    posts: list[dict],
    limit: int | None,
    log: Callable[[str], None],
    cancelled: Callable[[], bool],
    pause: float = PAGE_PAUSE_SECONDS,
) -> list[dict]:
    """Keep loading older posts of one profile until the end, the limit or a stop."""
    page_info = {}
    for data in _page_json_blocks(page_html):
        page_info = find_page_info(data)
        if page_info:
            break
    cursor = page_info.get("end_cursor") if page_info.get("has_next_page") else None
    if not cursor or (limit and len(posts) >= limit):
        return posts

    lsd_match = LSD_TOKEN.search(page_html)
    user_id = _profile_user_id(posts, page_html)
    query_id = core.load_settings().get("threads_doc_id") or find_profile_query_id(
        page_html, lambda url: session.get(url, timeout=30).text,
    )
    if not (lsd_match and user_id and query_id):
        log("Threads: saving the newest posts only - could not find how the page loads older ones.")
        return posts

    lsd = lsd_match.group(1)
    headers = {
        "X-FB-LSD": lsd,
        "X-IG-App-ID": IG_APP_ID,
        "Origin": "https://www.threads.com",
        "Referer": page_url,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    seen_codes = {post["code"] for post in posts}
    pages = 0
    while cursor and pages < MAX_PAGES and not cancelled():
        if limit and len(posts) >= limit:
            break
        time.sleep(pause)
        form = {
            "lsd": lsd,
            "doc_id": query_id,
            "variables": json.dumps({"userID": user_id, "after": cursor, "first": 25}),
        }
        try:
            response = session.post(GRAPHQL_URL, data=form, headers=headers, timeout=30)
            response.raise_for_status()
            data = parse_graphql(response.text)
        except (requests.RequestException, ValueError) as error:
            log(f"Threads: stopped loading older posts ({error}). Saving what was found.")
            break
        found: list[dict] = []
        _collect_posts(data, found, seen_codes)
        found = [post for post in found if _post_owner(post).lower() == username.lower()]
        if not found:
            break
        posts.extend(found)
        pages += 1
        page_info = find_page_info(data)
        cursor = page_info.get("end_cursor") if page_info.get("has_next_page") else None
    if pages:
        log(f"Threads: loaded {pages} more page(s), {len(posts)} posts in total.")
    return posts


def _load_posts(session, target: core.Target, limit, log, cancelled, pause) -> list[dict]:
    page_url = target.url or f"https://www.threads.com/@{target.username}"
    response = session.get(page_url, timeout=30)
    response.raise_for_status()
    posts = find_posts(response.text)

    if target.url:
        code_match = POST_CODE_IN_URL.search(target.url)
        if code_match:
            posts = [post for post in posts if post["code"] == code_match.group(1)]
        return posts

    posts = [post for post in posts if _post_owner(post).lower() == target.username.lower()]
    if posts:
        posts = load_older_posts(
            session, page_url, response.text, target.username, posts,
            limit, log, cancelled, pause,
        )
    return posts


def preview(target: core.Target, session=None) -> list[dict]:
    """Picker items for one Threads post link, numbered like download(pick=...) counts them."""
    session = session or new_session()
    posts = _load_posts(session, target, None, lambda _message: None, lambda: False, 0)
    items = []
    for post in posts:
        for item in media_items(post):
            items.append({"index": len(items) + 1, "video": item["video"], "thumb": item["thumb"]})
    return items


def download(
    target: core.Target,
    types: list[str],
    staging_dir: Path,
    output_dir: Path,
    limit: int | None,
    log: Callable[[str], None],
    cancelled: Callable[[], bool],
    pick: list[int] | None = None,
    session=None,
    pause: float = PAGE_PAUSE_SECONDS,
) -> int:
    """Stage new Threads media for one target; returns the number of files downloaded."""
    session = session or new_session()
    posts = _load_posts(session, target, limit, log, cancelled, pause)
    if not posts:
        log("Threads: no posts found on that page (private account, or sign-in needed).")
        return 0
    if limit:
        posts = posts[:limit]

    wanted_videos = target.url is not None or "videos" in types
    wanted_photos = target.url is not None or "posts" in types
    picked = set(pick or [])
    downloaded = 0
    item_number = 0
    for post in posts:
        account = _post_owner(post) or target.username or "unknown"
        if not core.USERNAME_PATTERN.match(account):
            continue
        already_saved = core.saved_file_names(core.zip_path_for(output_dir, "threads", account))
        account_dir = staging_dir / "threads" / account / core.MIXED_MEDIA_KIND
        for item in media_items(post):
            item_number += 1
            if cancelled():
                return downloaded
            if picked and item_number not in picked:
                continue
            if (item["video"] and not wanted_videos) or (not item["video"] and not wanted_photos):
                continue
            if item["name"] in already_saved:
                continue
            account_dir.mkdir(parents=True, exist_ok=True)
            media = session.get(item["url"], timeout=120)
            media.raise_for_status()
            (account_dir / item["name"]).write_bytes(media.content)
            downloaded += 1
            log(f"saved {item['name']}")
    return downloaded
