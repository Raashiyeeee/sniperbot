"""
ai/gemini_client.py
───────────────────
Google Gemini Developer API async client.
Supports Gemini 2.0 Flash (default), 1.5 Flash, 1.5 Pro via Google AI Studio API.
Implements:
- Structured JSON output enforcement
- Exponential backoff retry logic (tenacity)
- API key masking (never logs keys)
- Daily request budgeting and rate limiting
- Safe timeout handling
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiAPIError(Exception):
    """Base exception for Gemini API failures."""
    pass


class GeminiRateLimitError(GeminiAPIError):
    """Raised when Gemini returns HTTP 429 Too Many Requests."""
    pass


class GeminiBudgetExceededError(GeminiAPIError):
    """Raised when daily configured request budget is exhausted."""
    pass


def mask_key(key: str) -> str:
    """Mask API key for safe logging (e.g. 'AIzaSy...4xQ')."""
    if not key or len(key) < 8:
        return "[NOT SET]"
    return f"{key[:6]}...{key[-4:]}"


class GeminiClient:
    """
    Async client for Google Gemini Developer API.
    Handles authentication, structured JSON generation, retries, and quota tracking.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        daily_limit: Optional[int] = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.gemini_model or "gemini-2.0-flash"
        self.timeout = timeout or settings.gemini_timeout or 30.0
        self.max_retries = max_retries or settings.gemini_max_retries or 3
        self.daily_limit = daily_limit or settings.gemini_max_daily_requests or 1000

        self._daily_requests: int = 0
        self._last_reset_date = datetime.now(timezone.utc).date()
        self._lock = asyncio.Lock()

        logger.info(
            "gemini_client_initialized",
            model=self.model,
            has_key=bool(self.api_key),
            key_preview=mask_key(self.api_key),
            daily_limit=self.daily_limit,
        )

    def _check_and_reset_daily_budget(self) -> None:
        """Reset daily request counter at midnight UTC."""
        today = datetime.now(timezone.utc).date()
        if today > self._last_reset_date:
            self._daily_requests = 0
            self._last_reset_date = today

    async def _increment_request_count(self) -> None:
        async with self._lock:
            self._check_and_reset_daily_budget()
            if self._daily_requests >= self.daily_limit:
                raise GeminiBudgetExceededError(
                    f"Daily Gemini request limit of {self.daily_limit} reached. Request halted to preserve budget."
                )
            self._daily_requests += 1

    @property
    def is_configured(self) -> bool:
        """Return True if Gemini is enabled and has an API key configured."""
        return bool(self.api_key and self.api_key.strip())

    async def generate_structured_json(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.1,
    ) -> Dict[str, Any]:
        """
        Send a prompt to Gemini with response_mime_type='application/json'
        and return the parsed JSON dictionary.
        """
        if not self.is_configured:
            raise GeminiAPIError("GEMINI_API_KEY is not configured in environment or .env")

        await self._increment_request_count()

        # Build payload according to Gemini Developer API v1beta spec
        url = f"{GEMINI_BASE_URL}/models/{self.model}:generateContent"
        params = {"key": self.api_key}

        payload: Dict[str, Any] = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": temperature,
            },
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Antigravity-SniperBot/3.2.0",
        }

        # Execute with exponential backoff
        return await self._call_with_retry(url, params, headers, payload)

    async def _call_with_retry(
        self,
        url: str,
        params: Dict[str, str],
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute HTTP request with retry logic for transient errors and rate limits."""
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        url,
                        params=params,
                        headers=headers,
                        json=payload,
                    )

                if response.status_code == 200:
                    data = response.json()
                    return self._extract_json_from_response(data)

                elif response.status_code == 429:
                    logger.warning("gemini_rate_limited", attempt=attempt, status=429)
                    if attempt == self.max_retries:
                        raise GeminiRateLimitError("Gemini rate limit exceeded after retries")
                    await asyncio.sleep(2.0 ** attempt)
                    continue

                elif response.status_code in (500, 502, 503, 504):
                    logger.warning("gemini_service_unavailable", status=response.status_code, attempt=attempt)
                    if attempt == self.max_retries:
                        raise GeminiAPIError(f"Gemini server error {response.status_code}")
                    await asyncio.sleep(1.5 ** attempt)
                    continue

                else:
                    err_text = response.text[:300]
                    raise GeminiAPIError(f"Gemini API returned error {response.status_code}: {err_text}")

            except (httpx.TimeoutException, httpx.NetworkError) as net_err:
                last_error = net_err
                logger.warning("gemini_network_timeout", attempt=attempt, error=str(net_err))
                if attempt == self.max_retries:
                    raise GeminiAPIError(f"Gemini network error after {self.max_retries} attempts: {net_err}")
                await asyncio.sleep(1.5 ** attempt)

        raise GeminiAPIError(f"Gemini request failed: {last_error}")

    def _extract_json_from_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract and parse structured JSON text from Gemini candidate response."""
        raw_text: str = ""
        try:
            candidates = response_data.get("candidates", [])
            if not candidates:
                raise GeminiAPIError("Gemini returned empty candidates list")

            first_candidate = candidates[0]
            content = first_candidate.get("content", {})
            parts = content.get("parts", [])
            if not parts:
                raise GeminiAPIError("Gemini candidate content has no parts")

            raw_text = parts[0].get("text", "").strip()

            # Strip markdown json code fences if present
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)

            return json.loads(raw_text)

        except json.JSONDecodeError as jde:
            logger.error("gemini_malformed_json", error=str(jde), text_preview=raw_text[:200])
            raise GeminiAPIError(f"Gemini output was not valid JSON: {jde}")
