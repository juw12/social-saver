# Social Saver

One small app to save posts, reels, videos, stories and highlights from
Instagram, Threads, X and TikTok. Runs on Windows, as an Android app, and as a
private web app you can add to the home screen of an iPhone or Android phone. You pick the types you want. Everything lands
in one zip per account, sorted into folders. Run it again and it only adds what
is new.

![Social Saver, dark theme](docs/screenshot.png)

Free, runs on your own PC, phone or private server, no account with us and no API keys. MIT licence.

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

Three ways. Pick one:

| Way | Phones | Needs a PC? |
|-----|--------|-------------|
| [Android app](#android-app-no-pc) | Android 10+ | no |
| [Your own web app](#web-app-iphone-and-android-no-pc) | iPhone and Android | no (free cloud server) |
| [Phone + PC](#phone--pc-same-wi-fi) | iPhone and Android | yes, same Wi-Fi |

### Android app (no PC)

1. On the phone, download **[SocialSaver-android.apk](https://github.com/juw12/social-saver/releases/latest/download/SocialSaver-android.apk)**.
2. Open it. Android asks to allow installs from this source (Chrome or Files): allow it,
   then tap **Install**. It is not from the Play Store, so Play Protect may warn once.
3. Open **Social Saver**. It is the same screen as on the PC and does the downloading on
   the phone itself (Python and the download engines are inside the app).

- **Share to the app:** in Instagram, Threads, X or TikTok tap Share > **Social Saver**.
  The link is filled in for you.
- **Sign in:** the Sign in button opens the platform's real login page inside the app;
  only the session cookie is kept, on the phone. **Import cookies file** also works.
- Zips go to **Download/SocialSaver** on the phone (tap a zip in "Your zips").
- Updates: download the newer APK and install it over the old one; sign-ins stay.

### Web app (iPhone and Android, no PC)

Your own private copy on a free cloud server ([Render](https://render.com)). One-time
setup, about 5 minutes:

1. Press **[Deploy to Render](https://render.com/deploy?repo=https://github.com/juw12/social-saver)**
   and sign in to Render with your GitHub account (free plan, no card needed for it).
2. Render asks for **SOCIAL_SAVER_PASSWORD**: type a password only you know. Press
   **Deploy** and wait until it says *Live* (first build takes a few minutes).
3. Open your address (like `https://social-saver-xxxx.onrender.com`) on the phone and
   type the password.
4. Keep it as an app: iPhone Safari Share > **Add to Home Screen**; Android Chrome
   menu > **Add to Home screen** (on Android you can then also share posts to it).

- Tap a zip in "Your zips" to save it to the phone (iPhone: Files app > Downloads).
- Sign in with **Import cookies file** (export cookies on any browser, see
  [Sign in](#sign-in-for-private-accounts-you-follow)). The browser on your phone keeps
  them and sends them again whenever the server restarts.
- Free plan limits: the server sleeps after 15 minutes without use, so the first open
  takes about a minute; files on the server are wiped when it restarts, so save your
  zips to the phone right away.
- Instagram often blocks cloud servers. If Instagram fails there, use the Android app
  or Phone + PC for Instagram. X and TikTok usually work.

### Phone + PC (same Wi-Fi)

In the app on the PC, turn on **Use on your phone** (bottom card) and scan the QR code
with the phone camera. The phone shows the same screen; the PC does the downloading.
Tap a zip in "Your zips" to keep a copy on the phone.

- Keep it as an app: iPhone Safari Share > **Add to Home Screen**; Android Chrome
  menu > **Add to Home screen**.
- The first time, Windows may ask whether Python may use the network: allow
  **Private networks**.
- Only phones that scanned the QR code get in (the link carries a private key).
  **New key** locks out every phone that scanned before.
- Sign in on the PC. On the phone you can also use **Import cookies file**.
- **iPhone share button (optional):** in the Shortcuts app make a shortcut that
  receives URLs from the Share Sheet, then *URL Encode* the input and *Open URLs*
  `<your Social Saver address>&url=<encoded input>` (use `?url=` if the address has no
  `?`). Sharing a post to it opens Social Saver with the link already filled in. This
  works for the web app too.

**Android with Termux (older way):** install [Termux](https://f-droid.org/packages/com.termux/)
(from F-Droid or GitHub, not the old Play Store build) and run:

```
curl -fsSL https://raw.githubusercontent.com/juw12/social-saver/main/mobile/termux-setup.sh | bash
```

Then **share** a post or profile **to Termux** and it is saved to `Download/SocialSaver`.

**Why no iPhone app?** Apple does not allow downloader apps in the App Store and iOS
cannot run Python apps from outside it, so on an iPhone use the web app.

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
python app.py --self-test <file url>   # one real engine download, prints SELFTEST OK
SOCIAL_SAVER_PASSWORD=... python app.py --serve --host 0.0.0.0   # web app mode (password login)
```

- `app.py` - window, local API, job runner
- `saver/core.py` - link parsing, engine config, zip sorting, duplicate check
- `saver/threads.py` - Threads reader (beta)
- `saver/phone.py` - phone mode: key, QR code, the Wi-Fi server
- `saver/engine.py` - runs gallery-dl inside the app process (Android has no subprocesses)
- `android/` - Android app (Kotlin WebView + Python via Chaquopy); `android/sync.sh` copies
  the Python app in, CI builds the APK and starts it on an emulator
- `Dockerfile`, `render.yaml` - the web app (Render blueprint)
- `mobile/termux-setup.sh` - Android setup (Termux)
- `ui.html` - the screen
- `tests/` - uses a fake engine, so tests never touch the real sites

Set `SOCIAL_SAVER_HOME` to move the data folder (cookies, history, settings) and
`SOCIAL_SAVER_OUTPUT_DIR` to change the default zip folder.

## Status

Early version. The app, zip sorting and duplicate check are covered by tests with a fake
engine; CI also runs a real engine download in the web app container and in the Android
app on an emulator (not yet on a real phone). A real signed-in download has not been confirmed yet on every platform; please
open an issue if one breaks.
