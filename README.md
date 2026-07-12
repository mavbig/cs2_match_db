# CS2 Match DB

Self-hosted Counter-Strike 2 match history and player tracker. Syncs your Valve MM/Premier matches via Steam Game Coordinator (same approach as csstats.gg), FACEIT matches via API, and provides a searchable web UI for co-play history and player profiles.

## Features

- **Automatic Steam match sync** — server-side GC sync using your Match History Auth Code (no local agent)
- **FACEIT match sync** — periodic import via FACEIT Data API
- **Player indexing** — all 10 players from each match stored with full scoreboard stats
- **Co-play search** — "How many times have I played with X?"
- **Manual lookup** — paste a Steam profile URL to search and enrich
- **Leetify aim stats** — optional enrichment for aim/utility/opening ratings
- **Name history** — tracks alias changes over time from match encounters

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Oracle Cloud ARM VM (Docker Compose)               │
│  ┌─────────┐ ┌─────────┐ ┌───────────┐ ┌─────────┐ │
│  │ Next.js │ │ FastAPI │ │ steam-sync│ │ Worker  │ │
│  │ :3472*  │ │  :8000  │ │ (Node/GC) │ │ (ARQ)   │ │
│  └────┬────┘ └────┬────┘ └─────┬─────┘ └────┬────┘ │
│       └───────────┴────────────┴────────────┘      │
│                         │                           │
│              ┌──────────┴──────────┐                │
│              │ PostgreSQL + Redis  │                │
│              └─────────────────────┘                │
└─────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Clone and configure

```bash
cp .env.example .env
# Edit .env with your credentials (see below)
```

### 2. Start the stack

```bash
docker compose up -d --build
```

Services:
- **Web UI**: http://localhost:3472 (override with `WEB_PORT` in `.env`)
- **API**: http://localhost:8000
- **API docs**: http://localhost:8000/docs

### 3. Complete onboarding (Web UI → Settings)

1. **Steam64 ID** — your Steam ID (find at steamid.io)
2. **Match History Auth Code** — generate at [Steam CS2 help wizard](https://help.steampowered.com/en/wizard/HelpWithGameIssue/?appid=730&issueid=128)
3. **Oldest Share Code** — CS2 → Watch → Your Matches → copy oldest `CSGO-XXXXX-...` code
4. **Steam Web API Key** — [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey)
5. **Bot Steam account** — create a secondary Steam account; set `STEAM_BOT_USERNAME`, `STEAM_BOT_PASSWORD` in `.env` (optionally `STEAM_BOT_SHARED_SECRET` for 2FA)
6. **FACEIT API Key** — [developers.faceit.com](https://developers.faceit.com/)
7. **Leetify API Key** (optional) — [leetify.com/app/developer](https://leetify.com/app/developer)

After saving settings, the **steam-sync** service will automatically import your match history (every ~5 minutes). To start immediately:

```bash
docker compose up -d --build steam-sync
docker compose logs steam-sync --tail 50
```

## How match history gets imported

| Source | What it imports | Whose matches |
|--------|-----------------|---------------|
| **steam-sync** (automatic) | Valve MM/Premier games via Game Coordinator | **Yours only** (requires auth code + share code + bot account) |
| **FACEIT sync** | FACEIT games via API | **Yours** (your FACEIT nickname) |
| **Import Share Code** | One specific game | Any match you have the `CSGO-...` code for |
| **Player Search** | Profile + stats only | Does **not** import match history |

When **your** matches sync, all **9 other players** in each game are saved automatically. You cannot fetch another player's full Steam match history unless they provide their own auth code (Steam privacy).

## Manual Actions (Settings page)

| Button | What it does |
|--------|----------------|
| **Trigger Steam Sync** | Creates a sync job record. The actual Steam import is performed by the **steam-sync** container, which runs automatically on a timer. If matches aren't appearing, check `docker compose logs steam-sync`. |
| **Trigger FACEIT Sync** | Immediately queues a job to pull **your** FACEIT match history (requires FACEIT API key + nickname in Settings). |
| **Trigger Enrichment** | Refreshes **stats** for known players (Leetify aim ratings, FACEIT ELO, Steam avatars). Does not import new matches. |
| **Import Share Code** | Paste a single match code (`CSGO-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX`) from CS2 → Watch → Your Matches to import that one game. |

To get a share code in CS2: **Watch → Your Matches → select a match → Copy match sharing code** (bottom right).

## Oracle Cloud ARM Deployment

This stack is designed for ARM64 (Oracle Cloud Free Tier Ampere VMs).

```bash
# On your Ubuntu VM
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER
# Log out and back in

git clone <your-repo> cs2_match_db
cd cs2_match_db
cp .env.example .env
nano .env   # fill in credentials

docker compose up -d --build
```

Open ports in Oracle Cloud security list:
- **3472** (or your `WEB_PORT`) — Web UI only (API is proxied through the web app; port 8000 does not need to be public)

For production, add a reverse proxy with TLS (Caddy recommended):

```
your-domain.com {
    reverse_proxy web:3000
}
```

The web app proxies `/api/v1/*` to the FastAPI backend internally — you only need to expose the web port.

## Troubleshooting

### "Failed to fetch" on Search or Settings

This usually means the browser tried to call `localhost:8000` instead of your VM. Pull the latest code and rebuild the web container:

```bash
git pull
docker compose up -d --build web
```

You should only need to open **one port** (`WEB_PORT`, default 3472) in your firewall/security list.

### steam-sync crash: `Cannot find module '/app/dist/index.js'`

The host volume mount was overwriting the compiled build inside the container. Pull the latest code and rebuild **without** cached layers:

```bash
git pull
docker compose up -d --build --no-cache steam-sync
docker compose logs steam-sync --tail 30
```

You should see `[steam-sync] Starting CS2 match sync service` followed by sync activity or a clear configuration message.

### steam-sync runs but imports 0 matches

Check that all of these are set (Settings UI **and/or** `.env`):

- `MY_STEAM64_ID`
- `STEAM_AUTH_CODE` (Match History Auth Code)
- `STEAM_OLDEST_SHARE_CODE`
- `STEAM_BOT_USERNAME` / `STEAM_BOT_PASSWORD` in `.env`

Then inspect logs for GC connection errors:

```bash
docker compose logs steam-sync --tail 50
```

## Bot Account Setup

The `steam-sync` service needs a **dedicated Steam bot account** to connect to the CS2 Game Coordinator. This is the same model csstats.gg and Leetify use.

1. Create a new Steam account (not your main account)
2. Add CS2 to the bot library (free)
3. Set credentials in `.env`:
   ```
   STEAM_BOT_USERNAME=your_bot_username
   STEAM_BOT_PASSWORD=your_bot_password
   STEAM_BOT_SHARED_SECRET=   # optional, from Steam Desktop Authenticator .maFile
   ```
4. If using 2FA, extract `shared_secret` from the bot's `.maFile`

Your **personal Steam login is never stored** — only the read-only Match History Auth Code.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_PASSWORD` | Yes | Database password |
| `API_SECRET_KEY` | Yes | Random secret string |
| `API_SYNC_TOKEN` | Yes | Token for steam-sync → API auth |
| `MY_STEAM64_ID` | Yes | Your Steam64 ID |
| `STEAM_BOT_USERNAME` | Yes | Bot account for GC connection |
| `STEAM_BOT_PASSWORD` | Yes | Bot account password |
| `STEAM_BOT_SHARED_SECRET` | No | 2FA shared secret for bot |
| `STEAM_API_KEY` | Yes | Steam Web API key |
| `STEAM_AUTH_CODE` | Yes* | Match History Auth Code (*or set via UI) |
| `STEAM_OLDEST_SHARE_CODE` | Yes* | Seed share code for backfill |
| `FACEIT_API_KEY` | No | FACEIT Data API key |
| `FACEIT_NICKNAME` | No | Your FACEIT nickname |
| `LEETIFY_API_KEY` | No | Leetify Public API key |
| `SYNC_POLL_INTERVAL` | No | Seconds between GC polls (default 300) |

## Usage

### Search for a player

Go to **Search** and paste:
- A Steam profile URL: `https://steamcommunity.com/id/mavbig`
- A Steam64 ID: `76561198012345678`
- A player name from your indexed matches

### View co-play history

On any player profile, see **Times Played With You** — how many matches you've shared and when.

### Import a single match

Settings → paste a share code → **Import Share Code** (see **Manual Actions** above).

## Project Structure

```
cs2_match_db/
├── docker-compose.yml
├── packages/
│   ├── api/          # FastAPI backend
│   ├── worker/       # ARQ enrichment worker
│   ├── steam-sync/   # Node.js GC match sync
│   └── web/          # Next.js frontend
└── migrations/       # Alembic DB migrations
```

## Development (local)

```bash
# Start infrastructure only
docker compose up -d postgres redis

# API (from packages/api)
pip install -r requirements.txt
alembic -c ../../migrations/alembic.ini upgrade head
uvicorn app.main:app --reload

# Web (from packages/web)
npm install && npm run dev

# Worker (from packages/worker)
pip install -r requirements.txt
arq worker.WorkerSettings

# Steam sync (from packages/steam-sync)
npm install && npm run dev
```

## Limitations

- **Other players' full match history** is not accessible unless they provide their auth code (Steam privacy)
- **Share codes expire** after ~30 days — run initial backfill promptly after setup
- **Leetify stats** require the player to have a public Leetify profile
- **Bot account** must maintain GC connection; reconnects automatically on failure

## License

MIT
