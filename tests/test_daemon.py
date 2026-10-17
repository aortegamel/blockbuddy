import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from agent import daemon


class RunPlanTests(unittest.TestCase):
    def setUp(self):
        self.log = patch.object(daemon, "log").start()
        self.addCleanup(patch.stopall)
        self.steps = [
            {"n": 1, "for_quest": "A", "action": {"action": "gather"}},
            {"n": 2, "for_quest": "B", "action": {"action": "craft"}},
        ]
        self.plan = {
            "steps": self.steps,
            "target_quests": [{"id": "A"}, {"id": "B"}],
        }

    def test_retries_once_then_marks_all_targets_after_success(self):
        actions = Mock()
        actions.run.side_effect = [(False, "retry"), (True, "ok"), (True, "ok")]
        mark = Mock()

        result = daemon.run_plan(None, None, self.plan, actions, mark)

        self.assertEqual(result, (2, None))
        self.assertEqual(actions.run.call_args_list,
                         [call(self.steps[0]), call(self.steps[0]), call(self.steps[1])])
        self.assertEqual(mark.call_args_list, [call("A"), call("B")])

    def test_second_failure_stops_plan_without_marking_targets(self):
        actions = Mock()
        actions.run.side_effect = [(True, "ok"), (False, "blocked"), (False, "blocked")]
        mark = Mock()

        result = daemon.run_plan(None, None, self.plan, actions, mark)

        self.assertEqual(result, (1, self.steps[1]))
        self.assertEqual(actions.run.call_args_list,
                         [call(self.steps[0]), call(self.steps[1]), call(self.steps[1])])
        mark.assert_not_called()

    def test_first_failure_does_not_run_later_steps(self):
        actions = Mock()
        actions.run.return_value = (False, "blocked")

        self.assertEqual(daemon.run_plan(None, None, self.plan, actions),
                         (0, self.steps[0]))
        self.assertEqual(actions.run.call_args_list, [call(self.steps[0]), call(self.steps[0])])

    def test_success_does_not_retry(self):
        actions = Mock()
        actions.run.return_value = (True, "ok")
        self.assertEqual(daemon.run_plan(None, None, self.plan, actions), (2, None))
        self.assertEqual(actions.run.call_args_list, [call(step) for step in self.steps])


class BaseSummaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "base.json"
        patcher = patch.object(daemon, "BASE_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_file_returns_none(self):
        self.assertIsNone(daemon.load_base_summary())

    def test_empty_base_preserves_notes(self):
        self.path.write_text(json.dumps({"notes": ["Keep the seeds"]}), encoding="utf-8")
        self.assertEqual(daemon.load_base_summary(), {
            "notes": ["Keep the seeds"], "storage_totals": {}, "containers": [],
        })

    def test_merges_storage_and_limits_container_and_total_items(self):
        items = {f"item_{i:02}": i + 1 for i in range(85)}
        self.path.write_text(json.dumps({
            "notes": ["Workshop"],
            "containers": {
                "first": {"items": items, "position": {"x": 2.9, "y": 64.8, "z": -3.9},
                          "screen": "Chest"},
                "second": {"items": {"item_84": 100}},
            },
        }), encoding="utf-8")

        summary = daemon.load_base_summary()

        self.assertEqual(summary["notes"], ["Workshop"])
        self.assertEqual(len(summary["storage_totals"]), 80)
        self.assertEqual(summary["storage_totals"]["item_84"], 185)
        self.assertEqual(list(summary["storage_totals"])[0], "item_84")
        self.assertNotIn("item_00", summary["storage_totals"])
        chest, fallback = summary["containers"]
        self.assertEqual(chest["at"], {"x": 2, "y": 64, "z": -3})
        self.assertEqual(chest["kind"], "Chest")
        self.assertEqual(list(chest["items"]), [f"item_{i:02}" for i in range(84, 74, -1)])
        self.assertEqual(fallback["at"], {"x": 0, "y": 0, "z": 0})
        self.assertEqual(fallback["kind"], "container")


class DaemonSpawnTests(unittest.TestCase):
    def test_missing_companion_spawns_alex(self):
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            plan_path.write_text('{"steps": []}', encoding="utf-8")
            bridge = Mock()
            bridge.call.return_value = {}
            actions = Mock()
            actions.agent_state.return_value = None
            with patch.object(daemon, "wait_for_bridge", return_value=bridge), \
                    patch.object(daemon, "Actions", return_value=actions), \
                    patch.object(daemon, "Ledger") as ledger, \
                    patch.object(daemon, "log"), patch.object(daemon.time, "sleep"), \
                    patch.object(daemon, "BASE_PATH", daemon.BASE_PATH), \
                    patch("sys.argv", ["daemon", "--mp", "--body", "companion",
                                       "--plan", str(plan_path)]):
                ledger.return_value.done = set()
                daemon.main()
            self.assertIn(call("chat", text="/blockbuddy spawn Alex"),
                          bridge.call.call_args_list)


if __name__ == "__main__":
    unittest.main()
