import copy
import unittest
from unittest.mock import patch

from agent import actions
from tests.fakes import FakeGame


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ActionsTests(unittest.TestCase):
    def setUp(self):
        self.game = FakeGame(auto_effects=False)
        self.logs = []
        self.actions = actions.Actions(self.game.result_only(), lambda: set(), self.logs.append)
        self.clock = Clock()
        self.time_patch = patch.object(actions.time, "time", self.clock.time)
        self.sleep_patch = patch.object(actions.time, "sleep", self.clock.sleep)
        self.monotonic_patch = patch.object(actions.time, "monotonic", self.clock.time)
        self.time_patch.start()
        self.sleep_patch.start()
        self.monotonic_patch.start()
        self.addCleanup(self.time_patch.stop)
        self.addCleanup(self.sleep_patch.stop)
        self.addCleanup(self.monotonic_patch.stop)

    def test_agent_state_reads_real_chat_protocol(self):
        self.game.agent_state.update(name="Michael", following=True, mining=False)
        self.game.chat('[agentstate] {"name":"stale"}')
        self.assertEqual(self.actions.agent_state(), self.game.agent_state)
        self.assertEqual(self.game.commands, ["/blockbuddy state"])

    def test_agent_state_accepts_tuple_bridge_without_second_parser(self):
        self.actions.bridge = self.game
        self.assertEqual(self.actions.agent_state(), self.game.agent_state)

    def test_item_selectors_preserve_namespaces_and_reject_ambiguity(self):
        state = {"inventory": {"minecraft:iron_sword": 1, "mod:steel_sword": 2,
                               "minecraft:oak_log": 4, "mod:maple_log": 3}}
        self.assertEqual(self.actions._item_count(state, "minecraft:iron_sword"), 1)
        self.assertEqual(self.actions._item_count(state, "sword"), 0)
        self.assertEqual(self.actions._item_count(state, "#minecraft:logs"), 7)
        del state["inventory"]["mod:steel_sword"]
        self.assertEqual(self.actions._item_count(state, "sword"), 1)

    def test_agent_state_skips_bad_payload_and_accepts_later_json(self):
        def emit(text):
            if text == "/blockbuddy state":
                self.game.chat("[agentstate] not-json")
                self.game.chat('[agentstate] {"name":"Alex", "health":17}')
        self.game.on_command = emit
        self.game.agent_state = None
        # Override the fake's default state response to exercise the wire parser.
        def chat_response(**kwargs):
            self.game.commands.append(kwargs["text"])
            emit(kwargs["text"])
            return True, True
        self.game.responses["chat"] = chat_response
        self.assertEqual(self.actions.agent_state(), {"name": "Alex", "health": 17})

    def test_agent_state_timeout_is_bounded(self):
        self.game.responses["chat"] = (True, True)
        self.assertIsNone(self.actions.agent_state(timeout=3))
        self.assertLessEqual(self.clock.now, 1004.5)

    def test_agent_mine_requires_new_items(self):
        self.game.agent_state["inventory"] = {"dirt": 4}
        def mine(text):
            if text == "/blockbuddy mine minecraft:dirt 8":
                self.game.agent_state["inventory"]["dirt"] += 8
        self.game.on_command = mine
        ok, note = self.actions.agent_mine({"block": "minecraft:dirt", "count": 8, "timeout": 30})
        self.assertTrue(ok, note)
        self.assertIn("/blockbuddy mine minecraft:dirt 8", self.game.commands)
        self.assertIn("4 -> 12", note)

    def test_agent_mine_stops_at_timeout_without_progress(self):
        ok, _ = self.actions.agent_mine({"block": "minecraft:dirt", "count": 8, "timeout": 1})
        self.assertFalse(ok)
        self.assertIn("/blockbuddy stop", self.game.commands)
        self.assertLess(self.clock.now, 1030)

    def test_agent_mine_does_not_report_partial_count_as_complete(self):
        def mine(text):
            if text == "/blockbuddy mine dirt 8":
                self.game.agent_state["inventory"]["dirt"] = 2
        self.game.on_command = mine
        ok, _ = self.actions.agent_mine({"block": "dirt", "count": 8, "timeout": 1})
        self.assertFalse(ok)

    def test_agent_give_checks_player_inventory(self):
        self.game.agent_state["inventory"] = {"dirt": 8}
        def give(text):
            if text == "/blockbuddy give minecraft:dirt 4":
                self.game.state["inventory"]["minecraft:dirt"] = 4
        self.game.on_command = give
        ok, note = self.actions.agent_give({"item": "minecraft:dirt", "count": 4, "timeout": 10})
        self.assertTrue(ok, note)
        self.assertEqual(self.game.commands, [
            "/blockbuddy run follow", "/blockbuddy give minecraft:dirt 4", "/blockbuddy stop",
        ])

    def test_agent_give_does_not_claim_unchanged_inventory(self):
        self.game.state["inventory"] = {"dirt": 64}
        ok, _ = self.actions.agent_give({"item": "dirt", "count": 1, "timeout": 1})
        self.assertFalse(ok)

    def test_agent_give_does_not_report_partial_transfer_as_complete(self):
        def give(text):
            if text == "/blockbuddy give dirt 4":
                self.game.state["inventory"]["dirt"] = 1
        self.game.on_command = give
        ok, _ = self.actions.agent_give({"item": "dirt", "count": 4, "timeout": 1})
        self.assertFalse(ok)

    def test_agent_craft_checks_actual_result(self):
        def craft(text):
            if text == "/blockbuddy craft minecraft:stick 4":
                self.game.agent_state["inventory"]["stick"] = 4
        self.game.on_command = craft
        ok, note = self.actions.agent_craft({"item": "minecraft:stick", "count": 4})
        self.assertTrue(ok, note)

    def test_agent_craft_does_not_accept_preexisting_items(self):
        self.game.agent_state["inventory"] = {"stick": 64}
        ok, _ = self.actions.agent_craft({"item": "minecraft:stick", "count": 4})
        self.assertFalse(ok)

    def test_run_dispatches_without_mutating_plan(self):
        step = {"action": {"action": "agent_run", "cmd": "follow"}}
        before = copy.deepcopy(step)
        self.assertTrue(self.actions.run(step)[0])
        self.assertEqual(step, before)
        self.assertIn("/blockbuddy run follow", self.game.commands)

    def test_run_propagates_failure(self):
        with patch.object(self.actions, "agent_craft", return_value=(False, "missing ingredients")):
            result = self.actions.run({"action": {"action": "agent_craft", "item": "stick"}})
        self.assertEqual(result, (False, "missing ingredients"))

    def test_player_body_remaps_goto(self):
        self.actions.body = "player"
        with patch.object(self.actions, "goto", return_value=(True, "arrived")) as goto:
            self.assertTrue(self.actions.run({"action": {"action": "agent_run", "cmd": "goto 1 64 -3"}})[0])
        goto.assert_called_once_with({"cmd": "goto 1 64 -3", "x": 1.0, "y": 64.0, "z": -3.0})


if __name__ == "__main__":
    unittest.main()
