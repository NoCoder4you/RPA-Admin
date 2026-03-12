"""Core utilities for Discord x Habbo motto verification.

This module is framework-agnostic so it can be tested without the Discord runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import secrets
import string
from typing import Callable
from urllib import parse, request
from urllib.error import HTTPError, URLError


class HabboApiError(RuntimeError):
    """Raised when the Habbo API cannot be reached or returns invalid data."""


@dataclass(frozen=True)
class VerificationChallenge:
    """A one-time code issued to a Discord user for Habbo motto verification."""

    habbo_name: str
    code: str
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        """Return True when the challenge has passed its expiry timestamp."""

        return now >= self.expires_at


class VerificationManager:
    """Creates and stores short-lived verification challenges per Discord user."""

    def __init__(
        self,
        *,
        ttl_minutes: int = 5,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.ttl_minutes = ttl_minutes
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._challenges: dict[int, VerificationChallenge] = {}

    def get_or_create(self, discord_user_id: int, habbo_name: str) -> VerificationChallenge:
        """Get an active challenge or generate a new one when expired/missing.

        A challenge is tied to both the Discord user and Habbo name. If the user submits
        a different Habbo name, a new challenge is created to avoid cross-account confusion.
        """

        current = self._challenges.get(discord_user_id)
        now = self._now_fn()

        if current and not current.is_expired(now) and current.habbo_name.lower() == habbo_name.lower():
            return current

        challenge = VerificationChallenge(
            habbo_name=habbo_name,
            code=self._generate_code(),
            expires_at=now + timedelta(minutes=self.ttl_minutes),
        )
        self._challenges[discord_user_id] = challenge
        return challenge

    def get_active(self, discord_user_id: int) -> VerificationChallenge | None:
        """Return the currently active challenge, removing it when expired."""

        challenge = self._challenges.get(discord_user_id)
        if not challenge:
            return None

        if challenge.is_expired(self._now_fn()):
            self._challenges.pop(discord_user_id, None)
            return None
        return challenge

    def clear(self, discord_user_id: int) -> None:
        """Delete a challenge after successful verification."""

        self._challenges.pop(discord_user_id, None)

    @staticmethod
    def _generate_code(length: int = 8) -> str:
        """Create a short uppercase code users can copy into their Habbo motto."""

        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))


def fetch_habbo_profile(habbo_name: str, *, hotel_domain: str = "habbo.com") -> dict:
    """Fetch a Habbo public user profile JSON document.

    Args:
        habbo_name: Habbo username to query.
        hotel_domain: Domain for the API host (e.g. "habbo.com", "habbo.es").

    Raises:
        HabboApiError: if the API call fails or data is malformed.
    """

    encoded_name = parse.quote(habbo_name)
    url = f"https://www.{hotel_domain}/api/public/users?name={encoded_name}"

    try:
        with request.urlopen(url, timeout=10) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise HabboApiError(f"Failed to fetch Habbo profile: {exc}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HabboApiError("Habbo API returned invalid JSON.") from exc

    if not isinstance(data, dict) or "motto" not in data:
        raise HabboApiError("Habbo API response is missing expected profile fields.")

    return data


def motto_contains_code(profile: dict, challenge_code: str) -> bool:
    """Check if a Habbo profile's motto currently includes the verification code."""

    motto = str(profile.get("motto", ""))
    return challenge_code in motto
