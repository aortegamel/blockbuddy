# Body — built-in FakePlayer (replaces the old PlayerEngine design)

The `blockbuddy` mod spawns its own server-side FakePlayer-like entity. The
entity carries its own inventory and acts through vanilla mechanics only:

- Movement: vanilla pathfinding (`goto` x y z, `follow`, `stop`).
- Mining: nearest matching blocks broken with vanilla destroy logic, drops
  picked up into the body's own inventory (`mine <block> [n]`, modded ids
  included).
- Crafting: the existing recursive RecipeManager logic (kubejs/datapack
  recipes work, intermediates resolved automatically).
- Transfers: `give` hands loot to the player (FTB quest tasks check the
  player inventory); `take`/`store` move items between the body and nearby
  chests or machine slots (`IItemHandler`); `spawn` creates the body with no
  third-party account, character, or controller classes.
- Commands: `/blockbuddy <subcommand>` with `/agent` as a short alias.

The planner daemon issues these through the Minescript chat bridge — no new
protocol. The old `/agent run <cmd>` free-form line is gone; movement goes
through `goto`/`follow`/`stop` and mining through `mine`.

## Legacy note (avoided, not used — history only)

Earlier revisions drove a PlayerEngine companion entity (Automatone
pathfinding plus AltoClef task chains, via Player2NPC). That design is
replaced. PlayerEngine / Player2NPC (LGPL-3.0), Automatone, and AltoClef are
NOT dependencies: nothing in `build.gradle` references them, no jars ship in
`libs/`, and no source imports their packages. They appear in this file only
as history so nobody re-adds them.
