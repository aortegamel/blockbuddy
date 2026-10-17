# In-game bridge

The bridge connects the Python runtime to a Minecraft 1.21.1 NeoForge client through Minescript.
ATM10 is the reference pack. Baritone and a separate body mod are not required.
The BlockBuddy mod supplies the companion and `/blockbuddy` commands.

## Processes and ports

```text
agent/hermes_bridge.py                         primary Python runtime
  HTTP 127.0.0.1:48751                         local control API
  TCP  127.0.0.1:48750 <--- Minescript agent_bridge.py
                                      |
                                      +--- game state, chat, commands
                                      +--- BlockBuddy NeoForge companion
```

Use `agent/hermes_bridge.py` for chat classification, dialogue, and automatic gearing.
The alternative `agent/daemon.py` runs the legacy quest planner.
Never run both together. Both bind TCP port `48750`.
The Minescript script connects to whichever process owns that port.

The Java mod executes companion actions. Python owns automatic gearing.
The bridge does not supply a WebSocket service or general machine GUI automation.

Python renews `/blockbuddy brain on` every 30 seconds.
Java suppresses its event dialogue during this lease.
The lease expires after 1,200 ticks without renewal.
This equals 60 seconds at 20 ticks per second.

On multiplayer servers, `/blockbuddy spawn` and `/blockbuddy voice` require permission level 2.
Singleplayer permits these commands.

## Install

1. Install the BlockBuddy jar in your instance's `mods/` directory.
2. Install a compatible Minescript NeoForge 1.21.1 jar in the same directory.
3. Set the target instance from the repository root:

   ```bash
   export BLOCKBUDDY_MC_DIR="/path/to/instance/minecraft"
   ```

4. Copy the script with the installer:

   ```bash
   python tools/install_bridge.py
   ```

5. Start the primary Python runtime:

   ```bash
   python agent/hermes_bridge.py
   ```

6. Open a world.
7. If the bridge does not connect, enter `\agent_bridge` in chat.

The installer copies `bridge/minescript/agent_bridge.py` to `<instance>/minescript/agent_bridge.py`.
It creates a minimal Minescript configuration only when no configuration exists.
It adds `autorun[*]=agent_bridge` only when no `autorun[*]` entry exists.
It preserves an existing autorun entry.
Check your Minescript configuration if that entry starts another script.

## Local HTTP API

The primary runtime binds HTTP to `127.0.0.1:48751`, not to a public interface.
The API has no authentication or TLS. Do not expose it through a public proxy.
Local programs that can call it can issue game actions.

- `GET /health` reports the connection state.
- `POST /call` forwards one JSON operation to the Minescript bridge.
- Send `Content-Type: application/json` for POST requests. Parameters such as `charset=utf-8` are allowed.
- Do not send an `Origin` header. The runtime rejects any request that includes this header.
- Send a JSON object with a nonblank string `op`.
- Set `Content-Length` to the body length, from 1 through 1,048,576 bytes.

Check the connection:

```bash
curl http://127.0.0.1:48751/health
```

A connected response includes `"ok": true` and `"bridge": "connected"`.
A waiting response includes `"bridge": "waiting"`.
An HTTP health response alone does not establish that Minecraft connected.

Read game state:

```bash
curl -H 'Content-Type: application/json' \
  -d '{"op":"state"}' \
  http://127.0.0.1:48751/call
```

Send a companion command:

```bash
curl -H 'Content-Type: application/json' \
  -d '{"op":"chat","text":"/blockbuddy mine minecraft:dirt 4"}' \
  http://127.0.0.1:48751/call
```

The response contains `{"ok": true, "result": ...}` or `{"ok": false, "error": ...}`.
A failed bridge call returns HTTP `503`.
A successful chat call only means the bridge sent the text. Check inventory and companion state for action completion.

## TCP protocol

The game connects to TCP `127.0.0.1:48750`.
Each request and response occupies one UTF-8 JSON line:

```json
{"id": 1, "op": "state"}
{"id": 1, "ok": true, "result": {}}
```

The empty result above illustrates framing, not the actual state schema.
Responses use the matching request ID.
Errors contain `"ok": false` and an `"error"` message.
Operations include state, chat, inventory, nearby entities, block queries, container operations, and capability probes.
The runtime serializes calls over one active connection.
The Minescript script reconnects after a lost connection.

## Dialogue and audio

The Python runtime builds dialogue context from game state and events.
It stores at most 20 companion facts in `data/memory.json`.
`agent/soul.md` supplies the persona.
Chat remains the primary output when optional audio is unavailable.

Optional Kokoro playback runs on the Python host through `kokoro-onnx` and `sounddevice`.
It requires local model files and host audio support.
Set `BLOCKBUDDY_TTS=1` to enable this option.
See the [audio settings](../README.md#dialogue-memory-and-optional-audio) for model paths and package installation.

The Java mod does not download speech models or replace generated speech with villager sounds.
Host playback does not guarantee positional audio at the companion.
The voice evaluator measures dialogue text, not audible speech quality.

## Checks and limits

See the [six testing layers](../README.md#testing-six-layers) for commands and coverage.
The Python suite uses fake game peers for HTTP, TCP, action, and reconnection checks.
The simulator accepts `/event night`, `/event hurt`, and `/event crafted stick 4`.
Headless GameTests exercise Java behavior without a graphical client.
None of those layers establishes Minescript or full ATM10 integration.
Complete the final manual ATM10 check in the main README before you report pack behavior as checked.
