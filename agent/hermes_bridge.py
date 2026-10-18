"""Hermes bridge shim — Hermes owns 127.0.0.1:48750, daemon.py retires.

The in-game Minescript bridge connects HERE as its client (same JSON-lines
protocol daemon.py spoke: {"id": N, "op": ...} -> {"id": N, "ok": ..., ...}).

Hermes drives Minecraft over HTTP on 127.0.0.1:48751:
  POST /call {"op": "chat", "text": "/blockbuddy mine #minecraft:logs 8"}
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

_lock = threading.Lock()  # Connection state only. Never hold this during recv.
_request_lock = threading.Lock()
_conn = None
_buf = b""
_next_id = 1


def log(msg):
    print(f"[hermes-bridge] {msg}", flush=True)


def _close_socket(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    sock.close()


def _accept_loop(srv, stop_event=None):
    global _conn, _buf
    while stop_event is None or not stop_event.is_set():
        try:
            sock, _ = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        sock.settimeout(1.5)
        try:
            peek = sock.recv(4, socket.MSG_PEEK)
        except socket.timeout:
            peek = None
        except OSError:
            sock.close()
            continue
        if peek == b"":
            sock.close()
            continue
        methods = (b"GET ", b"POST", b"HEAD", b"PUT ", b"OPTI", b"DELE", b"PATC", b"CONN", b"TRAC")
        if peek and any(method.startswith(peek) or peek.startswith(method) for method in methods):
            sock.close()
            continue
        sock.settimeout(15)
        with _lock:
            old, _conn, _buf = _conn, sock, b""
        if old is not None:
            _close_socket(old)
        log("bridge connected")


def call(op, **kwargs):
    """Send one request. Never replay a request after a reconnect."""
    global _next_id, _buf, _conn
    with _request_lock:
        with _lock:
            conn, buf = _conn, _buf
            if conn is None:
                return False, "bridge not connected — run \\agent_bridge in-game"
            req_id = _next_id
            _next_id += 1
        payload = json.dumps({**kwargs, "id": req_id, "op": op}).encode() + b"\n"
        try:
            conn.sendall(payload)
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    raise ConnectionError("bridge closed")
                buf += chunk
                if len(buf) > 1048576:
                    raise ValueError("bridge response exceeds 1 MiB")
            line, buf = buf.split(b"\n", 1)
            resp = json.loads(line)
            if (not isinstance(resp, dict) or type(resp.get("id")) is not int
                    or resp["id"] != req_id or type(resp.get("ok")) is not bool):
                raise ValueError("invalid bridge response identity or shape")
            if ("result" if resp["ok"] else "error") not in resp:
                raise ValueError("bridge response has no result or error")
            with _lock:
                if _conn is not conn:
                    raise ConnectionError("bridge replaced")
                _buf = buf
            if not resp["ok"]:
                return False, str(resp.get("error", "bridge operation failed"))
            return True, resp.get("result")
        except (ConnectionError, OSError, ValueError, UnicodeError, RecursionError) as exc:
            with _lock:
                if _conn is conn:
                    _conn, _buf = None, b""
            _close_socket(conn)
            return False, f"bridge dropped ({exc}) — reconnect with \\agent_bridge"


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
        if self.headers.get("Origin") is not None:
            self._send(403, {"ok": False, "error": "Origin headers are not allowed"})
            return
        if self.headers.get_content_type() != "application/json":
            self._send(415, {"ok": False, "error": "Content-Type must be application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 1 <= length <= 1048576:
                raise ValueError("Content-Length must be between 1 and 1048576")
            req = json.loads(self.rfile.read(length))
            if not isinstance(req, dict):
                raise ValueError("request must be a JSON object")
            op = req.pop("op", None)
            req.pop("id", None)
            if not isinstance(op, str) or not op.strip():
                raise ValueError("op must be a nonblank string")
        except (ValueError, UnicodeError, RecursionError) as exc:
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
_CLASSIFIER_SYSTEM = (
    "You classify one Minecraft chat line into a JSON command. "
    '{"intent": one of follow, stop, wait, mine, craft, give, take, store, '
    'goto, protect, gear, status, unknown; '
    '"item": block or item id or null; "count": number or null; '
    '"x": number or null; "y": number or null; "z": number or null}. '
    "Use #minecraft:logs for wood. Keep the exact requested block: "
    "cobblestone is minecraft:cobblestone and stone is minecraft:stone. "
    "Counts must be integers from 1 to 64. "
    "'tools', 'gear', 'get tools', 'i need tools' -> gear. "
    "'where are you', 'how are you', 'are you ok' -> status. "
    "'wait here' -> stop. 'mine 8 dirt' -> mine dirt 8. 'mine 16 cobblestone' -> mine minecraft:cobblestone 16. "
    "'mine8dirt' -> mine dirt 8. 'craft 4 sticks' -> craft stick 4. "
    "'give me your sword' -> give sword 1. "
    "'go to 100 64 -20' -> goto. 'protect me' -> protect. "
    "'stop guarding' -> protect. 'hello' -> unknown."
)
_HOSTILES = ("zombie", "skeleton", "spider", "creeper", "enderman", "witch",
             "drowned", "husk", "stray", "pillager", "phantom", "slime",
             "magma", "blaze", "ghast", "piglin", "hoglin", "warden",
             "silverfish", "cave_spider")
_CLARIFY = ("not sure what you mean — try 'follow', 'mine dirt 8', "
            "'gear up', or 'status'?")

def _player_message(message, player_name):
    """Accept only chat from the connected player."""
    if not isinstance(message, str) or not isinstance(player_name, str) or not player_name:
        return None
    if re.search(r"\[(?:blockbuddy|agentstate|bbevent|baritone)\]", message, re.I):
        return None
    match = re.fullmatch(r"<([^<>]+)> (.+)", message)
    if match and match[1] == player_name:
        return match[2]
    return None


def _is_own(message):
    """Recognize protocol markers without assuming the buddy's name."""
    return bool(re.search(r"\[(?:blockbuddy|agentstate|bbevent|baritone)\]", str(message), re.I))

def _bc(bridge_call, op, **kwargs):
    """Normalize bridge results: hermes call() returns (ok, result),
    daemon BridgeClient returns the result (or None)."""
    ret = bridge_call(op, **kwargs)
    if isinstance(ret, tuple) and len(ret) == 2:
        return bool(ret[0]), ret[1]
    return ret is not None, ret

_GEAR_WORDS = frozenset({"gear", "tools", "tool", "pickaxe", "sword"})
_ITEM = re.compile(r"(?:[a-z0-9_.-]+:)?[a-z0-9_./-]+")


def _integer(value, low, high):
    if isinstance(value, bool):
        raise ValueError("a boolean is not a count")
    if isinstance(value, str):
        if not re.fullmatch(r"-?\d+", value):
            raise ValueError("expected an integer")
    elif not isinstance(value, (int, float)):
        raise ValueError("expected an integer")
    number = int(value)
    if number != float(value) or not low <= number <= high:
        raise ValueError("integer is outside the supported range")
    return number


def _validated_intent(parsed):
    if not isinstance(parsed, dict):
        return {"intent": "unknown"}
    intent = str(parsed.get("intent", "unknown")).strip().lower()
    out = {"intent": intent if intent in INTENTS else "unknown"}
    item = parsed.get("item")
    if item is not None:
        if not isinstance(item, str):
            raise ValueError("item must be a string")
        item = item.strip()
        if item in ("#logs", "#minecraft:logs"):
            item = "#minecraft:logs"
        elif not _ITEM.fullmatch(item):
            raise ValueError("invalid item selector")
        out["item"] = item
    for key in ("count", "x", "y", "z"):
        if parsed.get(key) is not None:
            bounds = (1, 64) if key == "count" else (-30000000, 30000000)
            out[key] = _integer(parsed[key], *bounds)
    return out


def _classify(llm_complete, text):
    """Classify one chat line. Reject unsafe model output."""
    if not isinstance(text, str) or re.search(r"\[(?:agentstate|bbevent|blockbuddy)\]", text, re.I):
        return {"intent": "unknown"}
    if re.fullmatch(r"(hi|hey|hello|yo|sup|howdy|good (morning|evening|day|night)|greetings)[!., ]*", text.lower().strip()):
        return {"intent": "unknown"}
    if re.fullmatch(r"(?:please )?stop guarding[!. ]*", text.lower().strip()):
        return {"intent": "protect"}
    if re.fullmatch(r"(?:please )?(?:stop|wait)(?: (?:here|now|please|mining|crafting|everything|that|gearing up))?[!. ]*", text.lower().strip()):
        return {"intent": "stop"}
    status_question = re.fullmatch(r"(?:(?:where|how) are you|are you (?:ok|okay|all right))[?!. ]*", text.lower().strip())
    command_words = re.search(r"\b(?:follow|mine|craft|give|take|store|go|come|stay|wait|stop|protect|guard|gear|tools?|pickaxe|sword|status|health)\b", text, re.I)
    if text.strip().endswith("?") and not status_question and not command_words:
        return {"intent": "unknown"}
    try:
        raw = llm_complete(_CLASSIFIER_SYSTEM, f"Chat line: {json.dumps(text)}\nReturn JSON only.", max_tokens=100, temperature=0)
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            match = re.search(r"\{.*\}", str(raw), re.DOTALL)
            parsed = json.loads(match[0]) if match else None
        return _validated_intent(parsed)
    except Exception:
        return {"intent": "unknown"}


class _BridgeAdapter:
    def __init__(self, bridge_call):
        self.call = bridge_call

    def echo(self, text):
        return _bc(self.call, "echo", text=f"[blockbuddy] {text}")


def _agent_state(bridge_call, timeout=10, cancel_event=None):
    """Use the Actions protocol parser for every companion query."""
    try:
        if __package__:
            from .actions import Actions
        else:
            from actions import Actions
        return Actions(_BridgeAdapter(bridge_call), lambda: set(), log).agent_state(
            timeout=timeout, cancel_event=cancel_event)
    except Exception:
        return None


def _report(say_fn, director, text, kind="failed", **fields):
    if director is not None:
        director.result({"type": kind, "ok": kind != "failed", "fallback": text, **fields})
    else:
        say_fn(text)


def _inv(state):
    raw = state.get("inventory", {}) if isinstance(state, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _count(state, item):
    return sum(n for key, n in _inv(state).items()
               if key == item or (":" not in key and item == "minecraft:" + key)
               if isinstance(n, (int, float)) and not isinstance(n, bool))


def _has_buddy(state):
    """A missing companion reads as None or {"agent": None} on the wire."""
    if state is None:
        return False
    if isinstance(state, dict) and "agent" in state and state["agent"] is None:
        return False
    return isinstance(state, dict)


def _coords(pos):
    try:
        if isinstance(pos, dict):
            return float(pos["x"]), float(pos["y"]), float(pos["z"])
        if isinstance(pos, (list, tuple)) and len(pos) == 3:
            return float(pos[0]), float(pos[1]), float(pos[2])
    except (TypeError, ValueError, KeyError):
        return None
    return None


def _proximity(player_pos, buddy_pos):
    """Euclidean distance plus a 32-block nearby flag; (None, None) unknown."""
    first, second = _coords(player_pos), _coords(buddy_pos)
    if first is None or second is None:
        return None, None
    distance = ((first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2
                + (first[2] - second[2]) ** 2) ** 0.5
    return distance, distance <= 32


def _planks_total(state):
    return sum(n for key, n in _inv(state).items()
               if str(key).endswith("_planks")
               if isinstance(n, (int, float)) and not isinstance(n, bool))


def _planks_candidates(log_item):
    """Vanilla-rule guess first, then the log's own namespace and bare path."""
    bare = str(log_item).split(":")[-1]
    wood = bare.removesuffix("_log").removesuffix("_stem").removeprefix("stripped_")
    namespace = str(log_item).split(":")[0] if ":" in str(log_item) else "minecraft"
    ordered = [f"minecraft:{wood}_planks"]
    if namespace != "minecraft":
        ordered.append(f"{namespace}:{wood}_planks")
    ordered.append(f"{wood}_planks")
    seen, out = set(), []
    for candidate in ordered:
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out



_GEAR_LOCK = threading.Lock()
_GEAR_COMMAND_LOCK = threading.Lock()
_GEAR_CANCEL = threading.Event()


def _cancel_gear():
    _GEAR_CANCEL.set()
    with _GEAR_COMMAND_LOCK:
        pass


def _gear_run(bridge_call, say_fn, cancel_event=None, director=None, idle=False):
    """Observe each step before issuing the next command."""
    cancel = cancel_event or _GEAR_CANCEL

    def state():
        return _agent_state(bridge_call, timeout=2, cancel_event=cancel)

    def send(command):
        with _GEAR_COMMAND_LOCK:
            if cancel.is_set():
                return False
            ok, _ = _bc(bridge_call, "chat", text=command)
            return ok

    def ensure(command, predicate, timeout=120):
        current = state()
        if not _has_buddy(current):
            raise RuntimeError("I cannot read my inventory, so I stopped gearing.")
        if predicate(current):
            return current
        if not send(command):
            raise RuntimeError("The bridge did not accept the gear command.")
        deadline = time.monotonic() + timeout
        while not cancel.is_set() and time.monotonic() < deadline:
            current = state()
            if _has_buddy(current) and predicate(current):
                return current
            cancel.wait(0.5)
        raise RuntimeError("I could not finish that gear step.")

    try:
        current = state()
        if cancel.is_set():
            return
        if not _has_buddy(current):
            return
        has_pick = lambda st: any(str(k).endswith("_pickaxe") and n > 0 for k, n in _inv(st).items())
        has_sword = lambda st: any(str(k).endswith("_sword") and n > 0 for k, n in _inv(st).items())
        if idle and has_pick(current) and has_sword(current):
            return
        logs = lambda st: max((n for k, n in _inv(st).items()
                               if str(k).endswith(("_log", "_stem"))), default=0) >= 4
        current = ensure("/blockbuddy mine #minecraft:logs 8", logs)
        log_item = max((k for k in _inv(current) if str(k).endswith(("_log", "_stem"))),
                       key=lambda k: _inv(current)[k])
        picked = None
        for candidate in _planks_candidates(log_item):
            before = _planks_total(current)
            if _count(current, candidate) >= 1:
                picked = candidate
                break
            if not send(f"/blockbuddy craft {candidate} 16"):
                raise RuntimeError("The bridge did not accept the gear command.")
            current = state()
            if not _has_buddy(current):
                raise RuntimeError("I cannot read my inventory, so I stopped gearing.")
            if _planks_total(current) > before or _count(current, candidate) >= 1:
                picked = candidate
                break
        if picked is None:
            raise RuntimeError(f"I could not craft planks from {log_item}.")
        ensure("/blockbuddy craft minecraft:stick 8", lambda st: _count(st, "minecraft:stick") >= 8, 15)
        ensure("/blockbuddy craft minecraft:wooden_pickaxe 1", has_pick, 15)
        ensure("/blockbuddy mine minecraft:stone 8", lambda st: _count(st, "minecraft:cobblestone") >= 8)
        ensure("/blockbuddy craft minecraft:stone_pickaxe 1", lambda st: _count(st, "minecraft:stone_pickaxe") >= 1, 15)
        ensure("/blockbuddy craft minecraft:stone_sword 1", lambda st: _count(st, "minecraft:stone_sword") >= 1, 15)
        if not send("/blockbuddy run follow"):
            raise RuntimeError("I could not follow you to deliver the sword.")
        ok, player = _bc(bridge_call, "state")
        if not ok or not isinstance(player, dict):
            raise RuntimeError("I cannot check your inventory for the sword.")
        before = _count(player, "minecraft:stone_sword")
        deadline = time.monotonic() + 30
        while not cancel.is_set() and time.monotonic() < deadline:
            if not send("/blockbuddy give minecraft:stone_sword 1"):
                break
            ok, player = _bc(bridge_call, "state")
            if ok and isinstance(player, dict) and _count(player, "minecraft:stone_sword") > before:
                return
            cancel.wait(1)
        raise RuntimeError("I made the gear, but I could not deliver your sword.")
    except Exception as exc:
        if not cancel.is_set():
            _report(say_fn, director, str(exc))


def _start_gear(bridge_call, say_fn, director=None, idle=False):
    global _GEAR_CANCEL
    if not _GEAR_LOCK.acquire(blocking=False):
        return False
    with _GEAR_COMMAND_LOCK:
        _GEAR_CANCEL = threading.Event()
        cancel = _GEAR_CANCEL

    def run():
        try:
            _gear_run(bridge_call, say_fn, cancel, director, idle)
        finally:
            _GEAR_LOCK.release()
    threading.Thread(target=run, daemon=True, name="gear").start()
    return True


def _handle_intent(parsed, raw, bridge_call, say_fn, llm_complete, director=None, player_name=""):
    """Issue commands once. Java events own successful command dialogue."""
    try:
        parsed = _validated_intent(parsed)
        intent = parsed["intent"]
        if director is not None and intent != "unknown":
            director.player(raw, player_name, respond=False)
        command = None
        if intent in ("mine", "craft", "give", "take", "store"):
            item = parsed.get("item")
            if not item or (item.startswith("#") and intent != "mine"):
                raise ValueError("Please name one valid item or block.")
            count = parsed.get("count", 8 if intent == "mine" else 1)
            command = f"/blockbuddy {intent} {item} {count}"
        elif intent == "follow":
            command = "/blockbuddy run follow"
        elif intent in ("stop", "wait"):
            _cancel_gear()
            command = "/blockbuddy stop"
        elif intent == "goto":
            if not all(k in parsed for k in ("x", "y", "z")):
                raise ValueError("Please give all three coordinates.")
            command = f"/blockbuddy run goto {parsed['x']} {parsed['y']} {parsed['z']}"
        elif intent == "protect":
            if set(re.findall(r"[a-z]+", raw.lower())) & {"off", "stop", "down", "no"}:
                _cancel_gear()
                command = "/blockbuddy stop"
            else:
                command = "/blockbuddy run follow"
        elif intent == "gear":
            if not set(re.findall(r"[a-z]+", raw.lower())) & _GEAR_WORDS:
                intent = "unknown"
            else:
                _start_gear(bridge_call, say_fn, director)
                return True
        elif intent == "status":
            def status():
                st = _agent_state(bridge_call)
                if not isinstance(st, dict):
                    _report(say_fn, director, "I cannot read my state right now.")
                    return
                pos = st.get("position", st.get("pos"))
                _report(say_fn, director,
                        f"My health is {st.get('health')}. My position is {pos}.",
                        "status", buddy=st)
            threading.Thread(target=status, daemon=True, name="buddy-status").start()
            return False
        if command:
            ok, error = _bc(bridge_call, "chat", text=command)
            if not ok:
                _report(say_fn, director, f"The bridge rejected that command: {error}")
        elif intent == "unknown":
            if director is not None:
                director.player(raw, player_name)
            else:
                say_fn(_CLARIFY)
    except (ValueError, TypeError, OverflowError) as exc:
        _report(say_fn, director, str(exc))
    except Exception:
        _report(say_fn, director, "I could not send that command.")
    return False

def _nearby_hostiles(state, entities):
    pos = state.get("position") if isinstance(state, dict) else None
    if not isinstance(pos, dict) or not isinstance(entities, list):
        return []
    nearby = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        kind = f"{entity.get('type', '')} {entity.get('name', '')}".lower()
        if not any(hostile in kind for hostile in _HOSTILES):
            continue
        ep = entity.get("position") or []
        try:
            distance = sum((ep[i] - pos[key]) ** 2 for i, key in enumerate(("x", "y", "z")))
        except (TypeError, IndexError, KeyError):
            continue
        if distance <= 64:
            nearby.append(entity)
    return nearby


def _guard_trip(bridge_call, last_health, state=None, entities=None):
    """Check damage and nearby monsters without inventing a destination."""
    try:
        if state is None:
            ok, state = _bc(bridge_call, "state")
            if not ok:
                return False, last_health, None
        if not isinstance(state, dict):
            return False, last_health, None
        health, pos = state.get("health"), state.get("position")
        hurt = (isinstance(last_health, (int, float)) and isinstance(health, (int, float))
                and last_health - health >= 4)
        if entities is None:
            ok, entities = _bc(bridge_call, "entities")
            if not ok:
                entities = []
        return bool(hurt or _nearby_hostiles(state, entities)), health, pos
    except Exception:
        return False, last_health, None

def _observe_loop(bridge_call, director, cache, stop_event):
    """Read slow state away from the chat loop."""
    last_health, last_guard, missing = None, 0.0, False
    while not stop_event.is_set():
        try:
            ok, player = _bc(bridge_call, "state")
            if ok and isinstance(player, dict):
                cache["player"] = player
            player = cache.get("player", {})
            ok, entities = _bc(bridge_call, "entities")
            entities = entities if ok else []
            if director is not None:
                buddy = _agent_state(bridge_call, timeout=2, cancel_event=stop_event)
                present = _has_buddy(buddy)
                if present and isinstance(buddy, dict):
                    cache["buddy"] = buddy
                elif not present:
                    cache["buddy"] = {"agent": None}
                buddy = cache.get("buddy", {})
                missing = not _has_buddy(buddy)
                distance, nearby = _proximity(player.get("position"),
                                             buddy.get("pos", buddy.get("position")))
                director.observe({**buddy, "buddy": buddy,
                                  "player_name": player.get("player_name"),
                                  "player_health": player.get("health"),
                                  "player_position": player.get("position"),
                                  "player_distance": distance,
                                  "player_nearby": nearby,
                                  "world_info": player.get("world_info", {}),
                                  "hostiles": _nearby_hostiles(player, entities),
                                  "job": "mining" if buddy.get("mining") else
                                         "following" if buddy.get("following") else "idle"})
            trip, last_health, _ = _guard_trip(bridge_call, last_health, player, entities)
            if trip and not missing and time.monotonic() - last_guard > 30 and not _GEAR_LOCK.locked():
                last_guard = time.monotonic()
                _bc(bridge_call, "chat", text="/blockbuddy run follow")
        except Exception:
            pass
        stop_event.wait(30 if missing else 5)


def nl_watch(bridge_call, llm_complete, say_fn, poll_interval=1.5,
             idle_timeout=60.0, max_polls=None, director=None, context=None):
    """Read player chat while background workers observe state and gather gear."""
    cache = context if context is not None else {}
    stop_event = threading.Event()
    try:
        ok, first = _bc(bridge_call, "chat_log", since=0)
        since = first.get("latest", 0) if ok and isinstance(first, dict) else 0
    except Exception:
        since = 0
    if context is None:
        threading.Thread(target=_observe_loop, args=(bridge_call, director, cache, stop_event),
                         daemon=True, name="buddy-observer").start()
    last_heard, last_heartbeat, idle_started = time.monotonic(), float("-inf"), False
    pending, polls = [], 0
    try:
        while max_polls is None or polls < max_polls:
            polls += 1
            time.sleep(poll_interval)
            try:
                if director is not None and cache.get("buddy") is not None \
                        and not _has_buddy(cache.get("buddy")):
                    continue
                if director is not None and cache.get("buddy") is None \
                        and context is not None and "buddy" not in cache:
                    buddy = _agent_state(bridge_call, timeout=2)
                    if not _has_buddy(buddy):
                        cache["buddy"] = buddy if buddy is not None else {"agent": None}
                        continue
                    cache["buddy"] = buddy
                if director is not None and time.monotonic() - last_heartbeat >= 30:
                    ok, _ = _bc(bridge_call, "chat", text="/blockbuddy brain on")
                    if ok:
                        last_heartbeat = time.monotonic()
                ok, logs = _bc(bridge_call, "chat_log", since=since)
                if ok and isinstance(logs, dict):
                    since = logs.get("latest", since)
                    for entry in logs.get("entries", []) or []:
                        msg = entry.get("message", "") if isinstance(entry, dict) else ""
                        if not isinstance(msg, str):
                            continue
                        if msg.startswith("[bbevent]"):
                            if director is not None:
                                director.result(msg)
                        elif not _is_own(msg) and re.fullmatch(r"<[^<>]+> .+", msg):
                            pending.append(msg)
                player_name = cache.get("player", {}).get("player_name")
                if player_name:
                    for msg in pending:
                        text = _player_message(msg, player_name)
                        if text is None:
                            continue
                        last_heard, idle_started = time.monotonic(), False
                        _handle_intent(_classify(llm_complete, text), text, bridge_call,
                                       say_fn, llm_complete, director, player_name)
                    pending.clear()
                if not idle_started and time.monotonic() - last_heard > idle_timeout:
                    idle_started = _start_gear(bridge_call, say_fn, director, idle=True)
            except Exception:
                pass
    finally:
        stop_event.set()


def _say(text):
    """Echo a fallback line without a second language-model call."""
    _bc(call, "echo", text=f"[blockbuddy] {text}")


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((BRIDGE_HOST, BRIDGE_PORT))
    srv.listen(1)
    threading.Thread(target=_accept_loop, args=(srv,), daemon=True).start()
    try:
        if __package__:
            from . import llm
            from .director import Director
        else:
            import llm
            from director import Director
        director = Director(_BridgeAdapter(call), complete_fn=llm.complete)
        threading.Thread(target=nl_watch, args=(call, llm.complete, _say),
                         kwargs={"director": director}, daemon=True, name="nl-watch").start()
        log("nl-watch started")
    except Exception as exc:
        log(f"nl-watch not started: {exc}")
    httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    httpd.daemon_threads = True
    log(f"ready: bridge :{BRIDGE_PORT} + http :{HTTP_PORT} — waiting for \\agent_bridge")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
