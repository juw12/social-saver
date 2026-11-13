"""Pure logic for Social Saver: targets, download plans, sign-in cookies and zip sorting.

Nothing in here touches the network or the UI, so it can be unit-tested directly.
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
VIDEO_FILTER = "extension in ('mp4', 'mov', 'm4v', 'webm', 'mkv')"
PHOTO_FILTER = "extension not in ('mp4', 'mov', 'm4v', 'webm', 'mkv')"

# "types" are the zip subfolders the user can tick for a whole-account download.
PLATFORMS = {
    "instagram": {
        "label": "Instagram",
        "domains": ("instagram.com",),
        "types": ("posts", "reels", "stories", "highlights"),
        "login_url": "https://www.instagram.com/accounts/login/",
        "auth_cookie": "sessionid",
        "engine_category": "instagram",
        "beta": False,
    },
    "threads": {
        "label": "Threads",
        "domains": ("threads.com", "threads.net"),
        "types": ("posts", "videos"),
        "login_url": "https://www.threads.com/login",
        "auth_cookie": "sessionid",
        "engine_category": None,  # no gallery-dl support; handled by saver/threads.py
        "beta": True,
    },
    "x": {
        "label": "X",
        "domains": ("x.com", "twitter.com"),
        "types": ("posts", "videos"),
        "login_url": "https://x.com/i/flow/login",
        "auth_cookie": "auth_token",
        "engine_category": "twitter",
        "beta": False,
    },
    "tiktok": {
        "label": "TikTok",
        "domains": ("tiktok.com",),
        "types": ("videos", "posts", "stories"),
        "login_url": "https://www.tiktok.com/login",
        "auth_cookie": "sessionid",
        "engine_category": "tiktok",
        "beta": False,
    },
}

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
NOT_A_PROFILE = {"explore", "home", "i", "foryou", "following", "search", "reels", "login"}
SHORT_LINK_HOSTS = {"vt.tiktok.com", "vm.tiktok.com"}  # share links: /<code>/ is one post, not a user
LINK_IN_TEXT = re.compile(r"https?://[^\s<>\"']+")
NETSCAPE_HTTPONLY_PREFIX = "#HttpOnly_"
THUMBNAIL_KEYS = ("display_url", "thumbnail", "thumbnail_url", "cover", "origin_cover", "image")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}

# Staging subfolder written by the engine -> type folder inside the zip.
KIND_TO_TYPE = {
    "post": "posts",
    "reel": "reels",
    "story": "stories",
    "stories": "stories",
    "highlights": "highlights",
}
MIXED_MEDIA_KIND = "media"  # photos and videos together; split by file extension


@dataclass(frozen=True)
class Target:
    """One line typed by the user: a whole account (username) or a single link (url)."""

    platform: str
    username: str | None = None
    url: str | None = None

    @property
    def display(self) -> str:
        label = PLATFORMS[self.platform]["label"]
        return f"{label} @{self.username}" if self.username else f"{label} link {self.url}"


@dataclass(frozen=True)
class EngineRun:
    """One gallery-dl invocation."""

    url: str
    file_filter: str | None = None


# ---------------------------------------------------------------- locations

def data_home() -> Path:
    """Private app data (sign-in cookies, download history, staging)."""
    override = os.environ.get("SOCIAL_SAVER_HOME")
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SocialSaver"


def is_termux() -> bool:
    """Running inside Termux on an Android phone."""
    return "com.termux" in os.environ.get("PREFIX", "")


def default_output_dir() -> Path:
    phone_downloads = Path.home() / "storage" / "downloads"
    if is_termux() and phone_downloads.exists():
        return phone_downloads / "SocialSaver"
    if os.name == "nt" and Path("E:/").exists():
        return Path("E:/SocialSaver")
    return Path.home() / "Downloads" / "SocialSaver"


def cookie_path(platform: str) -> Path:
    return data_home() / "cookies" / f"{platform}.txt"


def load_settings() -> dict:
    settings_file = data_home() / "settings.json"
    settings = {"output_dir": str(default_output_dir())}
    if settings_file.exists():
        settings.update(json.loads(settings_file.read_text(encoding="utf-8")))
    return settings


def save_settings(settings: dict) -> None:
    data_home().mkdir(parents=True, exist_ok=True)
    (data_home() / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- targets

def platform_for_host(host: str) -> str | None:
    host = host.lower()
    for platform, spec in PLATFORMS.items():
        for domain in spec["domains"]:
            if host == domain or host.endswith("." + domain):
                return platform
    return None


def extract_link(text: str) -> str:
    """The first link in shared text ("Look at this! https://..."), or the text itself."""
    match = LINK_IN_TEXT.search(text)
    return match.group(0).rstrip(".,!?)") if match else text.strip()


def parse_target(text: str, default_platform: str) -> Target:
    """Turn "natgeo", "@natgeo", a profile link or a post link into a Target."""
    text = text.strip()
    if "/" not in text:
        username = text.lstrip("@")
        if not USERNAME_PATTERN.match(username):
            raise ValueError(f"'{text}' is not a valid username or link")
        return Target(default_platform, username=username)

    parsed = urlparse(text if "://" in text else "https://" + text)
    platform = platform_for_host(parsed.hostname or "")
    if platform is None:
        raise ValueError(f"'{text}' is not an Instagram, Threads, X or TikTok link")

    segments = [segment for segment in parsed.path.split("/") if segment]
    is_short_link = (parsed.hostname or "").lower() in SHORT_LINK_HOSTS
    if len(segments) == 1 and segments[0].lower() not in NOT_A_PROFILE and not is_short_link:
        username = segments[0].lstrip("@")
        if USERNAME_PATTERN.match(username):
            return Target(platform, username=username)

    clean_url = f"https://{parsed.hostname}{parsed.path}"
    if parsed.query:
        clean_url += "?" + parsed.query
    return Target(platform, url=clean_url)


# ---------------------------------------------------------------- download plans

def _mixed_media_filter(chosen: set[str]) -> str | None:
    if {"posts", "videos"} <= chosen:
        return None
    return VIDEO_FILTER if "videos" in chosen else PHOTO_FILTER


def plan_runs(target: Target, types: list[str]) -> list[EngineRun]:
    """gallery-dl invocations for one target (Threads is handled separately)."""
    if target.url:
        return [EngineRun(target.url)]

    user = target.username
    chosen = set(types)
    runs: list[EngineRun] = []

    if target.platform == "instagram":
        profile = f"https://www.instagram.com/{user}"
        if "reels" in chosen:
            runs.append(EngineRun(f"{profile}/reels/"))
        if "posts" in chosen:
            # The posts feed also lists reels; keep them out unless reels were asked for.
            only_posts = None if "reels" in chosen else "type == 'post'"
            runs.append(EngineRun(f"{profile}/posts/", only_posts))
        if "stories" in chosen:
            runs.append(EngineRun(f"https://www.instagram.com/stories/{user}/"))
        if "highlights" in chosen:
            runs.append(EngineRun(f"{profile}/highlights/"))

    elif target.platform == "x":
        if chosen & {"posts", "videos"}:
            runs.append(EngineRun(f"https://x.com/{user}/media", _mixed_media_filter(chosen)))

    elif target.platform == "tiktok":
        if chosen & {"posts", "videos"}:
            runs.append(
                EngineRun(f"https://www.tiktok.com/@{user}/posts", _mixed_media_filter(chosen))
            )
        if "stories" in chosen:
            runs.append(EngineRun(f"https://www.tiktok.com/@{user}/stories"))

    return runs


def build_engine_config(staging_dir: Path, history_file: Path) -> dict:
    """gallery-dl config: where files land, the no-duplicates history, sign-in cookies.

    Every file is staged as <staging>/<platform>/<account>/<kind>/..., which is what
    sort_staging_into_zips() expects.
    """
    extractor = {
        "base-directory": str(staging_dir),
        "archive": str(history_file),
        "instagram": {
            "directory": {
                "type == 'highlight'": [
                    "instagram", "{username}", "highlights", "{highlight_title|post_id}",
                ],
                "": ["instagram", "{username}", "{type}"],
            },
        },
        "twitter": {
            "directory": ["x", "{user[name]}", MIXED_MEDIA_KIND],
        },
        "tiktok": {
            "directory": {
                "subcategory == 'stories'": ["tiktok", "{user}", "stories"],
                "": ["tiktok", "{user}", MIXED_MEDIA_KIND],
            },
        },
    }
    for platform, spec in PLATFORMS.items():
        category = spec["engine_category"]
        if category and is_signed_in(platform):
            extractor[category]["cookies"] = str(cookie_path(platform))
    return {"extractor": extractor}


# ---------------------------------------------------------------- sign-in cookies

def _expiry_to_epoch(value) -> int:
    """Cookie expiry as unix time; 0 means a session cookie."""
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value))
        except ValueError:
            pass
        try:
            return int(parsedate_to_datetime(value).timestamp())
        except (TypeError, ValueError):
            pass
    return 0


def write_cookie_file(path: Path, cookies: list[dict]) -> None:
    """Save cookies in the Netscape cookies.txt format gallery-dl and yt-dlp read."""
    lines = ["# Netscape HTTP Cookie File"]
    for cookie in cookies:
        domain = cookie["domain"]
        lines.append("\t".join([
            domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            cookie.get("path") or "/",
            "TRUE" if cookie.get("secure") else "FALSE",
            str(_expiry_to_epoch(cookie.get("expires"))),
            cookie["name"],
            cookie["value"],
        ]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def is_signed_in(platform: str) -> bool:
    path = cookie_path(platform)
    if not path.exists():
        return False
    auth_cookie = PLATFORMS[platform]["auth_cookie"]
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) == 7 and fields[5] == auth_cookie and fields[6]:
            return True
    return False


def sign_out(platform: str) -> None:
    cookie_path(platform).unlink(missing_ok=True)


def parse_cookie_export(text: str) -> list[dict]:
    """Cookies from a browser export: Netscape cookies.txt, or the JSON list that
    cookie extensions (Cookie-Editor, EditThisCookie) save."""
    stripped = text.lstrip("\ufeff").strip()
    if stripped.startswith(("[", "{")):
        try:
            data = json.loads(stripped)
        except ValueError as error:
            raise ValueError("That cookie file is not valid JSON.") from error
        if isinstance(data, dict):
            data = data.get("cookies", [])
        cookies = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict) or not item.get("name") or not item.get("domain"):
                continue
            cookies.append({
                "name": str(item["name"]),
                "value": str(item.get("value", "")),
                "domain": str(item["domain"]),
                "path": item.get("path") or "/",
                "secure": bool(item.get("secure")),
                "expires": item.get("expirationDate") or item.get("expires") or 0,
            })
        return cookies

    cookies = []
    for line in stripped.splitlines():
        if line.startswith(NETSCAPE_HTTPONLY_PREFIX):
            line = line[len(NETSCAPE_HTTPONLY_PREFIX):]
        elif not line.strip() or line.startswith("#"):
            continue
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) != 7:
            continue
        domain, _include_subdomains, path, secure, expires, name, value = fields
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "secure": secure.upper() == "TRUE",
            "expires": expires,
        })
    return cookies


def import_cookies(platform: str, text: str) -> int:
    """Use cookies exported from the user's own browser instead of the sign-in window.

    Keeps only this platform's cookies and refuses a file without the login cookie,
    so a wrong or logged-out export never replaces a working sign-in.
    """
    spec = PLATFORMS[platform]
    cookies = [
        cookie for cookie in parse_cookie_export(text)
        if platform_for_host(cookie["domain"].lstrip(".")) == platform
    ]
    if not cookies:
        raise ValueError(
            f"No {spec['label']} cookies in that file. Export them from your browser while "
            f"you are on {spec['domains'][0]} and signed in."
        )
    auth_cookie = spec["auth_cookie"]
    if not any(cookie["name"] == auth_cookie and cookie["value"] for cookie in cookies):
        raise ValueError(
            f"That file has {spec['label']} cookies but not the login one ({auth_cookie}). "
            f"Sign in to {spec['label']} in your browser, then export again."
        )
    write_cookie_file(cookie_path(platform), cookies)
    return len(cookies)


# ---------------------------------------------------------------- item picker

def _url_extension(url: str) -> str:
    return Path(urlparse(url).path).suffix.lower()


def thumbnail_for(metadata: dict, file_url: str) -> str | None:
    """A small picture to show for one item in the picker, or None."""
    for key in THUMBNAIL_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    extension = "." + str(metadata.get("extension") or "").lower().lstrip(".")
    if extension in IMAGE_EXTENSIONS or _url_extension(file_url) in IMAGE_EXTENSIONS:
        return file_url
    return None


def preview_items_from_engine(messages: list) -> list[dict]:
    """Items of one post from `gallery-dl --dump-json` output, numbered like --range counts them."""
    for message in messages:
        if message and message[0] == -1:
            details = message[-1] if isinstance(message[-1], dict) else {}
            raise ValueError(f"{details.get('error', 'Error')}: {details.get('message', '')}".strip())
    items = []
    for message in messages:
        if not message or message[0] != 3 or len(message) < 3:
            continue
        file_url, metadata = message[1], message[2] or {}
        extension = "." + str(metadata.get("extension") or "").lower().lstrip(".")
        is_video = extension in VIDEO_EXTENSIONS or _url_extension(file_url) in VIDEO_EXTENSIONS
        items.append({
            "index": len(items) + 1,
            "video": is_video,
            "thumb": thumbnail_for(metadata, file_url),
        })
    return items


def range_for_pick(pick: list[int]) -> str:
    """gallery-dl --range value for the items ticked in the picker, e.g. '1,3,4'."""
    return ",".join(str(index) for index in sorted(set(pick)))


# ---------------------------------------------------------------- zips

def archive_name(staged_file: Path) -> str:
    """Path inside the zip for a file staged as <kind>/<...>/<name>."""
    parts = staged_file.parts
    if len(parts) < 2:
        return f"other/{staged_file.name}"
    kind, rest = parts[0], parts[1:]
    if kind == MIXED_MEDIA_KIND:
        type_folder = "videos" if staged_file.suffix.lower() in VIDEO_EXTENSIONS else "posts"
    else:
        type_folder = KIND_TO_TYPE.get(kind, kind)
    return "/".join([type_folder, *rest])


def zip_path_for(output_dir: Path, platform: str, account: str) -> Path:
    return output_dir / f"{platform}_{account}.zip"


def saved_file_names(zip_path: Path) -> set[str]:
    """Bare file names already stored in an account zip."""
    if not zip_path.exists():
        return set()
    with zipfile.ZipFile(zip_path) as archive:
        return {name.rsplit("/", 1)[-1] for name in archive.namelist()}


def sort_staging_into_zips(staging_dir: Path, output_dir: Path) -> dict[Path, int]:
    """Move every staged file into <output>/<platform>_<account>.zip under its type folder.

    A file whose zip path already exists is dropped, so re-runs never create duplicates.
    Returns {zip path: number of files added}.
    """
    added: dict[Path, int] = {}
    if not staging_dir.exists():
        return added
    output_dir.mkdir(parents=True, exist_ok=True)

    for platform_dir in sorted(path for path in staging_dir.iterdir() if path.is_dir()):
        for account_dir in sorted(path for path in platform_dir.iterdir() if path.is_dir()):
            staged_files = sorted(
                path for path in account_dir.rglob("*")
                if path.is_file() and path.suffix != ".part"
            )
            if not staged_files:
                continue
            zip_path = zip_path_for(output_dir, platform_dir.name, account_dir.name)
            new_count = 0
            with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_STORED) as archive:
                existing = set(archive.namelist())
                for staged_file in staged_files:
                    name_in_zip = archive_name(staged_file.relative_to(account_dir))
                    if name_in_zip not in existing:
                        archive.write(staged_file, name_in_zip)
                        existing.add(name_in_zip)
                        new_count += 1
                    staged_file.unlink()
            added[zip_path] = new_count
            _remove_empty_dirs(account_dir)
    return added


def _remove_empty_dirs(root: Path) -> None:
    for folder in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    if not any(root.iterdir()):
        root.rmdir()


# ---------------------------------------------------------------- messages

def explain_engine_message(line: str, platform_label: str) -> str | None:
    """A plain-words hint for a known engine error line, or None."""
    lowered = line.lower()
    if "authrequired" in lowered or "login required" in lowered or "401" in lowered \
            or "authenticationerror" in lowered or "redirect to login" in lowered:
        return f"{platform_label} needs you to be signed in for this. Use the Sign in button, then retry."
    if "403" in lowered or "proxyerror" in lowered or "forbidden" in lowered:
        return (f"{platform_label} refused the connection (403). On an office network this usually "
                "means the site is blocked there; otherwise sign in again.")
    if "notfounderror" in lowered or "404" in lowered:
        return f"{platform_label} could not find that account or post (or it is private and you do not follow it)."
    if "429" in lowered or "rate limit" in lowered or "too many requests" in lowered:
        return f"{platform_label} is rate-limiting. Wait a while, then run again; finished files are kept."
    return None
