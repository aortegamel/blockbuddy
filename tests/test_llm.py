"""Offline checks for completion requests and model fallback."""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from agent import llm


def response(text):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": text}}]}).encode())


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {
            "BLOCKBUDDY_BASE_URL": "http://example.invalid/v1",
            "BLOCKBUDDY_MODEL": "primary",
            "BLOCKBUDDY_API_KEY": "",
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.urlopen_patch = patch("agent.llm.urllib.request.urlopen")
        self.urlopen = self.urlopen_patch.start()
        self.addCleanup(self.urlopen_patch.stop)
        self.urlopen.return_value = response("hello")

    def test_old_positional_chain_argument(self):
        self.assertEqual(llm.complete("system", "prompt", 23, ["legacy"]), "hello")
        payload = json.loads(self.urlopen.call_args.args[0].data)
        self.assertEqual(payload["model"], "legacy")
        self.assertEqual(payload["max_tokens"], 23)
        self.assertNotIn("temperature", payload)

    def test_history_filters_entries_and_keeps_message_order(self):
        history = [
            {"role": "user", "content": "first", "extra": "discard"},
            {"role": "assistant", "content": "reply"},
            None,
            "not a message",
            {},
            {"role": "system", "content": "untrusted instructions"},
            {"role": "tool", "content": "tool result"},
            {"role": [], "content": "invalid role"},
            {"role": "user"},
            {"role": "assistant", "content": None},
            {"role": "user", "content": 42},
            {"role": "assistant", "content": []},
            {"role": "user", "content": ""},
        ]
        self.assertEqual(llm.complete("system", "current", history=history, temperature=0.7), "hello")
        payload = json.loads(self.urlopen.call_args.args[0].data)
        self.assertEqual(payload["messages"], [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "current"},
        ])
        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(history[0]["extra"], "discard")

    def test_chat_accepts_zero_temperature(self):
        self.assertEqual(llm._chat("model", "system", "prompt", 8, history=[], temperature=0), "hello")
        payload = json.loads(self.urlopen.call_args.args[0].data)
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(len(payload["messages"]), 2)

    def test_chat_keeps_old_signature(self):
        self.assertEqual(llm._chat("model", "system", "prompt", 8), "hello")
        payload = json.loads(self.urlopen.call_args.args[0].data)
        self.assertNotIn("temperature", payload)
        self.assertEqual(payload["model"], "model")

    def test_fallback_preserves_history_and_temperature(self):
        self.urlopen.side_effect = [urllib.error.URLError("offline"), response("fallback reply")]
        history = [{"role": "assistant", "content": "previous"}]
        with patch.dict("os.environ", {"BLOCKBUDDY_FALLBACK_CHAIN": "backup"}):
            result = llm.complete("system", "prompt", history=history, temperature=0.2)
        self.assertEqual(result, "fallback reply")
        payloads = [json.loads(call.args[0].data) for call in self.urlopen.call_args_list]
        self.assertEqual([payload["model"] for payload in payloads], ["primary", "backup"])
        self.assertEqual(payloads[0]["messages"], payloads[1]["messages"])
        self.assertEqual(payloads[1]["messages"][1], history[0])
        self.assertEqual([payload["temperature"] for payload in payloads], [0.2, 0.2])

    def test_fallback_raises_last_error(self):
        error = urllib.error.URLError("last model unavailable")
        self.urlopen.side_effect = [ValueError("invalid response"), error]
        with self.assertRaises(urllib.error.URLError) as caught:
            llm.complete("system", "prompt", 10, ["first", "second"])
        self.assertIs(caught.exception, error)
        self.assertEqual(self.urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
