import contextlib
import importlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import planner, state, world_state
from tools import snbt


class SNBTTests(unittest.TestCase):
    def test_ftb_dialect_comments_nested_values_and_typed_numbers(self):
        text = '''{
            # FTB omits commas between fields.
            title: "A quest"
            enabled: true
            disabled: false
            byte: 1b
            short: -2S
            long: 300L
            float: 1.5f
            double: -2.25D
            exponent: 2e3
            nested: { "quoted key": [1, 2, {name: enum_value}] }
            empty: []
        }'''
        self.assertEqual(snbt.loads(text), {
            "title": "A quest", "enabled": True, "disabled": False,
            "byte": 1, "short": -2, "long": 300, "float": 1.5,
            "double": -2.25, "exponent": 2000.0,
            "nested": {"quoted key": [1, 2, {"name": "enum_value"}]}, "empty": [],
        })

    def test_typed_arrays_and_empty_compounds(self):
        self.assertEqual(snbt.loads('{a:[I;1,-2,3], b:[B;1b,0b], c:[L;4L], d:[I;], e:{}}'),
                         {"a": [1, -2, 3], "b": [1, 0], "c": [4], "d": [], "e": {}})

    def test_strings_decode_quotes_backslashes_and_control_escapes(self):
        text = r'''{a:"say \"hello\"", b:'it\'s fine', c:"C:\\temp", d:"a\nb\tc"}'''
        self.assertEqual(snbt.loads(text), {
            "a": 'say "hello"', "b": "it's fine", "c": "C:\\temp", "d": "a\nb\tc",
        })

    def test_malformed_values_raise_parser_error(self):
        for text in ("", "{", "[", '{a:"unterminated}', '{a:"trailing\\',
                     "{a 1}", "{a:}", "[1,}", "{} trailing"):
            with self.subTest(text=text):
                with self.assertRaises(snbt.SNBTError):
                    snbt.loads(text)

    def test_file_loader_uses_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quest.snbt"
            path.write_text('{title:"Café"}', encoding="utf-8")
            self.assertEqual(snbt.load(path), {"title": "Café"})


class ExtractJSONTests(unittest.TestCase):
    def test_ignores_braces_inside_strings(self):
        for value in ("a } bracket", "a { bracket", 'a \\"quote" and } bracket'):
            with self.subTest(value=value):
                expected = {"steps": [{"text": value}], "nested": {"ok": True}}
                response = "```json\n" + json.dumps(expected) + "\n```\nExplanation"
                self.assertEqual(planner.extract_json(response), expected)

    def test_returns_first_object_without_consuming_trailing_text(self):
        self.assertEqual(planner.extract_json('Plan: {"steps": []} then {"other": 1}'),
                         {"steps": []})

    def test_rejects_missing_incomplete_and_invalid_objects(self):
        for text in ("no object", '[1, 2]', '{"steps": [', '{"bad": nope}'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    planner.extract_json(text)


class FrontierTests(unittest.TestCase):
    def setUp(self):
        self.quests = [
            {"id": "root", "optional": False, "dependencies": []},
            {"id": "next", "optional": False, "dependencies": ["root", "chapter"]},
            {"id": "optional", "optional": True, "dependencies": []},
            {"id": "dangling", "optional": False, "dependencies": ["missing"]},
        ]
        self.graph = {"chapters": [{"id": "chapter", "quests": self.quests}]}

    def test_initial_frontier_excludes_optional_and_unmet_dependencies(self):
        self.assertEqual(state.frontier(self.graph, {}), [self.quests[0]])

    def test_completed_dependency_unlocks_quest_and_chapter_dependency_does_not_block(self):
        game_state = {"completed_quests": ["root"]}
        before = json.loads(json.dumps(game_state))
        self.assertEqual(state.frontier(self.graph, game_state), [self.quests[1]])
        self.assertEqual(game_state, before)

    def test_optional_flag_and_completion_filter(self):
        self.assertEqual(state.frontier(self.graph, {}, include_optional=True),
                         [self.quests[0], self.quests[2]])
        done = {"completed_quests": ["root", "next", "optional"]}
        self.assertEqual(state.frontier(self.graph, done, include_optional=True), [])

    def test_fresh_states_do_not_share_mutable_values(self):
        first, second = state.fresh_state(), state.fresh_state()
        first["completed_quests"].append("root")
        first["inventory"]["dirt"] = 1
        self.assertEqual(second["completed_quests"], [])
        self.assertEqual(second["inventory"], {})


class QuestFolderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.quests = self.root / "quests"
        (self.quests / "chapters").mkdir(parents=True)
        self.lang = self.quests / "lang.json"
        self.out = self.root / "graph.json"
        with patch.dict(os.environ, {"BLOCKBUDDY_MC_DIR": str(self.root)}):
            self.builder = importlib.import_module("tools.build_quest_graph")

    def build(self):
        with patch.object(self.builder, "QUESTS", self.quests), \
                patch.object(self.builder, "LANG", self.lang), \
                patch.object(self.builder, "OUT", self.out), \
                contextlib.redirect_stdout(io.StringIO()):
            self.builder.main()
        return state.load_graph(self.out)

    def test_builds_tiny_quest_folder_and_computes_frontier(self):
        self.lang.write_text(json.dumps({
            "chapter.title": "&aBeginnings", "quest.title": "&bCollect stone",
            "quest.description": "&7Use your hands",
        }), encoding="utf-8")
        (self.quests / "chapters" / "start.snbt").write_text('''{
            id:"C1" title:"{chapter.title}" order_index:1
            quests:[
                {id:"A" title:"{quest.title}" description:["{quest.description}", ""]
                 tasks:[{id:"T1" type:"item" item:{id:"minecraft:stone" tag:{quality:1b}}
                         count:2L consume_items:false}]
                 rewards:[{type:"item" item:{id:"minecraft:apple"} count:1}]}
                {id:"B" dependencies:["A", "C1"]
                 tasks:[{id:"T2" item:"minecraft:oak_planks"}] optional:true}
            ]
        }''', encoding="utf-8")
        (self.quests / "chapters" / "earlier.snbt").write_text(
            '{id:"C0" filename:"earlier" order_index:0 quests:[]}', encoding="utf-8")

        graph = self.build()

        self.assertEqual([c["id"] for c in graph["chapters"]], ["C0", "C1"])
        self.assertEqual(graph["chapters"][1]["title"], "Beginnings")
        index = state.quest_index(graph)
        self.assertEqual(index["A"]["title"], "Collect stone")
        self.assertEqual(index["A"]["description"], ["Use your hands"])
        self.assertEqual(index["A"]["tasks"][0], {
            "id": "T1", "type": "item", "item": "minecraft:stone",
            "item_nbt": {"quality": 1}, "count": 2, "consume_items": False,
        })
        self.assertEqual(index["A"]["rewards"],
                         [{"type": "item", "item": "minecraft:apple", "count": 1}])
        self.assertEqual(index["B"]["title"], "Oak Planks")
        self.assertEqual(graph["stats"], {
            "chapters": 2, "quests": 2, "dependency_edges": 2, "root_quests": 1,
            "dangling_dependencies": [], "has_cycle": False,
        })
        self.assertEqual([q["id"] for q in state.frontier(graph, {})], ["A"])
        self.assertEqual([q["id"] for q in state.frontier(
            graph, {"completed_quests": ["A"]}, include_optional=True)], ["B"])

    def test_snbt_language_folder_and_cycle_diagnostics(self):
        self.lang = self.quests / "lang" / "en_us"
        self.lang.mkdir(parents=True)
        (self.lang / "chapter.snbt").write_text(
            '{"chapter.title":"&dCycle tests"}', encoding="utf-8")
        (self.quests / "chapters" / "cycle.snbt").write_text('''{
            id:"C" title:"{chapter.title}"
            quests:[{id:"A" dependencies:["B", "missing"]}
                    {id:"B" dependencies:["A"]}]
        }''', encoding="utf-8")
        graph = self.build()
        self.assertEqual(graph["chapters"][0]["title"], "Cycle tests")
        self.assertEqual(graph["stats"]["dangling_dependencies"], ["missing"])
        self.assertTrue(graph["stats"]["has_cycle"])
        self.assertEqual(state.frontier(graph, {}), [])

    def test_world_progress_reads_both_layouts_and_skips_bad_files(self):
        for layout in ("ftbquests", "serverdata/ftbquests"):
            with self.subTest(layout=layout):
                world = self.root / layout.replace("/", "_")
                progress = world / layout / "teams"
                progress.mkdir(parents=True)
                (progress / "team.snbt").write_text(
                    '{nested:[{completed:{"00ab":123L, "cdEF":456L}}]}', encoding="utf-8")
                (progress / "broken.snbt").write_text("{", encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    done = world_state.completed_quests(world)
                self.assertEqual(done, {"00AB", "CDEF"})
                self.assertIn("broken.snbt", output.getvalue())
        self.assertEqual(world_state.completed_quests(self.root / "absent"), set())


if __name__ == "__main__":
    unittest.main()
