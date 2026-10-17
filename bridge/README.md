# In-game bridge — design

The planner (agent/planner.py) emits steps in a fixed action vocabulary.
This layer executes them inside the running ATM10 client.

## Architecture (chosen: Option A first, B when GUIs are needed)

```
┌────────────────────── Minecraft 1.21.1 NeoForge (ATM10) ─────────────────────────┐
│  Minescript 4.x (NeoForge)       ← Python inside the client                      │
│     └─ bridge script: state dump + action executor, TCP/stdio to agent daemon    │
└───────────────────────────────────────────────────────────────────────────────────┘
                                  │ JSON lines
┌─────────────── agent daemon (Python, outside the game) ───────────────┐
│ loop: read state → planner (Claude) → send next action → observe      │
└────────────────────────────────────────────────────────────────────────┘
```

### Option A — Minescript + Baritone (fast to build)
- **Minescript** (NeoForge 1.21.1 jar) runs Python inside the client: player pos,
  inventory, chat, key presses, block queries.
- **Baritone** executes `goto/goto_block/mine` — bridge sends `#mine dirt` etc.
  via chat. `collect`, `kill`, `place` are Minescript key/look primitives.
- **craft/smelt**: vanilla crafting via Minescript screen-click helpers;
  recipe resolution from pack data (kubejs recipes + mod defaults) — start
  with a hardcoded early-game recipe table, replace with dumped recipe JSON.
- **quest_checkmark**: `/ftbquests change progress complete <player> <quest_id>`
  (equivalent to clicking the checkbox in the book).
- **Quest progress state**: read from the save dir
  `saves/<world>/serverdata/ftbquests/` (SNBT, reuse tools/snbt.py).

### Option B — custom NeoForge mod (needed for machine GUIs)
Small NeoForge mod exposing a localhost WebSocket:
- `GET state` → position, inventory, nearby entities, open GUI contents
- `POST action` → slot clicks in ANY container GUI (modded machines),
  item insertion/extraction, precise interaction.
This is the layer that makes Mekanism/Embers/Witch's Oven automation possible.
Estimated effort: the biggest single chunk of the project.

## Failure handling
Every action gets a timeout + verify predicate (e.g. `mine dirt 4` →
inventory delta check). On failure: retry once, then replan with the failure
in state, then `manual` escalation (chat message to the player).

## Install checklist (M1)
Instance: set `BLOCKBUDDY_MC_DIR` to your instance minecraft/ folder
(MC 1.21.1, NeoForge 21.1.x — ATM10)

1. [x] Pack installed (Prism Launcher)
2. [x] `minecraft/mods/minescript-neoforge-1.21.1-*.jar` (Minescript 4.x NeoForge build runs system Python;
       scripts live in `minecraft/minescript/`)
3. [ ] Allocate 6 GB RAM to the instance (Prism → instance → Edit → Settings → Java memory)
4. [ ] Launch, create a world, verify: `\help` (Minescript)
