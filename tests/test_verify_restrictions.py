"""Unit tests for verification restriction storage and enforcement behavior."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from habbo_verification_core import VerifyRestrictionStore

try:
    from COGS.ServerVerifyRPA import HabboVerificationCog
    from COGS.UserVerifyRestrict import VerifyRestrictionsCog
except ModuleNotFoundError:
    HabboVerificationCog = None
    VerifyRestrictionsCog = None


class VerifyRestrictionStoreTests(unittest.TestCase):
    """Validate JSON persistence and lookups for DNH/BoS restriction lists."""

    def test_add_remove_and_lookup_usernames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = VerifyRestrictionStore(file_path=Path(temp_dir) / "JSON" / "VerifyRestrictions.json")

            self.assertTrue(store.add_username("DNH", "Siren"))
            self.assertFalse(store.add_username("dnh", "Siren"))
            self.assertTrue(store.add_username("BoS", "Danger"))

            self.assertEqual(store.get_group_for_username("Siren"), VerifyRestrictionStore.GROUP_DNH)
            self.assertEqual(store.get_group_for_username("Danger"), VerifyRestrictionStore.GROUP_BOS)
            self.assertIsNone(store.get_group_for_username("Missing"))

            self.assertTrue(store.remove_username("BoS", "Danger"))
            self.assertFalse(store.remove_username("BoS", "Danger"))
            self.assertIsNone(store.get_group_for_username("Danger"))

    def test_read_data_normalizes_invalid_json_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "JSON" / "VerifyRestrictions.json"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                json.dumps({"DNH": ["Siren", "Siren", "  "], "BoS": "bad-shape"}),
                encoding="utf-8",
            )

            store = VerifyRestrictionStore(file_path=file_path)

            self.assertEqual(store.get_all_usernames("DNH"), ["Siren"])
            self.assertEqual(store.get_all_usernames("BoS"), [])


@unittest.skipIf(VerifyRestrictionsCog is None, "discord.py is not installed in the test environment")
class VerifyRestrictionsCogCommandTests(unittest.IsolatedAsyncioTestCase):
    """Validate the combined slash commands for maintaining verification restriction lists."""

    async def test_dnh_command_add_reports_success(self) -> None:
        cog = VerifyRestrictionsCog(bot=MagicMock())
        cog.restriction_store = SimpleNamespace(
            add_username=MagicMock(return_value=True),
            _normalize_group_name=MagicMock(return_value="DNH"),
            _normalize_username=MagicMock(return_value="Siren"),
        )
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))

        await cog.dnh.callback(cog, interaction, "add", "Siren")

        interaction.response.send_message.assert_awaited_once_with(
            "✅ Added **Siren** to **DNH** verification restrictions.",
            ephemeral=True,
        )

    async def test_bos_command_remove_reports_missing_entry(self) -> None:
        cog = VerifyRestrictionsCog(bot=MagicMock())
        cog.restriction_store = SimpleNamespace(
            remove_username=MagicMock(return_value=False),
            _normalize_group_name=MagicMock(return_value="BoS"),
            _normalize_username=MagicMock(return_value="Danger"),
        )
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))

        await cog.bos.callback(cog, interaction, "remove", "Danger")

        interaction.response.send_message.assert_awaited_once_with(
            "**Danger** was not listed in **BoS**.",
            ephemeral=True,
        )


@unittest.skipIf(HabboVerificationCog is None, "discord.py is not installed in the test environment")
class HabboVerificationRestrictionEnforcementTests(unittest.IsolatedAsyncioTestCase):
    """Validate DNH and BoS enforcement helpers triggered after verification success."""

    async def test_enforce_restrictions_removes_employee_roles_for_dnh(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        user = SimpleNamespace(
            roles=[SimpleNamespace(id=1, name="Employee"), SimpleNamespace(id=2, name="VIP")],
            remove_roles=AsyncMock(),
        )
        interaction = SimpleNamespace(guild=SimpleNamespace(name="RPA"), user=user)
        cog.verify_restriction_store = SimpleNamespace(get_group_for_username=MagicMock(return_value="DNH"))
        cog.badge_role_mapper = SimpleNamespace(get_all_mapped_role_ids=MagicMock(return_value={1, 3}))

        status = await cog._enforce_restrictions_after_verification(interaction=interaction, habbo_username="Siren")

        user.remove_roles.assert_awaited_once()
        removed_roles = user.remove_roles.await_args.args
        self.assertEqual([role.name for role in removed_roles], ["Employee"])
        self.assertIn("DNH matched", status)
        self.assertIn("Employee", status)

    async def test_enforce_restrictions_bans_bos_member(self) -> None:
        cog = HabboVerificationCog(bot=MagicMock())
        user = SimpleNamespace(send=AsyncMock())
        guild = SimpleNamespace(name="RPA", ban=AsyncMock())
        interaction = SimpleNamespace(guild=guild, user=user)
        cog.verify_restriction_store = SimpleNamespace(get_group_for_username=MagicMock(return_value="BoS"))

        status = await cog._enforce_restrictions_after_verification(interaction=interaction, habbo_username="Danger")

        user.send.assert_awaited_once()
        guild.ban.assert_awaited_once_with(
            user,
            reason="Verification restriction policy: BoS user must contact Foundation before joining",
        )
        self.assertIn("BoS matched", status)
        self.assertIn("member banned from the server", status)


if __name__ == "__main__":
    unittest.main()
