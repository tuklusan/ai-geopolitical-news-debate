"""
OpenAI-compatible LLM client for Live News Debate Wall.

Uses the chat completions endpoint with an OpenAI-compatible provider.
Returns plain-text model output only. The API key is passed via header and
is never stored, displayed, or logged.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

import aiohttp

logger = logging.getLogger("live_news_wall.llm")

# Prior turns included as conversational context.
MAX_CONTEXT_LINES = 12
# Prior points quoted back to the model so it does not repeat them.
MAX_PRIOR_POINTS = 20


def _build_user_prompt(
    persona_system: str,
    topic_title: str,
    recent_lines: List[str],
    topic_summary: str = "",
    prior_points: Optional[List[str]] = None,
) -> List[dict]:
    """Build the OpenAI-style messages list with a strict plain-text contract."""
    system = (
        persona_system
        + "\n\nCRITICAL OUTPUT CONTRACT:\n"
        "- Return ONLY the final visible chat message.\n"
        "- Do NOT return JSON.\n"
        "- Do NOT use Markdown code fences.\n"
        "- Do NOT include fields such as \"text\", \"new_point\", \"speaker\", "
        "\"analysis\", or \"reasoning\".\n"
        "- Do NOT prefix the answer with your persona name.\n"
        "- Respond directly as the spoken words only."
    )
    messages = [{"role": "system", "content": system}]

    if recent_lines:
        context = "Recent discussion so far:\n" + "\n".join(
            recent_lines[-MAX_CONTEXT_LINES:]
        )
        messages.append({"role": "user", "content": context})

    points = [p for p in (prior_points or []) if p]
    if points:
        messages.append(
            {
                "role": "user",
                "content": (
                    "Points ALREADY MADE in this discussion. Do not repeat any "
                    "of them, do not rephrase them, and do not reuse their "
                    "imagery or examples:\n"
                    + "\n".join(f"- {p}" for p in points[-MAX_PRIOR_POINTS:])
                ),
            }
        )

    headline = f"Current headline to discuss: \"{topic_title}\"."
    if topic_summary:
        flat = " ".join(topic_summary.split())
        if len(flat) > 600:
            flat = flat[:600].rstrip() + "…"
        headline += f"\nHeadline summary: {flat}"
    messages.append(
        {
            "role": "user",
            "content": (
                headline
                + "\nReact to the message immediately before yours and add ONE "
                "genuinely new observation, implication, disagreement, "
                "question, risk, or practical consequence that is not in the "
                "list above. Do not invent facts, statistics, quotations, or "
                "announcements. Give your single short spoken reply now."
            ),
        }
    )
    return messages


class LLMClient:
    """OpenAI-compatible chat completions client."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        temperature: float = 0.55,
        max_tokens: int = 140,
        timeout_seconds: float = 90.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def generate(
        self,
        persona_system: str,
        topic_title: str,
        recent_lines: List[str],
        session: Optional[aiohttp.ClientSession] = None,
        extra_instruction: Optional[str] = None,
        topic_summary: str = "",
        prior_points: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Generate a single plain-text reply. Returns None on failure."""
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession()
        try:
            assert session is not None
            messages = _build_user_prompt(
                persona_system,
                topic_title,
                recent_lines,
                topic_summary=topic_summary,
                prior_points=prior_points,
            )
            if extra_instruction:
                messages.append({"role": "user", "content": extra_instruction})
            payload = {
                "model": self._model,
                "messages": messages,
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
            }
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            url = f"{self._base_url}/chat/completions"
            # The timeout is applied per request so that it also governs a
            # caller-supplied session that was created without one.
            async with session.post(
                url, json=payload, headers=headers, timeout=self._timeout
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    retry_after = resp.headers.get("Retry-After", "")
                    logger.warning(
                        "LLM HTTP %s%s: %s",
                        resp.status,
                        f" (Retry-After: {retry_after})" if retry_after else "",
                        body[:200],
                    )
                    return None
                data = await resp.json()
            return _extract_text(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            logger.warning("LLM generate failed: %s", exc)
            return None
        finally:
            if own_session and session is not None:
                await session.close()


def _extract_text(data: dict) -> Optional[str]:
    """Extract the assistant text from an OpenAI-compatible response dict."""
    try:
        choices = data.get("choices") or []
        if not choices:
            return None
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            # Some providers return a list of parts.
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(str(part.get("text", "")))
                else:
                    parts.append(str(part))
            content = " ".join(parts).strip()
        if content is None:
            content = ""
        return str(content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM response parse failed: %s", exc)
        return None
