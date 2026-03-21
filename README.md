# RPA-Admin

## Component Docs

- [AutoRoles Cog](docs/autoroles-cog.md)
- AutoInvite Cog: configure `JSON/InterlinkedRoles.json` so each `main_server_role_id` points at the linked `special_unit_server_id` that should send a unique single-use invite when that role is granted in the matching `main_server_id`. These invites stay valid until redeemed, then immediately expire because they are single-use.
