"""Live check of the web downloader from a datacenter IP (like Vercel's): real TikTok search,
real file through the proxy, real zip. Instagram, X and Threads are only reported (Instagram and X
need cookies; Threads is beta and Meta may refuse a datacenter IP)."""
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from saver.webdl import create_app  # noqa: E402

client = create_app().test_client()


def search(query: str, platform: str, count: int = 3) -> list[dict]:
    response = client.post("/api/list", json={"query": query, "platform": platform, "count": count})
    messages = [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line.strip()]
    print(f"{platform} {query}: HTTP {response.status_code}, last message: {json.dumps(messages[-1])[:300] if messages else None}")
    return messages


def looks_like_media(data: bytes) -> bool:
    return data[4:8] == b"ftyp" or data[:3] == b"\xff\xd8\xff" or data[:4] in (b"RIFF", b"\x89PNG")


failures = []
for query in ("tiktok", "https://www.tiktok.com/@scout2015/video/6718335390845095173"):
    messages = search(query, "tiktok")
    items = [message["item"] for message in messages if message["type"] == "item"]
    if not items:
        failures.append(f"no TikTok items for {query}")
        continue
    first = items[0]
    print(f"  first item: {first['name']} video={first['video']} thumb={bool(first['thumb'])}")
    response = client.get("/api/file?t=" + first["file"])
    body = response.get_data()
    print(f"  file: HTTP {response.status_code}, {len(body)} bytes, {response.headers.get('Content-Type')}, "
          f"{response.headers.get('Content-Disposition')}")
    if response.status_code != 200 or len(body) < 20_000 or not looks_like_media(body):
        failures.append(f"file download failed for {query}")
    if not first["thumb"]:
        failures.append(f"no thumbnail for {query}")
    else:
        thumb = client.get("/api/file?t=" + first["thumb"])
        picture = thumb.get_data()
        print(f"  thumbnail: HTTP {thumb.status_code}, {len(picture)} bytes, {thumb.headers.get('Content-Type')}")
        if thumb.status_code != 200 or len(picture) < 1000:
            failures.append(f"thumbnail failed for {query}")
    if len(items) >= 2:
        archive_response = client.post("/api/zip", data={"t": [item["file"] for item in items[:2]], "name": "live"})
        archive = zipfile.ZipFile(io.BytesIO(archive_response.get_data()))
        names = archive.namelist()
        print(f"  zip: {names}")
        if "NOT-SAVED.txt" in names or len(names) != 2 or archive.testzip() is not None:
            failures.append("zip of two TikTok files failed")

# Expected to need cookies from a server; printed so the README stays honest.
search("natgeo", "instagram", 2)
search("NASA", "x", 2)
# Threads (beta), by username and by account link: reported, not required.
for threads_query in ("zuck", "https://www.threads.com/@zuck"):
    threads_items = [message["item"] for message in search(threads_query, "threads", 2) if message["type"] == "item"]
    print(f"  threads items: {len(threads_items)}")
    if threads_items:
        threads_file = client.get("/api/file?t=" + threads_items[0]["file"])
        threads_body = threads_file.get_data()
        print(f"  threads first item: {threads_items[0]['name']}, file HTTP {threads_file.status_code}, "
              f"{len(threads_body)} bytes, media={looks_like_media(threads_body)}")

if failures:
    print("FAILED:", failures)
    sys.exit(1)
print("LIVE WEB DOWNLOADER OK")
