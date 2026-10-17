"""Hermes bridge shim — Hermes owns 127.0.0.1:48750, daemon.py retires.

The in-game Minescript bridge connects HERE as its client (same JSON-lines
protocol daemon.py spoke: {"id": N, "op": ...} -> {"id": N, "ok": ..., ...}).

Hermes drives Minecraft over HTTP on 127.0.0.1:48751:
  POST /call {"op": "chat", "text": "/blockbuddy mine kubejs:dead_log 8"}
  POST /call {"op": "state"} | {"op": "probe"} | {"op": "chat_log", "since": 0} ...
  GET  /health  -> {"bridge": "connected"|"waiting", ...}

Any bridge op passes through (chat/state/echo/chat_log/getblock/container/
probe/...). Hermes issues /blockbuddy mine|goto|craft via the chat op.
503 means the game is not connected yet — run \\agent_bridge in-game.

Stdlib only. Hermes launches this via its terminal tool; one process owns
both ports for the whole session.
"""
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BRIDGE_HOST, BRIDGE_PORT = "127.0.0.1", 48750
HTTP_HOST, HTTP_PORT = "127.0.0.1", 48751

_lock = threading.Lock()
_conn = None          # live bridge socket or None
_buf = b""
_next_id = 1


def log(msg):
    print(f"[hermes-bridge] {msg}", flush=True)


def _accept_loop(srv):
    global _conn, _buf
    while True:
        sock, _ = srv.accept()
        sock.settimeout(1.5)
        try:
            peek = sock.recv(1, socket.MSG_PEEK)
        except (socket.timeout, OSError):
            peek = b""
        if peek[:4] in (b"GET ", b"POST", b"HEAD", b"PUT ", b"OPTI"):
            # HTTP health probe, not a bridge — close, keep waiting
            try:
                sock.close()
            except OSError:
                pass
            continue
        # silent (or JSON-speaking) peer is the bridge: it waits for OUR first op
        sock.settimeout(300)  # planning pauses must not kill recv
        with _lock:
            old = _conn
            _conn = sock
            _buf = b""
        if old is not None:
            try:
                old.close()
            except OSError:
                pass
        log("bridge connected (in-game bridge connected)")


def call(op, **kwargs):
    """Forward one op to the in-game bridge; (ok, result-or-error)."""
    global _next_id, _buf, _conn
    with _lock:
        conn = _conn
        if conn is None:
            return False, "bridge not connected — run \\agent_bridge in-game"
        req_id = _next_id
        _next_id += 1
        payload = json.dumps({"id": req_id, "op": op, **kwargs}).encode() + b"\n"
        try:
            conn.sendall(payload)
            while b"\n" not in _buf:
                chunk = conn.recv(65536)
                if not chunk:
                    raise ConnectionError("bridge closed")
                _buf += chunk
            line, _buf = _buf.split(b"\n", 1)
        except (ConnectionError, OSError) as exc:
            try:
                conn.close()
            except OSError:
                pass
            _conn = None
            _buf = b""
            return False, f"bridge dropped ({exc}) — reconnect with \\agent_bridge"
    resp = json.loads(line)
    if not resp.get("ok"):
        return False, str(resp.get("error"))
    return True, resp.get("result")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            with _lock:
                up = _conn is not None
            self._send(200, {"ok": True, "bridge": "connected" if up else "waiting",
                             "bridge_port": BRIDGE_PORT, "http_port": HTTP_PORT})
        else:
            self._send(404, {"ok": False, "error": "use POST /call or GET /health"})

    def do_POST(self):
        if self.path != "/call":
            self._send(404, {"ok": False, "error": "use POST /call"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            op = req.pop("op", None)
            req.pop("id", None)
            if not op:
                raise ValueError("missing 'op'")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"ok": False, "error": f"bad request: {exc}"})
            return
        ok, result = call(op, **req)
        log(f"{op} -> {'ok' if ok else 'FAIL: ' + str(result)[:100]}")
        self._send(200 if ok else 503, {"ok": ok, "result" if ok else "error": result})


# -- NL companion loop -------------------------------------------------------
# Player talks in plain English in game chat; this loop polls chat_log,
# classifies each new player line with the LLM, and fires the matching
# /blockbuddy command. The guard reflex needs no LLM. Stdlib only.
INTENTS = frozenset({"follow", "stop", "wait", "mine", "craft", "give",
                     "take", "store", "goto", "protect", "gear", "status",
                     "unknown"})
def _soul():
    """Load Bob's soul; plain human fallback if the file is missing."""
    try:
        from pathlib import Path
        p = Path(__file__).resolve().parent / "soul.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    return "You are Bob, a helpful robot companion in Minecraft. Talk like a normal human friend: short, warm, a little playful."

_SOUL = None

def _voice(llm_complete, text):
    """Gateway-voiced line in Bob's soul; falls back to the raw text on failure."""
    global _SOUL
    if _SOUL is None:
        _SOUL = _soul()
    try:
        line = llm_complete(
            _SOUL + "\n\nReply with ONE short in-character chat line (two sentences max). No quotes, no stage directions.",
            f'Bob just did or will do: "{text}". Say it in your voice.', max_tokens=80)
        line = str(line).strip().strip('"').strip()
        return line.split("\n")[0][:200] if line else text
    except Exception:
        return text

_CLASSIFIER_SYSTEM = (
    "You classify one Minecraft chat line into a JSON command. "
    '{"intent": one of follow, stop, wait, mine, craft, give, take, store, '
    'goto, protect, gear, status, unknown; '
    '"item": block or item id or null; "count": number or null; '
    '"x": number or null; "y": number or null; "z": number or null}. '
    "Bare names are fine, the game resolves them: dirt, log, cobblestone, "
    "stick, pickaxe, sword. 'tools', 'gear', 'get tools', 'i need tools', "
    "'i dont have tools' -> gear. 'where are you', 'how are you', "
    "'are you ok' -> status. 'come here', 'stay with me' -> follow. "
    "Examples: 'follow me' -> follow; 'come here' -> follow; "
    "'wait here' -> stop; 'mine 8 dirt' -> mine dirt 8; "
    "'mine some wood' -> mine log 8; 'craft 4 sticks' -> craft; "
    "'give me your sword' -> give; 'go to 100 64 -20' -> goto; "
    "'protect me' -> protect; 'stop guarding' -> protect off; "
    "'gear up' -> gear; 'go get tools' -> gear; 'how are you' -> status; "
    "'where are you' -> status; 'hello' -> unknown."
)
_HOSTILES = ("zombie", "skeleton", "spider", "creeper", "enderman", "witch",
             "drowned", "husk", "stray", "pillager", "phantom", "slime",
             "magma", "blaze", "ghast", "piglin", "hoglin", "warden",
             "silverfish", "cave_spider")
# ponytail: fixed sleeps between steps, not inventory-verified waits;
# upgrade to agent_state-verified waits if steps fire before ingredients land.
_GEAR_STEPS = (
    ("/blockbuddy mine kubejs:dead_log 16", 20),
    ("/blockbuddy craft kubejs:flimsy_planks 8", 8),
    ("/blockbuddy mine minecraft:cobblestone 16", 20),
    ("/blockbuddy craft minecraft:stone_pickaxe 1", 8),
    ("/blockbuddy craft minecraft:stone_sword 1", 8),
    ("/blockbuddy give minecraft:stone_sword 1", 8),
)
_CLARIFY = ("not sure what you mean — try 'follow', 'mine dirt 8', "
            "'gear up', or 'status'?")

def _is_own(msg):
    ml = str(msg).lower()
    return ("[blockbuddy]" in ml or "[agentstate]" in ml
            or "<michael" in ml or "<agent" in ml or "<bob" in ml)

def _chat_reply(llm_complete, text):
    """Single-pass conversational reply in Steve's voice. Plain words, one line. Never raises."""
    global _SOUL
    if _SOUL is None:
        _SOUL = _soul()
    try:
        line = llm_complete(
            _SOUL + "\n\nThe player just said something that is NOT a command. "
            "Reply as Michael, talking like Steve from the Minecraft movie: "
            "'First we mine, then we craft!', 'Chicken jockey!', 'Flint and steel!'. "
            "Short, loud, happy, simple words a kid understands. ONE short line. "
            "If they ask for tools/gear, say you're on it and getting gear. "
            "No quotes, no stage directions.",
            f'Player said: "{text}"', max_tokens=80)
        line = str(line).strip().strip('"').strip().split("\n")[0][:200]
        return line or None
    except Exception:
        return None

def _bc(bridge_call, op, **kwargs):
    """Normalize bridge results: hermes call() returns (ok, result),
    daemon BridgeClient returns the result (or None)."""
    ret = bridge_call(op, **kwargs)
    if isinstance(ret, tuple) and len(ret) == 2:
        return bool(ret[0]), ret[1]
    return ret is not None, ret

_GEAR_WORDS = frozenset({"gear", "tools", "tool", "pickaxe", "sword"})

def _classify(llm_complete, text):
    """Tiny LLM classifier -> {intent, item?, count?, x?, y?, z?}. Never raises."""
    tl = str(text).lower()
    # ponytail: greeting + question-mark fast paths; the LLM calls everything tool-adjacent 'gear'
    if re.fullmatch(r"(hi|hey|hello|yo|sup|howdy|good (morning|evening|day|night)|greetings)[!., ]*", tl.strip()):
        return {"intent": "unknown"}
    if tl.strip().endswith("?") and not any(w in tl for w in _GEAR_WORDS | {"follow", "mine", "craft", "give", "take", "store", "go", "come", "stay", "wait", "stop", "protect", "guard"}):
        return {"intent": "unknown"}
    try:
        raw = llm_complete(_CLASSIFIER_SYSTEM, f'Chat line: "{text}"\nReturn JSON only.', max_tokens=60)
    except Exception:
        return {"intent": "unknown"}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", str(raw), re.DOTALL)
        if not m:
            return {"intent": "unknown"}
        try:
            parsed = json.loads(m.group(0))
        except ValueError:
            return {"intent": "unknown"}
    if not isinstance(parsed, dict):
        return {"intent": "unknown"}
    intent = str(parsed.get("intent", "unknown")).lower().strip()
    out = {"intent": intent if intent in INTENTS else "unknown"}
    if parsed.get("item"):
        out["item"] = str(parsed["item"]).strip()
    for key in ("count", "x", "y", "z"):
        try:
            if parsed.get(key) is not None:
                out[key] = int(float(parsed[key]))
        except (ValueError, TypeError):
            pass
    return out

def _agent_state(bridge_call, timeout=10):
    """Ask the companion for its [agentstate] JSON via chat; None on failure."""
    try:
        ok, first = _bc(bridge_call, "chat_log", since=0)
        since = first.get("latest", 0) if ok and isinstance(first, dict) else 0
        _bc(bridge_call, "chat", text="/blockbuddy state")
        deadline = time.time() + timeout
        while time.time() < deadline:
            ok, logs = _bc(bridge_call, "chat_log", since=since)
            if ok and isinstance(logs, dict):
                if logs.get("latest"):
                    since = logs["latest"]
                for entry in logs.get("entries", []) or []:
                    msg = entry.get("message", "") if isinstance(entry, dict) else ""
                    if "[agentstate]" in msg:
                        try:
                            return json.loads(msg.split("[agentstate]", 1)[1].strip())
                        except ValueError:
                            pass
            time.sleep(1.5)
    except Exception:
        pass
    return None

def _needs_gear(bridge_call):
    try:
        st = _agent_state(bridge_call)
    except Exception:
        return True
    if not isinstance(st, dict):
        return True
    inv = st.get("inventory") or {}
    return not any("pickaxe" in str(k).lower() or "sword" in str(k).lower() for k in inv)

_GEAR_LOCK = threading.Lock()

def _gear_run(bridge_call, say_fn):
    """Gear chain body: runs on its own thread, releases the lock when done."""
    try:
        say_fn("First we mine, then we craft! Gear incoming!")
        for cmd, wait_s in _GEAR_STEPS:
            try:
                _bc(bridge_call, "chat", text=cmd)
            except Exception:
                pass
            time.sleep(wait_s)
        say_fn("Flint and steel! Sword and tools for you!")
    finally:
        try:
            _GEAR_LOCK.release()
        except RuntimeError:
            pass

def _self_gear(bridge_call, say_fn):
    """Legacy entry: run the chain inline (tests). Prod path uses _gear_run + lock."""
    _gear_run(bridge_call, say_fn)

def _handle_intent(parsed, raw, bridge_call, say_fn, llm_complete):
    """Fire the matching bridge op plus an ack line. True if gear ran. Never raises."""
    def ack(text):
        try:
            say_fn(text)
        except Exception:
            pass
    try:
        intent = parsed.get("intent", "unknown") if isinstance(parsed, dict) else "unknown"
        if intent not in INTENTS:
            intent = "unknown"
        item = parsed.get("item") if isinstance(parsed, dict) else None
        item = str(item).strip() if item else None
        try:
            n = int(parsed.get("count", 0) or 0)
        except (ValueError, TypeError, AttributeError):
            n = 0
        if n <= 0:
            n = 8 if intent == "mine" else 1
        n = min(max(n, 1), 64)
        if intent in ("mine", "craft", "give", "take", "store"):
            if not item:
                ack(_CLARIFY)
                return False
            _bc(bridge_call, "chat", text=f"/blockbuddy {intent} {item} {n}")
            ack({"mine": f"on it, mining {n} {item}!",
                 "craft": f"on it, crafting {n} {item}!",
                 "give": f"coming over with {n} {item}!",
                 "take": f"on it, grabbing {n} {item}!",
                 "store": f"stowing {n} {item} away!"}[intent])
            return False
        if intent == "follow":
            _bc(bridge_call, "chat", text="/blockbuddy run follow")
            ack("staying close!")
            return False
        if intent in ("stop", "wait"):
            _bc(bridge_call, "chat", text="/blockbuddy stop")
            ack("waiting here!")
            return False
        if intent == "goto":
            try:
                x, y, z = int(parsed["x"]), int(parsed["y"]), int(parsed["z"])
            except (KeyError, ValueError, TypeError):
                ack(_CLARIFY)
                return False
            _bc(bridge_call, "chat", text=f"/blockbuddy run goto {x} {y} {z}")
            ack(f"on my way to {x} {y} {z}!")
            return False
        if intent == "protect":
            words = set(re.findall(r"[a-z]+", str(raw).lower()))
            if words & {"off", "stop", "down", "no"}:
                _bc(bridge_call, "chat", text="/blockbuddy stop")
                ack("standing down — shout if you need me!")
            else:
                _bc(bridge_call, "chat", text="/blockbuddy run follow")
                ack("guarding you!")
            return False
        if intent == "gear":
            if not any(w in re.findall(r"[a-z]+", str(raw).lower()) for w in _GEAR_WORDS):
                return _handle_intent({"intent": "unknown"}, raw, bridge_call, say_fn, llm_complete)
            if _GEAR_LOCK.locked():
                ack("already gearing up — one sec!")
                return True
            _GEAR_LOCK.acquire()
            threading.Thread(target=_gear_run, args=(bridge_call, say_fn),
                             daemon=True).start()
            return True
        if intent == "status":
            ok, st = _bc(bridge_call, "state")
            if ok and isinstance(st, dict):
                p = st.get("position") or {}
                if isinstance(p, dict) and all(k in p for k in ("x", "y", "z")):
                    where = f"at {int(p['x'])} {int(p['y'])} {int(p['z'])}"
                else:
                    where = "position unknown"
                ack(f"status: health {st.get('health')}, {where}")
            else:
                ack("no game data right now — is the bridge connected?")
            return False
        reply = _chat_reply(llm_complete, raw)
        if reply:
            _bc(bridge_call, "echo", text=f"[blockbuddy] {reply}")
        else:
            ack(_CLARIFY)
    except Exception:
        try:
            ack(_CLARIFY)
        except Exception:
            pass
        return False

def _guard_trip(bridge_call, last_health):
    """Check player damage and nearby monsters. Returns (trip, health, pos). Never raises."""
    try:
        ok, st = _bc(bridge_call, "state")
    except Exception:
        return False, last_health, None
    if not ok or not isinstance(st, dict):
        return False, last_health, None
    health, pos = st.get("health"), st.get("position")
    if (isinstance(last_health, (int, float)) and isinstance(health, (int, float))
            and last_health - health >= 4):
        return True, health, pos
    try:
        ok, ents = _bc(bridge_call, "entities")
    except Exception:
        return False, health, pos
    if ok and isinstance(ents, list) and isinstance(pos, dict):
        px, py, pz = pos.get("x", 0), pos.get("y", 0), pos.get("z", 0)
        for e in ents:
            if not isinstance(e, dict):
                continue
            kind = f"{e.get('type', '')} {e.get('name', '')}".lower()
            if not any(h in kind for h in _HOSTILES):
                continue
            ep = e.get("position") or [None, None, None]
            try:
                d = ((ep[0] - px) ** 2 + (ep[1] - py) ** 2 + (ep[2] - pz) ** 2) ** 0.5
            except (TypeError, IndexError):
                continue
            if d <= 8:
                return True, health, pos
    return False, health, pos

def nl_watch(bridge_call, llm_complete, say_fn, poll_interval=1.5, idle_timeout=60.0):
    """NL companion loop: classify player chat, guard reflex, idle self-gear. Runs forever."""
    try:
        ok, first = _bc(bridge_call, "chat_log", since=0)
        since = first.get("latest", 0) if ok and isinstance(first, dict) else 0
    except Exception:
        since = 0
    last_heard, last_health, last_guard, geared = time.time(), None, 0.0, False
    while True:
        try:
            time.sleep(poll_interval)
        except Exception:
            continue
        try:
            ok, logs = _bc(bridge_call, "chat_log", since=since)
            if ok and isinstance(logs, dict):
                if logs.get("latest"):
                    since = logs["latest"]
                for entry in logs.get("entries", []) or []:
                    msg = entry.get("message", "") if isinstance(entry, dict) else str(entry)
                    if not msg or _is_own(msg):
                        continue
                    last_heard = time.time()
                    if _handle_intent(_classify(llm_complete, msg), msg, bridge_call, say_fn, llm_complete):
                        geared = True
        except Exception:
            pass
        try:
            trip, last_health, pos = _guard_trip(bridge_call, last_health)
            if trip and time.time() - last_guard > 30:
                last_guard = time.time()
                if isinstance(pos, dict) and all(k in pos for k in ("x", "y", "z")):
                    _bc(bridge_call, "chat", text=(
                        f"/blockbuddy run goto {int(pos['x'])} "
                        f"{int(pos['y'])} {int(pos['z'])}"))
                _bc(bridge_call, "chat", text="/blockbuddy run follow")
                try:
                    say_fn("got you, sticking close!")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if not geared and time.time() - last_heard > idle_timeout \
                    and _needs_gear(bridge_call):
                _self_gear(bridge_call, say_fn)
                geared = True
        except Exception:
            pass

_LAST_VOICED = [0.0]

def _say(text):
    """Prod say_fn: fire-and-forget voice + echo. LLM voices off-loop; never blocks chat."""
    def _run():
        try:
            import llm
            voiced = _voice(llm.complete, text)
        except Exception:
            voiced = text
        try:
            call("echo", text=f"[blockbuddy] {voiced}")
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((BRIDGE_HOST, BRIDGE_PORT))
    srv.listen(1)
    threading.Thread(target=_accept_loop, args=(srv,), daemon=True).start()
    try:
        import llm
        threading.Thread(target=nl_watch, args=(call, llm.complete, _say),
                         daemon=True, name="nl-watch").start()
        log("nl-watch started (NL companion loop)")
    except Exception as exc:
        log(f"nl-watch not started: {exc}")
    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    httpd.daemon_threads = True
    log(f"ready: bridge :{BRIDGE_PORT} + http :{HTTP_PORT} — waiting for \\agent_bridge")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
