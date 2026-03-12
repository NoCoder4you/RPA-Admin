"""Unit tests for Habbo verification core logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import MagicMock, patch

from habbo_verification_core import (
    HabboApiError,
    VerificationManager,
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

    def test_motto_contains_code(self) -> None:
        profile = {"motto": "Hello CODE42 world"}
        self.assertTrue(motto_contains_code(profile, "CODE42"))
        self.assertFalse(motto_contains_code(profile, "MISSING"))


if __name__ == "__main__":
    unittest.main()
