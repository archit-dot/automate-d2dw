# Telepost

Production-oriented Telegram route automation built around one Telegram user session and one admin control bot.

## Architecture

```text
Telegram sources
      ↓
source monitor
      ↓
route round-robin
      ↓
DiskWala conversion
      ↓
route-specific queue (max 40)
      ↓
route timetable / publisher
      ↓
Telegram destination
```

A single source can belong to multiple independent routes. Every route has one destination, its own source rotation, queue, timetable, operating window, Auto-Pilot state, header and footer.

## Important Telegram entity handling

Telethon cannot reliably turn an arbitrary numeric Telegram ID into a usable API peer unless the current user session has already encountered that chat and therefore has its account-specific access hash. Telepost therefore resolves sources and destinations **before saving them**, stores the resolved peer information, and reuses the stored input peer later.

The UI supports:

- Select a source/destination directly from the Telegram user's dialogs.
- Enter a `-100...` Telegram ID.
- Enter `@username`.
- Enter a Telegram link.

For a private chat with no public username, the Telegram user account must actually have access to the chat. If Telegram has never exposed that peer to the session, a bare numeric ID cannot manufacture the missing access hash.

## Locked behavior

- Multiple sources → one destination per route.
- The same source can belong to multiple routes.
- Each route has an independent queue and timetable.
- Strict round-robin source rotation.
- One eligible logical post per source turn.
- Posts without DiskWala links are skipped and do not count toward 40.
- Queue/fetch hard limit: 40 posts.
- Media albums are treated as one logical post.
- DiskWala conversion requests are serialized to prevent response cross-correlation.
- Fetch Old starts at the supplied post and moves toward newer posts.
- Fetch Old accepts an optional count; 0 means through the latest available post.
- Header/footer are route-specific.
- No promo-post feature.
- No every-N-post notification feature.
- Button-driven admin interface.
- Advanced Control Center dashboard.

## Route workflow

```text
Routes
  ↓
Create Route
  ↓
Route name
  ↓
Select configured destination
  ↓
Select route sources with buttons
  ↓
Configure timetable/window/header/footer
  ↓
Start Auto-Pilot
```

## DiskWala

Default converter:

```text
@DW2DW_LinkConverterBot
```

Telepost forwards the complete logical source post to the converter, waits for the converter response, stores the response message IDs, and publishes those response messages later when the route timetable fires.

## Environment

```env
BOT_TOKEN=
OWNER_ID=
API_ID=
API_HASH=
PHONE_NUMBER=
SESSION_NAME=sessions/telepost_user
DISKWALA_BOT=@DW2DW_LinkConverterBot
TIMEZONE=Asia/Kolkata
DB_PATH=data/telepost.db
```

## Install

Recommended Python version for a stable deployment is Python 3.11–3.13.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill the values.

Create the Telegram user session once:

```powershell
python setup_session.py
```

Then run:

```powershell
python app.py
```

## Security

Never commit:

- `.env`
- `sessions/`
- `*.session`
- `data/`
- `logs/`
- `.venv/`

The Telegram user session is as sensitive as a login credential.
