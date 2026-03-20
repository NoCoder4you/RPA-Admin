"""Unit tests for Habbo verification core logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from habbo_verification_core import (
    BadgeRoleMapper,
    HabboApiError,
    VerificationManager,
    VerifiedUserStore,
    ServerConfigStore,
    fetch_habbo_group_ids,
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
    """Validate Habbo API parsing and motto/group checks."""

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

    @patch("habbo_verification_core.request.urlopen")
    def test_fetch_habbo_group_ids_extracts_multiple_id_shapes(self, mock_urlopen: MagicMock) -> None:
        """Accept different group ID field names from Habbo groups API payloads."""

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            [{"groupId": "g-1"}, {"id": "g-2"}, {"uniqueId": "g-3"}, {"other": 1}]
        ).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        group_ids = fetch_habbo_group_ids("hhus-abc")

        self.assertEqual(group_ids, {"g-1", "g-2", "g-3"})

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

    def test_is_verified_and_get_habbo_username(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VerifiedUserStore(file_path=Path(temp_dir) / "JSON" / "VerifiedUsers.json")
            store.save(discord_id="123456", habbo_username="Siren")

            self.assertTrue(store.is_verified("123456"))
            self.assertEqual(store.get_habbo_username("123456"), "Siren")
            self.assertFalse(store.is_verified("404"))
            self.assertIsNone(store.get_habbo_username("404"))

    def test_get_all_entries_returns_normalized_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "JSON" / "VerifiedUsers.json"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                json.dumps([
                    {"discord_id": 123, "habbo_username": "Siren"},
                    {"discord_id": "999", "habbo_username": "Other"},
                ]),
                encoding="utf-8",
            )

            store = VerifiedUserStore(file_path=file_path)
            self.assertEqual(
                store.get_all_entries(),
                [
                    {"discord_id": "123", "habbo_username": "Siren"},
                    {"discord_id": "999", "habbo_username": "Other"},
                ],
            )


class ServerConfigStoreTests(unittest.TestCase):
    """Validate single-server audit-channel resolution from serverconfig.json."""

    def test_get_audit_channel_id_from_single_server_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            self.assertEqual(store.get_audit_channel_id(), 456)

    def test_get_audit_channel_id_returns_none_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            self.assertIsNone(store.get_audit_channel_id())

    def test_set_and_get_message_log_channel_id(self) -> None:
        """Ensure the message event log channel ID is persisted in serverconfig.json."""

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            store.set_message_log_channel_id(1484025952706232450)

            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("audit_log_channel_id"), "456")
            self.assertEqual(data.get("message_log_channel_id"), "1484025952706232450")
            self.assertEqual(store.get_message_log_channel_id(), 1484025952706232450)

    def test_set_and_get_profanity_log_channel_id(self) -> None:
        """Ensure the profanity log channel ID is persisted in serverconfig.json."""

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            store.set_profanity_log_channel_id(987654321)

            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("audit_log_channel_id"), "456")
            self.assertEqual(data.get("profanity_log_channel_id"), "987654321")
            self.assertEqual(store.get_profanity_log_channel_id(), 987654321)

    def test_set_and_get_muted_role_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            store.set_muted_role_id(789)

            # Ensure muted role persistence keeps existing server config keys untouched.
            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("audit_log_channel_id"), "456")
            self.assertEqual(data.get("muted_role_id"), "789")
            self.assertEqual(store.get_muted_role_id(), 789)

    def test_set_and_get_verification_reaction_message_id(self) -> None:
        """Ensure verification reaction message ID is persisted in serverconfig.json."""

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            store.set_verification_reaction_message_id(1481010999157981256)

            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("audit_log_channel_id"), "456")
            self.assertEqual(data.get("verification_reaction_message_id"), "1481010999157981256")
            self.assertEqual(store.get_verification_reaction_message_id(), 1481010999157981256)

    def test_set_and_get_base_rpa_employee_role_id(self) -> None:
        """Ensure the shared employee role ID is persisted in serverconfig.json."""

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            store.set_base_rpa_employee_role_id(1479388404260012092)

            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("audit_log_channel_id"), "456")
            self.assertEqual(data.get("base_rpa_employee_role_id"), "1479388404260012092")
            self.assertEqual(store.get_base_rpa_employee_role_id(), 1479388404260012092)


    def test_set_and_get_request_channel_id(self) -> None:
        """Ensure the requests channel ID is persisted in serverconfig.json."""

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            store.set_request_channel_id(1479465446632853524)

            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("audit_log_channel_id"), "456")
            self.assertEqual(data.get("request_channel_id"), "1479465446632853524")
            self.assertEqual(store.get_request_channel_id(), 1479465446632853524)

    def test_set_and_get_admin_role_id(self) -> None:
        """Ensure the admin role ID is persisted in serverconfig.json."""

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            store.set_admin_role_id(1484029753185931336)

            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("audit_log_channel_id"), "456")
            self.assertEqual(data.get("admin_role_id"), "1484029753185931336")
            self.assertEqual(store.get_admin_role_id(), 1484029753185931336)

    def test_set_and_get_webhook_archive_channel_id(self) -> None:
        """Ensure the webhook archive channel ID is persisted in serverconfig.json."""

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            store.set_webhook_archive_channel_id(1484040953370120292)

            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("audit_log_channel_id"), "456")
            self.assertEqual(data.get("webhook_archive_channel_id"), "1484040953370120292")
            self.assertEqual(store.get_webhook_archive_channel_id(), 1484040953370120292)

    def test_set_and_get_new_applications_channel_id(self) -> None:
        """Ensure the new-applications channel ID is persisted in serverconfig.json."""

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            store.set_new_applications_channel_id(1485000000000000000)

            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("audit_log_channel_id"), "456")
            self.assertEqual(data.get("new_applications_channel_id"), "1485000000000000000")
            self.assertEqual(store.get_new_applications_channel_id(), 1485000000000000000)

    def test_set_and_get_unit_leadership_role_id(self) -> None:
        """Ensure the Unit Leadership role ID is persisted in serverconfig.json."""

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "serverconfig.json"
            file_path.write_text(json.dumps({"audit_log_channel_id": "456"}), encoding="utf-8")

            store = ServerConfigStore(file_path=file_path)
            store.set_unit_leadership_role_id(1486000000000000000)

            data = json.loads(file_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("audit_log_channel_id"), "456")
            self.assertEqual(data.get("unit_leadership_role_id"), "1486000000000000000")
            self.assertEqual(store.get_unit_leadership_role_id(), 1486000000000000000)


class BadgeRoleMapperTests(unittest.TestCase):
    """Validate role mapping and employee-role hierarchy behavior."""

    def test_resolve_role_ids_selects_highest_employee_plus_other_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            mapping_path = Path(temp_dir) / "BadgesToRoles.json"
            mapping_path.write_text(
                json.dumps(
                    {
                        # Order is highest-to-lowest and must choose only one employee role.
                        "EmployeeRoles": [
                            {"role_id": 10, "group_id": "foundation"},
                            {"role_id": 11, "group_id": "security"},
                        ],
                        "SpecialUnits": [{"role_id": 20, "group_id": "special"}],
                        "MiscRoles": [{"role_id": 30, "group_id": "misc"}],
                        "Donators": [{"role_id": 40, "group_id": "donor"}],
                    }
                ),
                encoding="utf-8",
            )

            # Provide an explicit config-store stub to keep this test isolated from repo serverconfig.json.
            config_store = MagicMock(get_base_rpa_employee_role_id=MagicMock(return_value=None))
            mapper = BadgeRoleMapper(file_path=mapping_path, server_config_store=config_store)
            role_ids = mapper.resolve_role_ids({"foundation", "security", "special", "misc", "donor"})

            self.assertEqual(role_ids, [10, 20, 30, 40])

    def test_resolve_role_ids_adds_base_employee_role_when_rpaemployee_yes(self) -> None:
        """Ensure users marked as RPA employees get the shared employee Discord role."""

        with tempfile.TemporaryDirectory() as temp_dir:
            mapping_path = Path(temp_dir) / "BadgesToRoles.json"
            mapping_path.write_text(
                json.dumps(
                    {
                        "EmployeeRoles": [
                            {
                                "role_id": 10,
                                "group_id": "foundation",
                                "rpaemployee": "yes",
                            }
                        ],
                        "SpecialUnits": [],
                        "MiscRoles": [],
                        "Donators": [],
                    }
                ),
                encoding="utf-8",
            )

            config_store = MagicMock(get_base_rpa_employee_role_id=MagicMock(return_value=1479388404260012092))
            mapper = BadgeRoleMapper(file_path=mapping_path, server_config_store=config_store)
            role_ids = mapper.resolve_role_ids({"foundation"})

            self.assertEqual(role_ids, [10, 1479388404260012092])

    def test_resolve_role_ids_ignores_base_employee_role_when_rpaemployee_not_yes(self) -> None:
        """Ensure the shared employee role is not granted without explicit entitlement."""

        with tempfile.TemporaryDirectory() as temp_dir:
            mapping_path = Path(temp_dir) / "BadgesToRoles.json"
            mapping_path.write_text(
                json.dumps(
                    {
                        "EmployeeRoles": [{"role_id": 10, "group_id": "foundation"}],
                        "SpecialUnits": [],
                        "MiscRoles": [],
                        "Donators": [],
                    }
                ),
                encoding="utf-8",
            )

            config_store = MagicMock(get_base_rpa_employee_role_id=MagicMock(return_value=1479388404260012092))
            mapper = BadgeRoleMapper(file_path=mapping_path, server_config_store=config_store)
            role_ids = mapper.resolve_role_ids({"foundation"})

            self.assertEqual(role_ids, [10])

    def test_resolve_role_ids_skips_base_employee_role_when_config_not_set(self) -> None:
        """Ensure no shared employee role is added when serverconfig lacks the role ID."""

        with tempfile.TemporaryDirectory() as temp_dir:
            mapping_path = Path(temp_dir) / "BadgesToRoles.json"
            mapping_path.write_text(
                json.dumps(
                    {
                        "EmployeeRoles": [
                            {"role_id": 10, "group_id": "foundation", "rpaemployee": "yes"}
                        ],
                        "SpecialUnits": [],
                        "MiscRoles": [],
                        "Donators": [],
                    }
                ),
                encoding="utf-8",
            )

            config_store = MagicMock(get_base_rpa_employee_role_id=MagicMock(return_value=None))
            mapper = BadgeRoleMapper(file_path=mapping_path, server_config_store=config_store)
            role_ids = mapper.resolve_role_ids({"foundation"})

            self.assertEqual(role_ids, [10])


    def test_get_all_mapped_role_ids_excludes_base_role_when_not_configured(self) -> None:
        """Ensure role-removal scope stays accurate when no base employee role is configured."""

        with tempfile.TemporaryDirectory() as temp_dir:
            mapping_path = Path(temp_dir) / "BadgesToRoles.json"
            mapping_path.write_text(
                json.dumps(
                    {
                        "EmployeeRoles": [{"role_id": 10, "group_id": "foundation"}],
                        "SpecialUnits": [],
                        "MiscRoles": [],
                        "Donators": [],
                    }
                ),
                encoding="utf-8",
            )

            config_store = MagicMock(get_base_rpa_employee_role_id=MagicMock(return_value=None))
            mapper = BadgeRoleMapper(file_path=mapping_path, server_config_store=config_store)
            self.assertEqual(mapper.get_all_mapped_role_ids(), {10})

    def test_get_all_mapped_role_ids_includes_all_supported_categories(self) -> None:
        """Ensure stale-role cleanup can rely on a full managed-role set."""

        with tempfile.TemporaryDirectory() as temp_dir:
            mapping_path = Path(temp_dir) / "BadgesToRoles.json"
            mapping_path.write_text(
                json.dumps(
                    {
                        "EmployeeRoles": [{"role_id": 10, "group_id": "foundation"}],
                        "SpecialUnits": [{"role_id": "20", "group_id": "special"}],
                        "MiscRoles": [{"role_id": 30, "group_id": "misc"}],
                        "Donators": [{"role_id": 40, "group_id": "donor"}],
                        # Legacy category is still supported in production configs.
                        "DonationRoles": [{"role_id": 50, "group_id": "legacy_donor"}],
                    }
                ),
                encoding="utf-8",
            )

            config_store = MagicMock(get_base_rpa_employee_role_id=MagicMock(return_value=1479388404260012092))
            mapper = BadgeRoleMapper(file_path=mapping_path, server_config_store=config_store)
            self.assertEqual(mapper.get_all_mapped_role_ids(), {10, 20, 30, 40, 50, 1479388404260012092})


if __name__ == "__main__":
    unittest.main()
