"""Score the chat classifier without Minecraft. Use --fake-llm for an offline smoke check."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.hermes_bridge import _classify
from agent import llm
from tests.fakes import FakeLLM


# Expected fields describe the command, not a preferred model response format.
CASES = [
    ("follow me", {"intent": "follow"}),
    ("come here", {"intent": "follow"}),
    ("stay with me", {"intent": "follow"}),
    ("please follow me", {"intent": "follow"}),
    ("stop", {"intent": "stop"}),
    ("wait here", {"intent": "stop"}),
    ("stop moving", {"intent": "stop"}),
    ("stay here", {"intent": "stop"}),
    ("mine 8 dirt", {"intent": "mine", "item": "dirt", "count": 8}),
    ("mine8dirt", {"intent": "mine", "item": "dirt", "count": 8}),
    ("mine dirt 8", {"intent": "mine", "item": "dirt", "count": 8}),
    ("please mine 16 cobblestone", {"intent": "mine", "item": "cobblestone", "count": 16}),
    ("mine 4 minecraft:stone", {"intent": "mine", "item": "minecraft:stone", "count": 4}),
    ("mine 12 sand", {"intent": "mine", "item": "sand", "count": 12}),
    ("mine 8 #minecraft:logs", {"intent": "mine", "item": "#minecraft:logs", "count": 8}),
    ("mine 3 kubejs:dead_log", {"intent": "mine", "item": "kubejs:dead_log", "count": 3}),
    ("craft 4 sticks", {"intent": "craft", "item": "stick", "count": 4}),
    ("craft 1 crafting table", {"intent": "craft", "item": "crafting_table", "count": 1}),
    ("please craft 2 torches", {"intent": "craft", "item": "torch", "count": 2}),
    ("craft 1 minecraft:stone_pickaxe", {"intent": "craft", "item": "minecraft:stone_pickaxe", "count": 1}),
    ("craft 8 kubejs:flimsy_planks", {"intent": "craft", "item": "kubejs:flimsy_planks", "count": 8}),
    ("give me 4 dirt", {"intent": "give", "item": "dirt", "count": 4}),
    ("give me 1 sword", {"intent": "give", "item": "sword", "count": 1}),
    ("give me 8 sticks", {"intent": "give", "item": "stick", "count": 8}),
    ("give me 2 minecraft:apple", {"intent": "give", "item": "minecraft:apple", "count": 2}),
    ("take 4 dirt", {"intent": "take", "item": "dirt", "count": 4}),
    ("take 8 cobblestone", {"intent": "take", "item": "cobblestone", "count": 8}),
    ("take 1 sword", {"intent": "take", "item": "sword", "count": 1}),
    ("take 2 minecraft:apple", {"intent": "take", "item": "minecraft:apple", "count": 2}),
    ("store 16 dirt", {"intent": "store", "item": "dirt", "count": 16}),
    ("store 8 sticks", {"intent": "store", "item": "stick", "count": 8}),
    ("store 1 sword", {"intent": "store", "item": "sword", "count": 1}),
    ("store 4 minecraft:cobblestone", {"intent": "store", "item": "minecraft:cobblestone", "count": 4}),
    ("go to 100 64 -20", {"intent": "goto", "x": 100, "y": 64, "z": -20}),
    ("walk to -12 70 30", {"intent": "goto", "x": -12, "y": 70, "z": 30}),
    ("go to 0 64 0", {"intent": "goto", "x": 0, "y": 64, "z": 0}),
    ("please go to -100 80 -200", {"intent": "goto", "x": -100, "y": 80, "z": -200}),
    ("protect me", {"intent": "protect"}),
    ("guard me", {"intent": "protect"}),
    ("stop guarding", {"intent": "protect"}),
    ("gear up", {"intent": "gear"}),
    ("get tools", {"intent": "gear"}),
    ("I need tools", {"intent": "gear"}),
    ("I don't have tools", {"intent": "gear"}),
    ("status", {"intent": "status"}),
    ("where are you", {"intent": "status"}),
    ("how are you", {"intent": "status"}),
    ("are you ok", {"intent": "status"}),
    ("hello", {"intent": "unknown"}),
    ("good morning", {"intent": "unknown"}),
    ("what is your favorite color?", {"intent": "unknown"}),
    ("that sunset looks nice", {"intent": "unknown"}),
]
FIELDS = ("intent", "item", "count", "x", "y", "z")


def _field_value(field, value):
    # Bare vanilla names and their namespaced IDs identify the same item.
    if field == "item" and isinstance(value, str) and ":" not in value and not value.startswith("#"):
        return "minecraft:" + value
    return value


def evaluate(complete):
    """Print field mismatches. Return the number of incorrect cases."""
    passed = 0
    for text, expected in CASES:
        actual = _classify(complete, text)
        mismatches = [
            f"{field}: expected {expected.get(field)!r}, got {actual.get(field)!r}"
            for field in FIELDS
            if _field_value(field, expected.get(field)) != _field_value(field, actual.get(field))
        ]
        if mismatches:
            print(f"FAIL {text!r}")
            for detail in mismatches:
                print(f"  {detail}")
        else:
            passed += 1
    print(f"Score: {passed}/{len(CASES)} ({100 * passed / len(CASES):.1f}%)")
    return len(CASES) - passed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake-llm", action="store_true", help="Check classifier plumbing with a keyword-based fake model.")
    args = parser.parse_args(argv)
    if args.fake_llm:
        complete = FakeLLM(classify=True)
        print("Offline smoke mode uses keyword rules. It does not measure model quality.")
    else:
        complete = llm.complete
    return 1 if evaluate(complete) else 0


if __name__ == "__main__":
    raise SystemExit(main())
