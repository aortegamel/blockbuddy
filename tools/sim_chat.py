"""Run chat commands and dialogue without Minecraft. Pipe input or use the interactive prompt."""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import llm
from agent.director import Director
from agent.hermes_bridge import _classify, _handle_intent, _is_own
from tests.fakes import FakeGame, FakeLLM


class ConsoleBridge:
    """Print each command and each echo at the bridge boundary."""
    def __init__(self, game):
        self.game = game

    def call(self, op, **kwargs):
        ok, result = self.game.call(op, **kwargs)
        if ok and op == "chat" and kwargs.get("text", "").startswith("/blockbuddy "):
            print(kwargs["text"], flush=True)
        elif ok and op == "echo":
            print(kwargs.get("text", ""), flush=True)
        return ok, result

    def echo(self, text):
        return self.call("echo", text=text)


def _snapshot(game):
    return {
        **game.state, **game.agent_state,
        "player_health": game.state["health"],
        "job": (
            "mining" if game.agent_state.get("mining") else
            "following" if game.agent_state.get("following") else "idle"
        ),
        "hostiles": [],
    }


def _event(game, text):
    """Inject one event. Accept both 'crafted stick4' and 'crafted stick 4'."""
    if text == "night":
        game.state["world_info"].update(time=14000, daytime=False)
        return
    if text == "hurt":
        game.state["health"] = max(0, game.state["health"] - 4)
        return
    match = re.fullmatch(r"crafted\s+([#\w:./-]+?)\s*(\d+)", text)
    if match and int(match[2]) > 0:
        item = match[1] if ":" in match[1] else "minecraft:" + match[1]
        return game.event("craft", item=item, count=int(match[2]), ok=True)
    raise ValueError("Use /event night, /event hurt, or /event crafted stick4.")


def _fake_voice(system, prompt, **kwargs):
    try:
        event = json.loads(prompt).get("event", {})
        kind = event.get("type", "") if isinstance(event, dict) else str(event)
    except (ValueError, AttributeError):
        kind = ""
    line = {
        "mined": "Blocks collected. What are we building?",
        "craft": "Crafting finished. What comes next?",
        "night": "Night is here. Keep a torch close.",
        "playerhurt": "That looked painful. Stay close.",
        "hurt": "Stay close. I've got you.",
        "danger": "Stay close. I've got you.",
        "follow": "Right behind you!",
        "stop": "I'll wait here.",
        "give": "There you go. Put it to good use.",
        "take": "I've got it.",
        "store": "Stored and sorted.",
    }.get(kind, "Ready when you are.")
    return FakeLLM(line)(system, prompt, **kwargs)


def run(fake_llm=False):
    game = FakeGame()
    bridge = ConsoleBridge(game)
    classifier = FakeLLM(classify=True) if fake_llm else llm.complete
    complete = _fake_voice if fake_llm else llm.complete
    now = [0.0]
    interactive = sys.stdin.isatty()
    with tempfile.TemporaryDirectory(prefix="blockbuddy-sim-") as directory:
        director = Director(
            bridge, complete_fn=complete, clock=lambda: now[0],
            memory_path=str(Path(directory) / "memory.json"), start_worker=False,
        )
        director.observe(_snapshot(game))
        try:
            while True:
                try:
                    text = input("> " if interactive else "").strip()
                except EOFError:
                    break
                if not text:
                    continue
                if text in ("/quit", "/exit"):
                    break
                now[0] += 121
                first_event = len(game.events)
                if text.startswith("/event "):
                    try:
                        _event(game, text[len("/event "):].strip())
                    except ValueError as exc:
                        print(str(exc), file=sys.stderr)
                        continue
                elif _is_own(text):
                    print("Ignored a system marker.", file=sys.stderr)
                    continue
                elif text.startswith("/"):
                    print("Use plain chat, /event, or /quit.", file=sys.stderr)
                    continue
                else:
                    parsed = _classify(classifier, text)
                    _handle_intent(
                        parsed, text, bridge.call, bridge.echo, complete,
                        director=director, player_name=game.state.get("player_name", ""),
                    )
                director.observe(_snapshot(game))
                for event in game.events[first_event:]:
                    director.result(event)
                director.process_once()
        except KeyboardInterrupt:
            if interactive:
                print()
        finally:
            director.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake-llm", action="store_true", help="Use keyword-based classification and offline dialogue.")
    args = parser.parse_args(argv)
    return run(fake_llm=args.fake_llm)


if __name__ == "__main__":
    raise SystemExit(main())
