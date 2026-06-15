import subprocess
import tempfile
import unittest
from pathlib import Path
import json
import os
import importlib.util
import sys


class AgentLinkOpsScriptsTest(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parents[1]

    def test_reset_server_script_has_valid_bash_syntax(self):
        subprocess.check_call(["bash", "-n", str(self.project_root / "tests" / "reset_server_agent_link_state.sh")])

    def test_reset_agent_link_script_has_valid_bash_syntax(self):
        subprocess.check_call(["bash", "-n", str(self.project_root / "tests" / "reset_agent_link.sh")])

    def test_reset_agent_link_help_describes_side_effects(self):
        output = subprocess.check_output(
            ["bash", str(self.project_root / "tests" / "reset_agent_link.sh"), "--help"],
            text=True,
        )
        self.assertIn("OPENCLAW_HOME", output)
        self.assertIn("openclaw.json", output)
        self.assertIn("sessions.json", output)
        self.assertIn("TOOLS.md", output)
        self.assertIn("remove-plugin", output)
        self.assertIn("remove-remote", output)

    def test_openclaw_owner_friend_cli_flow_has_valid_bash_syntax(self):
        subprocess.check_call(["bash", "-n", str(self.project_root / "tests" / "integration" / "openclaw_owner_friend_cli_flow.sh")])

    def test_openclaw_owner_friend_cli_flow_supports_remote_target_agent(self):
        body = (self.project_root / "tests" / "integration" / "openclaw_owner_friend_cli_flow.sh").read_text(encoding="utf-8")
        self.assertIn("TARGET_OPENCLAW_HOST", body)
        self.assertIn("run_remote_openclaw", body)
        self.assertIn('"friends" in obj', body)

    def test_agent_friends_doc_uses_current_invite_url_path(self):
        body = (self.project_root / "docs" / "agent-friends.md").read_text(encoding="utf-8")
        self.assertIn("/v1/agents/invite?token=", body)
        self.assertNotIn("/agent-link/invite?token=", body)


if __name__ == "__main__":
    unittest.main()
