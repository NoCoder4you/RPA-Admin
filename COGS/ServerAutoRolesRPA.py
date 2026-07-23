import asyncio
from datetime import datetime, timedelta, timezone

import discord
import aiohttp
import json
import os
from pathlib import Path
from discord.ext import commands, tasks


BASE_DIR = Path(__file__).resolve().parent.parent
JSON_DIR = BASE_DIR / "JSON"


class AutoRoleUpdater(commands.Cog):
    """Synchronize roles while conservatively sharing Habbo API capacity."""

    # Reserve most of the shared Habbo API capacity for the user-facing /verify
    # command. Background and join-time role maintenance can safely run slower;
    # verification cannot, because a challenge is both interactive and expiring.
    UPDATE_INTERVAL_MINUTES = 10
    # A 150-request ceiling is a moderate reduction from the old 300-request
    # limit. Combined with profile-response reuse below, it retains capacity to
    # fully sync up to 75 members per cycle while leaving room for /verify.
    MAX_HABBO_REQUESTS_PER_INTERVAL = 150
    MIN_HABBO_REQUESTS_PER_INTERVAL = 60
    SUCCESS_REQUESTS_BEFORE_INCREASE = 100
    RECOVERY_REQUEST_STEP = 15
    RATE_LIMIT_DECREASE_FACTOR = 0.5
    RATE_LIMIT_COOLDOWN_MINUTES = 30

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

        self._habbo_rate_limited_until = None
        self._habbo_request_lock = asyncio.Lock()
        self._last_habbo_request_started_at = None
        self._habbo_request_target = self.MAX_HABBO_REQUESTS_PER_INTERVAL
        self._successful_habbo_requests = 0
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

    def _rate_limit_is_active(self):
        """Return whether a previous HTTP 429 is still in its cooldown window."""

        return (
            self._habbo_rate_limited_until is not None
            and datetime.now(timezone.utc) < self._habbo_rate_limited_until
        )

    def _start_rate_limit_cooldown(self, response):
        """Reduce throughput and honor Retry-After after a Habbo HTTP 429."""

        current_target = getattr(self, "_habbo_request_target", self.MAX_HABBO_REQUESTS_PER_INTERVAL)
        reduced_target = int(current_target * self.RATE_LIMIT_DECREASE_FACTOR)
        self._habbo_request_target = max(self.MIN_HABBO_REQUESTS_PER_INTERVAL, reduced_target)
        self._successful_habbo_requests = 0

        retry_after = response.headers.get("Retry-After")
        try:
            seconds = max(float(retry_after), 0.0)
        except (TypeError, ValueError):
            seconds = self.RATE_LIMIT_COOLDOWN_MINUTES * 60
        self._habbo_rate_limited_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)

    def _record_habbo_request_success(self):
        """Gradually restore throughput after sustained successful API responses."""

        current_target = getattr(self, "_habbo_request_target", self.MAX_HABBO_REQUESTS_PER_INTERVAL)
        if current_target >= self.MAX_HABBO_REQUESTS_PER_INTERVAL:
            self._habbo_request_target = self.MAX_HABBO_REQUESTS_PER_INTERVAL
            self._successful_habbo_requests = 0
            return

        self._successful_habbo_requests = getattr(self, "_successful_habbo_requests", 0) + 1
        if self._successful_habbo_requests >= self.SUCCESS_REQUESTS_BEFORE_INCREASE:
            self._habbo_request_target = min(
                self.MAX_HABBO_REQUESTS_PER_INTERVAL,
                current_target + self.RECOVERY_REQUEST_STEP,
            )
            self._successful_habbo_requests = 0

    def _habbo_request_interval_seconds(self):
        """Return spacing derived from the current adaptive request target."""

        target = getattr(self, "_habbo_request_target", self.MAX_HABBO_REQUESTS_PER_INTERVAL)
        return (self.UPDATE_INTERVAL_MINUTES * 60) / target

    async def _wait_for_habbo_request_slot(self):
        """Serialize Habbo request starts using the current adaptive pace."""

        # Join-time syncs and the background loop can overlap, so they must share
        # one lock and timestamp instead of each independently consuming the limit.
        async with self._habbo_request_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._last_habbo_request_started_at is not None:
                elapsed = now - self._last_habbo_request_started_at
                delay = self._habbo_request_interval_seconds() - elapsed
                if delay > 0:
                    await asyncio.sleep(delay)
            self._last_habbo_request_started_at = loop.time()

    async def fetch_habbo_user(self, session: aiohttp.ClientSession, habbo_name: str):
        await self._wait_for_habbo_request_slot()
        url = f"https://www.habbo.com/api/public/users?name={habbo_name}"
        async with session.get(url) as response:
            if response.status == 429:
                self._start_rate_limit_cooldown(response)
                return None
            if response.status != 200:
                return None
            self._record_habbo_request_success()
            return await response.json()

    async def fetch_habbo_groups(self, session: aiohttp.ClientSession, habbo_id: str):
        await self._wait_for_habbo_request_slot()
        url = f"https://www.habbo.com/api/public/users/{habbo_id}/groups"
        async with session.get(url) as response:
            if response.status == 429:
                self._start_rate_limit_cooldown(response)
                return []
            if response.status != 200:
                return []
            self._record_habbo_request_success()
            return await response.json()

    @tasks.loop(minutes=UPDATE_INTERVAL_MINUTES)
    async def update_roles_task(self):
        self.roles_data = self.load_roles_data()
        self.verified_users = self.load_server_data()

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            print("Guild not found.")
            return

        # Do not begin another batch while Habbo has asked this process to back off.
        if self._rate_limit_is_active():
            return

        async with aiohttp.ClientSession() as session:
            requested_member_count = 0
            for user_data in self.verified_users:
                # Stop the current batch immediately after either endpoint returns 429.
                if self._rate_limit_is_active():
                    break
                try:
                    user_id = int(user_data["discord_id"])
                    habbo_name = user_data["habbo_username"]
                except (KeyError, TypeError, ValueError):
                    continue

                member = guild.get_member(user_id)
                if not member:
                    continue

                if requested_member_count:
                    await asyncio.sleep(self.REQUEST_DELAY_SECONDS)
                requested_member_count += 1

                user_json = await self.fetch_habbo_user(session, habbo_name)
                if not user_json:
                    continue

                habbo_id = user_json.get("uniqueId")
                if not habbo_id:
                    continue

                groups_data = await self.fetch_habbo_groups(session, habbo_id)
                # An empty list caused by 429 is not proof that the user left every
                # group; abort before it can incorrectly remove their Discord roles.
                if self._rate_limit_is_active():
                    break

                added_roles, removed_roles = await self.assign_roles(
                    member=member,
                    groups_data=groups_data,
                    guild=guild,
                    habbo_name=habbo_name,
                    session=session,
                    # Reuse the profile response instead of spending another
                    # Habbo request solely to read the employee motto.
                    profile_motto=str(user_json.get("motto", "")),
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

    async def assign_roles(
        self,
        member,
        groups_data,
        guild,
        habbo_name=None,
        session=None,
        profile_motto=None,
    ):
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

        motto = str(profile_motto or "")
        if profile_motto is None and habbo_name and session:
            # Preserve compatibility for direct callers that do not already
            # have a profile response, while normal syncs avoid this request.
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
                profile_motto=str(user_json.get("motto", "")),
            )

    @update_roles_task.before_loop
    async def before_update_roles_task(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(AutoRoleUpdater(bot))
