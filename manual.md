# Live News Debate Wall — User & Operations Manual

> **AI Parody — Fictional Satire.** All personas are fictional AI-generated
> parodies. POTUS and the President of the European Commission represent the
> *offices* only — no real officeholder is ever named. No persona represents
> a real person. For entertainment only.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Main Functions](#2-main-functions)
3. [System Requirements](#3-system-requirements)
4. [Installation & Environment Dependencies](#4-installation--environment-dependencies)
5. [Configuration](#5-configuration)
6. [Running the Application](#6-running-the-application)
7. [Using the Web Interface](#7-using-the-web-interface)
8. [HTTP API Endpoints](#8-http-api-endpoints)
9. [The Four Personas](#9-the-four-personas)
10. [Auto-Scroll Behavior](#10-auto-scroll-behavior)
11. [Degraded-State Operation](#11-degraded-state-operation)
12. [SQLite Database](#12-sqlite-database)
13. [Testing](#13-testing)
14. [Troubleshooting](#14-troubleshooting)
15. [Project Structure](#15-project-structure)
16. [Glossary](#16-glossary)

---

## 1. Overview

**Live News Debate Wall** is a self-contained, production-quality Python
application that fetches business-news headlines from the France 24 English
RSS feed and generates a continuing **fictional AI-parody discussion** among
exactly four personas:

- **POTUS** — a fictional representation of the U.S. presidential *office*
- **President of the European Commission** — a fictional representation of
  that EU *office*
- **Gronk Vellumthud** — a fictional Vogon bureaucrat and poet
- **Yoda** — a fictional Jedi master parody

The application uses an OpenAI-compatible language model to generate the
conversation. Every model response is defensively validated before display or
storage: malformed, structured, fenced, empty, mid-sentence, or overlong
output is rejected — never displayed as a raw fallback and never persisted to
the database.

The entire user interface — HTML, CSS, and vanilla JavaScript — is embedded
inside the primary Python application and served by a built-in aiohttp web
server. No external web server, reverse proxy, frontend framework, or Node.js
runtime is required.

---

## 2. Main Functions

### News Feed Ingestion

- Fetches the France 24 English business RSS feed
  (`https://www.france24.com/en/business/rss`) at startup and periodically
  thereafter (default every 300 seconds).
- Parses RSS items using BeautifulSoup + lxml.
- Deduplicates feed items using a SQLite `known_feed_items` table.
- Activates the newest feed item as the current **discussion topic**.
- When a newer headline is discovered, the topic changes immediately and any
  in-flight model responses generated for the now-obsolete topic are
  discarded.

### AI-Parody Conversation Generation

- Generates a continuing fictional debate among the four personas.
- Uses **randomized speaker order** while preventing the same speaker from
  speaking twice consecutively (enforced via the SQLite `speaker_history`
  table).
- Spaces visible conversation messages by a **random delay of 3–6 seconds**
  (configurable via `MESSAGE_MIN_DELAY_SECONDS` and
  `MESSAGE_MAX_DELAY_SECONDS`).
- Each persona has a distinct style and a hard word/line limit enforced by
  post-generation validation (see [§9](#9-the-four-personas)).

### Defensive Model-Output Validation

Before any model response is displayed or stored, the validator
(`validator.py`) performs these checks:

1. **Fence stripping** — removes accidental leading/trailing Markdown fences
   (```` ``` ```` or `~~~`).
2. **Structured-data rejection** — rejects output resembling JSON, YAML,
   XML, Python dictionaries, or containing field names (`text`,
   `new_point`, `speaker`, `analysis`, `reasoning`, etc.).
3. **Code-block rejection** — rejects output resembling code (`print`,
   `def`, `import`, `console.log`, etc.).
4. **Empty rejection** — rejects empty or whitespace-only output.
5. **Mid-sentence rejection** — rejects prose that ends without terminal
   punctuation (`.`, `!`, `?`, `"`, `'`, curly quotes).
6. **Overlong rejection** — rejects output exceeding the persona word or
   line limit.
7. **Persona-prefix stripping** — strips `POTUS:` etc. and re-validates.

If validation fails, the engine retries generation **once** with a stricter
repair instruction. If the repair also fails, the contribution is **skipped
silently** and a sanitized warning is logged. Broken, partial, fenced, or
structured output is never inserted into the transcript.

### Web Interface

- Serves a responsive single-page application at `GET /`.
- Desktop layout: scrolling transcript on the left, fixed persona sidebar
  on the right, static top and bottom parody disclaimers.
- Mobile layout: persona information above the transcript, transcript
  remains vertically scrollable, no horizontal scrolling, static
  disclaimers.
- The transcript auto-scrolls correctly (see
  [§10](#10-auto-scroll-behavior)).

### SQLite Persistence

Persists messages, topics, known feed items, recent speaker history, topic
memory, and monotonically increasing message IDs (see
[§12](#12-sqlite-database)).

### Degraded-State Operation

Continues operating when RSS, the model API, or the API key is unavailable
(see [§11](#11-degraded-state-operation)).

---

## 3. System Requirements

- **Python 3.12 or later**
- **An OpenAI-compatible API key** (e.g., NVIDIA NIM, OpenAI, or any
  provider exposing the `/v1/chat/completions` endpoint)
- **Network access** to the France 24 RSS feed (for live headlines)
- **Network access** to the LLM endpoint (for conversation generation)
- A modern web browser (Chrome, Firefox, Safari, Edge) for viewing the
  interface

> **Note:** The application can start and serve the interface even without
> an API key or network access; see [§11](#11-degraded-state-operation).

---

## 4. Installation & Environment Dependencies

### Step 1 — Obtain the source code

Place all project files in a working directory, for example:

```bash
mkdir -p /opt/live-news-wall
cd /opt/live-news-wall
# Copy or extract the project files here
```

### Step 2 — Create and activate a virtual environment (recommended)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

On Windows:

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### Step 3 — Install Python dependencies

All dependencies are listed in `requirements.txt`:

| Package | Purpose |
|---|---|
| `aiohttp` | Async HTTP server, RSS fetching, LLM API calls |
| `aiohttp-cors` | CORS support for the aiohttp server |
| `beautifulsoup4` | RSS XML / HTML parsing |
| `lxml` | Fast XML parser backend for BeautifulSoup |
| `python-dotenv` | `.env` file loading |

Install with pip:

```bash
pip install -r requirements.txt
```

Or with `uv` (if available):

```bash
uv pip install -r requirements.txt
```

> **No** Nginx, Apache, Flask, FastAPI, Uvicorn, Node.js, React, Vue, or
> reverse proxy is required.

### Step 4 — Configure the application

```bash
cp config/.env.example config/.env
```

Edit `config/.env` and set your API key (see [§5](#5-configuration)).

### Step 5 — Verify the installation

```bash
# Compile-check all source and test modules
python -m compileall live_news_wall.py tests

# Run the full test suite (no real RSS feed or model API required)
python -m pytest -v
```

You should see all 68 tests pass.

---

## 5. Configuration

All configuration is read from environment variables, optionally loaded from a
`config/.env` file. The loader (`config_loader.py`) reads `config/.env` if it
exists, then falls back to process environment variables, then to built-in
defaults.

### Complete configuration reference

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible API base URL |
| `LLM_MODEL` | `z-ai/glm-5.2` | Model name sent in chat-completion requests |
| `LLM_TEMPERATURE` | `0.55` | Sampling temperature |
| `LLM_MAX_TOKENS` | `140` | Output token ceiling (high enough for a complete short response) |
| `LLM_TIMEOUT_SECONDS` | `90` | Request timeout in seconds |
| `LLM_API_KEY` | *(required for generation)* | API key — see below |
| `RSS_FEED_URL` | `https://www.france24.com/en/business/rss` | RSS feed URL |
| `HOST` | `0.0.0.0` | HTTP server bind address |
| `PORT` | `8765` | HTTP server port |
| `DB_PATH` | `live_news_wall.db` | SQLite database file path |
| `RSS_REFRESH_INTERVAL_SECONDS` | `300` | Seconds between periodic RSS refreshes |
| `MESSAGE_MIN_DELAY_SECONDS` | `3` | Minimum seconds between visible messages |
| `MESSAGE_MAX_DELAY_SECONDS` | `6` | Maximum seconds between visible messages |

### API key handling

Set `LLM_API_KEY` in `config/.env`:

```
LLM_API_KEY=your-real-api-key-here
```

The API key is read from configuration **only**. It is:

- ❌ Never hard-coded in source
- ❌ Never stored in SQLite
- ❌ Never displayed in the UI
- ❌ Never written to logs
- ❌ Never included in test output

When the key is absent (or set to the placeholder
`replace-with-your-real-api-key`), the application:

- Starts the HTTP server normally
- Continues RSS processing
- Reports that model generation is unavailable in the health indicator
- Does **not** make doomed model requests

### Example `config/.env`

```
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=z-ai/glm-5.2
LLM_TEMPERATURE=0.55
LLM_MAX_TOKENS=140
LLM_TIMEOUT_SECONDS=90
LLM_API_KEY=your-real-api-key-here
RSS_FEED_URL=https://www.france24.com/en/business/rss
HOST=0.0.0.0
PORT=8765
DB_PATH=live_news_wall.db
RSS_REFRESH_INTERVAL_SECONDS=300
MESSAGE_MIN_DELAY_SECONDS=3
MESSAGE_MAX_DELAY_SECONDS=6
```

---

## 6. Running the Application

### Foreground launch

```bash
python live_news_wall.py
```

The server starts on `http://0.0.0.0:8765/` by default. Open
`http://localhost:8765/` in your browser.

### Background launch

```bash
nohup python live_news_wall.py > live_news_wall.log 2>&1 &
```

To stop a background process:

```bash
# Find the process ID
pgrep -f live_news_wall.py
# Terminate it
kill <PID>
```

### systemd (Linux production deployment)

Create `/etc/systemd/system/live-news-wall.service`:

```ini
[Unit]
Description=Live News Debate Wall
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/live-news-wall
ExecStart=/opt/live-news-wall/.venv/bin/python live_news_wall.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/var/log/live-news-wall/live-news-wall.log
StandardError=append:/var/log/live-news-wall/live-news-wall.log

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable live-news-wall
sudo systemctl start live-news-wall
sudo systemctl status live-news-wall
```

To stop or restart:

```bash
sudo systemctl stop live-news-wall
sudo systemctl restart live-news-wall
```

To view logs:

```bash
sudo journalctl -u live-news-wall -f
# or
tail -f /var/log/live-news-wall/live-news-wall.log
```

### LAN URL

When the server binds to `0.0.0.0:8765`, it is reachable from other devices
on the same network at:

```
http://<your-lan-ip>:8765/
```

Find your LAN IP:

```bash
hostname -I
# or
ip addr show | grep 'inet ' | grep -v 127.0.0.1
```

Example: `http://192.168.1.100:8765/`

---

## 7. Using the Web Interface

### Desktop layout

When viewed on a wide screen (> 768 px):

- **Left side** — a scrolling transcript area showing the live debate.
  Each message displays the speaker's display name (color-coded) and the
  spoken text. A topic bar above the transcript shows the current headline.
- **Right side** — a fixed sidebar ("Debate Panel") with a persona card
  for each of the four participants, showing their display name, role,
  country, style description, and word/line limits. A health indicator
  at the bottom of the sidebar shows RSS and model status.
- **Top** — a permanent AI-parody disclaimer bar.
- **Bottom** — a permanent AI-parody disclaimer bar.

### Mobile layout

When viewed on a narrow screen (≤ 768 px):

- Persona information (the sidebar) appears **above** the transcript.
- The transcript remains readable and vertically scrollable.
- No horizontal scrolling occurs.
- The top and bottom AI-parody disclaimers remain static and visible.

### Interacting with the transcript

- **Reading** — messages appear automatically as they are generated (every
  3–6 seconds). The transcript polls the server every 2 seconds for new
  messages.
- **Scrolling up** — scroll upward at any time to read older messages.
  The interface will not fight you or force you back to the bottom.
- **Jump to latest** — when newer messages arrive while you are scrolled
  up, a "Jump to latest" button appears at the bottom-right of the
  transcript area. Click it to scroll to the newest message and resume
  automatic following.
- **Resuming auto-follow** — simply scroll back to the bottom of the
  transcript. Automatic following resumes immediately.

### Health indicator

The sidebar health indicator shows:

- **RSS: ok** or **RSS: degraded** — whether the RSS feed was last fetched
  successfully.
- **Model: ok**, **Model: degraded**, or **Model: unavailable (no API
  key)** — whether the model API is responding, degraded, or disabled due
  to a missing API key.

---

## 8. HTTP API Endpoints

The application exposes three HTTP endpoints:

### `GET /`

Returns the complete embedded HTML/CSS/JavaScript interface as
`text/html`. This is the primary user-facing page.

### `GET /api/messages?since=N`

Returns a JSON payload with messages whose `id` is greater than `N`, the
current topic, and the health snapshot. This endpoint is polled by the
browser every 2 seconds.

**Query parameter:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `since` | integer | `0` | Return only messages with `id > since` |

**Response format:**

```json
{
  "messages": [
    {
      "id": 1,
      "topic_id": 1,
      "speaker": "potus",
      "text": "Jobs are back, factories are humming, and the economy roars. We win on growth.",
      "created_at": 1234567890.0
    }
  ],
  "topic": "Current headline text",
  "health": {
    "rss_healthy": true,
    "model_healthy": true,
    "model_disabled": false,
    "active_topic": "Current headline text"
  }
}
```

The `speaker` field is one of: `"potus"`, `"eu"`, `"gronk"`, `"yoda"`.

### `GET /healthz`

Returns a JSON health snapshot. HTTP status is **200** when the system is
healthy, **503** when degraded.

**Response format:**

```json
{
  "rss_healthy": true,
  "model_healthy": true,
  "model_disabled": false,
  "active_topic": "Current headline text"
}
```

**Usage example (monitoring):**

```bash
curl http://localhost:8765/healthz
# Healthy:  {"rss_healthy": true, "model_healthy": true, ...}  (HTTP 200)
# Degraded: {"rss_healthy": false, "model_healthy": true, ...} (HTTP 503)
```

```bash
# Fetch the latest 50 messages from the command line:
curl "http://localhost:8765/api/messages?since=0" | python -m json.tool
```

---

## 9. The Four Personas

Each persona has a distinct style and a hard word or line limit. These limits
are enforced by post-generation validation — not by a low token ceiling. The
token ceiling (`LLM_MAX_TOKENS=140`) is set high enough for a complete short
response.

### POTUS

| Attribute | Value |
|---|---|
| **Display name** | POTUS |
| **Role** | Fictional President of the United States (office only) |
| **Country** | United States |
| **Style** | Concise, forceful, economy-first framing |
| **Word limit** | Maximum 45 words |
| **Line limit** | None (prose) |

Example: *"Jobs are back, factories are humming, and the economy roars. We win on growth."*

### President of the European Commission

| Attribute | Value |
|---|---|
| **Display name** | President of the European Commission |
| **Role** | Fictional President of the European Commission (office only) |
| **Country** | European Union |
| **Style** | Measured, policy-focused |
| **Word limit** | Maximum 65 words |
| **Line limit** | None (prose) |

Example: *"We welcome coordinated fiscal signals while safeguarding the single market. Stability, sustainability, and fair competition guide our response."*

### Gronk Vellumthud

| Attribute | Value |
|---|---|
| **Display name** | Gronk Vellumthud |
| **Role** | Vogon bureaucrat and poet |
| **Country** | Vogosphere |
| **Style** | Three short lines of approximate 5-7-5 Vogon bureaucratic poetry |
| **Word limit** | Maximum 36 total words |
| **Line limit** | Exactly 3 lines, maximum 12 words per line |

Example:
> *Bureaucratic forms filed late,*
> *Penalty assessed in triplicate,*
> *No appeals will be accepted.*

### Yoda

| Attribute | Value |
|---|---|
| **Display name** | Yoda |
| **Role** | Fictional Jedi master (parody) |
| **Country** | Dagobah |
| **Style** | Terse, reflective, frequently inverted syntax |
| **Word limit** | Maximum 40 words |
| **Line limit** | None (prose) |

Example: *"Grow, the economy must. Patience, young traders, you must have."*

### Plain-text output contract

The model prompt explicitly instructs the LLM to:

- Return **only** the final visible chat message
- **Not** return JSON
- **Not** use Markdown code fences
- **Not** include fields such as `"text"`, `"new_point"`, `"speaker"`,
  `"analysis"`, or `"reasoning"`
- **Not** prefix the answer with the persona name

---

## 10. Auto-Scroll Behavior

The transcript uses its own scrollable container (the `.transcript` div with
`overflow-y: auto`), not the page body. The auto-scroll logic works as
follows:

| Scenario | Behavior |
|---|---|
| **New message + viewer at/near bottom** | The transcript container scrolls to the newest complete message automatically. |
| **Viewer scrolls upward** | The view is **not** forced back to the bottom. The interface respects the viewer's scroll position. |
| **New messages arrive while scrolled up** | A **"Jump to latest"** button appears at the bottom-right of the transcript area. The button text updates to show the count of unseen messages (e.g., "Jump to latest (3 new) ↓"). |
| **Clicking "Jump to latest"** | Scrolls to the newest message and resumes automatic following. |
| **Viewer returns to bottom manually** | Automatic following resumes immediately. The "Jump to latest" button hides. |
| **Newest message visibility** | The newest message is fully visible, not hidden behind the fixed bottom disclaimer. The disclaimers are part of the flexbox layout (outside the transcript container), so the transcript's `clientHeight` already accounts for them. |

The "near bottom" threshold is 80 pixels: if the viewer is within 80 px of
the bottom of the transcript container, they are considered to be "following"
and new messages auto-scroll.

This behavior is identical on both desktop and mobile layouts — the same
JavaScript scroll logic runs in both cases. The only difference is the CSS
layout (sidebar right on desktop, sidebar above on mobile).

---

## 11. Degraded-State Operation

The application is designed to continue operating in a degraded state whenever
possible:

| Condition | Behavior |
|---|---|
| **RSS unavailable** | Continues serving the existing transcript; reports **RSS: degraded** in the health indicator; retries the RSS fetch on the next refresh cycle (default every 300 seconds). |
| **Model unavailable** | Continues serving the interface and stored transcript; continues RSS processing (new headlines are still discovered and activated); reports **Model: degraded** in the health indicator; retries generation on the next loop iteration. |
| **API key absent** | Starts the HTTP server; continues RSS processing; reports **Model: unavailable (no API key)** in the health indicator; does **not** make doomed model requests. |

In all degraded states, previously stored messages remain available via the
web interface and the `/api/messages` endpoint.

---

## 12. SQLite Database

The application persists all state in a SQLite database file
(`live_news_wall.db` by default, configurable via `DB_PATH`). The database
uses WAL journal mode for concurrent read performance.

### Tables

| Table | Purpose |
|---|---|
| `messages` | Conversation messages with monotonically increasing integer IDs (`id INTEGER PRIMARY KEY`), topic ID, speaker key, text, and creation timestamp. |
| `topics` | RSS headlines used as discussion topics. Each row has `title`, `link`, `summary`, `created_at`, and an `active` flag (1 = active, 0 = superseded). Only one topic is active at a time. |
| `known_feed_items` | Deduplication of seen RSS items, keyed by `link`. Prevents the same feed item from being re-registered on every refresh. |
| `speaker_history` | Recent speaker order, used to prevent the same speaker from speaking twice consecutively. |
| `topic_memory` | Per-topic memory stored as JSON, keyed by `topic_id`. |

### Message ID guarantees

Message IDs are **monotonically increasing**. The `next_message_id()` method
queries `COALESCE(MAX(id), 0) + 1` under an `asyncio.Lock` to guarantee
uniqueness and monotonicity even under concurrent access.

### Inspecting the database

```bash
# Open the database with the sqlite3 CLI
sqlite3 live_news_wall.db

# View recent messages
SELECT id, speaker, substr(text, 1, 80) FROM messages ORDER BY id DESC LIMIT 10;

# View all topics
SELECT id, active, title FROM topics ORDER BY id;

# Check known feed items
SELECT link, title FROM known_feed_items ORDER BY first_seen DESC LIMIT 10;

# Check speaker history
SELECT seq, speaker FROM speaker_history ORDER BY seq DESC LIMIT 10;
```

> **Important:** The API key is **never** stored in the database.

---

## 13. Testing

The application includes a comprehensive automated test suite. All tests use
fakes and mocks — **no real RSS feed or real model API is required**.

### Running the tests

```bash
# Full test suite
python -m pytest -v

# Compilation check
python -m compileall live_news_wall.py tests
```

### Test results

All 68 tests pass:

| Test file | Tests | Result |
|---|---|---|
| `tests/test_engine.py` | 12 | 12 passed |
| `tests/test_integration.py` | 20 | 20 passed |
| `tests/test_scrolling.py` | 9 | 9 passed |
| `tests/test_validation.py` | 27 | 27 passed |
| **Total** | **68** | **68 passed, 0 failed** |

### Test categories

#### `test_validation.py` (27 tests) — Model-output quality and validation

Proves that:

- Valid plain-text messages are accepted (POTUS, EU, Gronk 3-line poem, Yoda).
- Fenced JSON is rejected.
- Incomplete JSON is rejected.
- Raw Python dictionaries are rejected.
- Markdown code blocks are rejected.
- Empty and whitespace-only responses are rejected.
- Mid-sentence responses (no terminal punctuation) are rejected.
- Overlong responses are rejected.
- Persona word limits are enforced (POTUS 45, EU 65, Yoda 40).
- Gronk produces exactly three valid lines (max 12 words per line, max 36 total).
- YAML-like output is rejected.
- Persona-name prefixed output is stripped and re-validated.
- Fenced prose is safely extracted (fence stripped, prose accepted).
- Repair instructions are generated correctly for prose and Gronk personas.
- Fence stripping handles backticks, tildes, and unfenced text.

#### `test_engine.py` (12 tests) — Conversation engine

Proves that:

- No speaker speaks twice consecutively.
- All four personas are selectable.
- Valid messages are stored.
- A single repair attempt succeeds when the repair produces valid output.
- A failed repair causes the contribution to be skipped safely.
- Malformed model output is never stored.
- Only one repair attempt is made.
- Gronk valid output is stored.
- Topic does not churn when the feed is unchanged.
- Topic changes when a new headline appears.
- Obsolete-topic responses are discarded.
- Message IDs are monotonically increasing.

#### `test_scrolling.py` (9 tests) — Transcript scroll-state logic

Uses a faithful Python mirror of the embedded JavaScript scroll logic
(`FakeTranscript`) to verify:

- The transcript container (not the document body) is scrolled.
- A new message scrolls to the bottom when the viewer is near the bottom.
- Automatic scrolling does not override a viewer who scrolled upward.
- A "Jump to latest" control appears when unseen messages arrive.
- Selecting "Jump to latest" scrolls to the newest message and resumes
  following.
- Behavior remains correct after multiple messages.
- The newest message is fully visible (not hidden behind the bottom bar).
- Desktop and mobile layouts use the same logic (tested at different
  container heights).
- Following resumes after the viewer returns to the bottom.

#### `test_integration.py` (20 tests) — HTTP endpoints, persistence, rendered HTML

Proves that:

- `GET /` returns HTML with top and bottom disclaimers.
- `GET /api/messages` returns the correct JSON payload.
- `GET /api/messages?since=N` filters messages correctly.
- `GET /healthz` returns 200 when healthy and 503 when degraded.
- SQLite persistence works for messages, topics, known items, speaker
  history, and topic memory.
- Rendered HTML contains persona cards, scroll logic, responsive media
  query, transcript container ID, `textContent` usage (not `innerHTML`
  for message bodies), no API-key leak, and no dead `escapeHtml` function.
- Fixture messages contain no structured artifacts.
- The engine's aiohttp `ClientSession` is cleanly closed on startup failure
  (e.g., port conflict).

### Test fixtures

The `tests/fixtures.py` file contains realistic transcript examples for
human-style review:

- A short POTUS message
- A longer but compliant European Commission message
- A three-line Gronk poem
- A short Yoda response
- A second set of valid messages for each persona
- Rapid arrival of 12 messages across all four personas (for scrolling)
- A deliberately malformed fenced-JSON model response
- A deliberately incomplete JSON response
- A raw Python dictionary response
- A Markdown code block response
- An empty response
- A whitespace-only response
- A mid-sentence response
- An overlong POTUS response
- A Gronk response with the wrong line count
- A Gronk response with a line exceeding 12 words
- A persona-name prefixed response
- A YAML-like response
- A fenced prose response (should be accepted after fence stripping)

---

## 14. Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| **"Model: unavailable (no API key)"** in health indicator | `LLM_API_KEY` not set or still set to the placeholder | Set `LLM_API_KEY` in `config/.env` to your real API key |
| **"Model: degraded"** in health indicator | Model API is unreachable, returning errors, or timing out | Check network connectivity to `LLM_BASE_URL`; verify the API key is valid; check `LLM_TIMEOUT_SECONDS` |
| **"RSS: degraded"** in health indicator | France 24 RSS feed is unreachable | Check network connectivity; the app will retry on the next refresh cycle |
| **Port already in use** | Another process is using port 8765 | Change `PORT` in `config/.env`, or stop the conflicting process |
| **No messages appearing** | Model generation is disabled or failing | Check `curl http://localhost:8765/healthz`; verify the API key; check logs |
| **Messages not scrolling** | JavaScript disabled in browser | Enable JavaScript; the transcript container uses its own scroll |
| **"Jump to latest" not appearing** | You are already at the bottom | The button only appears when you are scrolled up and new messages arrive |
| **Blank page on load** | JavaScript error or browser incompatibility | Use a modern browser (Chrome, Firefox, Safari, Edge); check the browser console |
| **`IndentationError` or `SyntaxError` on startup** | Python version too old or source corrupted | Ensure Python 3.12+; run `python -m compileall live_news_wall.py tests` |
| **`ModuleNotFoundError: No module named 'aiohttp'`** | Dependencies not installed | Run `pip install -r requirements.txt` |
| **Database locked error** | Another instance is running with the same `DB_PATH` | Use a unique `DB_PATH` or stop the other instance |

### Checking logs

```bash
# Foreground: logs print to the terminal.

# Background:
tail -f live_news_wall.log

# systemd:
sudo journalctl -u live-news-wall -f
```

### Verifying health

```bash
curl http://localhost:8765/healthz
```

### Verifying the API key is not leaked

```bash
# The API key should never appear in any of these:
grep -ri "API_KEY\|api_key\|Bearer" live_news_wall.log
curl http://localhost:8765/ | grep -i "key"
curl http://localhost:8765/api/messages | grep -i "key"
```

---

## 15. Project Structure

```
live_news_wall.py          Main application entry point (orchestrator)
config_loader.py           Configuration loader (env vars / .env)
database.py                SQLite persistence layer
feed.py                    Async RSS feed client (aiohttp + BeautifulSoup)
llm_client.py              OpenAI-compatible LLM client (plain-text contract)
personas.py                Four persona definitions with style/word/line limits
validator.py               Defensive model-output validation and repair instructions
engine.py                  Conversation engine (speaker selection, generation, repair)
web_server.py              aiohttp web server + embedded HTML/CSS/JS interface
config/.env.example        Configuration example (copy to config/.env)
requirements.txt           Python dependencies
pyproject.toml             Project metadata
pytest.ini                 pytest configuration (asyncio_mode=auto)
tests/
  __init__.py              Test package init
  fixtures.py             Realistic transcript fixtures and malformed responses
  test_validation.py       Model-output quality and validation tests (27)
  test_engine.py           Speaker selection, generation, topic, monotonic IDs (12)
  test_scrolling.py        Transcript scroll-state logic tests (9)
  test_integration.py      HTTP endpoints, persistence, rendered HTML (20)
README.md                  Project overview
BUILD_NOTES.md             Build log, test results, defects, scrolling checks
manual.md                  This file
```

---

## 16. Glossary

| Term | Definition |
|---|---|
| **Persona** | One of the four fictional AI-parody debate participants (POTUS, EU Commission President, Gronk Vellumthud, Yoda). |
| **Topic** | The current RSS headline being discussed by the personas. Only one topic is active at a time. |
| **Validation** | The process of checking that model output is clean, complete, plain-text, and within persona limits before displaying or storing it. |
| **Repair** | A single retry of model generation with a stricter instruction when the first attempt fails validation. |
| **Following** | The state where the viewer is at or near the bottom of the transcript and new messages auto-scroll into view. |
| **Jump to latest** | A button that appears when the viewer is scrolled up and new messages arrive; clicking it scrolls to the newest message and resumes auto-following. |
| **Degraded state** | The application continues operating with reduced functionality when RSS, the model API, or the API key is unavailable. |
| **Monotonic ID** | Message IDs that always increase, never decrease or repeat, ensuring stable ordering. |
| **Plain-text contract** | The model prompt instruction to return only the final visible chat message — no JSON, fences, fields, or prefixes. |
| **aiohttp** | The async HTTP library used for the web server, RSS fetching, and LLM API calls. |
