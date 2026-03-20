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

from common_paths import json_file


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
        # Use the shared helper so path conventions stay consistent across modules.
        self.file_path = file_path or json_file("VerifiedUsers.json")

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
    """Read/write single-server configuration from JSON/serverconfig.json.

    Expected file shape:
    {
      "audit_log_channel_id": "123",
      "profanity_log_channel_id": "234",
      "message_log_channel_id": "345",
      "muted_role_id": "456",
      "base_rpa_employee_role_id": "789",
      "verification_reaction_message_id": "101112",
      "rules_acknowledgement_message_id": "111213",
      "awaiting_verification_channel_id": "111214",
      "awaiting_verification_role_id": "111215",
      "request_channel_id": "121314",
      "admin_role_id": "151617",
      "webhook_archive_channel_id": "181920",
      "new_applications_channel_id": "212223",
      "unit_leadership_role_id": "242526"
    }
    """

    def __init__(self, file_path: Path | None = None) -> None:
        # Keep server config in the shared JSON directory with the project's other persisted data.
        self.file_path = file_path or json_file("serverconfig.json")

    def get_audit_channel_id(self) -> int | None:
        """Return configured audit log channel ID for this single-server bot."""

        config = self._read_config()
        return self._safe_int(config.get("audit_log_channel_id"))

    def get_profanity_log_channel_id(self) -> int | None:
        """Return the configured channel ID used for profanity deletion logs."""

        config = self._read_config()
        return self._safe_int(config.get("profanity_log_channel_id"))

    def set_profanity_log_channel_id(self, channel_id: int) -> None:
        """Persist the profanity log channel ID while preserving other config keys."""

        config = self._read_config()
        config["profanity_log_channel_id"] = str(channel_id)
        self._write_config(config)

    def get_message_log_channel_id(self) -> int | None:
        """Return the configured channel ID used for message delete/edit event logs."""

        config = self._read_config()
        return self._safe_int(config.get("message_log_channel_id"))

    def set_message_log_channel_id(self, channel_id: int) -> None:
        """Persist the message-event log channel ID while preserving other config keys."""

        config = self._read_config()
        config["message_log_channel_id"] = str(channel_id)
        self._write_config(config)

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

    def get_verification_reaction_message_id(self) -> int | None:
        """Return message ID users must react to in order to receive verification-awaiting role."""

        config = self._read_config()
        return self._safe_int(config.get("verification_reaction_message_id"))

    def set_verification_reaction_message_id(self, message_id: int) -> None:
        """Persist verification reaction target message ID while preserving existing config keys."""

        config = self._read_config()
        config["verification_reaction_message_id"] = str(message_id)
        self._write_config(config)

    def get_rules_acknowledgement_message_id(self) -> int | None:
        """Return the rules acknowledgement embed message ID that members should react to."""

        config = self._read_config()
        return self._safe_int(config.get("rules_acknowledgement_message_id"))

    def set_rules_acknowledgement_message_id(self, message_id: int) -> None:
        """Persist the final rules acknowledgement message ID while preserving existing config keys."""

        config = self._read_config()
        config["rules_acknowledgement_message_id"] = str(message_id)
        self._write_config(config)

    def get_awaiting_verification_channel_id(self) -> int | None:
        """Return the configured channel ID used for per-user Awaiting Verification onboarding embeds."""

        config = self._read_config()
        return self._safe_int(config.get("awaiting_verification_channel_id"))

    def set_awaiting_verification_channel_id(self, channel_id: int) -> None:
        """Persist the Awaiting Verification onboarding channel ID while preserving existing config keys."""

        config = self._read_config()
        config["awaiting_verification_channel_id"] = str(channel_id)
        self._write_config(config)

    def get_awaiting_verification_role_id(self) -> int | None:
        """Return the configured Awaiting Verification role ID used to detect staging assignments."""

        config = self._read_config()
        return self._safe_int(config.get("awaiting_verification_role_id"))

    def set_awaiting_verification_role_id(self, role_id: int) -> None:
        """Persist the Awaiting Verification role ID while preserving existing config keys."""

        config = self._read_config()
        config["awaiting_verification_role_id"] = str(role_id)
        self._write_config(config)

    def get_request_channel_id(self) -> int | None:
        """Return the configured requests channel ID used for Habbo username-change notifications."""

        config = self._read_config()
        return self._safe_int(config.get("request_channel_id"))

    def set_request_channel_id(self, channel_id: int) -> None:
        """Persist the requests channel ID for username-change review notifications."""

        config = self._read_config()
        config["request_channel_id"] = str(channel_id)
        self._write_config(config)

    def get_admin_role_id(self) -> int | None:
        """Return the configured admin role ID that should be mentioned on username-change notifications."""

        config = self._read_config()
        return self._safe_int(config.get("admin_role_id"))

    def set_admin_role_id(self, role_id: int) -> None:
        """Persist the admin role ID used for username-change request pings."""

        config = self._read_config()
        config["admin_role_id"] = str(role_id)
        self._write_config(config)

    def get_webhook_archive_channel_id(self) -> int | None:
        """Return the configured archive channel ID used to source forwarded webhook embeds."""

        config = self._read_config()
        return self._safe_int(config.get("webhook_archive_channel_id"))

    def set_webhook_archive_channel_id(self, channel_id: int) -> None:
        """Persist the archive channel ID used for webhook application message forwarding."""

        config = self._read_config()
        config["webhook_archive_channel_id"] = str(channel_id)
        self._write_config(config)

    def get_new_applications_channel_id(self) -> int | None:
        """Return the configured channel ID used for new-application claim notifications."""

        config = self._read_config()
        return self._safe_int(config.get("new_applications_channel_id"))

    def set_new_applications_channel_id(self, channel_id: int) -> None:
        """Persist the channel ID used to post new-application notifications and claim buttons."""

        config = self._read_config()
        config["new_applications_channel_id"] = str(channel_id)
        self._write_config(config)

    def get_unit_leadership_role_id(self) -> int | None:
        """Return the configured Unit Leadership role ID mentioned on new-application notifications."""

        config = self._read_config()
        return self._safe_int(config.get("unit_leadership_role_id"))

    def set_unit_leadership_role_id(self, role_id: int) -> None:
        """Persist the Unit Leadership role ID used for new-application notification mentions."""

        config = self._read_config()
        config["unit_leadership_role_id"] = str(role_id)
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


class VerifyRestrictionStore:
    """Persist Habbo usernames that should trigger verification restrictions."""

    GROUP_DNH = "DNH"
    GROUP_BOS = "BoS"
    VALID_GROUPS = (GROUP_DNH, GROUP_BOS)

    def __init__(self, file_path: Path | None = None) -> None:
        # Keep restriction data in the shared JSON folder with the bot's other persisted state.
        self.file_path = file_path or json_file("VerifyRestrictions.json")

    def add_username(self, group_name: str, username: str) -> bool:
        """Add a Habbo username to one restriction group, returning True when it was new."""

        normalized_group = self._normalize_group_name(group_name)
        normalized_username = self._normalize_username(username)
        normalized_username_key = normalized_username.lower()

        data = self._read_data()
        usernames = data[normalized_group]
        if any(existing_username.lower() == normalized_username_key for existing_username in usernames):
            return False

        usernames.append(normalized_username)
        usernames.sort(key=str.lower)
        self._write_data(data)
        return True

    def remove_username(self, group_name: str, username: str) -> bool:
        """Remove a username from one restriction group, returning True when it existed."""

        normalized_group = self._normalize_group_name(group_name)
        normalized_username = self._normalize_username(username)
        normalized_username_key = normalized_username.lower()

        data = self._read_data()
        usernames = data[normalized_group]
        existing_username = next((entry for entry in usernames if entry.lower() == normalized_username_key), None)
        if existing_username is None:
            return False

        usernames.remove(existing_username)
        self._write_data(data)
        return True

    def get_group_for_username(self, username: str) -> str | None:
        """Return the restriction group a Habbo username belongs to, if any."""

        normalized_username_key = self._normalize_username(username).lower()
        data = self._read_data()
        for group_name in self.VALID_GROUPS:
            if any(entry.lower() == normalized_username_key for entry in data[group_name]):
                return group_name
        return None

    def is_username_restricted(self, username: str, group_name: str) -> bool:
        """Return True when the normalized username exists in the selected restriction group."""

        normalized_group = self._normalize_group_name(group_name)
        normalized_username_key = self._normalize_username(username).lower()
        return any(entry.lower() == normalized_username_key for entry in self._read_data()[normalized_group])

    def get_all_usernames(self, group_name: str) -> list[str]:
        """Return all usernames saved for one group in stable order."""

        normalized_group = self._normalize_group_name(group_name)
        return list(self._read_data()[normalized_group])

    def _read_data(self) -> dict[str, list[str]]:
        """Load the restrictions JSON with safe defaults for missing or invalid files."""

        default = {group_name: [] for group_name in self.VALID_GROUPS}
        if not self.file_path.exists():
            return default

        try:
            raw_data = json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

        if not isinstance(raw_data, dict):
            return default

        normalized_data = {group_name: [] for group_name in self.VALID_GROUPS}
        for group_name in self.VALID_GROUPS:
            group_entries = raw_data.get(group_name, [])
            if not isinstance(group_entries, list):
                continue

            seen: set[str] = set()
            for entry in group_entries:
                normalized_username = self._normalize_username(str(entry))
                normalized_username_key = normalized_username.lower()
                if not normalized_username or normalized_username_key in seen:
                    continue
                normalized_data[group_name].append(normalized_username)
                seen.add(normalized_username_key)

            normalized_data[group_name].sort(key=str.lower)

        return normalized_data

    def _write_data(self, data: dict[str, list[str]]) -> None:
        """Persist normalized restriction data to disk."""

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _normalize_group_name(self, group_name: str) -> str:
        """Normalize user-supplied group names to the store's canonical keys."""

        collapsed = str(group_name).strip().lower()
        alias_map = {"dnh": self.GROUP_DNH, "bos": self.GROUP_BOS, "ban on sight": self.GROUP_BOS}
        normalized = alias_map.get(collapsed)
        if normalized is None:
            raise ValueError(f"Unsupported restriction group: {group_name}")
        return normalized

    @staticmethod
    def _normalize_username(username: str) -> str:
        """Normalize Habbo usernames for case-insensitive storage and comparison."""

        return str(username).strip()


class BadgeRoleMapper:
    """Map Habbo group memberships to Discord role IDs via JSON/BadgesToRoles.json."""

    def __init__(
        self,
        file_path: Path | None = None,
        *,
        server_config_store: ServerConfigStore | None = None,
    ) -> None:
        # Keep role-mapping location centralized via common path utilities.
        self.file_path = file_path or json_file("BadgesToRoles.json")
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

        # Include the shared employee role so sync flows can remove it when entitlement is lost.
        # Example: user leaves all groups marked rpaemployee="yes" and should lose this role.
        base_rpa_employee_role_id = self.server_config_store.get_base_rpa_employee_role_id()
        if base_rpa_employee_role_id is not None:
            managed_role_ids.add(base_rpa_employee_role_id)

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
