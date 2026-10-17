# BlockBuddy

BlockBuddy is a Minecraft companion for **All the Mods 10 (ATM10)**.
The target is Minecraft **1.21.1**, NeoForge **21.1.x**, and **JDK 21**.
The project derives from [minemind](https://github.com/Boyan253/minemind).

Python reads chat, classifies requests with an OpenAI-compatible model, and sends commands to the BlockBuddy mod.
The mod supplies a server-side companion with an inventory, movement, mining, crafting, and item transfer commands.
Python owns automatic gearing. The Java mod does not run a second automatic gearing loop.
The automatic gearing sequence uses `#minecraft:logs` and vanilla recipes.
Pack-specific recipes and behavior need the final ATM10 check below.

## Runtime

```text
OpenAI-compatible model
          ^
          | Python classification and dialogue
agent/hermes_bridge.py
          | HTTP 127.0.0.1:48751: /health, /call
          | TCP  127.0.0.1:48750: JSON lines
bridge/minescript/agent_bridge.py inside Minescript
          | game state, chat, /blockbuddy commands
blockbuddy NeoForge mod
```

**Start `agent/hermes_bridge.py` for the primary runtime.**
`agent/daemon.py` is an alternative quest planner, not a second required service.
Never run both processes together. Both bind TCP port `48750`.
The Hermes bridge also exposes HTTP port `48751`.

The name “Hermes bridge” identifies the local Python process.
It does not require the optional wiki gateway.
See [bridge/README.md](bridge/README.md) for the protocol and HTTP restrictions.

## Install and start

Requirements:

- An ATM10 instance with Minecraft 1.21.1 and NeoForge 21.1.x.
- Python 3.10 or later for the bridge and tools.
- JDK 21 for the Java build and development runs.
- A compatible [Minescript](https://minescript.net) NeoForge 1.21.1 jar.
- An OpenAI-compatible model endpoint. The default endpoint uses local [opencode](https://opencode.ai).

1. Build the mod from `blockbuddy-mod/`:

   ```bash
   ./gradlew build
   ```

2. Copy the BlockBuddy jar from `blockbuddy-mod/build/libs/` into the instance's `mods/` directory.
3. Copy the compatible Minescript jar into the same `mods/` directory.
4. Copy `.env.example` to `.env` in the repository root:

   ```bash
   cp .env.example .env
   ```

5. Set the model endpoint and model name in `.env`.
6. Install the Minescript bridge from the repository root:

   ```bash
   export BLOCKBUDDY_MC_DIR="/path/to/instance/minecraft"
   python tools/install_bridge.py
   ```

7. Start the Python runtime from the repository root:

   ```bash
   python agent/hermes_bridge.py
   ```

8. Open a world in the Minecraft instance.
9. If the bridge does not connect automatically, enter `\agent_bridge` in game chat.
10. Spawn a companion with `/blockbuddy spawn Alex`.
11. Enter a request such as `follow me` or `mine 4 dirt` in chat.

On multiplayer servers, `/blockbuddy spawn` and `/blockbuddy voice` require permission level 2.
Singleplayer permits these commands.

The installer copies the script into the instance's `minescript/` directory.
It adds `autorun[*]=agent_bridge` only when no `autorun[*]` entry exists.
An existing autorun entry remains unchanged. In that case, start the bridge manually or edit the Minescript configuration yourself.
Use `/blockbuddy stop` to stop the companion's current work.

The runtime renews `/blockbuddy brain on` every 30 seconds.
The Java event-speech lease expires after 1,200 ticks without renewal.
This equals 60 seconds at 20 ticks per second.
This lease prevents duplicate event dialogue while Python controls it.

### Model configuration

`agent/llm.py` reads `.env` without a third-party dotenv package.
Exported environment variables override `.env` values.
The primary settings are:

| Variable | Purpose |
|---|---|
| `BLOCKBUDDY_BASE_URL` | OpenAI-compatible API base. Default: `http://localhost:4096/v1`. |
| `BLOCKBUDDY_MODEL` | Model name. Default: `opencode-default`. |
| `BLOCKBUDDY_API_KEY` | Optional bearer token for the model endpoint. |
| `BLOCKBUDDY_FALLBACK_CHAIN` | Optional comma-separated fallback model names. |
| `BLOCKBUDDY_MC_DIR` | Minecraft instance directory for installation and pack tools. |

The model client sends `POST {base}/chat/completions`.
Start your endpoint separately. For the default local endpoint, use `opencode serve`.
Do not commit `.env` or API keys.

### Dialogue, memory, and optional audio

Chat is the primary dialogue channel. It does not require an audio engine.
Dialogue context includes weather, time, held items, current work, health, nearby hostiles, and the current event.
The runtime stores bounded companion facts in `data/memory.json`, with at most 20 facts.
`agent/soul.md` defines the dialogue persona. These local files are not Minecraft world saves.

The alternative quest daemon uses separate local files:

- `data/quest_graph.json`: generated quest definitions and dependencies.
- `data/progress_ledger.json`: recorded quest completion IDs.
- `data/base_sp.json` or `data/base_mp.json`: surveyed storage and base notes.
- `data/session_log.jsonl`: appended session messages.

These generated files depend on the selected instance. They do not replace the pack's own quest saves.

Optional local Kokoro playback runs on the Python host through `kokoro-onnx` and `sounddevice`.
Install these packages only when you want host audio:

```bash
python -m pip install kokoro-onnx sounddevice
```

Set these values in `.env`:

```dotenv
BLOCKBUDDY_TTS=1
BLOCKBUDDY_KOKORO_MODEL=/absolute/path/to/kokoro-v1_0.onnx
BLOCKBUDDY_KOKORO_VOICES=/absolute/path/to/voices-v1.0.bin
BLOCKBUDDY_KOKORO_VOICE=af_heart
BLOCKBUDDY_KOKORO_SPEED=1.0
```

Supply the model and voice files yourself. The runtime does not download them.
Audio requires working host output and any native libraries that the audio packages require.
Audio is off by default.

The runtime sends chat before it attempts audio.
`/blockbuddy voice` explains the Python option. It does not enable Java playback.
The normal Python tests and headless Java checks do not need Kokoro or audio hardware.
The Java mod does not download generated speech. It does not substitute a villager sound for speech.

Host playback does **not** guarantee positional audio at the companion's location.

### Alternative quest planner

For the legacy quest loop, build the FTB Quests graph from the configured pack:

```bash
python tools/build_quest_graph.py
```

Stop `agent/hermes_bridge.py` before you start the alternative:

```bash
python agent/daemon.py
```

The daemon uses the extracted graph to plan quest work.
Do not interpret the primary chat runtime as proof that every ATM10 quest, machine GUI, or reward claim works automatically.
General machine GUI automation and automatic quest reward claims are not implemented.
Pack integration and actual audio quality require external checks.

## Testing: six layers

The repository separates deterministic checks from live model and Minecraft checks.
Commands below describe how to run each layer. They do not claim that this checkout passed them.

### 1. Python unit tests

Run from the repository root:

```bash
python -m unittest discover -s tests -v
```

The suite uses the Python standard library, `FakeGame`, and `FakeLLM`.
It checks bridge behavior, actions, daemon logic, quests, and dialogue direction without Minecraft or a live model.

### 2. Socket and HTTP integration

The Python suite also connects the real bridge shim to a fake Minescript peer over local sockets.
These checks exercise JSON-line transport, HTTP requests, disconnection, and reconnection.
They do not start Minecraft or test Minescript's game API.

### 3. Chat simulator and model evaluation

Start the terminal simulator without a live model:

```bash
python tools/sim_chat.py --fake-llm
```

Enter requests in its REPL, a terminal that reads commands interactively.
Inject game events with these commands:

```text
/event night
/event hurt
/event crafted stick 4
```

The simulator supplies fake game state. It does not prove real mining, recipe execution, or movement.
Run the evaluators separately to measure the configured live model:

```bash
python tools/eval_classifier.py
python tools/eval_voice.py
```

The classifier evaluator uses 52 cases. It prints mismatches and a final `Score: n/52 (p%)`.
It exits with status 1 when any case fails.
Use `python tools/eval_classifier.py --fake-llm` for an offline smoke check.
The offline classifier uses independent keyword rules. It does not measure model quality.

The voice evaluator uses 32 situations.
It writes reports named `voice-eval-<UTC timestamp>.json` in `data/voice_eval/`.
Each report includes model metadata, scenario inputs, generated lines, deterministic checks, and judge scores with notes.

Use `--output-dir` to select another report directory.
Use `python tools/eval_voice.py --fake-llm` for an offline smoke check.
The real evaluator uses the configured model to generate lines and judge them.
These evaluators use a real LLM by default. They can consume API quota.

The voice evaluator checks generated dialogue, not acoustic quality or positional playback.
The evaluator disables audio playback even when it uses the real model.
The voice evaluator exits with status 0 only when deterministic checks pass and the judge reports no error.
Judge scores do not impose an exit threshold. A successful exit does not establish dialogue quality.

### 4. Headless NeoForge GameTests

Use JDK 21. Run from `blockbuddy-mod/`:

```bash
./gradlew build runGameTestServer
```

GameTests use an empty test structure and a server without a graphical client.
They exercise companion names, mining cancellation, crafting, item giving, and state.
They do not load the full ATM10 pack or a Minescript client.
A server check cannot establish client rendering, audio playback, or pack compatibility.

### 5. Development client and IDE HotSwap

For an interactive development client, put a compatible Minescript NeoForge 1.21.1 jar in `blockbuddy-mod/libs/`.
Keep this local jar out of version control.
Run from `blockbuddy-mod/`:

```bash
./gradlew runClient
```

Point `BLOCKBUDDY_MC_DIR` at `blockbuddy-mod/run` before you run the bridge installer.
Start the Python runtime separately.
This development instance does not include ATM10 unless you install the pack yourself.

For method-only changes, import `blockbuddy-mod/` as a Gradle project in IntelliJ IDEA.
Select JDK 21 for the project and Gradle.
Start the NeoForge client configuration with the IDE debugger.
Compile the changed Java class while the debugger remains attached.
Use the IDE's Reload Changed Classes action to apply standard JVM HotSwap.
Standard HotSwap supports method-body edits. Restart the client after changes to fields, method signatures, registries, or resources.

### 6. Continuous integration

[The test workflow](.github/workflows/test.yml) runs on pushes and pull requests.
It runs Python unittest discovery and the Java build with `runGameTestServer`.
The Java job uses Temurin 21.
Neither job starts a graphical Minecraft client or calls a real LLM.
CI does not need the local Minescript jar or optional audio packages.

## Final ATM10 manual check

After the automated layers, make one final check in a real ATM10 world.
This check remains manual. This documentation does not claim that it ran.

1. Install the newly built BlockBuddy jar and the compatible Minescript jar in ATM10.
2. Install the bridge into that instance.
3. Start only `agent/hermes_bridge.py`.
4. Open a world with disposable test resources.
5. Check `/health` for `"bridge": "connected"`.
6. Spawn a companion with a non-default name.
7. Request follow, mining, crafting, item giving, and stop through chat.
8. Check that inventory and game state match the reported results.
9. Check dialogue after a night, injury, or crafting event.
10. If local Kokoro is enabled, listen for host playback.
11. Record the pack version, model, failures, and observed results.

This check covers pack recipes, client integration, and real behavior that fake peers and standalone GameTests cannot establish.
It does not establish compatibility with every ATM10 machine or quest.

## Credits and license

BlockBuddy uses [Minescript](https://minescript.net), [NeoForge](https://neoforged.net), and optional [FTB Quests](https://github.com/FTBTeam/FTB-Quests) data.
The repository does not require Baritone, PlayerEngine, or Player2NPC.

See [LICENSE](LICENSE) and [NOTICE](NOTICE) for the repository license and attribution.
External mods, model files, and optional audio packages retain their own licenses.
