import subprocess
import tempfile
import unittest
from pathlib import Path
import json
import os


class AgentLinkOpsScriptsTest(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parents[1]

    def test_reset_server_script_has_valid_bash_syntax(self):
        subprocess.check_call(["bash", "-n", str(self.project_root / "tests" / "reset_server_agent_link_state.sh")])

    def test_reset_client_script_has_valid_bash_syntax(self):
        subprocess.check_call(["bash", "-n", str(self.project_root / "tests" / "reset_client_agent_link_state.sh")])

    def test_reset_client_script_cleans_agent_link_artifacts_without_touching_other_config(self):
        script = self.project_root / "tests" / "reset_client_agent_link_state.sh"
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            (home / "plugins" / "dbim-mqtt").mkdir(parents=True)
            (home / "channels" / "dbim_mqtt" / "ava").mkdir(parents=True)
            (home / "workspace" / "ava" / ".agent-link").mkdir(parents=True)
            (home / "workspace-main" / ".agent-link").mkdir(parents=True)
            (home / "agents" / "ava" / "sessions").mkdir(parents=True)
            (home / "agents" / "main" / "sessions").mkdir(parents=True)

            config = {
                "plugins": {
                    "allow": ["dbim-mqtt", "other-plugin"],
                    "load": {"paths": [str(home / "plugins" / "dbim-mqtt"), "/opt/other"]},
                    "entries": {
                        "dbim-mqtt": {"enabled": True},
                        "other-plugin": {"enabled": True},
                    },
                },
                "channels": {
                    "dbim_mqtt": {
                        "enabled": True,
                        "instances": [
                            {"localAgentId": "ava", "agentId": "openclaw:ava"},
                            {"localAgentId": "main", "agentId": "openclaw:main"},
                        ],
                    },
                    "telegram": {"enabled": True},
                },
            }
            (home / "openclaw.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (home / "channels" / "dbim_mqtt" / "ava" / "state.json").write_text("{}", encoding="utf-8")
            (home / "workspace" / "ava" / ".agent-link" / "install-result.json").write_text("{}", encoding="utf-8")
            (home / "workspace" / "ava" / ".agent-link" / "install-check.log").write_text("log", encoding="utf-8")
            (home / "workspace-main" / ".agent-link" / "install-check.log").write_text("log", encoding="utf-8")
            (home / "agents" / "ava" / "sessions" / "sessions.json").write_text(
                json.dumps(
                    {
                        "agent:ava:main": {"sessionId": "dbim:bad"},
                        "agent:ava:keep": {"sessionId": "normal"},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (home / "agents" / "main" / "sessions" / "sessions.json").write_text(
                json.dumps(
                    {
                        "agent:main:main": {"sessionId": "dbim:another"},
                        "agent:main:keep": {"sessionId": "ok"},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.check_call(
                ["bash", str(script), "--all"],
                env={**os.environ, "OPENCLAW_HOME": str(home)},
            )

            updated = json.loads((home / "openclaw.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["plugins"]["allow"], ["other-plugin"])
            self.assertEqual(updated["plugins"]["load"]["paths"], ["/opt/other"])
            self.assertNotIn("dbim-mqtt", updated["plugins"]["entries"])
            self.assertNotIn("dbim_mqtt", updated["channels"])
            self.assertEqual(updated["channels"]["telegram"], {"enabled": True})
            self.assertFalse((home / "channels" / "dbim_mqtt").exists())
            self.assertFalse((home / "workspace" / "ava" / ".agent-link").exists())
            self.assertFalse((home / "workspace-main" / ".agent-link").exists())

            ava_sessions = json.loads((home / "agents" / "ava" / "sessions" / "sessions.json").read_text(encoding="utf-8"))
            main_sessions = json.loads((home / "agents" / "main" / "sessions" / "sessions.json").read_text(encoding="utf-8"))
            self.assertNotIn("agent:ava:main", ava_sessions)
            self.assertEqual(ava_sessions["agent:ava:keep"]["sessionId"], "normal")
            self.assertNotIn("agent:main:main", main_sessions)
            self.assertEqual(main_sessions["agent:main:keep"]["sessionId"], "ok")


if __name__ == "__main__":
    unittest.main()
