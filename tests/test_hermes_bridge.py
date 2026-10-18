import json
import threading
import unittest
from unittest.mock import patch

from agent import hermes_bridge as bridge
from tests.fakes import FakeGame, FakeLLM


class FakeModelTests(unittest.TestCase):
    def test_generic_classifier_accepts_new_counts_and_item_names(self):
        llm = FakeLLM(classify=True)
        for text in ("mine17granite", "mine 17 granite", "mine granite 17"):
            self.assertEqual(bridge._classify(llm, text),
                             {"intent": "mine", "item": "granite", "count": 17})

    def test_generic_classifier_keeps_exact_ids_and_tags(self):
        llm = FakeLLM(classify=True)
        self.assertEqual(bridge._classify(llm, "craft 3 example:strange_planks"),
                         {"intent": "craft", "item": "example:strange_planks", "count": 3})
        self.assertEqual(bridge._classify(llm, "mine 9 #minecraft:logs"),
                         {"intent": "mine", "item": "#minecraft:logs", "count": 9})

    def test_fake_game_records_one_result_for_one_command(self):
        game = FakeGame()
        self.assertTrue(game("chat", text="/blockbuddy mine dirt 7")[0])
        self.assertEqual(game.commands, ["/blockbuddy mine dirt 7"])
        self.assertEqual(game.agent_state["inventory"]["dirt"], 7)
        self.assertEqual(game.events, [{"type": "mined", "item": "dirt", "count": 7, "ok": True}])


class ClassificationTests(unittest.TestCase):
    def test_valid_json_normalizes_fields(self):
        llm = FakeLLM('{"intent":" MINE ","item":" minecraft:dirt ","count":"8"}')
        self.assertEqual(bridge._classify(llm, "mine eight dirt"),
                         {"intent": "mine", "item": "minecraft:dirt", "count": 8})
        self.assertEqual(len(llm.calls), 1)

    def test_fenced_json_and_coordinates(self):
        llm = FakeLLM('```json\n{"intent":"goto","x":"1","y":64,"z":-3}\n```')
        self.assertEqual(bridge._classify(llm, "go there"),
                         {"intent": "goto", "x": 1, "y": 64, "z": -3})

    def test_invalid_model_responses_stay_unknown(self):
        for result in ("", "not JSON", "[]", "null", '{"intent":"explode"}', RuntimeError("offline")):
            with self.subTest(result=result):
                self.assertEqual(bridge._classify(FakeLLM([result]), "do something"), {"intent": "unknown"})

    def test_invalid_count_never_raises(self):
        for count in ("Infinity", "NaN", "-Infinity", "8.9", -1):
            with self.subTest(count=count):
                result = bridge._classify(FakeLLM(json.dumps({"intent": "mine", "item": "dirt", "count": count})), "mine dirt")
                self.assertEqual(result, {"intent": "unknown"})

    def test_greetings_and_general_questions_skip_classifier(self):
        llm = FakeLLM(default='{"intent":"gear"}')
        for text in ("hi!", "hello", "what is your favorite color?"):
            self.assertEqual(bridge._classify(llm, text), {"intent": "unknown"})
        self.assertEqual(llm.calls, [])

    def test_health_question_reaches_classifier(self):
        llm = FakeLLM(default='{"intent":"status"}')
        self.assertEqual(bridge._classify(llm, "how are you?"), {"intent": "status"})
        self.assertEqual(len(llm.calls), 1)

    def test_guard_cancellation_keeps_protect_intent(self):
        llm = FakeLLM(classify=True)
        self.assertEqual(json.loads(llm("", "stop guarding")), {"intent": "protect"})
        self.assertEqual(bridge._classify(llm, "stop guarding"), {"intent": "protect"})


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.game = FakeGame(auto_effects=False)
        self.lines = []
        self.llm = FakeLLM(default="Hello there")

    def handle(self, parsed, raw="command"):
        return bridge._handle_intent(parsed, raw, self.game, self.lines.append, self.llm)

    def test_resource_intents_issue_one_command(self):
        for intent in ("mine", "craft", "give", "take", "store"):
            with self.subTest(intent=intent):
                self.game.commands.clear()
                self.handle({"intent": intent, "item": "minecraft:dirt", "count": 8})
                self.assertEqual(self.game.commands, [f"/blockbuddy {intent} minecraft:dirt 8"])

    def test_invalid_counts_are_rejected(self):
        self.handle({"intent": "mine", "item": "dirt", "count": -3})
        self.handle({"intent": "give", "item": "dirt", "count": "8.5"})
        self.assertEqual(self.game.commands, [])

    def test_missing_item_and_coordinates_do_not_issue_commands(self):
        self.handle({"intent": "mine"})
        self.handle({"intent": "goto", "x": 1})
        self.assertEqual(self.game.commands, [])

    def test_follow_wait_stop_and_goto(self):
        self.handle({"intent": "follow"})
        self.handle({"intent": "wait"})
        self.handle({"intent": "stop"})
        self.handle({"intent": "goto", "x": 1, "y": 64, "z": -3})
        self.assertEqual(self.game.commands, [
            "/blockbuddy run follow", "/blockbuddy stop", "/blockbuddy stop",
            "/blockbuddy run goto 1 64 -3",
        ])

    def test_protect_off_stops_instead_of_following(self):
        self.handle({"intent": "protect"}, "guard off")
        self.assertEqual(self.game.commands, ["/blockbuddy stop"])

    def test_result_only_adapter_uses_same_commands(self):
        bridge._handle_intent({"intent": "follow"}, "follow", self.game.result_only().call,
                              self.lines.append, self.llm)
        self.assertEqual(self.game.commands, ["/blockbuddy run follow"])

    def test_bridge_normalizes_failure_and_result_only_state(self):
        self.assertEqual(bridge._bc(self.game, "state"), (True, self.game.state))
        self.assertEqual(bridge._bc(self.game.result_only().call, "state"), (True, self.game.state))
        self.game.failures["state"] = "disconnected"
        self.assertEqual(bridge._bc(self.game, "state"), (False, "disconnected"))
        self.assertEqual(bridge._bc(self.game.result_only().call, "state"), (False, None))

    def test_unknown_chat_does_not_become_command(self):
        self.handle({"intent": "unknown"}, "what a nice view")
        self.assertEqual(self.game.commands, [])

    def test_status_queries_buddy_on_worker_thread(self):
        owner = threading.current_thread()
        observed = []
        threads = []
        thread_class = threading.Thread
        def new_thread(*args, **kwargs):
            thread = thread_class(*args, **kwargs)
            threads.append(thread)
            return thread
        def companion_state(*args, **kwargs):
            observed.append(threading.current_thread())
            return {"name": "Bob", "health": 7, "pos": [3, 64, 5]}
        try:
            with (patch.object(bridge, "_agent_state", side_effect=companion_state),
                  patch.object(bridge.threading, "Thread", side_effect=new_thread)):
                self.handle({"intent": "status"})
                for thread in threads:
                    thread.join(2)
        finally:
            for thread in threads:
                thread.join(2)
        self.assertEqual(len(observed), 1)
        self.assertIsNot(observed[0], owner)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertTrue(any("7" in line and "[3, 64, 5]" in line for line in self.lines))


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.game = FakeGame()

    def test_four_damage_triggers_guard(self):
        self.game.state["health"] = 16
        trip, health, position = bridge._guard_trip(self.game, 20)
        self.assertTrue(trip)
        self.assertEqual(health, 16)
        self.assertEqual(position, self.game.state["position"])

    def test_small_damage_does_not_trigger_guard(self):
        self.game.state["health"] = 19
        self.assertFalse(bridge._guard_trip(self.game, 20)[0])

    def test_nearby_hostile_triggers_but_distant_hostile_does_not(self):
        self.game.entities = [{"type": "minecraft:zombie", "position": [8, 64, 0]}]
        self.assertTrue(bridge._guard_trip(self.game, 20)[0])
        self.game.entities[0]["position"] = [9, 64, 0]
        self.assertFalse(bridge._guard_trip(self.game, 20)[0])

    def test_malformed_entities_and_bridge_failure_stay_safe(self):
        self.game.entities = [None, {"type": "minecraft:zombie", "position": [None]}]
        self.assertFalse(bridge._guard_trip(self.game, 20)[0])
        self.game.failures["state"] = "disconnected"
        self.assertEqual(bridge._guard_trip(self.game, 19), (False, 19, None))


class PlayerFilterTests(unittest.TestCase):
    def test_real_player_named_michael_is_not_filtered_as_bot(self):
        self.assertEqual(bridge._player_message("<Michael> follow me", "Michael"), "follow me")

    def test_protocol_and_nonplayer_lines_never_become_commands(self):
        for text in ("[agentstate] {}", '[bbevent] {"type":"mined"}',
                     "[blockbuddy] follow me", "<Bob> follow me", "Server saved the world",
                     "[Server] follow me", "<Other> follow me"):
            with self.subTest(text=text):
                self.assertIsNone(bridge._player_message(text, "Michael"))

    def test_poll_loop_consumes_only_new_player_chat(self):
        game = FakeGame(state={"player_name": "Michael"}, auto_effects=False)
        llm = FakeLLM(default='{"intent":"follow"}')
        calls = 0
        def log(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                game.chat("<Michael> follow me")
                game.chat("<Bob> follow me")
                game.chat('[bbevent] {"type":"mined","ok":true,"item":"dirt","count":8}')
                game.chat("[agentstate] {}")
                game.chat("Server saved the world")
            since = kwargs.get("since", 0)
            return True, {"latest": len(game.entries), "entries": [e for e in game.entries if e["id"] > since]}
        game.responses["chat_log"] = log
        director = type("SilentDirector", (), {
            "observe": lambda *args, **kwargs: None,
            "player": lambda *args, **kwargs: None,
            "result": lambda *args, **kwargs: None,
        })()
        with patch.object(bridge.time, "sleep"), patch.object(bridge, "_agent_state") as state:
            bridge.nl_watch(game, llm, lambda _: None, poll_interval=0, idle_timeout=999999,
                            max_polls=2, director=director,
                            context={"player": game.state, "buddy": game.agent_state})
        self.assertEqual(game.commands.count("/blockbuddy run follow"), 1)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(calls, 3)
        state.assert_not_called()

    def test_poll_bound_stops_on_bridge_failures(self):
        game = FakeGame(auto_effects=False)
        game.failures["chat_log"] = "offline"
        with patch.object(bridge.time, "sleep"):
            bridge.nl_watch(game, FakeLLM(), lambda _: None, poll_interval=0,
                            idle_timeout=999999, max_polls=3, context={"player": game.state})
        self.assertEqual(sum(op == "chat_log" for op, _ in game.calls), 4)


class GearTests(unittest.TestCase):
    def test_gear_uses_observed_results_and_delivers_sword(self):
        game = FakeGame()
        reports = []
        bridge._gear_run(game, reports.append, cancel_event=threading.Event())
        self.assertEqual(reports, [])
        self.assertEqual(game.state["inventory"].get("stone_sword"), 1)
        self.assertIn("/blockbuddy mine #minecraft:logs 8", game.commands)
        self.assertIn("/blockbuddy mine minecraft:stone 8", game.commands)
        self.assertEqual(game.commands.count("/blockbuddy give minecraft:stone_sword 1"), 1)

    def test_idle_gear_skips_existing_tools(self):
        game = FakeGame(agent_state={"inventory": {"minecraft:stone_pickaxe": 1,
                                                   "minecraft:stone_sword": 1}})
        bridge._gear_run(game, lambda _: None, cancel_event=threading.Event(), idle=True)
        self.assertFalse(any(command.startswith("/blockbuddy mine") for command in game.commands))

    def test_stop_cancels_remaining_gear_steps(self):
        game = FakeGame(auto_effects=False)
        cancel = threading.Event()
        original_state = bridge._agent_state
        def stop_during_state_poll(*args, **kwargs):
            if any(command.startswith("/blockbuddy mine") for command in game.commands):
                bridge._handle_intent({"intent": "stop"}, "stop", game, lambda _: None, FakeLLM())
            return original_state(*args, **kwargs)
        with (patch.object(bridge, "_GEAR_CANCEL", cancel),
              patch.object(bridge, "_agent_state", side_effect=stop_during_state_poll)):
            bridge._gear_run(game, lambda _: None, cancel_event=cancel)
        self.assertTrue(cancel.is_set())
        self.assertIn("/blockbuddy stop", game.commands)
        self.assertFalse(any(command.startswith(("/blockbuddy craft", "/blockbuddy give"))
                             for command in game.commands))

    def test_idle_and_explicit_gear_share_one_worker(self):
        started, finish = threading.Event(), threading.Event()
        threads = []
        thread_class = threading.Thread
        def new_thread(*args, **kwargs):
            thread = thread_class(*args, **kwargs)
            threads.append(thread)
            return thread
        def run(*args, **kwargs):
            started.set()
            finish.wait(2)
        try:
            with patch.object(bridge, "_gear_run", side_effect=run), \
                 patch.object(bridge.threading, "Thread", side_effect=new_thread):
                self.assertTrue(bridge._start_gear(FakeGame(), lambda _: None, idle=True))
                self.assertTrue(started.wait(1))
                self.assertFalse(bridge._start_gear(FakeGame(), lambda _: None))
                bridge._cancel_gear()
                self.assertTrue(bridge._GEAR_CANCEL.is_set())
        finally:
            finish.set()
            for thread in threads:
                thread.join(2)
                self.assertFalse(thread.is_alive())


class NullBuddyTests(unittest.TestCase):
    def test_missing_buddy_has_no_form(self):
        self.assertFalse(bridge._has_buddy(None))
        self.assertFalse(bridge._has_buddy({"agent": None}))
        self.assertTrue(bridge._has_buddy({"name": "Michael"}))

    def test_null_buddy_skips_heartbeat_and_idle_gear(self):
        game = FakeGame()
        director = type("SilentDirector", (), {
            "observe": lambda *args, **kwargs: None,
            "player": lambda *args, **kwargs: None,
            "result": lambda *args, **kwargs: None,
        })()
        with patch.object(bridge, "_agent_state", return_value=None):
            with patch.object(bridge.time, "sleep"):
                bridge.nl_watch(game, FakeLLM(), lambda _: None, poll_interval=0,
                                idle_timeout=-1, max_polls=3, director=director,
                                context={"player": game.state, "buddy": {"agent": None}})
        self.assertNotIn("/blockbuddy brain on", game.commands)
        self.assertFalse(any(command.startswith(("/blockbuddy mine", "/blockbuddy craft"))
                             for command in game.commands))

    def test_null_agent_dict_reports_quiet_inventory(self):
        game = FakeGame()
        reports = []
        with patch.object(bridge, "_agent_state", return_value={"agent": None}):
            bridge._gear_run(game, reports.append, cancel_event=threading.Event())
        self.assertEqual(game.commands, [])
        self.assertEqual(reports, [])

    def test_observe_passes_distance_and_nearby(self):
        seen = []
        director = type("RecordingDirector", (), {
            "observe": lambda self, state: seen.append(state),
            "player": lambda *args, **kwargs: None,
            "result": lambda *args, **kwargs: None,
        })()
        game = FakeGame(state={"player_name": "Alex",
                               "position": {"x": 0.0, "y": 64.0, "z": 0.0}},
                        agent_state={"name": "Michael", "pos": [40, 64, 0]})
        cache = {"player": game.state}
        stop = threading.Event()
        with patch.object(bridge, "_agent_state", return_value=dict(game.agent_state)):
            with patch.object(bridge.time, "sleep"):
                thread = threading.Thread(target=bridge._observe_loop,
                                          args=(game, director, cache, stop))
                thread.start()
                stop.wait(0.2) if hasattr(stop, "wait") else None
                stop.set()
                thread.join(2)
        self.assertTrue(seen)
        first = seen[0]
        self.assertEqual(first["player_distance"], 40.0)
        self.assertFalse(first["player_nearby"])

    def test_return_trip_marks_nearby(self):
        distance, nearby = bridge._proximity({"x": 0, "y": 64, "z": 0}, [8, 64, 0])
        self.assertAlmostEqual(distance, 8.0)
        self.assertTrue(nearby)

    def test_distance_transition_fires_awayreturn(self):
        from agent.director import Director
        from pathlib import Path
        import tempfile
        echo = type("Echo", (), {"echo": lambda self, line: None})()
        with tempfile.TemporaryDirectory() as directory:
            director = Director(echo, complete_fn=lambda *a, **k: "here",
                                memory_path=Path(directory) / "memory.json",
                                start_worker=False)
            try:
                director.observe({"player_distance": 40.0, "player_nearby": False})
                director.observe({"player_distance": 8.0, "player_nearby": True})
                self.assertIn("awayreturn", director._pending)
            finally:
                director.close()

    def test_planks_probe_falls_back_and_fails_honestly(self):
        game = FakeGame()
        reports = []
        calls = []
        original = FakeGame._command
        def command(fake, text):
            calls.append(text)
            words = text.split()
            if words[:2] == ["/blockbuddy", "craft"] and words[2] == "minecraft:oak_planks":
                # Vanilla guess stays unknown, so the probe must try the next id.
                return
            if words[:2] == ["/blockbuddy", "craft"] and words[2] == "oak_planks":
                fake.agent_state["inventory"]["oak_planks"] = \
                    fake.agent_state["inventory"].get("oak_planks", 0) + 16
                fake.event("craft", item="oak_planks", count=16, ok=True)
                return
            original(fake, text)
        with patch.object(FakeGame, "_command", command):
            game.agent_state["inventory"] = {"oak_log": 8}
            game.auto_effects = True
            bridge._gear_run(game, reports.append, cancel_event=threading.Event())
        self.assertEqual(reports, [])
        self.assertIn("/blockbuddy craft minecraft:oak_planks 16", calls)
        self.assertIn("/blockbuddy craft oak_planks 16", calls)

    def test_planks_probe_reports_when_nothing_works(self):
        game = FakeGame(auto_effects=False)
        reports = []
        game.agent_state["inventory"] = {"minecraft:oak_log": 8}
        def chat(**kwargs):
            game.commands.append(kwargs.get("text", ""))
            game.chat("[agentstate] " + json.dumps(game.agent_state))
            return True, {"sent": kwargs.get("text", "")}
        game.responses["chat"] = chat
        bridge._gear_run(game, reports.append, cancel_event=threading.Event())
        self.assertEqual(len(reports), 1)
        self.assertIn("planks", reports[0])

if __name__ == "__main__":
    unittest.main()