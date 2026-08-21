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
Asynchronous RSS feed client for Live News Debate Wall.

Fetches and parses RSS items using aiohttp. Falls back gracefully when the
feed is unavailable. Never raises to the caller; returns an empty list on
failure so the app continues in a degraded state.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

import aiohttp

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

logger = logging.getLogger("live_news_wall.feed")


@dataclass(frozen=True)
class FeedItem:
    """A single RSS item."""

    title: str
    link: str
    summary: str


# Punctuation that must not be preceded by a space once tags are removed.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%)\]}])")


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace.

    Tags are replaced with a space so that "a<br>b" does not become "ab";
    that would otherwise leave a stray space before punctuation whenever
    the source wraps a word in markup, as in "<b>summary</b>.".
    """
    if not text:
        return ""
    if BeautifulSoup is not None:
        soup = BeautifulSoup(text, "html.parser")
        text = soup.get_text(separator=" ")
    collapsed = " ".join(text.split())
    return _SPACE_BEFORE_PUNCT.sub(r"\1", collapsed)


def _extract_link(item) -> str:
    """Return the first usable link for an RSS item.

    An item may carry several <link> elements: a plain RSS one holding the
    URL as text, and Atom-style ones carrying it in an href attribute with
    no text at all. Taking only the first match can therefore yield an
    empty string and silently drop the whole item.
    """
    for candidate in item.find_all("link"):
        text = (candidate.get_text() or "").strip()
        if text:
            return text
        href = (candidate.get("href") or "").strip()
        if href:
            return href
    return ""


def parse_rss(xml_text: str) -> List[FeedItem]:
    """Parse RSS XML text into a list of :class:`FeedItem`.

    Pure function for testability; does not perform network I/O.
    """
    if BeautifulSoup is not None:
        soup = BeautifulSoup(xml_text, "xml")
        items = soup.find_all("item")
        result = []
        for it in items:
            title_el = it.find("title")
            desc_el = it.find("description")
            title = _strip_html(title_el.get_text() if title_el else "")
            link = _extract_link(it)
            summary = _strip_html(desc_el.get_text() if desc_el else "")
            if title and link:
                result.append(FeedItem(title=title, link=link, summary=summary))
        return result
    # Fallback regex parser (used only if bs4/lxml unavailable).
    items = []
    for m in re.finditer(r"<item>(.*?)</item>", xml_text, re.DOTALL):
        block = m.group(1)
        title_m = re.search(r"<title>(.*?)</title>", block, re.DOTALL)
        link_m = re.search(r"<link>(.*?)</link>", block, re.DOTALL)
        desc_m = re.search(r"<description>(.*?)</description>", block, re.DOTALL)
        if title_m and link_m:
            items.append(
                FeedItem(
                    title=_strip_html(title_m.group(1)),
                    link=link_m.group(1).strip(),
                    summary=_strip_html(desc_m.group(1) if desc_m else ""),
                )
            )
    return items


USER_AGENT = "LiveNewsDebateWall/1.0 (+RSS reader; contact: site owner)"


class FeedClient:
    """Fetches RSS items over HTTP with aiohttp."""

    def __init__(self, feed_url: str, timeout_seconds: float = 30.0):
        self._feed_url = feed_url
        self._timeout = aiohttp.ClientTimeout(
            total=timeout_seconds, connect=min(10.0, timeout_seconds)
        )

    async def fetch_items(self, session: Optional[aiohttp.ClientSession] = None) -> List[FeedItem]:
        """Fetch and parse the feed. Returns [] on any failure."""
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()
        try:
            assert session is not None
            # The timeout is applied per request so it also governs a
            # caller-supplied session created without one; otherwise a slow
            # feed can stall application startup indefinitely.
            async with session.get(
                self._feed_url,
                timeout=self._timeout,
                headers={"User-Agent": USER_AGENT},
            ) as resp:
                if resp.status != 200:
                    logger.warning("RSS feed returned status %s", resp.status)
                    return []
                text = await resp.text()
            return parse_rss(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            logger.warning("RSS fetch failed: %s", exc)
            return []
        finally:
            if own_session and session is not None:
                await session.close()
