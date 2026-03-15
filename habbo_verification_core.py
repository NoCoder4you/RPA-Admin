"""Core utilities for Discord x Habbo motto verification.

This module is framework-agnostic so it can be tested without the Discord runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
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


class VerifiedUserStore:
    """Persist verified Discord-to-Habbo mappings in JSON/VerifiedUsers.json.

    The JSON shape is a list of objects:
    [
      {"discord_id": "123", "habbo_username": "Siren"}
    ]
    """

    def __init__(self, file_path: Path | None = None) -> None:
        root_path = Path(__file__).resolve().parent
        self.file_path = file_path or (root_path / "JSON" / "VerifiedUsers.json")

    def save(self, discord_id: str, habbo_username: str) -> None:
        """Create/update one verified mapping and write it to disk."""

        entries = self._read_entries()

        # Update the existing entry for this Discord account, otherwise append a new one.
        updated = False
        for entry in entries:
            if entry.get("discord_id") == discord_id:
                entry["habbo_username"] = habbo_username
                updated = True
                break

        if not updated:
            entries.append({"discord_id": discord_id, "habbo_username": habbo_username})

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    def _read_entries(self) -> list[dict[str, str]]:
        """Read JSON file safely and normalize to a list."""

        if not self.file_path.exists():
            return []

        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Corrupted JSON should not crash verification; start from a clean list.
            return []

        if not isinstance(data, list):
            return []

        normalized: list[dict[str, str]] = []
        for row in data:
            if isinstance(row, dict):
                normalized.append(
                    {
                        "discord_id": str(row.get("discord_id", "")),
                        "habbo_username": str(row.get("habbo_username", "")),
                    }
                )
        return normalized


def fetch_habbo_profile(habbo_name: str) -> dict:
    """Fetch a Habbo public user profile JSON document.

    Args:
        habbo_name: Habbo username to query.

    Raises:
        HabboApiError: if the API call fails or data is malformed.
    """

    encoded_name = parse.quote(habbo_name)
    # This project verifies against the main .com hotel API only.
    url = f"https://www.habbo.com/api/public/users?name={encoded_name}"

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
