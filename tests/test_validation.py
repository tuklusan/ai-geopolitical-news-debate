"""
Unit tests for model-output validation (validator.py).

Proves that valid plain-text is accepted, fenced JSON is rejected or safely
extracted, incomplete JSON is rejected, raw dictionaries are rejected, code
blocks are rejected, empty responses are rejected, mid-sentence responses are
rejected, overlong responses are rejected, persona word limits are enforced,
and Gronk produces exactly three valid lines.
"""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from personas import PERSONAS
from validator import validate_output, strip_fences, repair_instruction
from tests.fixtures import (
    VALID_POTUS, VALID_EU, VALID_GRONK, VALID_YODA,
    MALFORMED_FENCED_JSON, MALFORMED_INCOMPLETE_JSON, MALFORMED_PYDICT,
    MALFORMED_CODEBLOCK, MALFORMED_EMPTY, MALFORMED_WHITESPACE,
    MALFORMED_MID_SENTENCE, MALFORMED_OVERLONG_POTUS,
    MALFORMED_GRONK_2LINES, MALFORMED_GRONK_LONG_LINE,
    MALFORMED_PREFIXED, MALFORMED_YAML, FENCED_PROSE,
)


class TestValidMessages:
    def test_valid_potus_accepted(self):
        r = validate_output(VALID_POTUS, PERSONAS["potus"])
        assert r.ok, r.reason
        assert r.text == VALID_POTUS

    def test_valid_eu_accepted(self):
        r = validate_output(VALID_EU, PERSONAS["eu"])
        assert r.ok, r.reason

    def test_valid_gronk_three_lines(self):
        r = validate_output(VALID_GRONK, PERSONAS["gronk"])
        assert r.ok, r.reason
        lines = [ln for ln in r.text.split("\n") if ln.strip()]
        assert len(lines) == 3

    def test_valid_yoda_accepted(self):
        r = validate_output(VALID_YODA, PERSONAS["yoda"])
        assert r.ok, r.reason


class TestMalformedRejection:
    def test_fenced_json_rejected(self):
        r = validate_output(MALFORMED_FENCED_JSON, PERSONAS["potus"])
        assert not r.ok

    def test_incomplete_json_rejected(self):
        r = validate_output(MALFORMED_INCOMPLETE_JSON, PERSONAS["potus"])
        assert not r.ok

    def test_raw_dictionary_rejected(self):
        r = validate_output(MALFORMED_PYDICT, PERSONAS["yoda"])
        assert not r.ok

    def test_code_block_rejected(self):
        r = validate_output(MALFORMED_CODEBLOCK, PERSONAS["potus"])
        assert not r.ok

    def test_empty_rejected(self):
        r = validate_output(MALFORMED_EMPTY, PERSONAS["potus"])
        assert not r.ok
        assert "empty" in r.reason.lower()

    def test_whitespace_rejected(self):
        r = validate_output(MALFORMED_WHITESPACE, PERSONAS["potus"])
        assert not r.ok

    def test_mid_sentence_rejected(self):
        r = validate_output(MALFORMED_MID_SENTENCE, PERSONAS["potus"])
        assert not r.ok
        assert "mid-sentence" in r.reason.lower()

    def test_overlong_potus_rejected(self):
        r = validate_output(MALFORMED_OVERLONG_POTUS, PERSONAS["potus"])
        assert not r.ok
        assert "exceeds" in r.reason.lower()

    def test_gronk_wrong_line_count_rejected(self):
        r = validate_output(MALFORMED_GRONK_2LINES, PERSONAS["gronk"])
        assert not r.ok
        assert "3" in r.reason

    def test_gronk_long_line_rejected(self):
        r = validate_output(MALFORMED_GRONK_LONG_LINE, PERSONAS["gronk"])
        assert not r.ok

    def test_persona_prefix_stripped(self):
        r = validate_output(MALFORMED_PREFIXED, PERSONAS["potus"])
        assert r.ok
        assert "POTUS:" not in r.text

    def test_yaml_rejected(self):
        r = validate_output(MALFORMED_YAML, PERSONAS["potus"])
        assert not r.ok

    def test_fenced_prose_extracted_and_accepted(self):
        r = validate_output(FENCED_PROSE, PERSONAS["potus"])
        assert r.ok
        assert "```" not in r.text


class TestPersonaLimits:
    def test_potus_word_limit_enforced(self):
        words = " ".join(["word"] * 46) + "."
        r = validate_output(words, PERSONAS["potus"])
        assert not r.ok

    def test_potus_at_limit_accepted(self):
        words = " ".join(["word"] * 44) + "."
        r = validate_output(words, PERSONAS["potus"])
        assert r.ok

    def test_eu_word_limit_enforced(self):
        words = " ".join(["word"] * 66) + "."
        r = validate_output(words, PERSONAS["eu"])
        assert not r.ok

    def test_yoda_word_limit_enforced(self):
        words = " ".join(["word"] * 41) + "."
        r = validate_output(words, PERSONAS["yoda"])
        assert not r.ok

    def test_gronk_word_limit_per_line(self):
        text = " ".join(["a"] * 13) + ".\nLine two here.\nLine three here."
        r = validate_output(text, PERSONAS["gronk"])
        assert not r.ok


class TestRepairInstruction:
    def test_repair_prose(self):
        msg = repair_instruction(PERSONAS["potus"], "ends mid-sentence")
        assert "plain-text" in msg.lower() or "complete" in msg.lower()

    def test_repair_gronk(self):
        msg = repair_instruction(PERSONAS["gronk"], "expected 3 lines")
        assert "three" in msg.lower()


class TestStripFences:
    def test_strip_backticks(self):
        assert strip_fences("```\nhello\n```") == "hello"

    def test_strip_tildes(self):
        assert strip_fences("~~~\nhello\n~~~") == "hello"

    def test_no_fence_unchanged(self):
        assert strip_fences("hello world.") == "hello world."
