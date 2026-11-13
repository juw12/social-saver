# Social Saver

One small Windows app to save posts, reels, videos, stories and highlights from
Instagram, Threads, X and TikTok. You pick the types you want. Everything lands
in one zip per account, sorted into folders. Run it again and it only adds what
is new.

![Social Saver, dark theme](docs/screenshot.png)

Free, runs on your own PC, no account with us and no API keys. MIT licence.

## Open it

Double-click `Start Social Saver.bat`.

The first run installs what it needs (Flask, pywebview, gallery-dl, yt-dlp).
You need Python 3.10 or newer on the PC.

## Use it

1. Pick the platform tab: Instagram, Threads, X or TikTok.
2. Type a username (or paste a profile link). One per line for several accounts.
   A link to a single post, reel or video saves just that one item.
3. Tick the types you want. Optional: fill in "Only the newest" to take just the latest few.
4. Press **Download**. The log shows progress.

**Only some photos of one post?** Paste a single post link and press **Choose items...**.
You get a grid of thumbnails; untick what you don't want and press **Save selected**.

## Where the files go

Default folder is `E:\SocialSaver` (change it in the app).

```
instagram_someuser.zip
    posts/
    reels/
    stories/
    highlights/<highlight name>/
tiktok_someuser.zip
    videos/
    posts/
    stories/
```

| Platform  | Types you can tick                  |
|-----------|-------------------------------------|
| Instagram | posts, reels, stories, highlights   |
| Threads   | posts, videos (beta)                |
| X         | posts (photos), videos              |
| TikTok    | videos, posts (photos), stories     |

## Sign in (for private accounts you follow)

Press **Sign in** on a tab. The real login page of that platform opens in its
own window. You type your password there, on their page. The app never sees
it. It only keeps the session cookie, on this PC, in
`%LOCALAPPDATA%\SocialSaver\cookies\`.

- Private accounts work only if your account already follows them.
- Stories and highlights on Instagram always need sign-in. X needs sign-in for
  almost everything.
- "Continue with Google" buttons may not work in the sign-in window. Use
  username and password, or the QR code.
- **Sign out** deletes the cookie file.

**Import cookies file** (next to Sign in) is the other way in: if the sign-in window
won't work (Google login, a security check), sign in with your normal browser, export
the cookies with an extension such as "Get cookies.txt LOCALLY" or "Cookie-Editor"
(cookies.txt or JSON), and pick that file. Only that platform's cookies are kept, and a
file without the login cookie is refused, so a bad export never breaks a working sign-in.

## No duplicates

The app remembers every item it has saved (`history.sqlite3`) and also checks
what is already inside the zip. A second run only adds new items.
"Forget download history" clears the memory; the zip check still stops
duplicates.

## Good to know

- **Office network:** the company proxy blocks Instagram, TikTok and Threads.
  Use the app on home Wi-Fi or a phone hotspot for those. X works at the office.
- **Threads is beta:** it reads the profile page, then loads older posts the same
  way the Threads website does when you scroll (it finds the current "load more"
  query in the page itself, because Meta changes it often). If that fails, you still
  get the newest posts and the log says so. Advanced: put `"threads_doc_id": "<id>"`
  in `%LOCALAPPDATA%\SocialSaver\settings.json` to pin the query id.
- **If a platform stops working:** press "Update download engines". Platforms change
  their sites often and the download engines get fixes every few days.
- Download slowly and for yourself only. Mass downloading can get an account
  rate-limited or restricted, and re-posting other people's content is not OK.

## For developers

```
python -m pytest -q      # no network needed; CI runs them on Windows + Linux
python app.py            # run with a console
```

- `app.py` - window, local API, job runner
- `saver/core.py` - link parsing, engine config, zip sorting, duplicate check
- `saver/threads.py` - Threads reader (beta)
- `ui.html` - the screen
- `tests/` - uses a fake engine, so tests never touch the real sites

Set `SOCIAL_SAVER_HOME` to move the data folder (cookies, history, settings).

## Status

Early version. The app, zip sorting and duplicate check are covered by tests with a fake
engine. A real signed-in download has not been confirmed yet on every platform; please
open an issue if one breaks.
