"""Stand-in for gallery-dl in tests: same command line, fake files, no network.

It stages files in the layout that saver.core.build_engine_config() asks the real
engine for (test_core.py checks that layout against real gallery-dl separately).
Supports --dump-json (picker preview) and --range with comma lists ("1,3").
"""
import argparse
import json
from pathlib import Path

# url fragment -> (type, staged folder below the account, file name)
FAKE_MEDIA = {
    "/reels/": [("reel", "reel", "reel1.mp4"), ("reel", "reel", "reel2.mp4")],
    "/posts/": [("post", "post", "photo1.jpg"), ("post", "post", "photo2.jpg"),
                ("reel", "reel", "reel1.mp4")],
    "/stories/": [("story", "story", "story1.jpg")],
    "/highlights/": [("highlight", "highlights/Trip", "trip1.jpg"),
                     ("highlight", "highlights/Trip", "trip2.mp4")],
    # one carousel post: photo, video, photo
    "/p/": [("post", "post", "car1.jpg"), ("post", "post", "car2.mp4"),
            ("post", "post", "car3.jpg")],
}


def wanted_positions(range_text: str) -> set[int]:
    positions: set[int] = set()
    for part in range_text.split(","):
        if "-" in part:
            first, last = part.split("-")
            positions.update(range(int(first), int(last) + 1))
        else:
            positions.add(int(part))
    return positions


parser = argparse.ArgumentParser()
parser.add_argument("--config-ignore", action="store_true")
parser.add_argument("--config", required=True)
parser.add_argument("--filter")
parser.add_argument("--range")
parser.add_argument("-j", "--dump-json", action="store_true")
parser.add_argument("url")
arguments = parser.parse_args()

extractor_config = json.loads(Path(arguments.config).read_text(encoding="utf-8"))["extractor"]
history_file = Path(extractor_config["archive"])
already_downloaded = set(history_file.read_text().split()) if history_file.exists() else set()
if "/p/" in arguments.url:
    account = "natgeo"
elif "/stories/" in arguments.url:
    account = arguments.url.rstrip("/").split("/")[-1]
else:
    account = arguments.url.rstrip("/").split("/")[-2]

media = next(items for fragment, items in FAKE_MEDIA.items() if fragment in arguments.url)

if arguments.dump_json:
    messages = [[2, {"username": account}]]
    for _media_type, _folder, file_name in media:
        extension = file_name.rsplit(".", 1)[1]
        metadata = {"username": account, "extension": extension}
        if file_name != "car3.jpg":  # car3 has no separate picture; the photo itself is used
            metadata["display_url"] = f"https://cdn.example/{file_name}.preview.jpg"
        messages.append([3, f"https://cdn.example/{file_name}", metadata])
    print(json.dumps(messages))
    raise SystemExit(0)

if arguments.filter:
    media = [
        item for item in media
        if eval(arguments.filter, {"type": item[0], "extension": item[2].rsplit(".", 1)[1]})
    ]
if arguments.range:
    positions = wanted_positions(arguments.range)
    media = [item for position, item in enumerate(media, start=1) if position in positions]

for _media_type, folder, file_name in media:
    staged_file = Path(extractor_config["base-directory"]) / "instagram" / account / folder / file_name
    if file_name in already_downloaded:
        print(f"# {staged_file}")
        continue
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    staged_file.write_bytes(file_name.encode())
    already_downloaded.add(file_name)
    print(staged_file)

history_file.write_text("\n".join(sorted(already_downloaded)))
