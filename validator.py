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
Defensive model-output validation for Live News Debate Wall.

Validates plain-text model output before displaying or storing it. Rejects
fenced JSON, incomplete JSON, raw dictionaries, Markdown code blocks, empty
responses, mid-sentence responses, and overlong responses. Provides a repair
instruction generator. Never displays raw model output as a fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from personas import Persona


# Characters that may legitimately end a complete sentence/poem line.
_TERMINATORS = ".!?\"')\u201d\u2019"
# Common opening/closing fence patterns.
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})[^\n]*\n(.*?)\n?\s*\1\s*$", re.DOTALL)
# Heuristic JSON/YAML/XML/dict structural indicators.
_JSON_RE = re.compile(r"^\s*[\{\[].*[\}\]]\s*$", re.DOTALL)
_YAML_KEY_RE = re.compile(
    r"^\s*[A-Za-z_][\w\- ]*\s*:\s*.+(\n\s*[A-Za-z_][\w\- ]*\s*:\s*.+)*\s*$"
)
_XML_RE = re.compile(r"^\s*<\?xml|<\w+[\s>].*</\w+>\s*$", re.DOTALL | re.IGNORECASE)
_PYDICT_RE = re.compile(r"^\s*\{['\"].*['\"]\s*:\s*.*\}\s*$", re.DOTALL)
_FIELD_RE = re.compile(
    r'"(text|new_point|speaker|analysis|reasoning|message|content|response)"\s*:',
    re.IGNORECASE,
)
_CODE_RE = re.compile(
    r"\b(print|def|import|console\.log|System\.out|echo)\s*\(|"
    r"^\s*(def|class|import|from|package|public|private|void)\s+",
    re.MULTILINE,
)


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating one model output."""

    ok: bool
    text: str
    reason: str = ""

    @classmethod
    def valid(cls, text: str) -> "ValidationResult":
        return cls(True, text, "")

    @classmethod
    def invalid(cls, reason: str, text: str = "") -> "ValidationResult":
        return cls(False, text, reason)


def strip_fences(text: str) -> str:
    """Strip a single surrounding Markdown fence if present.

    Returns the inner content. If the text is not fenced, returns it trimmed.
    """
    if not text:
        return text
    m = _FENCE_RE.match(text)
    if m:
        return m.group(2).strip()
    return text.strip()


def _looks_structured(text: str) -> Optional[str]:
    """Return a reason string if the text looks like structured data."""
    if not text:
        return None
    if _FIELD_RE.search(text):
        return "contains structured field name"
    if _CODE_RE.search(text):
        return "resembles code"
    if _PYDICT_RE.match(text):
        return "resembles a Python dictionary"
    if _JSON_RE.match(text):
        stripped = text.strip()
        if stripped[0] in "{[" and stripped[-1] in "}]":
            return "resembles JSON"
    if _XML_RE.match(text):
        return "resembles XML"
    if _YAML_KEY_RE.match(text) and ":" in text and "\n" in text:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) >= 2 and all(":" in ln for ln in lines[:3]):
            # tuple(), not the bare string: str.endswith with a string tests
            # for that whole sequence, so this guard never fired and prose
            # laid out as "Topic: point." lines was rejected as YAML.
            if not any(ln.strip().endswith(tuple(_TERMINATORS)) for ln in lines[:3]):
                return "resembles YAML"
    return None


def _ends_mid_sentence(text: str) -> bool:
    """Return True if the text ends without a clear terminal punctuation.

    Gronk poetry is allowed to end per-line; for prose personas we require
    the final character to be a sentence terminator.
    """
    if not text:
        return True
    stripped = text.rstrip()
    if not stripped:
        return True
    last = stripped[-1]
    return last not in _TERMINATORS


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w])


def _gronk_valid(text: str) -> Tuple[bool, str]:
    """Validate exactly three short poetry lines for Gronk."""
    raw_lines = text.split("\n")
    lines = [ln.strip() for ln in raw_lines if ln.strip()]
    if len(lines) != 3:
        return False, f"expected exactly 3 lines, got {len(lines)}"
    for i, ln in enumerate(lines, 1):
        wc = _word_count(ln)
        if wc > 12:
            return False, f"line {i} exceeds 12 words ({wc})"
        if wc == 0:
            return False, f"line {i} is empty"
    total_words = sum(_word_count(ln) for ln in lines)
    if total_words > 36:
        return False, f"total {total_words} words exceeds 36"
    return True, ""


def validate_output(text: str, persona: Persona) -> ValidationResult:
    """Validate raw model output for a persona.

    Returns a :class:`ValidationResult`. On success ``text`` holds the
    clean, displayable message. On failure ``reason`` explains why.
    """
    if text is None:
        return ValidationResult.invalid("empty output (None)")
    cleaned = strip_fences(text)
    if not cleaned.strip():
        return ValidationResult.invalid("empty output", cleaned)

    struct = _looks_structured(cleaned)
    if struct:
        return ValidationResult.invalid(struct, cleaned)

    # Reject persona-name prefix.
    first_line = cleaned.splitlines()[0].strip()
    if first_line.lower().startswith(persona.display_name.lower() + ":"):
        cleaned = cleaned.split(":", 1)[1].strip()
        if not cleaned:
            return ValidationResult.invalid("only a persona prefix", cleaned)
        struct = _looks_structured(cleaned)
        if struct:
            return ValidationResult.invalid(struct, cleaned)

    if persona.is_gronk:
        ok, reason = _gronk_valid(cleaned)
        if not ok:
            return ValidationResult.invalid(reason, cleaned)
        return ValidationResult.valid(cleaned)

    # Prose personas.
    wc = _word_count(cleaned)
    if wc > persona.max_words:
        return ValidationResult.invalid(
            f"{wc} words exceeds limit of {persona.max_words}", cleaned
        )
    if _ends_mid_sentence(cleaned):
        return ValidationResult.invalid("ends mid-sentence", cleaned)
    if "```" in cleaned or "~~~" in cleaned:
        return ValidationResult.invalid("contains code fence", cleaned)
    return ValidationResult.valid(cleaned)


def repair_instruction(persona: Persona, failed_reason: str) -> str:
    """Return a stricter repair instruction for a failed generation.

    The rejection reason is quoted back: a model told only that it failed
    is far more likely to repeat the same mistake on its single retry.
    """
    because = f" (reason: {failed_reason})" if failed_reason else ""
    if persona.is_gronk:
        return (
            f"Your previous response was rejected{because}. Reply again with "
            "EXACTLY three short lines of Vogon bureaucratic poetry, maximum "
            "12 words per line, no headings, no labels, no JSON, no code "
            "fences, no persona name. Output only the three poetry lines."
        )
    return (
        f"Your previous response was rejected{because}. Reply again with a "
        f"single short complete plain-text sentence or two (maximum "
        f"{persona.max_words} words) that ends with proper terminal "
        "punctuation. No JSON, no YAML, no XML, no dictionaries, no code "
        "fences, no field names, no persona name prefix, no analysis, no "
        "metadata. Output only the spoken words."
    )
