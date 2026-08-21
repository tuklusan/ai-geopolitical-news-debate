"""
OpenAI-compatible LLM client for Live News Debate Wall.

Uses the chat completions endpoint with an OpenAI-compatible provider.
Returns plain-text model output only. The API key is passed via header and
is never stored, displayed, or logged.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

import aiohttp

logger = logging.getLogger("live_news_wall.llm")


def _build_user_prompt(persona_system: str, topic_title: str, recent_lines: List[str]) -> List[dict]:
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
        context = "Recent discussion so far:\n" + "\n".join(recent_lines[-6:])
        messages.append({"role": "user", "content": context})
    messages.append(
        {
            "role": "user",
            "content": (
                f"Current headline to discuss: \"{topic_title}\".\n"
                "Give your single short spoken reply now."
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
    ) -> Optional[str]:
        """Generate a single plain-text reply. Returns None on failure."""
        own_session = session is None
        if own_session:
            session = aiohttp.ClientSession(timeout=self._timeout)
        try:
            assert session is not None
            messages = _build_user_prompt(persona_system, topic_title, recent_lines)
            if extra_instruction:
                messages.append(
                    {"role": "user", "content": extra_instruction}
                )
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
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("LLM HTTP %s: %s", resp.status, body[:200])
                    return None
                data = await resp.json()
            text = _extract_text(data)
            return text
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
