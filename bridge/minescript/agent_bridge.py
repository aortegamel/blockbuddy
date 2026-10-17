r"""agent_bridge — runs INSIDE Minecraft via Minescript 4.x.

Start from in-game chat:  \agent_bridge
Connects to the agent daemon on localhost:48750 (start daemon first) and
serves JSON-lines requests: {"id": N, "op": "...", ...} -> {"id": N, "ok": true, "result": ...}

Ops include probe, state, chat, echo, chat_log, getblock, and stop.
State retains legacy fields and adds player_name and world_info.
world_info.time is day_ticks modulo 24000. daytime means time < 12000.
Unavailable player_name and world_info fields are null.
main(stop_event=None) shares its event with the chat listener.
The stop operation replies before main closes its socket and joins the listener.
"""
import json
import math
import queue
import socket
import threading
import time
from collections import deque

import minescript  # provided by the Minescript mod's embedded interpreter

HOST, PORT = "127.0.0.1", 48750

CHAT_LOG = deque(maxlen=500)
CHAT_SEQ = {"n": 0}


def _fn(name):
    return getattr(minescript, name, None)


def _item_entry(stack):
    get = (lambda k: stack.get(k)) if isinstance(stack, dict) else (lambda k: getattr(stack, k, None))
    return get("item"), get("count") or 1


def op_probe(_req):
    wanted = ["player_position", "player_orientation", "player_inventory",
              "player_health", "player_hand_items", "player_name", "world_info",
              "chat", "echo", "execute", "getblock", "player_look_at",
              "player_press_forward", "player_press_use", "player_press_attack",
              "player_press_sneak", "player_press_jump",
              "player_inventory_select_slot", "player_inventory_slot_to_hotbar",
              "entities", "player_get_targeted_block", "container_click_slot",
              "container_get_items", "screen_name", "EventQueue"]
    return {name: bool(_fn(name)) for name in wanted}


def op_state(_req):
    out = {"name": None, "player_name": None,
           "world_info": {"time": None, "daytime": None, "raining": None}}
    name = _fn("player_name")
    if name:
        try:
            out["name"] = out["player_name"] = name()
        except Exception:
            pass
    pos = _fn("player_position")
    if pos:
        x, y, z = pos()
        out["position"] = {"x": round(x, 1), "y": round(y, 1), "z": round(z, 1)}
    health = _fn("player_health")
    if health:
        try:
            out["health"] = health()
        except Exception:
            out["health"] = None
    inv = _fn("player_inventory")
    if inv:
        items = {}
        for stack in inv() or []:
            item_id, count = _item_entry(stack)
            if item_id:
                items[item_id] = items.get(item_id, 0) + int(count)
        out["inventory"] = items
    world_info = _fn("world_info")
    if world_info:
        try:
            info = world_info()
            for key in ("day_ticks", "game_ticks", "raining", "hardcore"):
                val = getattr(info, key, None) if not isinstance(info, dict) else info.get(key)
                if val is not None:
                    out[key] = val
            # https://minescript.net/docs#world_info defines day_ticks as the day-night clock.
            clock = out.get("day_ticks")
            out["world_info"]["raining"] = out.get("raining")
            if clock is not None:
                out["world_info"]["time"] = clock % 24000
                out["world_info"]["daytime"] = clock % 24000 < 12000
        except Exception:
            pass
    screen = _fn("screen_name")
    if screen:
        try:
            out["open_screen"] = screen()
        except Exception:
            pass
    return out


def op_chat(req):
    text = req["text"]
    # Minescript: chat() sends chat/commands; execute() is command-only on some versions
    if text.startswith("/") and _fn("execute"):
        _fn("execute")(text)
    else:
        _fn("chat")(text)
    return True


def op_echo(req):
    fn = _fn("echo") or _fn("chat")
    fn(str(req["text"]))
    return True


def op_chat_log(req):
    since = req.get("since", 0)
    entries = [e for e in CHAT_LOG if e["n"] > since]
    return {"entries": entries, "latest": CHAT_SEQ["n"]}


def op_getblock(req):
    gb = _fn("getblock")
    return gb(req["x"], req["y"], req["z"]) if gb else None


def op_container(_req):
    """Items in the currently open container GUI (None if nothing open)."""
    fn = _fn("container_get_items")
    if not fn:
        return None
    items = fn()
    if items is None:
        return None
    out = []
    for stack in items:
        item_id, count = _item_entry(stack)
        if item_id:
            out.append({"item": item_id, "count": int(count)})
    return out


def op_look_at(req):
    _fn("player_look_at")(req["x"], req["y"], req["z"])
    return True


def op_press(req):
    """Hold/release a movement or click control: use, attack, forward, jump, sneak..."""
    fn = _fn("player_press_" + req["control"])
    if not fn:
        return None
    fn(bool(req["pressed"]))
    return True


def op_inventory_slots(_req):
    """Player inventory with slot numbers (0-8 hotbar, 9-35 main)."""
    inv = _fn("player_inventory")
    out = []
    for stack in inv() or []:
        get = (lambda k: stack.get(k)) if isinstance(stack, dict) else (lambda k: getattr(stack, k, None))
        item_id, count = _item_entry(stack)
        if item_id:
            out.append({"slot": get("slot"), "item": item_id, "count": int(count or 1),
                        "selected": bool(get("selected"))})
    return out


def op_select_slot(req):
    fn = _fn("player_inventory_select_slot")
    return fn(int(req["slot"])) if fn else None


def op_slot_to_hotbar(req):
    fn = _fn("player_inventory_slot_to_hotbar")
    return fn(int(req["slot"])) if fn else None


def op_hand(_req):
    fn = _fn("player_hand_items")
    if not fn:
        return None
    hands = fn()
    main = getattr(hands, "main_hand", None) if not isinstance(hands, (list, tuple)) else (hands[0] if hands else None)
    if main is None:
        return None
    item_id, count = _item_entry(main)
    return {"item": item_id, "count": int(count or 0)}


def op_entities(req):
    fn = _fn("entities")
    if not fn:
        return None
    out = []
    for e in fn() or []:
        get = (lambda k: e.get(k)) if isinstance(e, dict) else (lambda k: getattr(e, k, None))
        pos = get("position")
        out.append({"name": str(get("name") or ""), "type": str(get("type") or ""),
                    "position": list(pos) if pos else None, "health": get("health"),
                    "uuid": str(get("uuid") or "")})
    return out[:100]


def op_targeted_block(req):
    fn = _fn("player_get_targeted_block")
    if not fn:
        return None
    tb = fn(req.get("max_distance", 20))
    if tb is None:
        return None
    get = (lambda k: tb.get(k)) if isinstance(tb, dict) else (lambda k: getattr(tb, k, None))
    pos = get("position")
    return {"position": list(pos) if pos else None, "block": str(get("type") or get("block") or "")}


def op_container_click(req):
    fn = _fn("container_click_slot")
    return fn(int(req["slot"])) if fn else None


def op_screenshot(_req):
    fn = _fn("screenshot")
    if not fn:
        return None
    fn()
    return True


OPS = {"probe": op_probe, "state": op_state, "chat": op_chat, "echo": op_echo,
       "chat_log": op_chat_log, "getblock": op_getblock, "container": op_container,
       "look_at": op_look_at, "press": op_press, "inventory_slots": op_inventory_slots,
       "select_slot": op_select_slot, "slot_to_hotbar": op_slot_to_hotbar,
       "hand": op_hand, "entities": op_entities, "targeted_block": op_targeted_block,
       "container_click": op_container_click, "screenshot": op_screenshot}


def chat_listener(stop_event=None):
    """Capture chat until the shared stop event signals shutdown."""
    if stop_event is None:
        stop_event = threading.Event()
    eq_cls = _fn("EventQueue")
    if not eq_cls:
        return
    try:
        with eq_cls() as q:
            q.register_chat_listener()
            while not stop_event.is_set():
                try:
                    event = q.get(timeout=0.2)
                except queue.Empty:
                    continue
                msg = getattr(event, "message", None) or str(event)
                CHAT_SEQ["n"] += 1
                CHAT_LOG.append({"n": CHAT_SEQ["n"], "t": time.time(), "message": msg})
                if ".quit" in msg.lower() and "[agent" not in msg and "[bridge" not in msg:
                    minescript.echo("[bridge] .quit received — shutting down")
                    stop_event.set()
    except Exception as exc:
        minescript.echo(f"[bridge] chat listener stopped: {exc}")


def main(stop_event=None):
    """Serve requests until stop, then close the socket and join the listener."""
    if stop_event is None:
        stop_event = threading.Event()
    listener = threading.Thread(target=chat_listener, args=(stop_event,),
                                name="agent-bridge-chat", daemon=True)
    listener.start()
    retry_delay = 5
    outage_reported = False
    try:
        minescript.echo(f"[bridge] connecting to daemon at {HOST}:{PORT} ...")
        while not stop_event.is_set():
            try:
                sock = socket.create_connection((HOST, PORT), timeout=10)
            except OSError as exc:
                if not outage_reported:
                    minescript.echo(f"[bridge] FAILED to connect ({exc}) — retrying")
                    outage_reported = True
                stop_event.wait(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
                continue
            retry_delay = 5
            outage_reported = False
            try:
                # A read timeout only polls shutdown. A silent daemon stays connected.
                sock.settimeout(0.2)
                minescript.echo("[bridge] connected — agent has control. \\killjob to stop.")
                buf = b""
                while not stop_event.is_set():
                    try:
                        chunk = sock.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf and not stop_event.is_set():
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        try:
                            req = json.loads(line.decode("utf-8"))
                            if not isinstance(req, dict):
                                raise ValueError("request must be a JSON object")
                            if not isinstance(req.get("op"), str) or not req["op"]:
                                raise ValueError("request op must be a nonempty string")
                            req_id = req.get("id")
                            if (not isinstance(req_id, (str, int, float, type(None)))
                                    or isinstance(req_id, bool)
                                    or isinstance(req_id, float) and not math.isfinite(req_id)):
                                raise ValueError("request id must be a string, number, or null")
                        except (UnicodeError, ValueError, RecursionError) as exc:
                            resp = {"id": None, "ok": False,
                                    "error": f"{type(exc).__name__}: {exc}"}
                        else:
                            if req["op"] == "stop":
                                stop_event.set()
                                resp = {"id": req_id, "ok": True, "result": "bye"}
                            else:
                                try:
                                    result = OPS[req["op"]](req)
                                    resp = {"id": req_id, "ok": True, "result": result}
                                except Exception as exc:
                                    resp = {"id": req_id, "ok": False,
                                            "error": f"{type(exc).__name__}: {exc}"}
                        sock.sendall(json.dumps(resp).encode("utf-8") + b"\n")
            except OSError:
                pass
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
            if not stop_event.is_set():
                minescript.echo("[bridge] disconnected — retrying")
                outage_reported = True
                stop_event.wait(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
    finally:
        stop_event.set()
        listener.join()


if __name__ == "__main__":
    main()
