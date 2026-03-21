# AutoRoles Cog

`COGS/ServerAutoRolesRPA.py` contains the `HabboRoleUpdaterCog`, which keeps Discord roles aligned with the Habbo groups saved for already-verified members. It runs automatically in the background, supports a manual slash command for moderators, and re-applies saved access when verified users rejoin the server.

## What this cog does

The cog is responsible for three related jobs:

1. **Scheduled role syncing** for everyone stored in `JSON/VerifiedUsers.json`.
2. **Manual sync execution** through the `/uva` slash command.
3. **Join-time recovery** so previously verified members get their expected roles, nickname, and `Verified` access back when they re-enter the server.

The cog only works with users who are already recorded as verified. It does **not** perform the Habbo motto verification flow itself; that logic lives in the verification cog and the shared verification utilities.

## How the sync works

When a sync runs, the cog:

1. Loads every saved verified entry from `JSON/VerifiedUsers.json`.
2. Resolves the matching Discord member in the server.
3. Fetches that member's current Habbo profile.
4. Reads the member's Habbo group memberships.
5. Uses `JSON/BadgesToRoles.json` to map Habbo groups to Discord role IDs.
6. Adds newly-earned mapped roles.
7. Removes mapped roles the member no longer qualifies for.
8. Posts a concise audit embed when a role delta actually happened.

This means the cog acts as a **state reconciler**: Discord roles are treated as a reflection of the member's current Habbo group memberships, not as one-time grants.

## Automatic background updater

The cog starts a background task as soon as it is loaded.

- **Interval:** every 10 minutes.
- **Scope:** all saved verified users.
- **Startup behavior:** waits until the Discord bot cache is ready before the first run.

If the cog unloads, the loop is cleanly cancelled so the bot does not leave a dangling task behind.

## Manual slash command

Moderators can run:

- `/uva`

### Permission requirement

The command requires the Discord **Manage Roles** permission.

### What moderators receive

The command responds with an ephemeral summary embed that shows:

- Total verified entries scanned.
- How many users were updated.
- How many were skipped.
- How many errors occurred.

### Common skip/error cases

A user may be skipped or counted as an error when:

- Their saved entry is incomplete.
- Their Discord ID is invalid.
- They are no longer in the guild.
- Their Habbo profile cannot be fetched.
- Their Habbo profile does not expose a usable `uniqueId`.
- Habbo group lookups fail temporarily.
- The bot cannot manage one or more target roles.

## Join-time behavior for returning verified members

When a member rejoins the server, the cog checks whether their Discord ID exists in `JSON/VerifiedUsers.json`.

If the member was previously verified, the cog attempts to:

1. Reload their Habbo profile.
2. Recalculate mapped roles from current Habbo group memberships.
3. Re-add the Discord role named `Verified` if it exists and is missing.
4. Update the member nickname to the verified Habbo username.
5. Send a verification-log summary embed for staff visibility.

This makes rejoining smoother because verified users do not need to redo the full verification flow just to regain baseline access.

## Restriction handling

Before restoring a returning member's access, the cog checks `VerifyRestrictionStore`.

If the verified Habbo username is currently listed in a restriction group such as:

- `DNH`
- `BoS`

then the cog **does not** reapply verified access on join. Instead, it sends a staff log explaining that resync was skipped because the user is restricted.

## Files and data this cog depends on

### `JSON/VerifiedUsers.json`

Stores the persisted Discord-to-Habbo verification mapping. The cog uses this file as the source of truth for who should be included in background and join-time resyncs.

Expected shape:

```json
[
  {
    "discord_id": "123456789012345678",
    "habbo_username": "ExampleUser"
  }
]
```

### `JSON/BadgesToRoles.json`

Defines how Habbo group IDs map to Discord role IDs. The shared `BadgeRoleMapper` reads this file and returns the role IDs the member should currently have.

Supported categories include:

- `EmployeeRoles`
- `SpecialUnits`
- `MiscRoles`
- `Donators`
- `DonationRoles` (legacy compatibility)

### `JSON/serverconfig.json`

Provides the audit log channel ID and optional base employee role configuration used by shared mapping logic.

### `JSON/VerifyRestrictions.json`

Stores restricted Habbo usernames that should not regain verified access automatically on rejoin.

## Audit and logging behavior

The cog writes to two different staff-facing channels depending on the event.

### Audit log channel

Used for role delta notifications during syncs.

An embed is only sent when at least one managed role was added or removed. This keeps the audit channel from filling up with no-op updates.

### Fixed verification log channel

Used for join-time summaries when a stored verified member rejoins.

That embed includes:

- Member mention.
- Habbo username.
- Role sync result.
- Nickname sync result.
- Added roles.
- Removed roles.

## Role-management rules worth knowing

### Managed roles are removed when entitlement is lost

The cog does not only add roles. It also removes any mapped role that is managed by `BadgesToRoles.json` but is no longer supported by the member's current Habbo groups.

### Only mapped roles are touched

The cog computes a set of managed role IDs from the badge-role mapping configuration and only removes roles from that managed set. Unrelated Discord roles are left alone.

### The `Verified` role is special on rejoin

The `Verified` role is restored separately from Habbo badge mappings when a returning saved user rejoins. This ensures the member regains baseline verified access even if that role is not part of the badge-role mapping file.

## Failure handling

The cog is designed to fail softly in routine operational cases.

- Missing guild cache returns an empty summary rather than crashing.
- Missing channels silently skip logging.
- Habbo API failures increment error counts or skip join-time restoration.
- Discord permission issues produce failure statuses instead of uncaught exceptions.
- Missing `Verified` role results in a skipped status, not a hard failure.

## Operational checklist

If the autoroles system does not appear to work, check these items first:

1. The cog extension is loaded.
2. The bot can see the target guild.
3. `JSON/VerifiedUsers.json` contains the affected user.
4. `JSON/BadgesToRoles.json` contains the correct Habbo group → Discord role mappings.
5. `JSON/serverconfig.json` contains a valid audit log channel ID if you expect audit embeds.
6. The Discord roles still exist in the server.
7. The bot's highest role is above the roles it needs to add/remove.
8. The Habbo profile still has a valid `uniqueId` and publicly visible groups.
9. The user is not listed in `JSON/VerifyRestrictions.json` when testing rejoin restoration.

## Related code paths

If you need to trace or extend this feature, these files are the most relevant starting points:

- `COGS/ServerAutoRolesRPA.py` - autorole sync logic and join-time restoration.
- `COGS/ServerVerifyRPA.py` - initial verification flow that populates verified users.
- `COGS/VerifyRestrictionsCog.py` - moderation commands for verification restriction lists.
- `habbo_verification_core.py` - shared stores, Habbo API fetch helpers, and badge-role mapping logic.
- `tests/test_habbo_role_updater_cog.py` - focused tests for updater embed and join-time behavior.

## Short summary

The AutoRoles cog keeps verified Discord members synchronized with their Habbo state over time. It is best thought of as the maintenance layer that preserves role accuracy **after** verification has already happened.
