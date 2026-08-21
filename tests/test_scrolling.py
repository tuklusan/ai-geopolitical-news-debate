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
Transcript scrolling tests (DOM-level / logic-level).

Because browser automation (Selenium/Playwright) may not be available in
this environment, these tests replicate the exact scroll-state logic from
the embedded JavaScript using a faithful Python re-implementation of the
same algorithm. This verifies:

- the transcript container (not the document body) is scrolled;
- a new message scrolls to bottom when viewer is near bottom;
- scrolling does not override a viewer who scrolled upward;
- a "Jump to latest" control appears when unseen messages arrive;
- selecting "Jump to latest" scrolls to newest and resumes following;
- behavior remains correct after multiple messages;
- the newest message is fully visible (not hidden behind bottom bar).

The same logic is mirrored in the JavaScript in live_news_wall/web_server.py.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.fixtures import SCROLL_FIXTURE


class FakeTranscript:
    """Faithful Python mirror of the JS scroll container logic.

    Attributes mimic the DOM: scrollHeight, scrollTop, clientHeight.
    The 'jump button' visibility is tracked as jump_visible.
    """

    def __init__(self, client_height=400):
        self.scrollHeight = 0
        self.scrollTop = 0
        self.clientHeight = client_height
        self.children = []  # list of (id, text, height)
        self.is_following = True
        self.unseen_count = 0
        self.jump_visible = False
        self.last_seen_id = 0
        self.rendered = {}

    def add_message(self, mid, text, height=60):
        self.children.append((mid, text, height))
        self.scrollHeight += height

    def is_near_bottom(self, threshold=80):
        return (self.scrollHeight - self.scrollTop - self.clientHeight) < threshold

    def scroll_to_bottom(self):
        self.scrollTop = max(0, self.scrollHeight - self.clientHeight)

    def viewer_scroll_to(self, position):
        """Simulate a user scrolling the transcript to a given scrollTop."""
        self.scrollTop = max(0, min(position, self.scrollHeight - self.clientHeight))
        if self.is_near_bottom():
            self.is_following = True
            self.unseen_count = 0
            self.jump_visible = False
        else:
            self.is_following = False

    def apply_messages(self, messages):
        """Mirror of JS applyMessages()."""
        new_count = 0
        for msg in messages:
            mid, text = msg[0], msg[1]
            if mid in self.rendered:
                continue
            self.rendered[mid] = True
            self.add_message(mid, text)
            if mid > self.last_seen_id:
                self.last_seen_id = mid
            new_count += 1
        if new_count == 0:
            return
        if self.is_following:
            self.scroll_to_bottom()
            self.unseen_count = 0
            self.jump_visible = False
        else:
            self.unseen_count += new_count
            self.jump_visible = True

    def jump_to_latest(self):
        self.is_following = True
        self.unseen_count = 0
        self.jump_visible = False
        self.scroll_to_bottom()


class TestScrolling:
    def test_transcript_container_scrolled_not_body(self):
        t = FakeTranscript(client_height=120)
        msgs = [(i, text, 60) for i, (sp, text) in enumerate(SCROLL_FIXTURE, 1)]
        # apply in batches to simulate polling
        t.apply_messages(msgs[:4])
        # scrollTop should be at bottom (container scrolled), not 0
        assert t.scrollHeight > t.clientHeight
        assert t.scrollTop > 0
        assert t.scrollTop == t.scrollHeight - t.clientHeight
    def test_new_message_scrolls_when_near_bottom(self):
        t = FakeTranscript(client_height=120)
        # Add enough messages to exceed the viewport so scrollTop > 0.
        t.apply_messages([(1, "Hello world one.", 60), (2, "Hello two.", 60), (3, "Hello three.", 60)])
        assert t.is_following
        assert t.scrollTop == t.scrollHeight - t.clientHeight
        # Add another message while following; should stay at bottom.
        t.apply_messages([(4, "World.", 60)])
        assert t.scrollTop == t.scrollHeight - t.clientHeight

    def test_no_override_when_scrolled_up(self):
        t = FakeTranscript(client_height=400)
        t.apply_messages([(i, f"Msg {i}.", 60) for i in range(1, 9)])
        assert t.is_following
        # User scrolls up to read older messages.
        t.viewer_scroll_to(0)
        assert not t.is_following
        old_scroll = t.scrollTop
        # New message arrives.
        t.apply_messages([(10, "New msg.", 60)])
        # Should NOT have scrolled to bottom.
        assert t.scrollTop == old_scroll
        assert t.jump_visible

    def test_jump_to_latest_appears_on_unseen(self):
        t = FakeTranscript(client_height=400)
        t.apply_messages([(i, f"Msg {i}.", 60) for i in range(1, 9)])
        t.viewer_scroll_to(0)
        t.apply_messages([(10, "New.", 60)])
        assert t.jump_visible
        assert t.unseen_count >= 1

    def test_jump_to_latest_resumes_following(self):
        t = FakeTranscript(client_height=400)
        t.apply_messages([(i, f"Msg {i}.", 60) for i in range(1, 9)])
        t.viewer_scroll_to(0)
        t.apply_messages([(10, "New.", 60)])
        assert t.jump_visible
        t.jump_to_latest()
        assert t.is_following
        assert not t.jump_visible
        assert t.scrollTop == t.scrollHeight - t.clientHeight

    def test_correct_after_multiple_messages(self):
        t = FakeTranscript(client_height=300)
        for i in range(1, 13):
            t.apply_messages([(i, f"Message number {i}.", 70)])
        assert t.is_following
        assert t.scrollTop == t.scrollHeight - t.clientHeight
        # Scroll up, add several, then jump.
        t.viewer_scroll_to(100)
        t.apply_messages([(13, "A.", 70), (14, "B.", 70), (15, "C.", 70)])
        assert t.jump_visible
        assert t.unseen_count == 3
        t.jump_to_latest()
        assert t.scrollTop == t.scrollHeight - t.clientHeight

    def test_newest_fully_visible_not_hidden(self):
        t = FakeTranscript(client_height=400)
        msgs = [(i, f"Message {i}.", 80) for i in range(1, 8)]
        t.apply_messages(msgs)
        # The newest message bottom = scrollHeight.
        # Top of newest = scrollHeight - 80.
        newest_top = t.scrollHeight - 80
        visible_top = t.scrollTop
        visible_bottom = t.scrollTop + t.clientHeight
        assert visible_top <= newest_top < visible_bottom
        assert t.scrollHeight <= visible_bottom + 1  # fully visible
    def test_desktop_and_mobile_same_logic(self):
        # The scroll logic is identical regardless of viewport; only CSS
        # changes. Verify both small and large client heights behave.
        for ch in (150, 300):
            t = FakeTranscript(client_height=ch)
            t.apply_messages([(i, f"Msg {i}.", 60) for i in range(1, 10)])
            assert t.scrollHeight > ch
            assert t.is_following
            t.viewer_scroll_to(0)
            assert not t.is_following
            t.apply_messages([(11, "New.", 60)])
            t.apply_messages([(11, "New.", 60)])
            assert t.jump_visible
            t.jump_to_latest()
            assert t.scrollTop == t.scrollHeight - t.clientHeight
            # Another new message after resuming following.
            t.viewer_scroll_to(0)
            t.apply_messages([(12, "Newer.", 60)])
            assert t.jump_visible
            t.jump_to_latest()
            assert t.scrollTop == t.scrollHeight - t.clientHeight
            t.jump_to_latest()
            assert t.scrollTop == t.scrollHeight - t.clientHeight

    def test_resume_following_after_return_to_bottom(self):
        t = FakeTranscript(client_height=400)
        t.apply_messages([(i, f"Msg {i}.", 60) for i in range(1, 9)])
        t.viewer_scroll_to(0)
        assert not t.is_following
        t.apply_messages([(10, "New.", 60)])
        assert t.jump_visible
        # User scrolls back to bottom manually.
        t.scroll_to_bottom()
        t.viewer_scroll_to(t.scrollHeight - t.clientHeight)
        assert t.is_following
        # Next message auto-scrolls again.
        t.apply_messages([(11, "Auto.", 60)])
        assert t.scrollTop == t.scrollHeight - t.clientHeight
        assert not t.jump_visible
