import unittest

from app.sdk.openclaw_plugin import LocalCommandHandler


class AgentLinkPluginTest(unittest.TestCase):
    def test_local_command_handler_echo_mode(self):
        handler = LocalCommandHandler()

        ok, output = handler.handle({"task_id": "task_001", "input_text": "hello"})

        self.assertTrue(ok)
        self.assertIn("task_001", output)
        self.assertIn("hello", output)

    def test_local_command_handler_runs_command(self):
        handler = LocalCommandHandler("python3 -c 'print(\"handled\")'")

        ok, output = handler.handle({"task_id": "task_002"})

        self.assertTrue(ok)
        self.assertEqual(output, "handled")


if __name__ == "__main__":
    unittest.main()
