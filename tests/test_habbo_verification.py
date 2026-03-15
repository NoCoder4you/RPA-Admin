"""Unit tests for Habbo verification core logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from habbo_verification_core import (
    HabboApiError,
    VerificationManager,
    VerifiedUserStore,
    fetch_habbo_profile,
    motto_contains_code,
)


class VerificationManagerTests(unittest.TestCase):
    """Validate challenge generation, reuse, and expiration behavior."""

    def test_reuses_active_challenge_for_same_user_and_habbo(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        manager = VerificationManager(now_fn=lambda: now)

        first = manager.get_or_create(1, "Siren")
        second = manager.get_or_create(1, "Siren")

        self.assertEqual(first.code, second.code)
        self.assertEqual(first.expires_at, second.expires_at)

    def test_refreshes_challenge_after_expiration(self) -> None:
        current = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def now_fn() -> datetime:
            return current

        manager = VerificationManager(now_fn=now_fn)
        first = manager.get_or_create(1, "Siren")

        current = current + timedelta(minutes=6)
        second = manager.get_or_create(1, "Siren")

        self.assertNotEqual(first.code, second.code)

    def test_switching_habbo_name_creates_new_challenge(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        manager = VerificationManager(now_fn=lambda: now)

        first = manager.get_or_create(1, "Siren")
        second = manager.get_or_create(1, "OtherHabbo")

        self.assertNotEqual(first.code, second.code)


class HabboApiTests(unittest.TestCase):
    """Validate Habbo API parsing and motto code checks."""

    @patch("habbo_verification_core.request.urlopen")
    def test_fetch_habbo_profile_parses_json(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"name": "Siren", "motto": "CODE123"}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        data = fetch_habbo_profile("Siren")

        self.assertEqual(data["name"], "Siren")
        self.assertEqual(data["motto"], "CODE123")

    @patch("habbo_verification_core.request.urlopen")
    def test_fetch_habbo_profile_raises_on_bad_payload(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with self.assertRaises(HabboApiError):
            fetch_habbo_profile("Siren")

    @patch("habbo_verification_core.request.urlopen")
    def test_fetch_habbo_profile_uses_com_api(self, mock_urlopen: MagicMock) -> None:
        """Ensure profile requests always target habbo.com as requested."""

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"name": "Siren", "motto": "CODE123"}).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        fetch_habbo_profile("Siren")

        called_url = mock_urlopen.call_args.args[0]
        self.assertIn("https://www.habbo.com/api/public/users?name=Siren", called_url)

    def test_motto_contains_code(self) -> None:
        profile = {"motto": "Hello CODE42 world"}
        self.assertTrue(motto_contains_code(profile, "CODE42"))
        self.assertFalse(motto_contains_code(profile, "MISSING"))


class VerifiedUserStoreTests(unittest.TestCase):
    """Validate JSON persistence of verified Discord-to-Habbo mappings."""

    def test_save_creates_json_file_with_string_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VerifiedUserStore(file_path=Path(temp_dir) / "JSON" / "VerifiedUsers.json")
            store.save(discord_id="123456", habbo_username="Siren")

            data = json.loads(store.file_path.read_text(encoding="utf-8"))
            self.assertEqual(data, [{"discord_id": "123456", "habbo_username": "Siren"}])

    def test_save_updates_existing_discord_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "JSON" / "VerifiedUsers.json"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                json.dumps([
                    {"discord_id": "123456", "habbo_username": "OldName"},
                    {"discord_id": "999", "habbo_username": "Other"},
                ]),
                encoding="utf-8",
            )

            store = VerifiedUserStore(file_path=file_path)
            store.save(discord_id="123456", habbo_username="NewName")

            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(
                data,
                [
                    {"discord_id": "123456", "habbo_username": "NewName"},
                    {"discord_id": "999", "habbo_username": "Other"},
                ],
            )


if __name__ == "__main__":
    unittest.main()
