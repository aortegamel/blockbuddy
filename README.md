# BlockBuddy

**An autonomous AI player for _modded_ Minecraft.**

BlockBuddy reads your modpack's quest book, plans with an LLM, and spawns a
built-in server-side FakePlayer body to do the work — mining, crafting (modded
recipes included), fighting, hauling — while you keep playing. It is built for
**modpacks**, not just vanilla.

> `/blockbuddy spawn Bob` → the planner reads the pack's FTB quests → Bob walks off,
> mines the logged materials, crafts them through the modded recipe chain,
> comes back and hands you the result. Nobody touched the keyboard.

---

## Target pack: ATM9 (Forge 1.20.1)

BlockBuddy is **Forge-only**: Minecraft 1.20.1, Forge 47.x, JDK 17.
The reference pack is **All the Mods 9 (ATM9)** — the last All the Mods line
built for Forge on 1.20.1. Its successor ATM10 moved to NeoForge on
Minecraft 1.21.1, so ATM9 is the current Forge ATM target.
Sources: [ATM9 on CurseForge](https://www.curseforge.com/minecraft/modpacks/all-the-mods-9)
(Forge, 1.20.1) and [ATM10 files](https://www.curseforge.com/minecraft/modpacks/all-the-mods-10/files/all)
(NeoForge, 1.21.1).

## Why this exists

Every well-known Minecraft AI project is **vanilla-only**:

| Project | Why it can't play your modpack |
|---|---|
| Voyager, Mindcraft, mineflayer bots | Speak the vanilla protocol — can't even join a Forge server |
| Baritone-style executors | Brilliant pathing/mining, but vanilla item catalogues and Fabric-only builds |
| STEVE-1, JARVIS-VLA, pixel agents | Research models: vanilla training data, need an RTX 3090, ~5 FPS |
| AI-Player, ChatClef companions | Fabric-only, vanilla capabilities, cloud-locked brains |

Modded Minecraft — custom blocks, kubejs recipes, machine GUIs, 500-quest
progression books — is where the actually interesting automation problems
live. BlockBuddy targets exactly that gap.

## What it does today

- **Reads the whole quest book offline.** FTB Quests SNBT → dependency DAG
  (chapters, quests, item tasks, rewards). The planner always knows the exact
  frontier of available quests — no screen reading, no guessing.
- **Plans with any OpenAI-compatible brain.** Default is your local
  [opencode](https://opencode.ai) server; anything that serves
  `POST {base}/chat/completions` works. The prompt carries the world facts
  and action vocabulary, so the model plans rather than reasons from scratch
  (`BLOCKBUDDY_MODEL=opencode-default`).
- **Acts through a built-in body.** The `blockbuddy` mod spawns a server-side
  FakePlayer-like entity with its own inventory — no third-party body mod
  needed. Movement uses vanilla pathfinding; mining breaks blocks with vanilla
  destroy logic and picks up the drops:
  - `/blockbuddy mine <block> [n]` — mine **any** registered block, modded ids included
  - `/blockbuddy goto <x> <y> <z>` / `follow` / `stop` — vanilla pathfinding movement
  - `/blockbuddy craft <item> [n]` — craft **any** recipe from the game's RecipeManager
    (kubejs/datapack recipes work), resolving intermediate ingredients
    **recursively** (log → scrap → planks happens automatically)
  - `/blockbuddy take|store <item> [n]` — move items between the agent and nearby
    chests or machine slots (any `IItemHandler`)
  - `/blockbuddy give <item> [n]` — hand the loot to the player (quests check *your* inventory)
  - (`/agent` works as a short alias for `/blockbuddy`.)
- **Verifies everything.** Inventory deltas, quest-save reads, chat-response
  checks. Failed steps trigger replanning; repeatedly failing quests get set
  aside instead of looping.
- **Steer it from chat.** Type `.goal get me copper tools` in-game and the planner
  obeys; `.goal clear` returns it to autonomous questing. Progress updates and
  an end-of-session summary land in chat; every completed quest takes a screenshot.
- **Escalates gracefully.** What it can't do yet (machine GUIs, claiming
  quest rewards), it asks for in chat and waits for your `.done`.

Live-tested on Forge 1.20.1 with a 500+ quest FTB pack — the
built-in body autonomously mined modded blocks, crafted through modded recipe
chains, and progressed real quests.

## Architecture

```
┌─ your PC ────────────────────────────────────────────────────────────┐
│                                                                      │
│  daemon.py  ──plans──▶  brain: OpenAI-compatible POST                 │
│    │  ▲                  {BLOCKBUDDY_BASE_URL}/chat/completions        │
│    │  └── quest_graph.json  (built offline from the pack's           │
│    │       FTB Quests SNBT — full dependency DAG)                    │
│    ▼                                                                 │
│  TCP 127.0.0.1:48750  (JSON lines)                                   │
│    ▲                                                                 │
│ ┌──┴───────────────── Minecraft (Forge) ──────────────────────────┐  │
│ │ Minescript bridge  ── chat/state/inventory I/O                  │  │
│ │ blockbuddy mod ── /blockbuddy commands → built-in FakePlayer     │  │
│ │   body: own inventory, vanilla pathfinding + destroyBlock mining │  │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

The LLM never clicks pixels. Perception is files and registries (quest SNBT,
RecipeManager, capabilities); action is deterministic game logic. The model
only does what models are good at: deciding what to do next.

## Quick start

**Requirements:** a Forge 1.20.1 instance (ATM9), Python 3.10+, JDK 17+ (only to
build the mod), and a brain (local opencode server by default — see `.env` below).

```bash
git clone <repo-url> && cd blockbuddy

# 1. mods into your instance's mods/ folder:
#    - Minescript (Forge 1.20.1) for the chat/state bridge
#    - blockbuddy: cd blockbuddy-mod && ./gradlew build   → build/libs/*.jar

# 2. point the tools at your instance and install the bridge
export BLOCKBUDDY_MC_DIR="/path/to/instance/minecraft"
python tools/install_bridge.py

# 3. build the quest graph from your pack (FTB Quests packs)
python tools/build_quest_graph.py

# 4. run the brain, then in-game chat:  \agent_bridge
python agent/daemon.py
```

The daemon spawns the built-in body if none exists and starts working the quest
frontier. Type `/blockbuddy stop` in chat any time to halt it.

## Brain setup (`.env`)

Copy the example if one ships with your checkout, or create `.env` in the repo root:

```bash
BLOCKBUDDY_BASE_URL=http://localhost:4096/v1
BLOCKBUDDY_API_KEY=
BLOCKBUDDY_MODEL=opencode-default
HERMES_URL=
HERMES_API_KEY=
```

- Defaults assume an opencode server on localhost (`opencode serve`):
  `BLOCKBUDDY_BASE_URL` defaults to `http://localhost:4096/v1`,
  `BLOCKBUDDY_API_KEY` defaults to empty, `BLOCKBUDDY_MODEL` defaults to
  `opencode-default`.
- Any OpenAI-compatible endpoint works: set `BLOCKBUDDY_BASE_URL` to its base
  URL and `BLOCKBUDDY_MODEL` to the model name. The daemon sends
  `POST {BLOCKBUDDY_BASE_URL}/chat/completions`.
- The daemon loads `.env` via dotenv at startup; exported environment
  variables override `.env` values.

### Hermes wiki gateway (stub)

`HERMES_URL` / `HERMES_API_KEY` point at the Hermes wiki gateway, which serves
pack wiki lookups to the planner. This release only reads the two variables —
the gateway client is a stub. Leave them empty to run without wiki context.

## Status

Early alpha, moving fast. Honest state of the world:

- [x] Quest graph extraction (FTB Quests), frontier planning, LLM planner
- [x] Built-in FakePlayer body: mine (modded), craft (modded, recursive), take/store, give
- [x] Multiplayer mode (progress ledger + no-OP fallbacks)
- [x] Death detection, danger pause, verified human-assist steps
- [ ] Machine automation (insert/extract works; machine recipes next)
- [ ] Quest reward auto-claiming
- [ ] Other quest mods (HQM, Better Questing)
- [ ] Pack profiles — fully config-driven multi-pack support

## Credits

BlockBuddy stands on excellent shoulders:
[Minescript](https://minescript.net) ·
[FTB Quests](https://github.com/FTBTeam/FTB-Quests)

Avoided by design (replaced, not used): PlayerEngine / Player2NPC (LGPL-3.0),
Automatone, AltoClef, and Baritone stay out of the dependency tree so the repo
and the built jar stay MIT-only with zero downloaded jars beyond the Forge
toolchain.

## License

MIT for everything in this repo — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
No LGPL body mods are required or bundled; the only runtime mods are our own
`blockbuddy` jar plus Minescript for the chat/state bridge.
