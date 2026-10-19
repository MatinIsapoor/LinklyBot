# Telegram Link Manager 🤖🔗

A production-ready Telegram bot that shortens URLs, tracks clicks, organises
links by category and supports optional expiration — all from a clean
inline-keyboard panel.

Built with **Python 3.11+**, **aiogram 3**, **SQLAlchemy 2 (async)**,
**SQLite** and **aiohttp** (redirect server).

---

## ✨ Features

- **URL shortening** — send any `http(s)://` URL, get a short link instantly
  (`/new` or just paste the URL).
- **Click tracking** — every `GET /{code}` increments the counter and
  stamps `last_clicked_at`, then `302`-redirects to the original URL.
- **Link management** — paginated list (10 per page), detail view,
  edit (title / URL / category / expiry) and delete with confirmation.
- **Categories** — `Education`, `Video`, `Personal`, `Other` (picker keyboard).
- **Optional expiration** — presets (`1h / 1d / 7d / 30d / never`) or custom
  ISO8601 datetime (`2026-12-31T23:59`, UTC). Expired links return `410`.
- **User panel** — inline keyboards everywhere (`New link / My links / Stats`).
- **Statistics** — total links / clicks / active / expired, per-category
  histogram and top-5 most-clicked links; per-link card shows clicks,
  creation date and last click.
- **Privacy** — private-chat-only filter + ownership-scoped queries
  (users only ever see their own links).
- **Safety** — per-user throttling, HTML-escaping, ban flag, graceful errors.

---

## 🗂 Project structure

```text
telegram-link-manager/
├── bot/
│   ├── handlers/
│   │   ├── start.py      # /start /help /cancel + main menu
│   │   ├── shorten.py    # /new + free-text URL shortening
│   │   ├── manage.py     # /links list / view / edit / delete
│   │   └── stats.py      # /stats aggregate view
│   ├── keyboards/
│   │   └── inline.py     # CallbackData schemas + keyboards
│   ├── middlewares/
│   │   └── access.py     # user resolution, throttle, DI
│   ├── filters/
│   │   └── private.py    # private-chat-only gate
│   └── __init__.py       # build_dispatcher()
├── database/
│   ├── models.py         # User / Link ORM models
│   ├── crud.py           # ensure_user()
│   └── database.py       # Database facade (engine + sessions)
├── services/
│   └── link_service.py   # create/get/list/edit/delete/track + CATEGORIES
├── config.py             # env-based Config singleton
├── main.py               # polling/webhook + aiohttp server + keep-alive
├── requirements.txt
├── .env.example
└── README.md
```

### Database models

| User | Link |
|------|------|
| `telegram_id` (unique), `username`, `full_name`, `created_at` | `original_url`, `short_code` (unique, 6–8 chars), `title`, `category`, `user_id` (FK), `clicks`, `created_at`, `last_clicked_at`, `expires_at` |

---

## 🚀 Installation

**Requirements:** Python 3.11+, a Telegram bot token from [@BotFather](https://t.me/BotFather).

```bash
# 1. Clone / enter the project
cd telegram-link-manager

# 2. Create and activate a venv
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env        # Windows
# cp .env.example .env        # Linux/macOS
# …then edit .env and set BOT_TOKEN + BASE_URL…

# 5. Run
python main.py
```

The bot starts polling and the redirect server listens on `PORT`
(default `8080`). Open `http://localhost:8080/` to check health.
The database tables (`users`, `links`) are created automatically on every
start — no migrations or manual setup needed.

---

## ☁️ Deploy to Render (Web Service)

1. Push the project to GitHub (`.gitignore` already excludes `.venv`, `.env`,
   `__pycache__` and local `*.db` files).
2. Create a Postgres database for persistence — **required**: Render's
   filesystem is ephemeral, so a local SQLite file is erased on every
   redeploy, restart or spin-down.
   - Recommended free option: [Neon](https://neon.tech) → copy its
     `postgresql://...` connection string.
   - Or use Render Postgres (the free plan expires 30 days after creation).
3. In the Render Dashboard: **New → Web Service** → pick the repo, then:

   | Setting | Value |
   |---------|-------|
   | Language | `Python 3` |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `python main.py` |
   | Health Check Path | `/` |
   | Instance Type | `Free` (or any paid plan) |
   | Environment variables | `BOT_TOKEN` (from @BotFather), `DATABASE_URL` (step 2), optional `PYTHON_VERSION=3.11.9` |

4. Click **Create Web Service**. Render builds and runs `python main.py`.

That's all — no Blueprint needed. The app switches to **webhook mode**
automatically because `RENDER_EXTERNAL_URL` is present, and uses that URL for
`BASE_URL` and the Telegram webhook. Set `BASE_URL` yourself only when using a
custom domain.

### No sleeping on the free plan

Free web services spin down after 15 minutes **without inbound traffic**
(outbound polling doesn't count). The app therefore **self-pings its own
`/healthz` endpoint every 10 minutes** whenever it runs on Render
(`KEEP_ALIVE`, controlled by `KEEP_ALIVE` / `KEEP_ALIVE_INTERVAL`). Each ping
is inbound traffic, so the service — and the bot — stays awake 24/7.

Caveats:

- A self-ping only works while the process is running; it cannot wake an
  already-slept service. In practice the timer starts at boot and pings well
  before the 15-minute deadline, so it never sleeps. If Render ever restarts
  or suspends the instance, an external monitor (UptimeRobot, cron-job.org, …)
  is the only way to wake it.
- Staying awake uses ~730 of the 750 free instance hours per month.
- Render Postgres free instances **expire 30 days after creation** — use Neon
  (or a paid Render database) for something long-lived.

### Alternative: persistent disk (paid)

To avoid an external database, run a paid instance with a disk instead:

- Instance type: any paid plan (Starter or higher)
- Disk mount path: `/var/data`
- `DATABASE_URL=sqlite+aiosqlite:////var/data/bot.db` (four slashes = absolute path)

---

## ⚙️ Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | ✅ | — | Telegram bot token from @BotFather |
| `BASE_URL` | — | `RENDER_EXTERNAL_URL` or `https://example.com` | Public base URL used to build short links (`http://localhost:8080` for local dev) |
| `DATABASE_URL` | — | `sqlite+aiosqlite:///./bot.db` | SQLAlchemy async URL; `postgres://` / `postgresql://` strings are converted for asyncpg (incl. `sslmode`) |
| `SHORT_CODE_LENGTH` | — | `6` | Short-code length (6–8 recommended) |
| `WEB_HOST` / `PORT` | — | `0.0.0.0` / `8080` | Redirect server bind address (Render sets `PORT`) |
| `USE_WEBHOOK` | — | auto on Render | `true` delivers updates via webhook instead of polling |
| `WEBHOOK_SECRET` | — | derived from `BOT_TOKEN` | Optional Telegram webhook secret token |
| `WEBHOOK_PATH` | — | `/telegram/webhook` | Path the webhook endpoint is served on |
| `KEEP_ALIVE` | — | auto on Render | Self-ping `/healthz` to stop free-plan spin-down |
| `KEEP_ALIVE_INTERVAL` | — | `600` | Seconds between self-pings (clamped to 60–840) |
| `RENDER_EXTERNAL_URL` | — | auto | Set by Render; used as `BASE_URL` fallback and auto-enables webhooks |
| `LOG_LEVEL` | — | `INFO` | `DEBUG / INFO / WARNING / ERROR` |

---

## 🖥 Usage

1. `/start` — open the main panel.
2. Send any URL — bot replies with the short link card.
3. `/new` — guided creation flow (same result, explicit state).
4. `/links` — paginated list → tap a row → **Open / Edit / Delete**.
5. Edit → pick field: **Category** (buttons), **Expiry** (presets or
   `2026-12-31T23:59` / `none`), **Title** (free text), **URL** (free text).
6. `/stats` — totals, per-category breakdown, top links.
7. Open `BASE_URL/{code}` in a browser — click is counted, you are redirected.

---

## 📸 Screenshots

> Placeholders — replace with real captures.

```text
docs/screenshots/01-start-menu.png     # /start panel (New link / My links / Stats)
docs/screenshots/02-link-created.png   # "Link created!" card with short URL
docs/screenshots/03-links-list.png     # paginated "Your links" list
docs/screenshots/04-link-detail.png    # detail card (clicks, dates, Open/Edit/Delete)
docs/screenshots/05-stats.png          # statistics overview
```

---

## 🔌 How the redirect works

```python
# main.py (simplified)
link = await service.resolve_and_track(short_code)
if link:
    raise web.HTTPFound(link.original_url)  # 302 + clicks += 1
return web.Response("Link not found.", status=404)
```

- Unknown/malformed code → `404`, expired → `410`, disabled → `403` (not counted).
- The "🌐 Open short link" button always opens the **short** URL so the
  click is counted server-side; the original URL is never a button.

---

## 🛡 Security notes

- `IsPrivate` filter runs **before** middleware — group/channel updates are
  dropped before any DB access.
- Every service query filters by `user_id` — cross-user access is impossible.
- All user strings are HTML-escaped before sending; exception details are
  never leaked to the chat.

---

## 📄 License

MIT — do whatever you want, attribution appreciated.
