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
        """Get an active challenge or generate a new one when expired/missing."""

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
    """Persist verified Discord-to-Habbo mappings in JSON/VerifiedUsers.json."""

    def __init__(self, file_path: Path | None = None) -> None:
        root_path = Path(__file__).resolve().parent
        self.file_path = file_path or (root_path / "JSON" / "VerifiedUsers.json")

    def save(self, discord_id: str, habbo_username: str) -> None:
        """Create/update one verified mapping and write it to disk."""

        entries = self._read_entries()
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


class BadgeRoleMapper:
    """Map Habbo group memberships to Discord role IDs via JSON/BadgesToRoles.json."""

    def __init__(self, file_path: Path | None = None) -> None:
        root_path = Path(__file__).resolve().parent
        self.file_path = file_path or (root_path / "JSON" / "BadgesToRoles.json")

    def resolve_role_ids(self, habbo_group_ids: set[str]) -> list[int]:
        """Return role IDs for matching groups with employee-role hierarchy rules.

        Employee roles are mutually exclusive: assign only the *highest* role the user
        qualifies for. Highest-to-lowest priority follows the order in EmployeeRoles in
        BadgesToRoles.json, where Foundation should appear before Security.
        """

        config = self._load_config()
        role_ids: list[int] = []

        # Assign one highest employee role based on file order (top = highest rank).
        for entry in config.get("EmployeeRoles", []):
            group_id = str(entry.get("group_id", ""))
            if group_id in habbo_group_ids:
                role_id = self._safe_int(entry.get("role_id"))
                if role_id is not None:
                    role_ids.append(role_id)
                break

        # Assign all matching roles in other categories.
        # Support both legacy "DonationRoles" and current "Donators" key names.
        for category in ("SpecialUnits", "MiscRoles", "Donators", "DonationRoles"):
            for entry in config.get(category, []):
                group_id = str(entry.get("group_id", ""))
                if group_id in habbo_group_ids:
                    role_id = self._safe_int(entry.get("role_id"))
                    if role_id is not None:
                        role_ids.append(role_id)

        return role_ids

    def _load_config(self) -> dict:
        """Read role mapping config safely, returning empty categories if unavailable."""

        default = {"EmployeeRoles": [], "SpecialUnits": [], "MiscRoles": [], "DonationRoles": []}
        if not self.file_path.exists():
            return default

        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

        if not isinstance(data, dict):
            return default
        return data

    @staticmethod
    def _safe_int(value: object) -> int | None:
        """Convert supported role-id values to int, returning None for invalid values."""

        try:
            return int(value)
        except (TypeError, ValueError):
            return None


def fetch_habbo_profile(habbo_name: str) -> dict:
    """Fetch a Habbo public user profile JSON document."""

    encoded_name = parse.quote(habbo_name)
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


def fetch_habbo_group_ids(habbo_unique_id: str) -> set[str]:
    """Fetch public Habbo groups for a user and return normalized group IDs."""

    encoded_id = parse.quote(habbo_unique_id)
    url = f"https://www.habbo.com/api/public/users/{encoded_id}/groups"

    try:
        with request.urlopen(url, timeout=10) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise HabboApiError(f"Failed to fetch Habbo groups: {exc}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HabboApiError("Habbo groups API returned invalid JSON.") from exc

    if not isinstance(data, list):
        raise HabboApiError("Habbo groups API response is missing expected list format.")

    group_ids: set[str] = set()
    for group in data:
        if isinstance(group, dict):
            # Group identifiers can be exposed under different keys; keep extraction tolerant.
            group_id = group.get("groupId") or group.get("id") or group.get("uniqueId")
            if group_id:
                group_ids.add(str(group_id))
    return group_ids


def motto_contains_code(profile: dict, challenge_code: str) -> bool:
    """Check if a Habbo profile's motto currently includes the verification code."""

    motto = str(profile.get("motto", ""))
    return challenge_code in motto
