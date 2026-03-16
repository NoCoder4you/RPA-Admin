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

    def get_habbo_username(self, discord_id: str) -> str | None:
        """Return the stored Habbo username for a Discord user, if present."""

        for entry in self._read_entries():
            if entry.get("discord_id") == discord_id:
                username = str(entry.get("habbo_username", "")).strip()
                return username or None
        return None

    def is_verified(self, discord_id: str) -> bool:
        """Check whether a Discord user already has a saved verification mapping."""

        return self.get_habbo_username(discord_id) is not None

    def get_all_entries(self) -> list[dict[str, str]]:
        """Return all verified Discord-to-Habbo entries for bulk role syncing."""

        return self._read_entries()

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


class ServerConfigStore:
    """Read/write single-server configuration from serverconfig.json.

    Expected file shape:
    {
      "audit_log_channel_id": "123",
      "muted_role_id": "456",
      "base_rpa_employee_role_id": "789"
    }
    """

    def __init__(self, file_path: Path | None = None) -> None:
        root_path = Path(__file__).resolve().parent
        self.file_path = file_path or (root_path / "serverconfig.json")

    def get_audit_channel_id(self) -> int | None:
        """Return configured audit log channel ID for this single-server bot."""

        config = self._read_config()
        return self._safe_int(config.get("audit_log_channel_id"))

    def get_muted_role_id(self) -> int | None:
        """Return configured muted role ID for this single-server bot."""

        config = self._read_config()
        return self._safe_int(config.get("muted_role_id"))

    def set_muted_role_id(self, role_id: int) -> None:
        """Persist muted role ID to serverconfig.json while preserving other keys."""

        config = self._read_config()
        config["muted_role_id"] = str(role_id)
        self._write_config(config)

    def get_base_rpa_employee_role_id(self) -> int | None:
        """Return the configured base role granted to all RPA employees."""

        config = self._read_config()
        return self._safe_int(config.get("base_rpa_employee_role_id"))

    def set_base_rpa_employee_role_id(self, role_id: int) -> None:
        """Persist the base RPA employee role ID while preserving existing config keys."""

        config = self._read_config()
        config["base_rpa_employee_role_id"] = str(role_id)
        self._write_config(config)

    def _write_config(self, config: dict) -> None:
        """Write config JSON safely, creating parent directories when needed."""

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def _read_config(self) -> dict:
        """Load config JSON with safe fallback for missing/corrupted files."""

        if not self.file_path.exists():
            return {}

        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

        return data if isinstance(data, dict) else {}

    @staticmethod
    def _safe_int(value: object) -> int | None:
        """Convert supported ID values to int, returning None for invalid values."""

        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class BadgeRoleMapper:
    """Map Habbo group memberships to Discord role IDs via JSON/BadgesToRoles.json."""

    def __init__(
        self,
        file_path: Path | None = None,
        *,
        server_config_store: ServerConfigStore | None = None,
    ) -> None:
        root_path = Path(__file__).resolve().parent
        self.file_path = file_path or (root_path / "JSON" / "BadgesToRoles.json")
        # Read optional shared employee-role configuration from serverconfig.json.
        self.server_config_store = server_config_store or ServerConfigStore()

    def resolve_role_ids(self, habbo_group_ids: set[str]) -> list[int]:
        """Return role IDs for matching groups with employee-role hierarchy rules."""

        config = self._load_config()
        role_ids: list[int] = []
        has_rpa_employee_badge = False

        # Assign one highest employee role based on file order (top = highest rank).
        for entry in config.get("EmployeeRoles", []):
            group_id = str(entry.get("group_id", ""))
            if group_id in habbo_group_ids:
                role_id = self._safe_int(entry.get("role_id"))
                if role_id is not None:
                    role_ids.append(role_id)
                # Employee entries can explicitly mark the user as an RPA employee.
                if self._is_yes(entry.get("rpaemployee")):
                    has_rpa_employee_badge = True
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
                    # Some categories also encode employee entitlement, so keep this check generic.
                    if self._is_yes(entry.get("rpaemployee")):
                        has_rpa_employee_badge = True

        # Ensure all users flagged as RPA employees get the shared role configured in serverconfig.json.
        base_rpa_employee_role_id = self.server_config_store.get_base_rpa_employee_role_id()
        if (
            has_rpa_employee_badge
            and base_rpa_employee_role_id is not None
            and base_rpa_employee_role_id not in role_ids
        ):
            role_ids.append(base_rpa_employee_role_id)

        return role_ids

    def get_all_mapped_role_ids(self) -> set[int]:
        """Return every role ID managed by the Habbo mapping configuration.

        This helps sync flows remove stale mapped roles when a user no longer
        belongs to the backing Habbo groups.
        """

        config = self._load_config()
        managed_role_ids: set[int] = set()

        for category in ("EmployeeRoles", "SpecialUnits", "MiscRoles", "Donators", "DonationRoles"):
            for entry in config.get(category, []):
                role_id = self._safe_int(entry.get("role_id"))
                if role_id is not None:
                    managed_role_ids.add(role_id)

        return managed_role_ids

    def _load_config(self) -> dict:
        """Read role mapping config safely, returning empty categories if unavailable."""

        default = {"EmployeeRoles": [], "SpecialUnits": [], "MiscRoles": [], "Donators": []}
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

    @staticmethod
    def _is_yes(value: object) -> bool:
        """Return True when config values represent an affirmative "yes" state."""

        return str(value).strip().lower() == "yes"


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
            group_id = group.get("groupId") or group.get("id") or group.get("uniqueId")
            if group_id:
                group_ids.add(str(group_id))
    return group_ids


def motto_contains_code(profile: dict, challenge_code: str) -> bool:
    """Check if a Habbo profile's motto currently includes the verification code."""

    motto = str(profile.get("motto", ""))
    return challenge_code in motto
