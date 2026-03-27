import discord
import aiohttp
import json
import os
from pathlib import Path
from discord.ext import commands, tasks


BASE_DIR = Path(__file__).resolve().parent.parent
JSON_DIR = BASE_DIR / "JSON"


class AutoRoleUpdater(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.roles_file_path = JSON_DIR / "BadgesToRoles.json"
        self.server_data_path = JSON_DIR / "VerifiedUsers.json"

        self.roles_data = self.load_roles_data()
        self.verified_users = self.load_server_data()

        self.guild_id = 1479383702499885109
        self.log_channel_id = 1481456898346713208

        self.verified_role_id = 1481444119971627208
        self.awaiting_verification_role_id = 1481443898369900667

        self.rpa_employee_role_id = 1479388404260012092

        self.update_roles_task.start()

    def cog_unload(self):
        self.update_roles_task.cancel()

    def load_roles_data(self):
        if os.path.exists(self.roles_file_path):
            try:
                with open(self.roles_file_path, "r", encoding="utf-8") as file:
                    data = json.load(file)
                    return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                print(f"Error decoding {self.roles_file_path}.")
        return {}

    def load_server_data(self):
        if os.path.exists(self.server_data_path):
            try:
                with open(self.server_data_path, "r", encoding="utf-8") as file:
                    data = json.load(file)
                    return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                print(f"Error decoding {self.server_data_path}.")
        return []

    def get_verified_entry(self, discord_id: int):
        for entry in self.verified_users:
            try:
                if int(entry.get("discord_id", 0)) == discord_id:
                    return entry
            except (TypeError, ValueError):
                continue
        return None

    async def fetch_habbo_user(self, session: aiohttp.ClientSession, habbo_name: str):
        url = f"https://www.habbo.com/api/public/users?name={habbo_name}"
        async with session.get(url) as response:
            if response.status != 200:
                return None
            return await response.json()

    async def fetch_habbo_groups(self, session: aiohttp.ClientSession, habbo_id: str):
        url = f"https://www.habbo.com/api/public/users/{habbo_id}/groups"
        async with session.get(url) as response:
            if response.status != 200:
                return []
            return await response.json()

    @tasks.loop(minutes=10)
    async def update_roles_task(self):
        self.roles_data = self.load_roles_data()
        self.verified_users = self.load_server_data()

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            print("Guild not found.")
            return

        async with aiohttp.ClientSession() as session:
            for user_data in self.verified_users:
                try:
                    user_id = int(user_data["discord_id"])
                    habbo_name = user_data["habbo_username"]
                except (KeyError, TypeError, ValueError):
                    continue

                member = guild.get_member(user_id)
                if not member:
                    continue

                user_json = await self.fetch_habbo_user(session, habbo_name)
                if not user_json:
                    continue

                habbo_id = user_json.get("uniqueId")
                if not habbo_id:
                    continue

                groups_data = await self.fetch_habbo_groups(session, habbo_id)

                added_roles, removed_roles = await self.assign_roles(
                    member=member,
                    groups_data=groups_data,
                    guild=guild,
                    habbo_name=habbo_name,
                    session=session,
                )

                if added_roles is None and removed_roles is None:
                    continue

                log_channel = guild.get_channel(self.log_channel_id)
                if log_channel:
                    embed = discord.Embed(
                        title="Roles Updated",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="User", value=member.mention, inline=False)

                    if added_roles:
                        embed.add_field(
                            name="Added Roles",
                            value="\n".join(added_roles),
                            inline=False
                        )

                    if removed_roles:
                        embed.add_field(
                            name="Removed Roles",
                            value="\n".join(removed_roles),
                            inline=False
                        )

                    await log_channel.send(embed=embed)

    async def assign_roles(self, member, groups_data, guild, habbo_name=None, session=None):
        added_roles = []
        removed_roles = []

        current_roles = {role.id for role in member.roles}
        expected_roles = set()
        valid_role_ids = {self.rpa_employee_role_id}
        categories = ["EmployeeRoles", "SpecialUnits", "MiscRoles", "Donators"]

        for category in categories:
            for role_data in self.roles_data.get(category, []):
                rid = role_data.get("role_id")
                if rid:
                    valid_role_ids.add(rid)

        group_ids = {
            g.get("id")
            for g in groups_data
            if isinstance(g, dict) and g.get("id")
        }

        employee_roles = self.roles_data.get("EmployeeRoles", [])
        matched_employee_roles = [
            rd for rd in employee_roles
            if rd.get("group_id") in group_ids
        ]

        highest_employee_role = matched_employee_roles[0] if matched_employee_roles else None

        has_rpa_employee = False

        if highest_employee_role:
            role = guild.get_role(highest_employee_role.get("role_id"))
            if role:
                expected_roles.add(role.id)

        for emp_role in matched_employee_roles:
            if str(emp_role.get("rpaemployee", "")).lower() == "yes":
                has_rpa_employee = True

        for category in ["SpecialUnits", "MiscRoles", "Donators"]:
            for role_data in self.roles_data.get(category, []):
                if role_data.get("group_id") in group_ids:
                    role = guild.get_role(role_data.get("role_id"))
                    if role:
                        expected_roles.add(role.id)

        if has_rpa_employee:
            expected_roles.add(self.rpa_employee_role_id)


        roles_to_add = expected_roles - current_roles
        roles_to_remove = (current_roles - expected_roles) & valid_role_ids
        roles_to_remove -= roles_to_add

        motto = ""
        if habbo_name and session:
            try:
                user_json = await self.fetch_habbo_user(session, habbo_name)
                if user_json:
                    motto = user_json.get("motto", "")
            except Exception:
                motto = ""

        if self.rpa_employee_role_id in roles_to_remove and "rpa" in motto.lower():
            roles_to_remove.remove(self.rpa_employee_role_id)

        if not roles_to_add and not roles_to_remove:
            return None, None

        try:
            for role_id in roles_to_add:
                role = guild.get_role(role_id)
                if role:
                    await member.add_roles(role, reason="AutoRoleUpdater: add")
                    added_roles.append(role.name)

            for role_id in roles_to_remove:
                role = guild.get_role(role_id)
                if role:
                    await member.remove_roles(role, reason="AutoRoleUpdater: remove")
                    removed_roles.append(role.name)

        except discord.Forbidden:
            print(f"Skipping {member.name} - Missing permissions to update roles.")
            return None, None
        except Exception as e:
            print(f"Unexpected error while updating roles for {member.name}: {e}")
            return None, None

        return added_roles, removed_roles

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        entry = self.get_verified_entry(member.id)
        if entry is None:
            return

        habbo_name = entry.get("habbo_username")
        if not habbo_name:
            return

        try:
            await member.edit(nick=habbo_name)
        except discord.Forbidden:
            pass

        guild = member.guild

        awaiting_role = guild.get_role(self.awaiting_verification_role_id)
        verified_role = guild.get_role(self.verified_role_id)

        if awaiting_role:
            await member.remove_roles(awaiting_role, reason="User is verified")

        if verified_role:
            await member.add_roles(verified_role, reason="User is verified")

        async with aiohttp.ClientSession() as session:
            user_json = await self.fetch_habbo_user(session, habbo_name)
            if not user_json:
                return

            habbo_id = user_json.get("uniqueId")
            if not habbo_id:
                return

            groups_data = await self.fetch_habbo_groups(session, habbo_id)

            await self.assign_roles(
                member=member,
                groups_data=groups_data,
                guild=guild,
                habbo_name=habbo_name,
                session=session,
            )

    @update_roles_task.before_loop
    async def before_update_roles_task(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(AutoRoleUpdater(bot))