# Social Saver

One small app to save posts, reels, videos, stories and highlights from
Instagram, Threads, X and TikTok. Runs on Windows, and you can use it from an
Android phone or an iPhone too. You pick the types you want. Everything lands
in one zip per account, sorted into folders. Run it again and it only adds what
is new.

![Social Saver, dark theme](docs/screenshot.png)

Free, runs on your own PC, no account with us and no API keys. MIT licence.

## Open it

1. Download **[SocialSaver-windows.zip](https://github.com/juw12/social-saver/releases/latest/download/SocialSaver-windows.zip)** and unzip it.
2. Double-click `Start Social Saver.bat`.

The first run installs what it needs (Flask, pywebview, gallery-dl, yt-dlp). If the PC has no
Python yet, it installs Python 3.12 for you first (with winget, no admin needed).

## Use it

1. Pick the platform tab: Instagram, Threads, X or TikTok.
2. Type a username (or paste a profile link). One per line for several accounts.
   A link to a single post, reel or video saves just that one item.
3. Tick the types you want. Optional: fill in "Only the newest" to take just the latest few.
4. Press **Download**. The log shows progress.

**Only some photos of one post?** Paste a single post link and press **Choose items...**.
You get a grid of thumbnails; untick what you don't want and press **Save selected**.

## Use it on your phone (Android or iPhone)

**Phone + PC (Android and iPhone):** in the app on the PC, turn on **Use on your phone**
(bottom card) and scan the QR code with the phone camera. The phone and PC must be on
the same Wi-Fi. The phone shows the same screen; the PC does the downloading. Tap a
zip in "Your zips" to keep a copy on the phone.

- Keep it as an app: iPhone Safari Share > **Add to Home Screen**; Android Chrome
  menu > **Add to Home screen**.
- The first time, Windows may ask whether Python may use the network: allow
  **Private networks**.
- Only phones that scanned the QR code get in (the link carries a private key).
  **New key** locks out every phone that scanned before.
- Sign in on the PC. On the phone you can also use **Import cookies file**.
- **iPhone share button (optional):** in the Shortcuts app make a shortcut that
  receives URLs from the Share Sheet, then *URL Encode* the input and *Open URLs*
  `http://<link from the QR code>&url=<encoded input>`. Sharing a post to it opens
  Social Saver with the link already filled in.

**Android on its own (no PC):** install [Termux](https://f-droid.org/packages/com.termux/)
(from F-Droid or GitHub, not the old Play Store build) and run:

```
curl -fsSL https://raw.githubusercontent.com/juw12/social-saver/main/mobile/termux-setup.sh | bash
```

Then **share** any Instagram, Threads, X or TikTok post or profile **to Termux** and it
is saved to `Download/SocialSaver` on the phone. Or type `social-saver` in Termux to open
the full screen in the phone browser. If sharing to Termux does nothing, allow Termux
"Display over other apps" in Android settings.

**iPhone on its own:** not possible. iOS stops background apps and Apple does not
allow downloader apps, so on an iPhone use the "Phone + PC" way above.

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
python app.py --serve    # no window: open the screen in a browser (add --host 0.0.0.0 for phones)
python app.py --save <link or text with a link>   # save now and exit (Android "Share")
```

- `app.py` - window, local API, job runner
- `saver/core.py` - link parsing, engine config, zip sorting, duplicate check
- `saver/threads.py` - Threads reader (beta)
- `saver/phone.py` - phone mode: key, QR code, the Wi-Fi server
- `mobile/termux-setup.sh` - Android setup (Termux)
- `ui.html` - the screen
- `tests/` - uses a fake engine, so tests never touch the real sites

Set `SOCIAL_SAVER_HOME` to move the data folder (cookies, history, settings).

## Status

Early version. The app, zip sorting and duplicate check are covered by tests with a fake
engine. A real signed-in download has not been confirmed yet on every platform; please
open an issue if one breaks.
