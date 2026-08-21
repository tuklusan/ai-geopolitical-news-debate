# ============================================================================
# Copyright (c) 2026 Supratim Sanyal of SANYALnet Labs.
# Proprietary rights reserved except as expressly licensed herein.
#
# LIVE NEWS DEBATE WALL
# This file is governed by the SANYALnet Labs Non-Commercial License in the
# root LICENSE file. Non-Commercial use is permitted; Commercial Use and use
# for AI/ML model training are prohibited unless separately authorized.
#
# Attribution is required: "Based on original work by Supratim Sanyal of
# SANYALnet Labs." See LICENSE for full terms, warranty disclaimer, termination,
# patent, trademark, and governing-law provisions.
# ============================================================================

"""
aiohttp web server for Live News Debate Wall.

Serves the embedded responsive HTML/CSS/JS interface and JSON API endpoints.
No Flask, FastAPI, Uvicorn, Nginx, Apache, Node.js, React, Vue, or reverse
proxy required.
"""
from __future__ import annotations

import json
import logging
import time
from html import escape
from typing import Optional

from aiohttp import web

import personas as persona_mod
from database import Database
from engine import ConversationEngine

logger = logging.getLogger("live_news_wall.web")

# Message page size: the documented default and hard ceiling.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

# Concurrent-request ceiling and the TCP accept queue behind it.
DEFAULT_MAX_CLIENTS = 1024
LISTEN_BACKLOG = 128
# Sent with a 503 so a shedding client waits instead of retrying instantly.
RETRY_AFTER_SECONDS = 5

# Characters per second for the typewriter effect. The engine paces itself
# to the same figure so a new turn tends to arrive as the previous one
# finishes typing, rather than piling up behind it.
DEFAULT_TYPING_CPS = 25.0


def render_persona_cards(persona_info) -> str:
    """Render the speaker panel as static HTML.

    Server-rendered rather than built by script, so the panel explaining
    that every speaker is fictional is present for a reader with
    JavaScript disabled, for a text browser, and for a crawler.
    """
    cards = []
    for p in persona_info:
        cards.append(
            '<div class="persona-card {key}">'
            '<div class="name"><span class="avatar" aria-hidden="true">{avatar}</span>{name}</div>'
            '<div class="role">{role}</div>'
            '<div class="style">{style}</div>'
            "</div>".format(
                key=escape(p["key"], quote=True),
                avatar=escape(p["avatar"]),
                name=escape(p["display_name"]),
                role=escape(p["role"]),
                style=escape(p["style"]),
            )
        )
    return "\n      ".join(cards)


def build_html_page(typing_cps: float = DEFAULT_TYPING_CPS) -> str:
    """Return the full embedded HTML/CSS/JS page string."""
    persona_info = persona_mod.persona_public_info()
    persona_json = json.dumps(persona_info, ensure_ascii=False)
    try:
        cps = float(typing_cps)
    except (TypeError, ValueError):
        cps = DEFAULT_TYPING_CPS
    if cps <= 0:
        cps = DEFAULT_TYPING_CPS
    return (
        HTML_PAGE
        .replace("__PERSONA_CARDS__", render_persona_cards(persona_info))
        .replace("__PERSONA_JSON__", persona_json)
        .replace("__TYPING_CPS__", repr(cps))
    )


# ---------------------------------------------------------------------------
# Embedded HTML/CSS/JS
# ---------------------------------------------------------------------------
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Live News Debate Wall — AI Parody</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #0d1117;
    --panel: #161b22;
    --panel2: #1c2330;
    --border: #30363d;
    --text: #e6edf3;
    --muted: #8b949e;
    --accent: #58a6ff;
    --potus: #ff6b6b;
    --eu: #4dabf7;
    --gronk: #69db7c;
    --yoda: #ffd43b;
    --jump: #58a6ff;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; height: 100%;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    overflow: hidden;
  }
  body {
    display: flex; flex-direction: column;
    min-height: 100vh; height: 100vh;
    /* Dynamic viewport units keep the bottom disclaimer on screen when a
       mobile browser's URL bar shows or hides. */
    min-height: 100dvh; height: 100dvh;
  }

  /* Mandatory parody notice. Pinned top and bottom, never scrolled away,
     never collapsed, faded, abbreviated, or truncated. Height is content
     driven so the full text always wraps into view on any width. */
  .disclaimer {
    background: #4a1111;
    color: #ffe9e9;
    border: 2px solid #ff7b72;
    font-size: 12px;
    font-weight: 700;
    text-align: center;
    padding: 7px 12px;
    flex-shrink: 0;
    z-index: 100;
    line-height: 1.35;
    max-height: none;
    overflow: visible;
    opacity: 1;
  }
  .disclaimer.top { border-width: 0 0 2px 0; }
  .disclaimer.bottom { border-width: 2px 0 0 0; }
  .disclaimer strong {
    display: block;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    overflow-wrap: anywhere;
    word-break: normal;
    white-space: normal;
    text-overflow: clip;
  }

  .main {
    flex: 1;
    display: flex;
    min-height: 0; /* allow children to scroll */
    overflow: hidden;
  }

  /* Desktop layout: transcript left, sidebar right */
  .transcript-wrap {
    flex: 1; min-width: 0;
    display: flex; flex-direction: column;
    padding: 8px;
    position: relative;
  }
  .sidebar {
    width: 300px; flex-shrink: 0;
    background: var(--panel);
    border-left: 1px solid var(--border);
    padding: 14px;
    overflow-y: auto;
  }

  /* Transcript scroll container */
  .topic-bar {
    background: var(--panel2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 12px;
    margin-bottom: 8px;
    font-size: 14px;
    flex-shrink: 0;
  }
  .topic-bar .label { color: var(--muted); font-weight: 600; }
  .topic-bar .title { color: var(--accent); }
  /* Subtle "still trying" hint shown only while polling is backing off. */
  .topic-bar .retrying {
    color: #d29922; font-size: 12px; margin-left: 8px; white-space: nowrap;
  }
  .topic-bar .retrying::before { content: "\25CF"; margin-right: 4px; }
  .topic-bar .retrying[hidden] { display: none; }

  .transcript {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    min-height: 0;
    scroll-behavior: smooth;
  }
  .transcript::-webkit-scrollbar { width: 8px; }
  .transcript::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }

  .msg {
    margin: 0 0 14px 0;
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--panel2);
    border-left: 3px solid var(--border);
    word-wrap: break-word; overflow-wrap: break-word;
    white-space: pre-wrap;
  }
  .msg .who {
    font-weight: 700; font-size: 13px;
    margin-bottom: 4px;
  }
  .msg .body { font-size: 15px; line-height: 1.5; }
  /* Caret shown only while a message is being typed out. */
  .msg .body.typing::after {
    content: "8C";
    margin-left: 1px;
    color: var(--muted);
    animation: caret-blink 1s steps(1) infinite;
  }
  @keyframes caret-blink { 50% { opacity: 0; } }
  .msg.potus { border-left-color: var(--potus); }
  .msg.potus .who { color: var(--potus); }
  .msg.eu { border-left-color: var(--eu); }
  .msg.eu .who { color: var(--eu); }
  .msg.gronk { border-left-color: var(--gronk); }
  .msg.gronk .who { color: var(--gronk); }
  .msg.yoda { border-left-color: var(--yoda); }
  .msg.yoda .who { color: var(--yoda); }

  /* Jump to latest control */
  .jump-btn {
    position: absolute;
    bottom: 18px; right: 24px;
    background: var(--jump); color: #fff;
    border: none; border-radius: 20px;
    padding: 8px 18px; font-size: 13px; font-weight: 600;
    cursor: pointer;
    box-shadow: 0 2px 10px rgba(0,0,0,0.5);
    display: none; z-index: 50;
    transition: opacity 0.2s;
  }
  .jump-btn:hover { opacity: 0.9; }

  /* Sidebar persona cards */
  .sidebar h2 { font-size: 16px; margin: 0 0 12px 0; color: var(--text); }
  .persona-card {
    background: var(--panel2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px;
    margin-bottom: 10px;
  }
  .persona-card .name { font-weight: 700; font-size: 14px; }
  .avatar { margin-right: 6px; font-style: normal; }
  .persona-card .role { font-size: 12px; color: var(--muted); margin: 3px 0; }
  .persona-card .style { font-size: 12px; color: var(--text); margin-top: 6px; }
  .persona-card.potus { border-left: 3px solid var(--potus); }
  .persona-card.potus .name { color: var(--potus); }
  .persona-card.eu { border-left: 3px solid var(--eu); }
  .persona-card.eu .name { color: var(--eu); }
  .persona-card.gronk { border-left: 3px solid var(--gronk); }
  .persona-card.gronk .name { color: var(--gronk); }
  .persona-card.yoda { border-left: 3px solid var(--yoda); }
  .persona-card.yoda .name { color: var(--yoda); }

  .health { font-size: 12px; color: var(--muted); margin-top: 12px; }
  .health .ok { color: #3fb950; }
  .health .bad { color: #f85149; }

  /* Attribution required by Section 1(b) of the project licence. */
  .attribution {
    font-size: 11px; color: var(--muted);
    margin: 14px 0 0 0; padding-top: 10px;
    border-top: 1px solid var(--border);
    line-height: 1.4;
  }
  .attribution a { color: var(--accent); }

  /* A viewer who asked for less motion gets the text at once. */
  @media (prefers-reduced-motion: reduce) {
    .msg .body.typing::after { animation: none; content: none; }
    .transcript { scroll-behavior: auto; }
  }

  /* Mobile layout */
  @media (max-width: 768px) {
    .main { flex-direction: column; }
    .sidebar {
      width: 100%; max-height: 38vh;
      border-left: none; border-bottom: 1px solid var(--border);
      order: -1; /* persona info above transcript */
    }
    .transcript-wrap { padding: 6px; }
    .disclaimer { font-size: 11px; padding: 6px 8px; }
    .jump-btn { bottom: 12px; right: 12px; padding: 7px 14px; }
  }

  /* Respect a viewer who has asked for reduced motion. */
  @media (prefers-reduced-motion: reduce) {
    .transcript { scroll-behavior: auto; }
    .jump-btn { transition: none; }
  }
</style>
</head>
<body>

<div class="disclaimer top" role="note" aria-label="AI-generated parody warning">
  <strong>MANDATORY AI PARODY NOTICE: EVERY MESSAGE ON THIS PAGE IS GENERATED BY ARTIFICIAL INTELLIGENCE FOR FICTIONAL PARODY AND SOFTWARE DEMONSTRATION. NO REAL PERSON PARTICIPATED IN THIS CONVERSATION. NOTHING SHOWN HERE IS A REAL STATEMENT, QUOTATION, VIEW, ENDORSEMENT, POLICY, PROMISE, OR OFFICIAL POSITION OF ANY PERSON, GOVERNMENT, INSTITUTION, POLITICAL OFFICE, CREATOR, OR RIGHTS HOLDER.</strong>
</div>

<div class="main">
  <div class="transcript-wrap">
    <div class="topic-bar">
      <span class="label">Current Headline: </span><span class="title" id="topic">Loading…</span><span
        class="retrying" id="retryIndicator" role="status" aria-live="polite" hidden>Reconnecting…</span>
    </div>
    <div class="transcript" id="transcript" role="log" aria-live="polite" aria-label="Live debate transcript"></div>
    <button class="jump-btn" id="jumpBtn" type="button" aria-label="Jump to latest message">Jump to latest ↓</button>
  </div>

  <aside class="sidebar" id="sidebar">
    <h2>Debate Panel</h2>
    <div id="personaCards">
      __PERSONA_CARDS__
    </div>
    <div class="health" id="health">Health: …</div>
    <p class="attribution">
      Live News Debate Wall — based on original work by Supratim Sanyal of
      SANYALnet&nbsp;Labs. Licensed for non-commercial use; see the LICENSE
      file. <a href="https://supratim-sanyal.blogspot.com/2026/07/build-live-ai-news-debate-wall-chatdev-linux.html"
      rel="noopener noreferrer" target="_blank">How this was built</a>.
    </p>
  </aside>
</div>

<div class="disclaimer bottom" role="note" aria-label="AI-generated parody warning">
  <strong>MANDATORY AI PARODY NOTICE: EVERY MESSAGE ON THIS PAGE IS GENERATED BY ARTIFICIAL INTELLIGENCE FOR FICTIONAL PARODY AND SOFTWARE DEMONSTRATION. NO REAL PERSON PARTICIPATED IN THIS CONVERSATION. NOTHING SHOWN HERE IS A REAL STATEMENT, QUOTATION, VIEW, ENDORSEMENT, POLICY, PROMISE, OR OFFICIAL POSITION OF ANY PERSON, GOVERNMENT, INSTITUTION, POLITICAL OFFICE, CREATOR, OR RIGHTS HOLDER.</strong>
</div>

<script>
"use strict";
/* ---------- Persona data injected by server ---------- */
var PERSONAS = __PERSONA_JSON__;

/* ---------- State ---------- */
var transcript = document.getElementById('transcript');
var jumpBtn = document.getElementById('jumpBtn');
var topicEl = document.getElementById('topic');
var healthEl = document.getElementById('health');
var lastSeenId = 0;
var renderedIds = {};
var isFollowing = true;       // viewer is at/near bottom
var unseenCount = 0;          // messages added while not following
var basePollMs = 2000;        // normal cadence
var MAX_BACKOFF_MS = 30000;   // ceiling while the server is unreachable
var currentDelayMs = basePollMs;
var pollTimer = null;
var retryEl = document.getElementById('retryIndicator');

/* ---------- Typewriter ---------- */
var TYPING_CPS = __TYPING_CPS__;   // characters per second, from the server
var TYPE_TICK_MS = 40;             // one timer per message, not per character
var typeQueue = [];
var typingActive = false;
var firstPaintDone = false;        // the backlog on load appears at once
var reduceMotion = !!(window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches);
/* The wall is designed to stay open for days. Without a cap the transcript
   grows without bound and the tab's memory grows with it. */
var MAX_RENDERED = 500;

/* The speaker panel is server-rendered and static; script only needs the
   persona data to label incoming messages. */
/* ---------- Message rendering ---------- */
function renderMessage(msg, animate) {
  var div = document.createElement('div');
  div.className = 'msg ' + msg.speaker;
  div.setAttribute('data-id', msg.id);

  var who = document.createElement('div');
  who.className = 'who';
  var display = msg.speaker;
  var avatar = '';
  for (var i = 0; i < PERSONAS.length; i++) {
    if (PERSONAS[i].key === msg.speaker) {
      display = PERSONAS[i].display_name;
      avatar = PERSONAS[i].avatar || '';
      break;
    }
  }
  if (avatar) {
    var badge = document.createElement('span');
    badge.className = 'avatar';
    badge.setAttribute('aria-hidden', 'true');
    badge.textContent = avatar;
    who.appendChild(badge);
  }
  who.appendChild(document.createTextNode(display));

  var body = document.createElement('div');
  body.className = 'body';
  body.textContent = msg.text;  // textContent prevents HTML/JSON injection
  if (animate) queueTyping(body, msg.text);

  div.appendChild(who);
  div.appendChild(body);
  transcript.appendChild(div);
  return div;
}

function trimTranscript() {
  // Never remove content above a viewer who is reading older messages:
  // dropping nodes off the top shifts scrollTop and jerks their position.
  // The cap is reapplied as soon as they return to the bottom.
  if (!isFollowing) return;
  while (transcript.children.length > MAX_RENDERED) {
    var oldest = transcript.firstElementChild;
    if (!oldest) break;
    var oldId = oldest.getAttribute('data-id');
    if (oldId !== null) delete renderedIds[oldId];
    transcript.removeChild(oldest);
  }
}

/* Type one message out, then start the next. Messages are queued rather
   than typed in parallel so two speakers never appear to talk at once. */
function queueTyping(bodyEl, text) {
  bodyEl.textContent = '';
  bodyEl.className = 'body typing';
  typeQueue.push({ el: bodyEl, text: text, i: 0 });
  pumpTypeQueue();
}

function finishTyping(job, timer) {
  if (timer) clearInterval(timer);
  job.el.textContent = job.text;
  job.el.className = 'body';
  typingActive = false;
  pumpTypeQueue();
}

function pumpTypeQueue() {
  if (typingActive) return;
  var job = typeQueue.shift();
  if (!job) return;
  typingActive = true;
  var started = Date.now();
  var timer = setInterval(function () {
    // How many characters should be visible is derived from elapsed time,
    // not from how many times this timer has fired. A throttled tab (a
    // backgrounded phone, battery saver) then catches up in larger steps
    // instead of typing for minutes and drifting out of step with the
    // server, which paces new turns to the same characters-per-second.
    var due = Math.ceil((Date.now() - started) / 1000 * TYPING_CPS);
    job.i = Math.min(job.text.length, Math.max(job.i, due));
    // textContent throughout: the growing message is never parsed as HTML.
    job.el.textContent = job.text.slice(0, job.i);
    if (isFollowing) scrollToBottom();
    if (job.i >= job.text.length) finishTyping(job, timer);
  }, TYPE_TICK_MS);
}

/* ---------- Scroll management ---------- */
function isNearBottom() {
  // Threshold: within 80px of the bottom of the transcript container.
  return (transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight) < 80;
}

function scrollToBottom() {
  transcript.scrollTop = transcript.scrollHeight;
}

function checkScroll() {
  if (isNearBottom()) {
    isFollowing = true;
    unseenCount = 0;
    jumpBtn.style.display = 'none';
  } else {
    isFollowing = false;
  }
}

transcript.addEventListener('scroll', checkScroll);

jumpBtn.addEventListener('click', function() {
  isFollowing = true;
  unseenCount = 0;
  jumpBtn.style.display = 'none';
  scrollToBottom();
});

/* ---------- Polling & update ---------- */
function applyMessages(messages) {
  var newCount = 0;
  messages.forEach(function(msg) {
    if (renderedIds[msg.id]) return;
    renderedIds[msg.id] = true;
    renderMessage(msg, firstPaintDone && !reduceMotion);
    lastSeenId = Math.max(lastSeenId, msg.id);
    newCount++;
  });
  if (newCount === 0) return;
  trimTranscript();

  if (isFollowing) {
    // Already at/near bottom: scroll the transcript container to newest.
    scrollToBottom();
    unseenCount = 0;
    jumpBtn.style.display = 'none';
  } else {
    // Viewer scrolled up: do not force scroll; show jump control.
    unseenCount += newCount;
    jumpBtn.style.display = 'block';
    if (unseenCount > 1) {
      jumpBtn.textContent = 'Jump to latest (' + unseenCount + ' new) ↓';
    } else {
      jumpBtn.textContent = 'Jump to latest ↓';
    }
  }
  // Ensure the newest message is fully visible (not hidden behind bottom bar).
  // The bottom disclaimer is static (outside transcript), so transcript
  // clientHeight already accounts for it via flexbox. No extra padding needed.
}

function updateTopic(topic) {
  if (topic) {
    topicEl.textContent = topic;
  }
}

function updateHealth(health) {
  var parts = [];
  parts.push('RSS: ' + (health.rss_healthy ? '<span class="ok">ok</span>' : '<span class="bad">degraded</span>'));
  if (health.model_disabled) {
    parts.push('Model: <span class="bad">unavailable (no API key)</span>');
  } else {
    parts.push('Model: ' + (health.model_healthy ? '<span class="ok">ok</span>' : '<span class="bad">degraded</span>'));
  }
  healthEl.innerHTML = 'Health — ' + parts.join(' | ');
}

/* Exponential backoff so a restarting server is not hammered, and the
   viewer is told the wall is still trying rather than silently frozen. */
function backoffDelay(currentMs) {
  return Math.min(currentMs * 2, MAX_BACKOFF_MS);
}

/* Honour a server-supplied Retry-After (seconds, or an HTTP date). */
function retryAfterMs(resp) {
  var header = resp.headers ? resp.headers.get('Retry-After') : null;
  if (!header) return 0;
  var seconds = parseInt(header, 10);
  if (!isNaN(seconds) && String(seconds) === header.trim()) {
    return Math.min(Math.max(seconds, 1) * 1000, MAX_BACKOFF_MS);
  }
  var when = Date.parse(header);
  if (isNaN(when)) return 0;
  return Math.min(Math.max(when - Date.now(), 1000), MAX_BACKOFF_MS);
}

function showRetrying(on) {
  if (!retryEl) return;
  if (on) { retryEl.removeAttribute('hidden'); }
  else { retryEl.setAttribute('hidden', ''); }
}

async function poll() {
  var nextDelay;
  try {
    var resp = await fetch('/api/messages?since=' + lastSeenId);
    if (!resp.ok) {
      // 503 from the client-capacity limiter carries Retry-After.
      nextDelay = retryAfterMs(resp) || backoffDelay(currentDelayMs);
      currentDelayMs = nextDelay;
      showRetrying(true);
      return;
    }
    var data = await resp.json();
    if (data.messages) applyMessages(data.messages);
    if (data.topic) updateTopic(data.topic);
    if (data.health) updateHealth(data.health);
    // Everything present on the first successful poll is history: show it
    // immediately. Only later arrivals are typed out.
    firstPaintDone = true;
    // Recovered: resume the normal cadence.
    currentDelayMs = basePollMs;
    showRetrying(false);
  } catch (e) {
    currentDelayMs = backoffDelay(currentDelayMs);
    showRetrying(true);
  } finally {
    // Self-scheduling: a fixed setInterval would stack overlapping
    // requests against a server that has stopped responding.
    pollTimer = setTimeout(poll, currentDelayMs);
  }
}

/* ---------- Init ---------- */
// Start following state: scroll to bottom immediately.
isFollowing = true;
scrollToBottom();
poll();
</script>
</body>
</html>
"""


class WebServer:
    """aiohttp application server."""

    def __init__(
        self,
        db: Database,
        engine: ConversationEngine,
        max_clients: int = DEFAULT_MAX_CLIENTS,
        backlog: int = LISTEN_BACKLOG,
        typing_cps: float = DEFAULT_TYPING_CPS,
    ):
        self._db = db
        self._engine = engine
        self._max_clients = max(1, int(max_clients))
        self._typing_cps = typing_cps
        self._backlog = max(1, int(backlog))
        # Requests currently being served. Counted rather than queued: an
        # unbounded wait queue would defer the overload instead of shedding
        # it, and the client polls again a moment later anyway.
        self._active = 0
        self._app = web.Application(middlewares=[self._capacity_middleware])
        self._runner: Optional[web.AppRunner] = None
        self._setup_routes()

    @property
    def active_requests(self) -> int:
        return self._active

    @web.middleware
    async def _capacity_middleware(self, request: web.Request, handler):
        """Shed load past ``max_clients`` concurrent requests."""
        if self._active >= self._max_clients:
            logger.warning(
                "At client capacity (%d); shedding %s %s",
                self._max_clients,
                request.method,
                request.path,
            )
            return self._json(
                {"error": "busy", "detail": "server at client capacity"},
                status=503,
                extra_headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
            )
        # No await between the check and the increment, so the count cannot
        # be overshot by another task slipping in.
        self._active += 1
        try:
            return await handler(request)
        finally:
            self._active -= 1

    def _setup_routes(self) -> None:
        self._app.router.add_get("/", self._index)
        self._app.router.add_get("/api/messages", self._messages)
        self._app.router.add_get("/healthz", self._healthz)

    async def _index(self, request: web.Request) -> web.Response:
        return web.Response(
            text=build_html_page(self._typing_cps), content_type="text/html"
        )

    async def _messages(self, request: web.Request) -> web.Response:
        raw_since = request.query.get("since", "0")
        try:
            since = int(raw_since)
        except (ValueError, TypeError):
            return self._bad_request("since must be an integer")
        if since < 0:
            return self._bad_request("since must not be negative")

        raw_limit = request.query.get("limit", str(DEFAULT_LIMIT))
        try:
            limit = int(raw_limit)
        except (ValueError, TypeError):
            return self._bad_request("limit must be an integer")
        if limit < 1:
            return self._bad_request("limit must be at least 1")
        limit = min(limit, MAX_LIMIT)

        if since == 0:
            # A fresh client must land on the newest conversation, not the
            # oldest page of a long transcript.
            stored = await self._db.get_latest_messages(limit)
            truncated = await self._db.count_messages() > len(stored)
        else:
            # Fetch one extra row to detect truncation without a second query.
            stored = await self._db.get_messages(since_id=since, limit=limit + 1)
            truncated = len(stored) > limit
            if truncated:
                stored = stored[:limit]
        topic = await self._db.get_active_topic()
        messages = [m.to_dict() for m in stored]
        payload = {
            "messages": messages,
            "latest_id": messages[-1]["id"] if messages else since,
            "truncated": truncated,
            "topic": topic.title if topic else None,
            "topic_id": topic.id if topic else None,
            "topic_link": topic.link if topic else None,
            "health": self._engine.health(),
            "server_time": time.time(),
        }
        return self._json(payload)

    async def _healthz(self, request: web.Request) -> web.Response:
        h = self._engine.health()
        ok = h["rss_healthy"] and (h["model_healthy"] or h["model_disabled"])
        status = 200 if ok else 503
        return self._json(h, status=status)

    @staticmethod
    def _json(payload: dict, status: int = 200, extra_headers: Optional[dict] = None) -> web.Response:
        """Return a JSON response that is never cached by the browser."""
        headers = {"Cache-Control": "no-store"}
        if extra_headers:
            headers.update(extra_headers)
        return web.json_response(payload, status=status, headers=headers)

    @classmethod
    def _bad_request(cls, detail: str) -> web.Response:
        """Return a small JSON 400. Never leaks internals to the client."""
        return cls._json({"error": "bad_request", "detail": detail}, status=400)

    async def start(self, host: str, port: int) -> None:
        # access_log=None: every browser polls every few seconds, and
        # logging each request buries the interesting lines and grows the
        # log file by gigabytes a year. Failures are still logged by the
        # handlers themselves.
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port, backlog=self._backlog)
        await site.start()
        logger.info(
            "Server running on http://%s:%s (max_clients=%d, backlog=%d)",
            host, port, self._max_clients, self._backlog,
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
