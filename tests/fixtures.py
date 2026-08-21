"""
Test fixtures: realistic transcript messages and malformed model responses.

These fixtures are used by validation, engine, and scrolling tests. They
include representative examples for every persona, rapid arrival, malformed
fenced-JSON, incomplete responses, and enough messages to require scrolling.
"""
from __future__ import annotations

VALID_POTUS = "Jobs are back, factories are humming, and the economy roars. We win on growth."
VALID_POTUS_2 = "Markets up, wages up, America first. The deal is done."
VALID_EU = (
    "We welcome coordinated fiscal signals while safeguarding the single market. "
    "Stability, sustainability, and fair competition guide our response."
)
VALID_EU_2 = (
    "Our policy framework balances innovation with consumer protection across all "
    "member states. We will consult Parliament before acting."
)
VALID_GRONK = (
    "Bureaucratic forms filed late,\n"
    "Penalty assessed in triplicate,\n"
    "No appeals will be accepted."
)
VALID_GRONK_2 = (
    "Budget deficit grows,\n"
    "Committee meets to discuss,\n"
    "Nothing is decided here."
)
VALID_YODA = "Grow, the economy must. Patience, young traders, you must have."
VALID_YODA_2 = "Strong the currency is, but stronger still, patience must be."

# Deliberately malformed fenced-JSON model response.
MALFORMED_FENCED_JSON = '```json\n{"text": "Markets are strong.", "speaker": "potus"}\n```'

# Incomplete JSON (no closing brace).
MALFORMED_INCOMPLETE_JSON = '{"text": "The economy is'

# Raw Python dictionary string.
MALFORMED_PYDICT = "{'speaker': 'yoda', 'text': 'Patience you must have.'}"

# Markdown code block (non-JSON).
MALFORMED_CODEBLOCK = "```\nprint('hello world')\n```"

# Empty response.
MALFORMED_EMPTY = ""

# Whitespace-only response.
MALFORMED_WHITESPACE = "   \n  \n  "

# Mid-sentence (no terminal punctuation).
MALFORMED_MID_SENTENCE = "The markets are responding well today and we expect"

# Overlong POTUS (exceeds 45 words).
MALFORMED_OVERLONG_POTUS = (
    "The economy is doing tremendously well and we have the best numbers in the history "
    "of our great country and frankly nobody has ever seen growth like this before and "
    "we are going to keep winning and winning and the jobs are coming back and the factories "
    "are reopening and the trade deals are the best ever signed by anyone period."
)

# Gronk with wrong line count (2 lines).
MALFORMED_GRONK_2LINES = "Forms are filed.\nNothing happens."

# Gronk with a line exceeding 12 words.
MALFORMED_GRONK_LONG_LINE = (
    "The bureaucratic oversight committee has reviewed your paperwork and found it lacking in every way,\n"
    "Penalty assessed,\n"
    "No appeals."
)

# Persona-name prefixed.
MALFORMED_PREFIXED = "POTUS: The economy is strong and we are winning."

# YAML-like.
MALFORMED_YAML = "speaker: potus\ntext: The economy is strong.\nanalysis: positive"

# Fenced with prose inside (should be accepted after stripping).
FENCED_PROSE = "```\nThe economy is strong and we are winning.\n```"

# Enough messages to require scrolling (12 messages).
SCROLL_FIXTURE = [
    ("potus", "Markets rally on strong jobs data. We win."),
    ("eu", "We note the data while monitoring wage trends across the single market."),
    ("gronk", "Data arrives late,\nAnalysts squint at numbers,\nConclusion deferred."),
    ("yoda", "Strong the numbers are. Hmm. Read them carefully, you must."),
    ("potus", "Best economy ever. No debate."),
    ("eu", "Our framework supports sustainable growth with fair competition."),
    ("gronk", "Growth reported now,\nCommittee schedules meeting,\nDecision postponed."),
    ("yoda", "Win, you say? Define winning, you must."),
    ("potus", "Winning means jobs and growth. Simple."),
    ("eu", "We define progress through shared indicators and cohesion."),
    ("gronk", "Indicators compiled,\nReport sent for review now,\nNo one will read it."),
    ("yoda", "Read it or not, the truth, it remains."),
]
