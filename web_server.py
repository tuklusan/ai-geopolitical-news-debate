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
from typing import Optional

from aiohttp import web

import personas as persona_mod
from database import Database
from engine import ConversationEngine

logger = logging.getLogger("live_news_wall.web")

# Message page size: the documented default and hard ceiling.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def build_html_page() -> str:
    """Return the full embedded HTML/CSS/JS page string."""
    persona_info = persona_mod.persona_public_info()
    persona_json = json.dumps(persona_info, ensure_ascii=False)
    return HTML_PAGE.replace("__PERSONA_JSON__", persona_json)


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
    --top-h: 44px;
    --bot-h: 40px;
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

  /* Disclaimer bars - permanent top & bottom */
  .disclaimer {
    background: #5a1a1a;
    color: #ffd6d6;
    font-size: 13px;
    font-weight: 600;
    text-align: center;
    padding: 0 12px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
    z-index: 100;
    line-height: 1.3;
  }
  .disclaimer.top { height: var(--top-h); position: static; }
  .disclaimer.bottom { height: var(--bot-h); position: static; }
  .disclaimer strong { text-transform: uppercase; letter-spacing: 0.5px; }

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

  /* Mobile layout */
  @media (max-width: 768px) {
    .main { flex-direction: column; }
    .sidebar {
      width: 100%; max-height: 38vh;
      border-left: none; border-bottom: 1px solid var(--border);
      order: -1; /* persona info above transcript */
    }
    .transcript-wrap { padding: 6px; }
    .disclaimer { font-size: 11px; padding: 0 6px; }
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

<div class="disclaimer top">
  <strong>AI Parody — Fictional Satire.</strong>&nbsp; All personas are fictional AI-generated parodies. Not real statements by real officials.
</div>

<div class="main">
  <div class="transcript-wrap">
    <div class="topic-bar">
      <span class="label">Current Headline: </span><span class="title" id="topic">Loading…</span>
    </div>
    <div class="transcript" id="transcript" role="log" aria-live="polite" aria-label="Live debate transcript"></div>
    <button class="jump-btn" id="jumpBtn" type="button" aria-label="Jump to latest message">Jump to latest ↓</button>
  </div>

  <aside class="sidebar" id="sidebar">
    <h2>Debate Panel</h2>
    <div id="personaCards"></div>
    <div class="health" id="health">Health: …</div>
    <p class="attribution">
      Live News Debate Wall — based on original work by Supratim Sanyal of
      SANYALnet&nbsp;Labs. Licensed for non-commercial use; see the LICENSE
      file. <a href="https://supratim-sanyal.blogspot.com/2026/07/build-live-ai-news-debate-wall-chatdev-linux.html"
      rel="noopener noreferrer" target="_blank">How this was built</a>.
    </p>
  </aside>
</div>

<div class="disclaimer bottom">
  <strong>AI Parody — Fictional Satire.</strong>&nbsp; No persona represents a real person. For entertainment only.
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
var pollIntervalMs = 2000;
/* The wall is designed to stay open for days. Without a cap the transcript
   grows without bound and the tab's memory grows with it. */
var MAX_RENDERED = 500;

/* ---------- Persona sidebar ---------- */
function renderSidebar() {
  var container = document.getElementById('personaCards');
  container.innerHTML = '';
  PERSONAS.forEach(function(p) {
    var card = document.createElement('div');
    card.className = 'persona-card ' + p.key;
    var name = document.createElement('div');
    name.className = 'name';
    name.textContent = p.display_name;
    var role = document.createElement('div');
    role.className = 'role';
    role.textContent = p.role;
    var style = document.createElement('div');
    style.className = 'style';
    style.textContent = p.style;
    card.appendChild(name);
    card.appendChild(role);
    card.appendChild(style);
    container.appendChild(card);
  });
}
/* ---------- Message rendering ---------- */
function renderMessage(msg) {
  var div = document.createElement('div');
  div.className = 'msg ' + msg.speaker;
  div.setAttribute('data-id', msg.id);

  var who = document.createElement('div');
  who.className = 'who';
  var display = msg.speaker;
  for (var i = 0; i < PERSONAS.length; i++) {
    if (PERSONAS[i].key === msg.speaker) { display = PERSONAS[i].display_name; break; }
  }
  who.textContent = display;

  var body = document.createElement('div');
  body.className = 'body';
  body.textContent = msg.text;  // textContent prevents HTML/JSON injection

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
    renderMessage(msg);
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

async function poll() {
  try {
    var resp = await fetch('/api/messages?since=' + lastSeenId);
    if (!resp.ok) return;
    var data = await resp.json();
    if (data.messages) applyMessages(data.messages);
    if (data.topic) updateTopic(data.topic);
    if (data.health) updateHealth(data.health);
  } catch (e) {
    // network blip; try again next interval
  }
}

/* ---------- Init ---------- */
renderSidebar();
// Start following state: scroll to bottom immediately.
isFollowing = true;
scrollToBottom();
poll();
setInterval(poll, pollIntervalMs);
</script>
</body>
</html>
"""


class WebServer:
    """aiohttp application server."""

    def __init__(self, db: Database, engine: ConversationEngine):
        self._db = db
        self._engine = engine
        self._app = web.Application()
        self._runner: Optional[web.AppRunner] = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        self._app.router.add_get("/", self._index)
        self._app.router.add_get("/api/messages", self._messages)
        self._app.router.add_get("/healthz", self._healthz)

    async def _index(self, request: web.Request) -> web.Response:
        return web.Response(text=build_html_page(), content_type="text/html")

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
    def _json(payload: dict, status: int = 200) -> web.Response:
        """Return a JSON response that is never cached by the browser."""
        return web.json_response(
            payload, status=status, headers={"Cache-Control": "no-store"}
        )

    @classmethod
    def _bad_request(cls, detail: str) -> web.Response:
        """Return a small JSON 400. Never leaks internals to the client."""
        return cls._json({"error": "bad_request", "detail": detail}, status=400)

    async def start(self, host: str, port: int) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        logger.info("Server running on http://%s:%s", host, port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
