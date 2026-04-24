import unittest
import subprocess
import os
import shutil
from pathlib import Path

from app.sdk.openclaw_plugin import LocalCommandHandler


class AgentLinkPluginTest(unittest.TestCase):
    def setUp(self):
        self.project_root = Path(__file__).resolve().parents[1]
        self.plugin_root = self.project_root / "backend" / "dbim-mqtt-plugin"
        self.node_env = os.environ.copy()
        self.node_available = shutil.which("node") is not None
        repo_node_modules = self.plugin_root / "node_modules"
        openclaw_home = Path(os.environ.get("OPENCLAW_HOME", Path.home() / ".openclaw"))
        local_plugin_node_modules = openclaw_home / "plugins" / "dbim-mqtt" / "node_modules"
        self.node_modules_path = None
        for candidate in (repo_node_modules, local_plugin_node_modules):
            if candidate.exists():
                self.node_modules_path = candidate
                self.node_env["NODE_PATH"] = str(candidate)
                break

    def run_node_script(self, script: str) -> str:
        if not self.node_available:
            self.skipTest("node 不可用，跳过插件 Node 测试")
        if self.node_modules_path is None:
            self.skipTest("未找到 dbim-mqtt 插件依赖目录，跳过 Node 集成测试")
        return subprocess.check_output(["node", "-e", script], text=True, env=self.node_env).strip()

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

    def test_dbim_mqtt_multi_instance_config_resolves_two_agents(self):
        script = f"""
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {{ resolvePluginInstances }} = require("{(self.plugin_root / 'lib' / 'config.js').as_posix()}");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "dbim-mqtt-"));
fs.mkdirSync(path.join(root, ".openclaw", "workspace", "ava"), {{ recursive: true }});
fs.mkdirSync(path.join(root, ".openclaw", "workspace", "mia"), {{ recursive: true }});
const previousHome = process.env.HOME;
process.env.HOME = root;
const instances = resolvePluginInstances({{
  config: {{
            channels: {{
              dbim_mqtt: {{
                enabled: true,
                replyMode: "openclaw-agent",
                instances: [
                  {{
                    localAgentId: "ava",
                    agentId: "ava",
                    connectUrl: "http://example.com:1880/agent-link/connect"
                  }},
                  {{
                    localAgentId: "mia",
                    agentId: "mia",
                    connectUrl: "http://example.com:1880/agent-link/connect"
                  }}
                ]
              }}
            }}
  }}
}});
process.env.HOME = previousHome;
console.log(JSON.stringify(instances));
"""
        output = self.run_node_script(script)
        self.assertIn('"agentId":"ava"', output)
        self.assertIn('"agentId":"mia"', output)
        self.assertIn('/channels/dbim_mqtt/ava/state.json', output)
        self.assertIn('/channels/dbim_mqtt/mia/state.json', output)
        self.assertIn('/workspace/ava/USER.md', output)
        self.assertIn('/workspace/mia/USER.md', output)

    def test_dbim_mqtt_prefers_modern_workspace_layout(self):
        script = f"""
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {{ resolvePluginInstances }} = require("{(self.plugin_root / 'lib' / 'config.js').as_posix()}");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "dbim-mqtt-"));
fs.mkdirSync(path.join(root, ".openclaw", "workspace", "ava"), {{ recursive: true }});
const previousHome = process.env.HOME;
process.env.HOME = root;
const instances = resolvePluginInstances({{
  config: {{
    channels: {{
      dbim_mqtt: {{
        instances: [{{ localAgentId: "ava", agentId: "ava", connectUrl: "http://example.com" }}],
      }},
    }},
  }},
}});
process.env.HOME = previousHome;
console.log(JSON.stringify(instances));
"""
        output = self.run_node_script(script)
        self.assertIn('/workspace/ava/USER.md', output)
        self.assertNotIn('/workspace-ava/USER.md', output)

    def test_dbim_mqtt_falls_back_to_legacy_workspace_layout_when_only_legacy_exists(self):
        script = f"""
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {{ resolvePluginInstances }} = require("{(self.plugin_root / 'lib' / 'config.js').as_posix()}");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "dbim-mqtt-"));
fs.mkdirSync(path.join(root, ".openclaw", "workspace-ava"), {{ recursive: true }});
const previousHome = process.env.HOME;
process.env.HOME = root;
const instances = resolvePluginInstances({{
  config: {{
    channels: {{
      dbim_mqtt: {{
        instances: [{{ localAgentId: "ava", agentId: "ava", connectUrl: "http://example.com" }}],
      }},
    }},
  }},
}});
process.env.HOME = previousHome;
console.log(JSON.stringify(instances));
"""
        output = self.run_node_script(script)
        self.assertIn('/workspace-ava/USER.md', output)
        self.assertNotIn('/workspace/ava/USER.md', output)

    def test_dbim_mqtt_infers_agent_from_user_md_when_config_is_main(self):
        script = f"""
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {{ resolvePluginInstances }} = require("{(self.plugin_root / 'lib' / 'config.js').as_posix()}");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "dbim-mqtt-"));
const workspace = path.join(root, ".openclaw", "workspace", "main");
fs.mkdirSync(workspace, {{ recursive: true }});
fs.writeFileSync(path.join(workspace, "USER.md"), "agent_id: ava\\n", "utf8");
fs.writeFileSync(path.join(workspace, "SOUL.md"), "Local Agent ID: ava\\n", "utf8");
const previousHome = process.env.HOME;
process.env.HOME = root;
const instances = resolvePluginInstances({{
  config: {{
    agents: {{
      list: [{{ id: "main" }}],
    }},
    channels: {{
      dbim_mqtt: {{
        agentId: "main",
        userProfileFile: "~/.openclaw/workspace/main/USER.md",
        connectUrl: "http://example.com",
      }},
    }},
  }},
}});
process.env.HOME = previousHome;
console.log(JSON.stringify(instances));
"""
        output = self.run_node_script(script)
        self.assertIn('"agentId":"ava"', output)
        self.assertIn('"localAgentId":"ava"', output)
        self.assertIn('/workspace/main/USER.md', output)

    def test_dbim_mqtt_reads_agent_summary_from_soul_md(self):
        script = f"""
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {{ readAgentSummary }} = require("{(self.plugin_root / 'lib' / 'owner-profile.js').as_posix()}");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "dbim-mqtt-"));
const workspace = path.join(root, ".openclaw", "workspace", "ava");
fs.mkdirSync(workspace, {{ recursive: true }});
fs.writeFileSync(path.join(workspace, "SOUL.md"), "## Agent Summary\\n擅长多轮对话和技术排障。\\n", "utf8");
const previousHome = process.env.HOME;
process.env.HOME = root;
const summary = readAgentSummary({{ agentId: "ava" }});
process.env.HOME = previousHome;
console.log(summary);
"""
        output = self.run_node_script(script)
        self.assertEqual(output, "擅长多轮对话和技术排障。")

    def test_dbim_mqtt_local_profile_uses_local_agent_id(self):
        script = f"""
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {{ readAgentSummary, readOwnerProfile }} = require("{(self.plugin_root / 'lib' / 'owner-profile.js').as_posix()}");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "dbim-mqtt-"));
const workspace = path.join(root, ".openclaw", "workspace", "local-ava");
fs.mkdirSync(workspace, {{ recursive: true }});
fs.writeFileSync(path.join(workspace, "USER.md"), "owner: test\\n", "utf8");
fs.writeFileSync(path.join(workspace, "SOUL.md"), "## Agent Summary\\n本地 ava 能力说明。\\n", "utf8");
const previousHome = process.env.HOME;
process.env.HOME = root;
const cfg = {{ agentId: "openclaw:platform-ava", localAgentId: "local-ava" }};
const summary = readAgentSummary(cfg);
const owner = readOwnerProfile(cfg);
process.env.HOME = previousHome;
console.log(JSON.stringify([summary, owner.local_agent_id, owner.user_md_path.includes("/workspace/local-ava/USER.md")]));
"""
        output = self.run_node_script(script)
        self.assertEqual(output, '["本地 ava 能力说明。","local-ava",true]')

    def test_dbim_mqtt_openclaw_runner_uses_local_agent_id(self):
        script = f"""
const {{ DbimMqttChannel }} = require("{(self.plugin_root / 'lib' / 'channel.js').as_posix()}");
const channel = new DbimMqttChannel({{ logger: {{ info: () => {{}}, warn: () => {{}}, error: () => {{}} }} }}, {{
  agentId: "openclaw:platform-ava",
  localAgentId: "local-ava",
  openClawCommand: "openclaw",
  openClawTimeoutSec: 9,
}});
channel.spawnProcess = async (command, args) => {{
  console.log(JSON.stringify([command, args]));
  return {{
    ok: true,
    stdout: JSON.stringify({{ payloads: [{{ text: "ok" }}] }}),
    stderr: "",
    combinedText: "",
  }};
}};
channel.runOpenClawAgent({{ task_id: "task_1", input_text: "hello" }}).catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
        output = self.run_node_script(script)
        self.assertIn('"--agent","local-ava"', output)
        self.assertIn('"--timeout","9"', output)

    def test_dbim_mqtt_channel_serializes_tasks_per_instance(self):
        script = f"""
const {{ DbimMqttChannel }} = require("{(self.plugin_root / 'lib' / 'channel.js').as_posix()}");

function delay(ms) {{
  return new Promise((resolve) => setTimeout(resolve, ms));
}}

const events = [];
const api = {{
  logger: {{
    info: () => {{}},
    error: () => {{}},
    warn: () => {{}},
  }},
}};
const channel = new DbimMqttChannel(api, {{
  agentId: "mia",
  localAgentId: "mia",
  replyMode: "echo",
  recordOpenClawSession: false,
}});

channel.recordSessionInbound = async () => {{}};
channel.recordSessionAssistant = async () => {{}};
channel.runHandler = async (payload) => {{
  events.push(`start:${{payload.task_id}}`);
  await delay(payload.task_id === "task_1" ? 80 : 0);
  events.push(`finish:${{payload.task_id}}`);
  return {{ ok: true, output: payload.task_id }};
}};

const messageApi = {{
  send: async (payload) => {{
    events.push(`send:${{payload.type}}:${{payload.task_id}}:${{payload.state || ""}}`);
  }},
}};

Promise.all([
  channel.handleTask({{ type: "task.dispatch", task_id: "task_1", input_text: "one" }}, messageApi),
  channel.handleTask({{ type: "task.dispatch", task_id: "task_2", input_text: "two" }}, messageApi),
]).then(() => {{
  console.log(JSON.stringify(events));
}}).catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
        output = self.run_node_script(script)
        self.assertEqual(
            output,
            '["send:task.ack:task_1:","start:task_1","finish:task_1","send:task.update:task_1:COMPLETED","send:task.ack:task_2:","start:task_2","finish:task_2","send:task.update:task_2:COMPLETED"]',
        )

    def test_dbim_mqtt_presence_auth_expired_detection(self):
        script = f"""
const {{ isAuthExpiredResponse }} = require("{(self.plugin_root / 'lib' / 'agent-link-core' / 'presence.js').as_posix()}");
console.log(JSON.stringify([
  isAuthExpiredResponse({{ status: 401, text: '{{"detail":"Token 已过期"}}' }}),
  isAuthExpiredResponse({{ status: 401, text: '{{"detail":"Token 无效"}}' }}),
  isAuthExpiredResponse({{ status: 500, text: 'Token 已过期' }}),
]));
"""
        output = self.run_node_script(script)
        self.assertEqual(output, "[true,false,false]")

    def test_dbim_mqtt_writes_install_result_mirrors_when_online(self):
        script = f"""
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {{ writeInstallResultMirrors }} = require("{(self.plugin_root / 'lib' / 'agent-link-core' / 'runtime.js').as_posix()}");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "dbim-report-"));
const userProfileFile = path.join(root, "workspace-main", "USER.md");
const stateFile = path.join(root, "channels", "dbim_mqtt", "main", "state.json");
fs.mkdirSync(path.dirname(userProfileFile), {{ recursive: true }});
fs.mkdirSync(path.dirname(stateFile), {{ recursive: true }});
writeInstallResultMirrors({{
  agentId: "main",
  localAgentId: "main",
  connectUrl: "https://hub.example.com/agent-link/connect",
  userProfileFile,
  stateFile,
}}, {{
  status: "online",
  agentId: "openclaw:main",
  tenantId: "owner_main",
}});
const workspaceReport = JSON.parse(fs.readFileSync(path.join(root, "workspace-main", ".agent-link", "install-result.json"), "utf8"));
const hostReport = JSON.parse(fs.readFileSync(path.join(root, "channels", "dbim_mqtt", "main", "install-result.json"), "utf8"));
console.log(JSON.stringify([workspaceReport.status, workspaceReport.stage, hostReport.state.tenantId]));
"""
        output = self.run_node_script(script)
        self.assertEqual(output, '["success","install_online","owner_main"]')

    def test_dbim_mqtt_retry_forces_reload_after_bootstrap_error(self):
        script = f"""
const {{ AgentLinkCoreRuntime }} = require("{(self.plugin_root / 'lib' / 'agent-link-core' / 'runtime.js').as_posix()}");
const calls = [];
const runtime = new AgentLinkCoreRuntime({{ logger: {{ info: () => {{}}, warn: () => {{}}, error: () => {{}} }} }}, {{
  agentId: "main",
  localAgentId: "main",
  stateFile: "/tmp/dbim-test-state.json",
  connectUrlFile: "/tmp/dbim-test-connect.txt",
  bootstrapRetryIntervalSec: 5,
}}, async () => {{}});
runtime.started = true;
runtime.stateStore = {{ write: () => {{}} }};
runtime.reload = async (reason, options = {{}}) => {{
  calls.push([reason, options.force === true]);
}};
const originalSetTimeout = global.setTimeout;
global.setTimeout = (fn) => {{
  fn();
  return {{ unref() {{}} }};
}};
runtime.scheduleRetry();
global.setTimeout = originalSetTimeout;
console.log(JSON.stringify(calls));
"""
        output = self.run_node_script(script)
        self.assertEqual(output, '[["retry",true]]')

    def test_dbim_mqtt_cli_requires_agent_when_multiple_instances_exist(self):
        script = f"""
const {{ selectCliTarget }} = require("{(self.plugin_root / 'lib' / 'cli.js').as_posix()}");
try {{
  selectCliTarget([
    {{ localAgentId: "main", platformAgentId: "openclaw:main", helperPath: "/tmp/main" }},
    {{ localAgentId: "ava", platformAgentId: "openclaw:ava", helperPath: "/tmp/ava" }},
  ], "");
}} catch (error) {{
  console.log(String(error.message || error));
}}
"""
        output = self.run_node_script(script)
        self.assertIn("multiple dbim_mqtt agents configured", output)
        self.assertIn("main, ava", output)

    def test_dbim_mqtt_cli_accepts_local_or_platform_agent_id(self):
        script = f"""
const {{ selectCliTarget }} = require("{(self.plugin_root / 'lib' / 'cli.js').as_posix()}");
const targets = [
  {{ localAgentId: "main", platformAgentId: "openclaw:main", config: {{ userProfileFile: "/tmp/main/USER.md" }} }},
  {{ localAgentId: "ava", platformAgentId: "openclaw:ava", config: {{ userProfileFile: "/tmp/ava/USER.md" }} }},
];
const byLocal = selectCliTarget(targets, "ava");
const byPlatform = selectCliTarget(targets, "openclaw:main");
console.log(JSON.stringify([byLocal.localAgentId, byPlatform.platformAgentId]));
"""
        output = self.run_node_script(script)
        self.assertEqual(output, '["ava","openclaw:main"]')

    def test_dbim_mqtt_writes_local_runbook_by_default(self):
        script = f"""
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const cp = require("node:child_process");
const {{ writeAgentLinkLocalControl }} = require("{(self.plugin_root / 'lib' / 'agent-link-core' / 'runtime.js').as_posix()}");
const {{ helperSource }} = require("{(self.plugin_root / 'lib' / 'agent-link-core' / 'local-control.js').as_posix()}");
function runCli(command) {{
  const env = {{
    ...process.env,
    DBIM_MQTT_CLI_CONFIG_JSON: JSON.stringify({{
      connectUrl: "https://hub.example.com/agent-link/connect",
      agentId: "openclaw:main",
      localAgentId: "main",
      userProfileFile,
      httpTimeoutMs: 1234,
      publicFriendToolsUrl: "https://hub.example.com/agent-link/friend-tools",
    }}),
  }};
  return JSON.parse(cp.execFileSync(process.execPath, ["-e", helperSource(), "_", command], {{ env, encoding: "utf8" }}));
}}
const root = fs.mkdtempSync(path.join(os.tmpdir(), "dbim-control-"));
const userProfileFile = path.join(root, "workspace-main", "USER.md");
fs.mkdirSync(path.dirname(userProfileFile), {{ recursive: true }});
fs.writeFileSync(userProfileFile, "owner: test\\n", "utf8");
const result = writeAgentLinkLocalControl({{
  agentId: "main",
  localAgentId: "main",
  connectUrl: "https://hub.example.com/agent-link/connect",
  userProfileFile,
  httpTimeoutMs: 1234,
}}, {{
  connectUrl: "https://hub.example.com/agent-link/connect",
  agentId: "openclaw:main",
  tenantId: "owner_main",
}});
fs.writeFileSync(path.join(root, "workspace-main", ".agent-link", "install-result.json"), JSON.stringify({{
  status: "success",
  stage: "install_online",
  state: {{ status: "online" }},
  updatedAt: "2026-04-23T00:00:00Z",
}}), "utf8");
const runbook = fs.readFileSync(result.runbookPath, "utf8");
const status = runCli("status");
const urls = runCli("urls");
const toolsExists = fs.existsSync(path.join(root, "workspace-main", "TOOLS.md"));
console.log(JSON.stringify([
  runbook.includes("openclaw dbim-mqtt --agent main"),
  runbook.includes("A2A Hub Agent Link 好友操作"),
  runbook.includes("agent-linkctl"),
  status.install_status,
  status.runtime_status,
  urls.friend_tools_url,
  toolsExists,
]));
"""
        output = self.run_node_script(script)
        self.assertEqual(
            output,
            '[true,true,false,"success","online","https://hub.example.com/agent-link/friend-tools",false]',
        )

    def test_dbim_mqtt_writes_workspace_tools_only_when_enabled(self):
        script = f"""
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {{ writeAgentLinkLocalControl }} = require("{(self.plugin_root / 'lib' / 'agent-link-core' / 'runtime.js').as_posix()}");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "dbim-control-"));
const userProfileFile = path.join(root, "workspace-main", "USER.md");
fs.mkdirSync(path.dirname(userProfileFile), {{ recursive: true }});
fs.writeFileSync(userProfileFile, "owner: test\\n", "utf8");
writeAgentLinkLocalControl({{
  agentId: "main",
  localAgentId: "main",
  connectUrl: "https://hub.example.com/agent-link/connect",
  userProfileFile,
  writeWorkspaceTools: true,
}}, {{
  connectUrl: "https://hub.example.com/agent-link/connect",
  agentId: "openclaw:main",
  tenantId: "owner_main",
}});
const tools = fs.readFileSync(path.join(root, "workspace-main", "TOOLS.md"), "utf8");
console.log(JSON.stringify([
  tools.includes("A2A Hub Agent Link"),
  tools.includes("openclaw dbim-mqtt --agent main send openclaw:ava"),
  tools.includes("agent-linkctl"),
]));
"""
        output = self.run_node_script(script)
        self.assertEqual(output, '[true,true,false]')


if __name__ == "__main__":
    unittest.main()
