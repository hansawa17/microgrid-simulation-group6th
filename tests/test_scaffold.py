"""仅校验协作样例；不代表业务模型或硬件已实现。标准库，无额外依赖。"""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ScaffoldTests(unittest.TestCase):
    def test_config(self):
        config = json.loads((ROOT / "common/config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(config["network"]["server_bind"], "0.0.0.0")
        self.assertNotEqual(config["network"]["server_host"], "0.0.0.0")
        self.assertTrue(0 < config["network"]["server_port"] < 65536)
        self.assertTrue(config["control"]["b_closed_loop"])
        self.assertFalse(config["control"]["c_closed_loop"])

    def test_message_examples(self):
        messages = json.loads((ROOT / "common/messages.example.json").read_text(encoding="utf-8"))
        directions = {
            "state": {("A", "B")},
            "dispatch": {("B", "A")},
            "wind_action": {("C", "A")},
            "parameter_update": {("B", "A"), ("C", "A")},
            "ack": {("A", "C")},
        }
        for message in messages:
            self.assertEqual(message["version"], 1)
            self.assertIn(
                (message["source"], message["target"]), directions[message["type"]]
            )
            self.assertIsInstance(message["seq"], int)
            self.assertGreaterEqual(message["sim_time_s"], 0)
            frame = (json.dumps(message, allow_nan=False) + "\n").encode("utf-8")
            self.assertLessEqual(len(frame), 4096)
            self.assertEqual(json.loads(frame), message)


if __name__ == "__main__":
    unittest.main()
