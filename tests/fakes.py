"""Deterministic game and language-model substitutes for offline checks."""
import copy
import json
import re
from collections import deque


class FakeLLM:
    def __init__(self, responses=None, default="", classify=False):
        if isinstance(responses, str):
            responses = [responses]
        self.responses = deque(responses or [])
        self.default = default
        self.calls = []
        self.classify = classify

    def __call__(self, system, prompt, **kwargs):
        self.calls.append({"system": system, "prompt": prompt, **kwargs})
        result = (self.responses.popleft() if self.responses else
                  self._classification(prompt) if self.classify else self.default)
        if isinstance(result, Exception):
            raise result
        return result

    complete = __call__

    @staticmethod
    def _classification(prompt):
        match = re.search(r'Chat line: "(.*)"\s*Return JSON only', str(prompt), re.DOTALL)
        text = match.group(1) if match else str(prompt)
        text = re.sub(r"([a-z])(\d)", r"\1 \2", text.lower()).strip(" .!?")
        text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
        text = re.sub(r"^(?:please|can you|could you|would you)\s+", "", text)
        out = {"intent": "unknown"}
        coordinate = re.search(r"\b(?:go(?:to| to)?|walk to|come to)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\b", text)
        if coordinate:
            out = dict(intent="goto", **dict(zip(("x", "y", "z"), map(int, coordinate.groups()))))
        else:
            resource = re.search(r"\b(mine|dig|chop|craft|make|give|bring|take|store)\b\s*(.*)", text)
            if resource:
                verb, rest = resource.groups()
                intent = {"dig": "mine", "chop": "mine", "make": "craft", "bring": "give"}.get(verb, verb)
                count = re.search(r"\b(\d+)\b", rest)
                rest = re.sub(r"\b\d+\b", "", rest)
                rest = re.sub(r"\b(?:me|some|a|an|the|of|please)\b", "", rest)
                item = "_".join(rest.split())
                if item:
                    if ":" not in item and not item.startswith("#"):
                        if re.search(r"(?:ch|sh|x|z)es$", item):
                            item = item[:-2]
                        elif item.endswith("s") and not item.endswith(("logs", "glass", "planks")):
                            item = item[:-1]
                    out = {"intent": intent, "item": item}
                    if count:
                        out["count"] = int(count.group(1))
            elif re.search(r"\b(?:gear|pickaxe|sword|tools?)\b", text):
                out = {"intent": "gear"}
            elif re.search(r"\b(?:protect|guard(?:ing)?|defend)\b", text):
                out = {"intent": "protect"}
            elif re.search(r"\b(?:stop|halt|cancel)\b", text):
                out = {"intent": "stop"}
            elif re.search(r"\b(?:follow|come|stick close|stay with)\b", text):
                out = {"intent": "follow"}
            elif re.search(r"\b(?:wait|stay)\b", text):
                out = {"intent": "stop"}
            elif re.search(r"\b(?:status|health|inventory|(?:how|where) are you|are you ok)\b", text):
                out = {"intent": "status"}
        return json.dumps(out)


class ResultOnlyBridge:
    """Adapt the Hermes tuple contract to the Actions client contract."""
    def __init__(self, game):
        self.game = game

    def call(self, op, **kwargs):
        ok, result = self.game.call(op, **kwargs)
        return result if ok else None


class FakeGame:
    def __init__(self, state=None, agent_state=None, auto_effects=True):
        self.state = {
            "player_name": "Alex", "health": 20, "hunger": 20,
            "position": {"x": 0, "y": 64, "z": 0}, "inventory": {},
            "world_info": {"time": 1000, "daytime": True, "raining": False},
        }
        self.state.update(copy.deepcopy(state or {}))
        self.agent_state = {
            "name": "Bob", "health": 20, "following": False,
            "mining": False, "inventory": {}, "pos": [1, 64, 0],
        }
        self.agent_state.update(copy.deepcopy(agent_state or {}))
        self.calls = []
        self.commands = []
        self.echoes = []
        self.entries = []
        self.events = []
        self.entities = []
        self.failures = {}
        self.on_command = None
        self.responses = {}
        self.auto_effects = auto_effects

    def chat(self, message):
        entry = {"id": len(self.entries) + 1, "message": str(message)}
        self.entries.append(entry)
        return entry

    def event(self, event_type, **fields):
        event = {"type": event_type, **fields}
        self.events.append(event)
        self.chat("[bbevent] " + json.dumps(event))
        return event

    def result_only(self):
        return ResultOnlyBridge(self)

    def echo(self, text):
        return self.call("echo", text=text)

    def _command(self, text):
        words = text.split()
        if len(words) < 2 or words[0] != "/blockbuddy":
            return
        action = words[1]
        inv = self.agent_state["inventory"]
        if action == "stop":
            self.agent_state.update(following=False, mining=False)
        elif action == "run" and len(words) >= 3:
            if words[2] == "follow":
                self.agent_state["following"] = True
            elif words[2] == "goto" and len(words) == 6:
                self.agent_state["pos"] = [int(value) for value in words[3:]]
            elif words[2] == "stop":
                self.agent_state.update(following=False, mining=False)
        elif action in {"mine", "craft", "give", "take", "store"} and len(words) >= 3:
            item = words[2]
            count = int(words[3]) if len(words) >= 4 else 1
            bare = item.split(":")[-1]
            if bare in {"#logs", "logs"}:
                bare = "oak_log"
            elif action == "mine" and bare == "stone":
                bare = "cobblestone"
            if action in {"mine", "craft", "take"}:
                inv[bare] = inv.get(bare, 0) + count
                moved = count
            else:
                moved = min(count, inv.get(bare, 0))
                inv[bare] = inv.get(bare, 0) - moved
                if action == "give":
                    player = self.state["inventory"]
                    player[bare] = player.get(bare, 0) + moved
            self.agent_state["mining"] = False
            self.event("mined" if action == "mine" else action,
                       item=bare, count=moved, ok=moved > 0)

    def call(self, op, **kwargs):
        self.calls.append((op, dict(kwargs)))
        if op in self.failures:
            return False, self.failures[op]
        if op in self.responses:
            response = self.responses[op]
            return response(**kwargs) if callable(response) else copy.deepcopy(response)
        if op == "state":
            return True, copy.deepcopy(self.state)
        if op == "entities":
            return True, copy.deepcopy(self.entities)
        if op == "chat_log":
            since = kwargs.get("since", 0)
            return True, {"latest": len(self.entries), "entries": copy.deepcopy([
                entry for entry in self.entries if entry["id"] > since
            ])}
        if op == "chat":
            text = kwargs.get("text", "")
            self.commands.append(text)
            if text == "/blockbuddy state":
                self.chat("[agentstate] " + json.dumps(self.agent_state))
            if self.on_command:
                self.on_command(text)
            elif self.auto_effects:
                self._command(text)
            return True, {"sent": text}
        if op == "echo":
            self.echoes.append(kwargs.get("text", ""))
            return True, True
        return True, None

    __call__ = call
