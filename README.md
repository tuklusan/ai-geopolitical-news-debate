# Live News Debate Wall

A production-quality Python application that fetches business-news headlines from
France 24 and generates a continuing **fictional AI-parody discussion** among four
personas: POTUS, the President of the European Commission, Gronk Vellumthud, and
Yoda.

> **AI Parody — Fictional Satire.** All personas are fictional AI-generated
> parodies. POTUS and the President of the European Commission represent the
> *offices* only — no real officeholder is ever named. No persona represents a
> real person. For entertainment only.

## Features

- **RSS feed fetching** from `https://www.france24.com/en/business/rss` at startup
  and periodically thereafter.
- **Four distinct personas** with enforced style and word/line limits.
- **Defensive model-output validation** — strips fences, rejects JSON/YAML/XML/
  dicts/code blocks/empty/mid-sentence/overlong output; never stores or displays
  malformed responses.
- **Single repair retry** with a stricter instruction; failed repairs are skipped
  safely.
- **Responsive layout** — desktop (transcript left, sidebar right) and mobile
  (persona info above transcript), with static top and bottom parody disclaimers.
- **Correct auto-scroll** — scrolls the transcript container (not the page body),
  respects upward scrolling, shows a "Jump to latest" control, and resumes
  following when the viewer returns to the bottom.
- **SQLite persistence** — messages, topics, known feed items, speaker history,
  topic memory, and monotonically increasing message IDs.
- **Degraded-state operation** — continues serving the interface and stored
  transcript when RSS, model, or API key is unavailable.
- **aiohttp** for the HTTP server, RSS requests, and async operation. No Flask,
  FastAPI, Uvicorn, Nginx, Apache, Node.js, React, Vue, or reverse proxy required.

## Requirements

- Python 3.12 or later
- Dependencies listed in `requirements.txt`:
  - `aiohttp`, `aiohttp-cors`, `beautifulsoup4`, `lxml`, `python-dotenv`

## Configuration

Copy `config/.env.example` to `config/.env` and fill in your values:

```bash
cp config/.env.example config/.env
```

Key settings (defaults shown in `config/.env.example`):

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible endpoint |
| `LLM_MODEL` | `z-ai/glm-5.2` | Model name |
| `LLM_TEMPERATURE` | `0.55` | Sampling temperature |
| `LLM_MAX_TOKENS` | `140` | Output token ceiling (high enough for a complete short response) |
| `LLM_TIMEOUT_SECONDS` | `90` | Request timeout |
| `LLM_API_KEY` | *(required for generation)* | API key — never hard-coded, stored, displayed, or logged |
| `RSS_FEED_URL` | `https://www.france24.com/en/business/rss` | RSS feed URL |
| `HOST` | `0.0.0.0` | HTTP bind address |
| `PORT` | `8765` | HTTP port |
| `DB_PATH` | `live_news_wall.db` | SQLite database path |
| `RSS_REFRESH_INTERVAL_SECONDS` | `300` | RSS refresh interval |
| `MESSAGE_MIN_DELAY_SECONDS` | `3` | Min seconds between messages |
| `MESSAGE_MAX_DELAY_SECONDS` | `6` | Max seconds between messages |

### API key handling

The API key is read from configuration only. It is **never** hard-coded, stored in
SQLite, displayed in the UI, logged, or included in test output. When the key is
absent, the app starts the HTTP server, continues RSS processing, reports that
model generation is unavailable, and does not make doomed model requests.

## Launch

### Foreground

```bash
python live_news_wall.py
```

### Background

```bash
nohup python live_news_wall.py > live_news_wall.log 2>&1 &
```

### systemd

See `manual.md` for a complete systemd unit file.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Embedded responsive HTML/CSS/JS interface |
| GET | `/api/messages?since=N` | JSON: messages with id > N, current topic, and health |
| GET | `/healthz` | JSON health check (200 ok, 503 degraded) |

## LAN URL

When the server binds to `0.0.0.0:8765`, it is reachable on the LAN at:

```
http://<your-lan-ip>:8765/
```

## Testing

```bash
python -m pytest -v
python -m compileall live_news_wall.py tests
```

All tests use fakes/mocks — no real RSS feed or real model API is required.

## Project Structure

```
live_news_wall.py      Main entry point
config_loader.py       Configuration loader (env vars / .env)
database.py            SQLite persistence layer
feed.py                Async RSS feed client (aiohttp)
llm_client.py          OpenAI-compatible LLM client
personas.py            Persona definitions and prompts
validator.py           Model-output validation and repair instructions
engine.py              Conversation engine (speaker selection, generation, repair)
web_server.py          aiohttp web server + embedded HTML/CSS/JS
config/.env.example    Configuration example
requirements.txt       Python dependencies
tests/                 Unit, integration, validation, and scrolling tests
README.md              This file
BUILD_NOTES.md         Build log, test results, defects, scrolling checks
manual.md              User and operations manual
```
