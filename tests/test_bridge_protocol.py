"""Exercise the real TCP shim and HTTP handler without Minecraft."""
import importlib.util
import json
import queue
import socket
import sys
import threading
import time
import types
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative_path, minescript=None):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"minescript": minescript} if minescript else {}):
        spec.loader.exec_module(module)
    return module


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    pause = threading.Event()
    while time.monotonic() < deadline:
        if predicate():
            return
        pause.wait(0.01)
    raise AssertionError("The socket operation did not reach the expected state.")


class FastStop:
    """Keep real socket I/O while shortening the shim's reconnect delays."""

    def __init__(self):
        self.event = threading.Event()
        self.delays = []

    def set(self):
        self.event.set()

    def is_set(self):
        return self.event.is_set()

    def wait(self, timeout=None):
        if timeout is not None and timeout >= 5:
            self.delays.append(timeout)
            timeout = 0.01
        return self.event.wait(timeout)


class FakeMinescript(types.ModuleType):
    def __init__(self):
        super().__init__("minescript")
        self.events = queue.Queue()
        self.listening = threading.Event()
        self.commands = []
        self.echoes = []
        self.player_name = lambda: "Alex"
        self.player_position = lambda: (12.25, 64, -3.75)
        self.player_health = lambda: 18
        self.player_inventory = lambda: [
            {"item": "minecraft:dirt", "count": 2},
            types.SimpleNamespace(item="minecraft:dirt", count=3),
        ]
        self.world_info = lambda: types.SimpleNamespace(
            game_ticks=40000, day_ticks=16000, raining=False)
        self.chat = self.commands.append
        self.execute = self.commands.append
        self.echo = self.echoes.append
        owner = self

        class EventQueue:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def register_chat_listener(self):
                owner.listening.set()

            def get(self, timeout=None):
                return owner.events.get(timeout=timeout)

        self.EventQueue = EventQueue

    def receive_chat(self, message):
        self.events.put(types.SimpleNamespace(message=message))


class SocketHarness:
    def __init__(self):
        self.hermes = load_module("protocol_hermes", "agent/hermes_bridge.py")
        self.hermes.log = lambda _message: None
        self.errors = []
        self.shims = []
        self.workers = []
        self.release_events = []
        self.stop = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.05)
        self.hermes.BRIDGE_PORT = self.listener.getsockname()[1]
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), self.hermes.Handler)
        self.http.daemon_threads = False
        self.hermes.HTTP_PORT = self.http.server_port
        self.closed = False

    def start_thread(self, target):
        def run():
            try:
                target()
            except BaseException as exc:
                self.errors.append(exc)

        thread = threading.Thread(target=run)
        self.workers.append(thread)
        thread.start()
        return thread

    def start(self):
        self.start_thread(lambda: self.hermes._accept_loop(self.listener, stop_event=self.stop))
        self.start_thread(lambda: self.http.serve_forever(poll_interval=0.05))

    def start_shim(self, fake=None):
        fake = fake or FakeMinescript()
        shim = load_module("protocol_shim", "bridge/minescript/agent_bridge.py", fake)
        shim.HOST, shim.PORT = self.listener.getsockname()
        stop = FastStop()
        old = self.hermes._conn
        thread = self.start_thread(lambda: shim.main(stop_event=stop))
        self.shims.append((shim, fake, stop, thread))
        wait_for(lambda: self.hermes._conn is not None and self.hermes._conn is not old)
        if not fake.listening.wait(2):
            raise AssertionError("The fake Minescript listener did not start.")
        return shim, fake, stop, thread

    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def call(self, op, **kwargs):
        return self.request("POST", "/call", json.dumps({"op": op, **kwargs}),
                            {"Content-Type": "application/json"})

    def close(self):
        if self.closed:
            return
        self.closed = True
        for release in self.release_events:
            release.set()
        for _shim, _fake, stop, _thread in self.shims:
            stop.set()
        self.stop.set()
        conn = self.hermes._conn
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        self.listener.close()
        self.http.shutdown()
        self.http.server_close()
        for thread in self.workers:
            thread.join(5)
        alive = [thread.name for thread in self.workers if thread.is_alive()]
        if alive:
            raise AssertionError(f"The socket tests left live threads: {alive}")
        if self.errors:
            raise AssertionError(f"A socket worker failed: {self.errors!r}")


class BridgeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.harness = SocketHarness()
        self.addCleanup(self.harness.close)
        self.harness.start()

    def test_state_chat_and_log_use_real_http_and_tcp(self):
        shim, fake, _stop, _thread = self.harness.start_shim()
        status, reply = self.harness.call("state")
        self.assertEqual(status, 200)
        state = reply["result"]
        self.assertEqual(state["player_name"], "Alex")
        self.assertEqual(state["position"], {"x": 12.2, "y": 64, "z": -3.8})
        self.assertEqual(state["health"], 18)
        self.assertEqual(state["inventory"], {"minecraft:dirt": 5})
        self.assertEqual(state["world_info"], {"time": 16000, "daytime": False, "raining": False})
        status, reply = self.harness.call("chat", text="/blockbuddy follow")
        self.assertEqual((status, reply), (200, {"ok": True, "result": True}))
        self.assertEqual(fake.commands, ["/blockbuddy follow"])
        fake.receive_chat("<Alex> hello")
        wait_for(lambda: shim.CHAT_SEQ["n"] == 1)
        status, reply = self.harness.call("chat_log", since=0)
        self.assertEqual(status, 200)
        self.assertEqual(reply["result"]["latest"], 1)
        self.assertEqual(reply["result"]["entries"][0]["message"], "<Alex> hello")
        self.assertEqual(self.harness.call("chat_log", since=1)[1]["result"]["entries"], [])

    def test_state_uses_day_clock_and_preserves_unknown_weather(self):
        fake = FakeMinescript()
        fake.world_info = lambda: {"day_ticks": 30000, "game_ticks": 90000}
        self.harness.start_shim(fake)
        status, reply = self.harness.call("state")
        self.assertEqual(status, 200)
        self.assertEqual(reply["result"]["world_info"],
                         {"time": 6000, "daytime": True, "raining": None})

    def test_state_reports_null_for_unavailable_minescript_fields(self):
        fake = FakeMinescript()
        del fake.player_name
        del fake.world_info
        self.harness.start_shim(fake)
        status, reply = self.harness.call("state")
        self.assertEqual(status, 200)
        self.assertIsNone(reply["result"]["player_name"])
        self.assertEqual(reply["result"]["world_info"], {"time": None, "daytime": None, "raining": None})

    def test_state_reports_null_when_optional_apis_fail(self):
        fake = FakeMinescript()

        def unavailable():
            raise RuntimeError("The world is unavailable.")

        fake.player_name = unavailable
        fake.world_info = unavailable
        self.harness.start_shim(fake)
        status, reply = self.harness.call("state")
        self.assertEqual(status, 200)
        self.assertIsNone(reply["result"]["player_name"])
        self.assertEqual(reply["result"]["world_info"], {"time": None, "daytime": None, "raining": None})

    def test_http_probe_does_not_replace_live_shim(self):
        self.harness.start_shim()
        original = self.harness.hermes._conn
        with socket.create_connection(self.harness.listener.getsockname(), timeout=5) as probe:
            probe.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            try:
                self.assertEqual(probe.recv(1024), b"")
            except ConnectionResetError:
                pass
        self.assertIs(self.harness.hermes._conn, original)
        self.assertEqual(self.harness.call("state")[0], 200)

    def test_http_rejects_browser_origins_and_bad_bodies(self):
        self.harness.start_shim()
        for body in (b"[]", b"null", b"{", b"\xff", b'{"op": 7}'):
            with self.subTest(body=body):
                status, reply = self.harness.request(
                    "POST", "/call", body, {"Content-Type": "application/json"})
                self.assertEqual(status, 400)
                self.assertFalse(reply["ok"])
        for origin in ("https://example.com", "http://localhost", "null"):
            with self.subTest(origin=origin):
                status, reply = self.harness.request(
                    "POST", "/call", '{"op":"state"}',
                    {"Origin": origin, "Content-Type": "application/json"})
                self.assertEqual(status, 403)
                self.assertFalse(reply["ok"])
        self.assertEqual(self.harness.call("state")[0], 200)
        status, health = self.harness.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["bridge"], "connected")

    def test_shim_restart_reconnects_to_same_accept_loop(self):
        _shim, _fake, _stop, first_thread = self.harness.start_shim()
        status, reply = self.harness.call("stop")
        self.assertEqual((status, reply), (200, {"ok": True, "result": "bye"}))
        first_thread.join(3)
        self.assertFalse(first_thread.is_alive())
        fake = FakeMinescript()
        fake.player_name = lambda: "Sam"
        self.harness.start_shim(fake)
        self.assertEqual(self.harness.call("state")[1]["result"]["player_name"], "Sam")

    def test_shim_reconnects_after_daemon_disconnect(self):
        _shim, fake, stop, thread = self.harness.start_shim()
        original = self.harness.hermes._conn
        original.shutdown(socket.SHUT_RDWR)
        original.close()
        wait_for(lambda: self.harness.hermes._conn is not original)
        self.assertTrue(thread.is_alive())
        self.assertEqual(stop.delays[0], 5)
        self.assertEqual(self.harness.call("state")[0], 200)
        notices = [text for text in fake.echoes if "disconnected" in text.lower()]
        self.assertEqual(len(notices), 1)

    def test_in_flight_reconnect_does_not_clear_new_connection(self):
        entered = threading.Event()
        release = threading.Event()
        self.harness.release_events.append(release)
        fake = FakeMinescript()

        def blocked_position():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("The test did not release the state request.")
            return (0, 64, 0)

        fake.player_position = blocked_position
        _shim, _fake, first_stop, _thread = self.harness.start_shim(fake)
        result = []
        request = self.harness.start_thread(lambda: result.append(self.harness.call("state")))
        self.assertTrue(entered.wait(3))
        replacement = FakeMinescript()
        replacement.player_name = lambda: "Replacement"
        self.harness.start_shim(replacement)
        request.join(3)
        self.assertFalse(request.is_alive())
        self.assertEqual(result[0][0], 503)
        first_stop.set()
        release.set()
        status, reply = self.harness.call("state")
        self.assertEqual(status, 200)
        self.assertEqual(reply["result"]["player_name"], "Replacement")


class ShimEnvelopeTests(unittest.TestCase):
    def test_malformed_lines_do_not_stop_actual_shim_main(self):
        fake = FakeMinescript()
        shim = load_module("envelope_shim", "bridge/minescript/agent_bridge.py", fake)
        stop = FastStop()
        errors = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(5)
            shim.HOST, shim.PORT = listener.getsockname()

            def run():
                try:
                    shim.main(stop_event=stop)
                except BaseException as exc:
                    errors.append(exc)

            thread = threading.Thread(target=run)
            thread.start()
            conn = None
            stream = None
            try:
                conn, _address = listener.accept()
                conn.settimeout(5)
                stream = conn.makefile("rb")
                conn.sendall(
                    b'{\n[]\nnull\n\xff\n{"id":8,"op":7}\n'
                    b'{"id":[],"op":"state"}\n{"id":42,"op":"state"}\n')
                for _index in range(6):
                    reply = json.loads(stream.readline())
                    self.assertFalse(reply["ok"])
                    self.assertIsNone(reply["id"])
                state = json.loads(stream.readline())
                self.assertTrue(state["ok"])
                self.assertEqual(state["id"], 42)
                conn.sendall(b'{"id": 43, "op": "missing"}\n{"id": 44, "op": "stop"}\n')
                error = json.loads(stream.readline())
                self.assertEqual((error["id"], error["ok"]), (43, False))
                self.assertEqual(json.loads(stream.readline()), {"id": 44, "ok": True, "result": "bye"})
            finally:
                stop.set()
                if conn is not None:
                    try:
                        conn.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                if stream is not None:
                    stream.close()
                if conn is not None:
                    conn.close()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def test_reconnect_backoff_caps_and_reports_outage_once(self):
        fake = FakeMinescript()
        shim = load_module("backoff_shim", "bridge/minescript/agent_bridge.py", fake)
        stop = FastStop()

        def wait(timeout=None):
            stop.delays.append(timeout)
            if len(stop.delays) == 6:
                stop.set()
            return stop.is_set()

        stop.wait = wait
        with patch.object(shim.socket, "create_connection", side_effect=ConnectionRefusedError("offline")):
            shim.main(stop_event=stop)
        self.assertEqual(stop.delays, [5, 10, 20, 40, 60, 60])
        notices = [text for text in fake.echoes if "retry" in text.lower()]
        self.assertEqual(len(notices), 1)


if __name__ == "__main__":
    unittest.main()
