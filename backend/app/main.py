"""
A2A Hub — FastAPI 应用入口
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.openapi.utils import get_openapi
from fastapi.openapi.docs import get_swagger_ui_html

from app.api.routes_agents import router as agents_router
from app.api.routes_contexts import router as contexts_router
from app.api.routes_agent_link import router as agent_link_router
from app.api.routes_openclaw import router as openclaw_router
from app.api.routes_approvals import router as approvals_router
from app.api.routes_deliveries import router as deliveries_router
from app.api.routes_docs_test import router as docs_test_router
from app.api.routes_events import router as events_router
from app.api.routes_messages import router as messages_router
from app.api.routes_routing import router as routing_router
from app.api.routes_services import router as services_router
from app.api.routes_service_accounts import router as service_accounts_router
from app.api.routes_tasks import router as tasks_router
from app.api.routes_agent_friends import router as agent_friends_router
from app.core.config import settings
from app.core.logging import setup_logging, get_logger
import app.models  # noqa: F401 — 确保所有 ORM mapper 完成配置

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("A2A Hub 启动", env=settings.APP_ENV, version=settings.APP_VERSION)
    yield
    logger.info("A2A Hub 关闭")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="""
## A2A Hub API

跨平台 Agent 协作中台，提供任务编排、路由、审批与投递能力。

### 认证方式
所有接口（除 `/health`）均需要 JWT Bearer Token。

在右上角 **Authorize** 按钮中填入：`Bearer <token>`

[查看错误记录](/docs/errors)

[项目说明（管理/业务读者）](/docs/readme)

""",
    lifespan=lifespan,
    docs_url=None,
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# 注册路由
app.include_router(contexts_router)
app.include_router(messages_router)
app.include_router(tasks_router)
app.include_router(agents_router)
app.include_router(routing_router)
app.include_router(services_router)
app.include_router(service_accounts_router)
app.include_router(agent_link_router)
app.include_router(openclaw_router)
app.include_router(approvals_router)
app.include_router(deliveries_router)
app.include_router(docs_test_router)
app.include_router(events_router)
app.include_router(agent_friends_router)


@app.get("/health", tags=["system"])
async def health():
    """健康检查，无需认证"""
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/docs", include_in_schema=False)
async def custom_swagger_docs():
    """Swagger UI，并内置 Agent 消息联调窗口。"""
    response = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Docs",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )
    html = response.body.decode("utf-8")
    panel = """
<style>
  :root {
    --agent-test-panel-gap: 18px;
    --agent-test-panel-top: 108px;
  }
  #agent-test-panel {
    position: fixed;
    top: var(--agent-test-panel-top);
    right: var(--agent-test-panel-gap);
    bottom: var(--agent-test-panel-gap);
    z-index: 900;
    width: min(400px, calc(100vw - (var(--agent-test-panel-gap) * 2)));
    overflow: auto;
    background: #111827;
    color: #f9fafb;
    border: 1px solid #334155;
    border-radius: 14px;
    box-shadow: 0 18px 48px rgba(15,23,42,.35);
    font: 13px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    backdrop-filter: blur(12px);
  }
  @media (max-width: 1120px), (max-height: 820px) {
    #agent-test-panel {
      position: static;
      width: auto;
      max-width: 1460px;
      margin: 16px auto 0;
      max-height: none;
    }
  }
  #agent-test-panel header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 14px;
    border-bottom: 1px solid #334155;
    font-weight: 700;
    background: linear-gradient(180deg, rgba(30,41,59,.92), rgba(15,23,42,.92));
  }
  #agent-test-panel header strong { font-size: 13px; }
  #agent-test-panel header .toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 0 0 auto;
  }
  #agent-test-panel .ghost-btn {
    width: 30px;
    height: 30px;
    margin: 0;
    padding: 0;
    border-radius: 8px;
    border: 1px solid #475569;
    background: rgba(15,23,42,.7);
    color: #e2e8f0;
    font-size: 18px;
    line-height: 1;
    cursor: pointer;
  }
  #agent-test-panel .ghost-btn:hover {
    background: rgba(30,41,59,.95);
    border-color: #64748b;
  }
  #agent-test-panel main { padding: 12px 14px; }
  #agent-test-panel.is-collapsed {
    bottom: auto;
    overflow: hidden;
  }
  #agent-test-panel.is-collapsed main {
    display: none;
  }
  #agent-test-panel label { display: block; margin: 8px 0 4px; color: #cbd5e1; }
  #agent-test-panel select, #agent-test-panel textarea, #agent-test-panel button {
    width: 100%;
    box-sizing: border-box;
    border-radius: 8px;
    border: 1px solid #475569;
    padding: 8px;
    transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
  }
  #agent-test-panel select, #agent-test-panel textarea {
    background: #0f172a;
    color: #f8fafc;
  }
  #agent-test-panel select:focus, #agent-test-panel textarea:focus, #agent-test-panel button:focus {
    outline: none;
    border-color: #60a5fa;
    box-shadow: 0 0 0 3px rgba(96,165,250,.16);
  }
  #agent-test-panel button {
    margin-top: 10px;
    background: linear-gradient(180deg, #3b82f6, #2563eb);
    color: white;
    border-color: #2563eb;
    cursor: pointer;
    font-weight: 700;
  }
  #agent-test-panel button:hover:enabled {
    filter: brightness(1.04);
  }
  #agent-test-panel button:disabled {
    opacity: .55;
    cursor: not-allowed;
  }
  #agent-test-panel .button-row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  #agent-test-panel .button-row button {
    min-height: 42px;
  }
  #agent-test-panel pre {
    margin: 10px 0 0;
    padding: 10px;
    border-radius: 8px;
    background: #020617;
    color: #d1fae5;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 250px;
    overflow: auto;
  }
  #agent-test-panel .hint { color: #94a3b8; font-size: 12px; margin-top: 8px; }
  #agent-test-panel .hint code {
    background: rgba(148,163,184,.12);
    color: #e2e8f0;
    border-radius: 6px;
    padding: 1px 5px;
  }
  @media (max-width: 680px) {
    #agent-test-panel .button-row {
      grid-template-columns: 1fr;
    }
  }
  #agent-onboarding-card {
    margin: 18px auto 0;
    max-width: 1460px;
    border: 1px solid #bfdbfe;
    border-radius: 12px;
    background: #eff6ff;
    color: #1e3a8a;
    padding: 14px 16px;
    font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  #agent-onboarding-card strong { color: #1d4ed8; }
  #agent-onboarding-card code {
    background: #dbeafe;
    border-radius: 6px;
    padding: 2px 5px;
  }
  #agent-onboarding-card button, #agent-onboarding-card a {
    display: inline-block;
    margin: 8px 8px 0 0;
    border-radius: 8px;
    border: 1px solid #2563eb;
    background: #2563eb;
    color: white;
    padding: 7px 10px;
    text-decoration: none;
    cursor: pointer;
    font-weight: 700;
  }
  #agent-onboarding-card a.secondary {
    background: white;
    color: #1d4ed8;
  }
  #agent-onboarding-card input {
    width: 260px;
    box-sizing: border-box;
    margin: 8px 8px 0 0;
    border-radius: 8px;
    border: 1px solid #93c5fd;
    background: white;
    color: #0f172a;
    padding: 7px 10px;
  }
  #agent-onboarding-copy-buffer {
    position: fixed;
    left: -9999px;
    top: 0;
    width: 1px;
    height: 1px;
    opacity: 0;
  }
</style>
<div id="agent-onboarding-card">
  <strong>OpenClaw Agent 接入指令：</strong>
  如果要让本机或内网里的 OpenClaw agent 自己完成注册、安装插件、配置 MQTT 长连接，请不要只丢一个裸 URL。
  直接复制 <code>/agent-link/prompt</code> 的完整任务给 agent；它会按步骤安装，重启后继续检查，并把安装结果回报给主人。
  <br>
  <button id="copy-agent-onboarding">复制给 Agent 的完整指令</button>
  <a class="secondary" href="/agent-link/prompt" target="_blank">打开指令文本</a>
  <a class="secondary" href="/agent-link/connect" target="_blank">打开 Agent Runbook</a>
  <a class="secondary" href="/docs/readme" target="_blank">管理/运营说明</a>
  <a class="secondary" href="/docs/services" target="_blank">服务管理</a>
  <a class="secondary" id="agent-error-link" href="/docs/errors" target="_blank">错误记录过滤</a>
  <textarea id="agent-onboarding-copy-buffer" aria-hidden="true" tabindex="-1"></textarea>
</div>
<div id="agent-test-panel">
  <header>
    <strong>Agent 平台消息测试</strong>
    <div class="toolbar">
      <button id="agent-test-toggle" class="ghost-btn" type="button" title="最小化测试面板" aria-label="最小化测试面板">−</button>
    </div>
  </header>
  <main>
    <label for="agent-test-select">已注册 Agent</label>
    <select id="agent-test-select"><option>加载中...</option></select>
    <label for="agent-friends-select">Agent 好友</label>
    <select id="agent-friends-select"><option value="">先选择 agent</option></select>
    <label for="agent-test-message">测试消息</label>
    <textarea id="agent-test-message" rows="3">请只回复：DOCS_AGENT_TEST_OK</textarea>
    <div class="button-row">
      <button id="agent-test-send">以平台名义发送并等待结果</button>
      <button id="agent-test-send-as-agent">以选中 Agent 身份发送</button>
    </div>
    <div class="hint">该窗口调用 <code>docs-test</code> 内部联调接口，自动创建 context、发送消息、轮询 task 和展示 assistant 回复。</div>
    <pre id="agent-test-output">等待操作...</pre>
  </main>
</div>
<script>
(function () {
  const panel = document.getElementById("agent-test-panel");
  const select = document.getElementById("agent-test-select");
  const button = document.getElementById("agent-test-send");
  const message = document.getElementById("agent-test-message");
  const output = document.getElementById("agent-test-output");
  const copyOnboarding = document.getElementById("copy-agent-onboarding");
  const panelToggle = document.getElementById("agent-test-toggle");
  const panelCollapsedKey = "a2a-hub.docs.agent-test-panel.collapsed";

  function setPanelCollapsed(collapsed) {
    if (!panel || !panelToggle) return;
    panel.classList.toggle("is-collapsed", collapsed);
    panelToggle.textContent = collapsed ? "+" : "−";
    panelToggle.title = collapsed ? "展开测试面板" : "最小化测试面板";
    panelToggle.setAttribute("aria-label", panelToggle.title);
    try {
      window.localStorage.setItem(panelCollapsedKey, collapsed ? "1" : "0");
    } catch (_) {
      // ignore storage errors
    }
  }

  if (panel && panelToggle) {
    let initialCollapsed = false;
    try {
      initialCollapsed = window.localStorage.getItem(panelCollapsedKey) === "1";
    } catch (_) {
      initialCollapsed = false;
    }
    setPanelCollapsed(initialCollapsed);
    panelToggle.addEventListener("click", () => {
      setPanelCollapsed(!panel.classList.contains("is-collapsed"));
    });
  }

  function log(value) {
    output.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }

  async function api(path, options) {
    const resp = await fetch(path, options || {});
    const text = await resp.text();
    let data;
    try { data = JSON.parse(text); } catch (_) { data = { error: text }; }
    if (!resp.ok || data.error) {
      throw new Error(JSON.stringify(data.error || data, null, 2));
    }
    return data.data;
  }

  if (copyOnboarding) {
    async function copyText(text) {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }

      const buffer = document.getElementById("agent-onboarding-copy-buffer");
      if (!buffer) return false;
      buffer.value = text;
      buffer.focus();
      buffer.select();
      buffer.setSelectionRange(0, text.length);
      return document.execCommand("copy");
    }

    copyOnboarding.addEventListener("click", async () => {
      try {
        const resp = await fetch("/agent-link/prompt");
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`);
        }
        const text = await resp.text();
        const copied = await copyText(text);
        if (!copied) {
          throw new Error("浏览器拒绝复制");
        }
        copyOnboarding.textContent = "已复制，可直接发给 Agent";
        setTimeout(() => { copyOnboarding.textContent = "复制给 Agent 的完整指令"; }, 2400);
      } catch (err) {
        window.open("/agent-link/prompt", "_blank");
        alert("浏览器阻止了自动复制，已打开指令文本页，请在新页面中全选复制。");
      }
    });
  }

  async function loadAgents() {
    try {
      const agents = await api("/v1/docs-test/agents");
      select.innerHTML = "";
      if (!agents.length) {
        select.innerHTML = "<option value=''>没有已注册 agent</option>";
        return;
      }
      for (const agent of agents) {
        const option = document.createElement("option");
        option.value = JSON.stringify({ tenant_id: agent.tenant_id, agent_id: agent.agent_id });
        option.textContent = `${agent.online ? "在线" : "离线"} | ${agent.public_number || ""} | ${agent.display_name || agent.agent_id.split(":").pop()}`;
        option.title = agent.agent_id;
        select.appendChild(option);
      } 
      log(`已加载 ${agents.length} 个 agent，选择后可直接发送测试消息。`);
      // trigger friends load on change
      select.addEventListener('change', loadFriendsForSelectedAgent);
    } catch (err) {
      log("加载 agent 失败：\\n" + err.message);
    }
  }

  async function loadFriendsForSelectedAgent() {
    const friendsSelect = document.getElementById('agent-friends-select');
    friendsSelect.innerHTML = '<option>加载中...</option>';
    if (!select.value) {
      friendsSelect.innerHTML = '<option value="">先选择 agent</option>';
      return;
    }
    try {
      const selected = JSON.parse(select.value);
      const agentId = selected.agent_id;
      const items = await api(`/v1/docs-test/agents/${encodeURIComponent(agentId)}/friends`);
      friendsSelect.innerHTML = '';
      for (const it of items) {
        const opt = document.createElement('option');
        opt.value = JSON.stringify({ id: it.id, target_agent_id: it.target_agent_id, tenant_id: it.tenant_id });
        const peerPn = it.peer_public_number || "无号码";
        opt.textContent = `${it.status} | ${peerPn}`;
        opt.title = `${it.requester_agent_id} ↔ ${it.target_agent_id}`;
        friendsSelect.appendChild(opt);
      }
      if (!items.length) friendsSelect.innerHTML = '<option value="">无好友记录</option>';
    } catch (err) {
      friendsSelect.innerHTML = '<option value="">加载好友失败</option>';
      log('加载好友失败:' + err.message);
    }
  }
 

  async function pollTask(tenantId, taskId) {
    for (let i = 1; i <= 90; i += 1) {
      const data = await api(`/v1/docs-test/tasks/${encodeURIComponent(taskId)}?tenant_id=${encodeURIComponent(tenantId)}`);
      const messages = data.messages || [];
      const assistant = messages.filter((item) => item.role === "assistant").pop();
      log({
        poll: i,
        state: data.task.state,
        task_id: taskId,
        output_text: data.task.output_text,
        assistant_reply: assistant && assistant.content_text,
        messages,
      });
      if (["COMPLETED", "FAILED", "CANCELED", "EXPIRED"].includes(data.task.state)) return data;
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
    throw new Error("等待任务完成超时");
  }

  button.addEventListener("click", async () => {
    if (!select.value) return;
    button.disabled = true;
    try {
      const selected = JSON.parse(select.value);
      log("正在发送...");
      const sent = await api("/v1/docs-test/messages/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tenant_id: selected.tenant_id,
          target_agent_id: selected.agent_id,
          message: message.value || "请只回复：DOCS_AGENT_TEST_OK",
        }),
      });
      log({ sent, status: "已发送，开始轮询任务状态" });
      await pollTask(sent.tenant_id, sent.task_id);
    } catch (err) {
      log("测试失败：\\n" + err.message);
    } finally {
      button.disabled = false;
    }
  });

  const sendAsAgentButton = document.getElementById('agent-test-send-as-agent');
  sendAsAgentButton.addEventListener('click', async () => {
    if (!select.value) return;
    sendAsAgentButton.disabled = true;
    try {
      const selected = JSON.parse(select.value);
      const agentId = selected.agent_id;
      log('正在以 agent 身份发送...');
      const sent = await api(`/v1/docs-test/agents/${encodeURIComponent(agentId)}/friends/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_agent_id: JSON.parse(document.getElementById('agent-friends-select').value || '{}').target_agent_id || selected.agent_id, message: message.value || '请只回复：DOCS_AGENT_TEST_OK' })
      });
      log({ sent, status: '已发送（以 agent 身份）', info: '注意：此接口为 admin 模拟，不代表真实 agent token 行为' });
      await pollTask(sent.tenant_id, sent.task_id);
    } catch (err) {
      log('发送失败：\\n' + err.message);
    } finally {
      sendAsAgentButton.disabled = false;
    }
  });
 

  loadAgents();
})();
</script>
"""
    if "<body>" in html:
        html = html.replace("<body>", "<body>\n" + panel, 1)
    else:
        html = html.replace("</body>", panel + "\n</body>")
    return HTMLResponse(html)


@app.get("/docs/services", include_in_schema=False)
async def docs_services_page():
    """服务展示与服务对话测试页面。"""
    html = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A2A Hub 服务管理</title>
  <style>
    :root {
      --bg: #0f172a;
      --surface: #1e293b;
      --border: #334155;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --success: #22c55e;
      --danger: #ef4444;
      --radius: 12px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      margin: 0;
      padding: 24px;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    h1 { font-size: 24px; margin-bottom: 20px; color: #fff; }
    h2 { font-size: 16px; margin: 20px 0 12px; color: #e2e8f0; }
    .nav { margin-bottom: 24px; display: flex; gap: 12px; flex-wrap: wrap; }
    .nav a {
      padding: 8px 16px;
      border-radius: 8px;
      text-decoration: none;
      color: #94a3b8;
      border: 1px solid #334155;
      transition: all 0.2s;
    }
    .nav a:hover { background: #1e293b; color: #f1f5f9; }
    .nav a.active { background: #3b82f6; color: white; border-color: #3b82f6; }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px;
      margin-bottom: 20px;
    }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
    .card-title { font-size: 16px; font-weight: 600; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
    .service-card {
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 10px;
      padding: 16px;
      transition: all 0.2s;
    }
    .service-card:hover { border-color: #3b82f6; }
    .service-card .title { font-size: 15px; font-weight: 600; margin-bottom: 8px; }
    .service-card .meta { font-size: 12px; color: #64748b; margin-bottom: 8px; }
    .service-card .summary { font-size: 13px; color: #94a3b8; margin-bottom: 12px; }
    .service-card .online { color: #22c55e; }
    .service-card .offline { color: #ef4444; }
    .service-card .tags { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
    .service-card .tag {
      background: #1e293b;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 11px;
      color: #94a3b8;
    }
    .service-card .actions { display: flex; gap: 8px; }
    .btn {
      padding: 8px 16px;
      border-radius: 8px;
      border: none;
      cursor: pointer;
      font-size: 13px;
      font-weight: 600;
      transition: all 0.2s;
    }
    .btn-primary { background: var(--primary); color: white; }
    .btn-primary:hover { background: var(--primary-hover); }
    .btn-outline { background: transparent; border: 1px solid #475569; color: #e2e8f0; }
    .btn-outline:hover { background: #334155; }
    .btn-sm { padding: 6px 12px; font-size: 12px; }
    input, select, textarea {
      width: 100%;
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 10px 12px;
      color: #f1f5f9;
      font-size: 14px;
      margin-bottom: 12px;
    }
    input:focus, select:focus, textarea:focus {
      outline: none;
      border-color: #3b82f6;
      box-shadow: 0 0 0 3px rgba(59,130,246,0.2);
    }
    textarea { resize: vertical; min-height: 80px; }
    .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    pre {
      background: #020617;
      border-radius: 8px;
      padding: 12px;
      overflow: auto;
      font: 12px/1.5 ui-monospace, monospace;
      color: #d1fae5;
      max-height: 300px;
    }
    .modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.7);
      z-index: 1000;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    .modal.active { display: flex; }
    .modal-content {
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 16px;
      width: 100%;
      max-width: 600px;
      max-height: 90vh;
      overflow: auto;
      padding: 24px;
    }
    .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
    .modal-title { font-size: 18px; font-weight: 600; }
    .modal-close { background: none; border: none; color: #94a3b8; font-size: 24px; cursor: pointer; }
    .message-list { max-height: 400px; overflow: auto; margin-bottom: 16px; }
    .message {
      padding: 10px 14px;
      border-radius: 10px;
      margin-bottom: 10px;
      font-size: 13px;
    }
    .message.user { background: #1e40af; margin-left: 20px; }
    .message.assistant { background: #166534; margin-right: 20px; }
    .message .time { font-size: 10px; opacity: 0.7; margin-top: 4px; }
    .empty { text-align: center; padding: 40px; color: #64748b; }
    .loading { text-align: center; padding: 20px; color: #64748b; }
    @media (max-width: 600px) {
      .form-row { grid-template-columns: 1fr; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>🛠️ A2A Hub 服务管理</h1>
    <div class="nav">
      <a href="/docs" class="">API 文档</a>
      <a href="/docs/services" class="active">服务管理</a>
      <a href="/docs/errors">错误记录</a>
    </div>

    <div class="card">
      <div class="card-header">
        <span class="card-title">📦 已注册服务</span>
        <button class="btn btn-primary btn-sm" onclick="showPublishModal()">+ 发布服务</button>
      </div>
      <div id="service-list" class="grid">
        <div class="loading">加载中...</div>
      </div>
    </div>
  </div>

  <!-- 发布服务弹窗 -->
  <div id="publish-modal" class="modal">
    <div class="modal-content">
      <div class="modal-header">
        <span class="modal-title">发布新服务</span>
        <button class="modal-close" onclick="closeModal('publish-modal')">&times;</button>
      </div>
      <div class="form-row">
        <div>
          <label>Handler Agent ID</label>
          <input id="pub-handler-agent-id" placeholder="openclaw:xxx:agent-name">
        </div>
        <div>
          <label>服务标题</label>
          <input id="pub-title" placeholder="服务名称">
        </div>
      </div>
      <div>
        <label>服务描述</label>
        <textarea id="pub-summary" placeholder="简要描述服务功能"></textarea>
      </div>
      <div class="form-row">
        <div>
          <label>可见性</label>
          <select id="pub-visibility">
            <option value="listed">公开 (listed)</option>
            <option value="unlisted">不公开 (unlisted)</option>
          </select>
        </div>
        <div>
          <label>标签 (逗号分隔)</label>
          <input id="pub-tags" placeholder="充值, 客服">
        </div>
      </div>
      <button class="btn btn-primary" onclick="publishService()" style="width:100%;">发布服务</button>
      <pre id="publish-result" style="margin-top:12px;display:none;"></pre>
    </div>
  </div>

  <!-- 服务对话弹窗 -->
  <div id="chat-modal" class="modal">
    <div class="modal-content" id="chat-modal-content">
      <div class="modal-header">
        <span class="modal-title" id="chat-modal-title">服务对话</span>
        <button class="modal-close" onclick="closeModal('chat-modal')">&times;</button>
      </div>
      <input type="hidden" id="chat-service-id">
      <input type="hidden" id="chat-tenant-id">
      <div id="message-list" class="message-list">
        <div class="empty">开始对话...</div>
      </div>
      <div id="chat-thinking" style="display:none;padding:8px 12px;background:#1e293b;border-radius:8px;margin-bottom:8px;color:#94a3b8;font-size:13px;">
        ⏳ Kavip 正在回复中...
      </div>
      <div style="display:flex;gap:8px;">
        <textarea id="chat-input" placeholder="输入消息..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage();}"></textarea>
        <button class="btn btn-primary" id="chat-send-btn" onclick="sendMessage()" style="height:76px;white-space:nowrap;">发送</button>
      </div>
    </div>
  </div>

  <script>
    const API_BASE = '';
    let currentServiceId = null;
    let currentThreadId = null;
    let currentTenantId = null;
    let pollTimer = null;

    // 调试日志
    console.log('[Service Page] 页面加载开始');
    window.onerror = function(msg, url, line, col, err) {
      console.error('[JS Error]', msg, 'at line', line, ':', err);
      return false;
    };

    async function api(path, options = {}) {
      console.log('[API]', path);
      const resp = await fetch(API_BASE + path, options);
      const text = await resp.text();
      let data;
      try { data = JSON.parse(text); } catch (_) { data = { error: text }; }
      if (!resp.ok || data.error) throw new Error(JSON.stringify(data.error || data, null, 2));
      return data.data;
    }

    async function loadServices() {
      console.log('[loadServices] 开始加载服务列表');
      const list = document.getElementById('service-list');
      if (!list) { console.error('[loadServices] service-list 元素不存在'); return; }
      try {
        const services = await api('/v1/docs-test/services');
        console.log('[loadServices] 获取到服务:', services);
        if (!services.length) {
          list.innerHTML = '<div class="empty">暂无注册服务</div>';
          return;
        }
        list.innerHTML = services.map(s => `
          <div class="service-card">
            <div class="title">${s.title}</div>
            <div class="meta">
              <span class="${s.handler_online ? 'online' : 'offline'}">● ${s.handler_online ? '在线' : '离线'}</span>
              &nbsp;|&nbsp;
              <span>${s.visibility}</span>
              &nbsp;|&nbsp;
              <span>${s.status}</span>
            </div>
            <div class="summary">${s.summary || '无描述'}</div>
            <div class="tags">
              ${(s.tags || []).map(t => `<span class="tag">${t}</span>`).join('')}
            </div>
            <div class="actions">
              <button class="btn btn-primary btn-sm" onclick="openChat('${s.service_id}', '${s.tenant_id}', '${s.title}')">💬 对话</button>
            </div>
          </div>
        `).join('');
      } catch (err) {
        list.innerHTML = `<div class="empty" style="color:#ef4444;">加载失败: ${err.message}</div>`;
      }
    }

    async function publishService() {
      const handlerAgentId = document.getElementById('pub-handler-agent-id').value.trim();
      const title = document.getElementById('pub-title').value.trim();
      const summary = document.getElementById('pub-summary').value.trim();
      const visibility = document.getElementById('pub-visibility').value;
      const tags = document.getElementById('pub-tags').value.split(',').map(t => t.trim()).filter(Boolean);
      const result = document.getElementById('publish-result');

      if (!handlerAgentId || !title) {
        result.style.display = 'block';
        result.textContent = '请填写必填项';
        return;
      }

      try {
        result.style.display = 'block';
        result.textContent = '发布中...';
        const svc = await api('/v1/docs-test/services', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ handler_agent_id: handlerAgentId, title, summary, visibility, tags })
        });
        result.textContent = JSON.stringify({ success: true, service_id: svc.service_id }, null, 2);
        setTimeout(() => { closeModal('publish-modal'); loadServices(); }, 1500);
      } catch (err) {
        result.style.display = 'block';
        result.textContent = '发布失败: ' + err.message;
      }
    }

    async function openChat(serviceId, tenantId, title) {
      document.getElementById('chat-modal-title').textContent = '💬 ' + title;
      document.getElementById('chat-service-id').value = serviceId;
      document.getElementById('chat-tenant-id').value = tenantId;
      document.getElementById('message-list').innerHTML = '<div class="loading">创建会话...</div>';
      openModal('chat-modal');

      try {
        const result = await api(`/v1/docs-test/services/${encodeURIComponent(serviceId)}/send`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: '请只回复：SERVICE_TEST_OK' })
        });
        currentThreadId = result.thread_id;
        currentTenantId = tenantId;
        document.getElementById('chat-input').disabled = true;
        document.getElementById('chat-send-btn').disabled = true;
        document.getElementById('chat-thinking').style.display = 'block';
        document.getElementById('message-list').innerHTML = '<div class="loading">💬 Kavip 正在思考...</div>';
        startPolling(result.thread_id, tenantId);
      } catch (err) {
        document.getElementById('message-list').innerHTML = `<div class="empty" style="color:#ef4444;">创建会话失败: ${err.message}</div>`;
      }
    }

    async function sendMessage() {
      const input = document.getElementById('chat-input');
      const text = input.value.trim();
      if (!text || !currentThreadId) return;
      input.value = '';

      const list = document.getElementById('message-list');
      list.innerHTML += `<div class="message user"><div>${escapeHtml(text)}</div></div>`;
      list.scrollTop = list.scrollHeight;
      document.getElementById('chat-input').disabled = true;
      document.getElementById('chat-send-btn').disabled = true;
      document.getElementById('chat-thinking').style.display = 'block';

      try {
        await api(`/v1/docs-test/services/${encodeURIComponent(document.getElementById('chat-service-id').value)}/send`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text })
        });
        // 继续轮询等待回复
        startPolling(currentThreadId, currentTenantId);
      } catch (err) {
        list.innerHTML += `<div class="empty" style="color:#ef4444;">发送失败: ${err.message}</div>`;
        document.getElementById('chat-input').disabled = false;
        document.getElementById('chat-send-btn').disabled = false;
        document.getElementById('chat-thinking').style.display = 'none';
      }
    }

    function startPolling(threadId, tenantId) {
      let pollCount = 0;
      if (pollTimer) clearInterval(pollTimer);
      console.log('[Polling] 开始轮询, threadId:', threadId);
      pollTimer = setInterval(async () => {
        pollCount++;
        try {
          const messages = await api(`/v1/docs-test/threads/${encodeURIComponent(threadId)}/messages?tenant_id=${encodeURIComponent(tenantId)}`);
          console.log('[Polling] 第', pollCount, '次, 消息数:', messages.length);
          renderMessages(messages);
          const assistant = messages.find(m => m.role === 'assistant' && m.content_text && m.content_text.trim());
          if (assistant) {
            console.log('[Polling] 收到回复:', assistant.content_text);
            clearInterval(pollTimer);
            pollTimer = null;
            document.getElementById('chat-input').disabled = false;
            document.getElementById('chat-send-btn').disabled = false;
            document.getElementById('chat-thinking').style.display = 'none';
          }
          if (pollCount > 60) {
            console.log('[Polling] 超时，停止轮询');
            clearInterval(pollTimer);
            pollTimer = null;
            document.getElementById('chat-thinking').style.display = 'none';
            renderMessages(messages);
          }
        } catch (err) {
          console.error('[Polling] 轮询错误:', err);
        }
      }, 2000);
    }

    function renderMessages(messages) {
      const list = document.getElementById('message-list');
      if (!messages.length) {
        list.innerHTML = '<div class="loading">等待回复...</div>';
        return;
      }
      list.innerHTML = messages.map(m => `
        <div class="message ${m.role}">
          <div>${escapeHtml(m.content_text || '')}</div>
          <div class="time">${m.created_at || ''}</div>
        </div>
      `).join('');
      list.scrollTop = list.scrollHeight;
    }

    async function loadServiceThreads(serviceId) {
      try {
        const threads = await api(`/v1/docs-test/services/${encodeURIComponent(serviceId)}/threads`);
        return threads;
      } catch (err) {
        console.error('加载会话失败:', err);
        return [];
      }
    }

    function showPublishModal() { openModal('publish-modal'); }
    function openModal(id) { document.getElementById(id).classList.add('active'); }
    function closeModal(id) { document.getElementById(id).classList.remove('active'); }
    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    // 点击弹窗背景关闭
    document.querySelectorAll('.modal').forEach(m => {
      m.addEventListener('click', e => { if (e.target === m) m.classList.remove('active'); });
    });

    loadServices();
  </script>
</body>
</html>
"""
    return HTMLResponse(html)


@app.get("/docs/errors", include_in_schema=False)
async def docs_error_records_page(agent_id: str | None = None):
    """Docs 错误记录查看页，支持按 agent_id 过滤。"""
    initial = (agent_id or "").replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    html = f"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A2A Hub Error Records</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      background: #f8fafc;
      color: #0f172a;
      font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .card {{
      max-width: 1380px;
      margin: 0 auto;
      background: white;
      border: 1px solid #cbd5e1;
      border-radius: 14px;
      padding: 18px;
      box-shadow: 0 12px 32px rgba(15, 23, 42, .08);
    }}
    .toolbar {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 14px;
    }}
    input, select, button {{
      border-radius: 8px;
      border: 1px solid #94a3b8;
      padding: 8px 10px;
      font: inherit;
    }}
    button {{
      cursor: pointer;
      background: #1d4ed8;
      border-color: #1d4ed8;
      color: white;
      font-weight: 700;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }}
    th, td {{
      border-top: 1px solid #e2e8f0;
      text-align: left;
      vertical-align: top;
      padding: 10px 8px;
      word-break: break-word;
    }}
    th {{ background: #eff6ff; }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
      color: #334155;
    }}
    .hint {{ color: #475569; margin-bottom: 12px; }}
  </style>
</head>
<body>
  <div class="card">
    <h2 style="margin-top:0;">Agent Link 错误记录</h2>
    <div class="hint">用于排查自注册、MQTT 建连、presence、task.update、回复失败和平台 500 等问题。支持按 agent 过滤。</div>
    <div class="toolbar">
      <input id="agent_id" placeholder="openclaw:mia" value="{initial}">
      <select id="source_side">
        <option value="">全部来源</option>
        <option value="platform">platform</option>
        <option value="agent">agent</option>
      </select>
      <button id="refresh">查询</button>
    </div>
    <table>
      <thead>
        <tr>
          <th style="width: 160px;">时间</th>
          <th style="width: 90px;">来源</th>
          <th style="width: 120px;">阶段</th>
          <th style="width: 110px;">分类</th>
          <th style="width: 140px;">Agent</th>
          <th style="width: 90px;">状态码</th>
          <th>摘要 / 详情</th>
        </tr>
      </thead>
      <tbody id="rows">
        <tr><td colspan="7">加载中...</td></tr>
      </tbody>
    </table>
  </div>
  <script>
  (function () {{
    const agentInput = document.getElementById("agent_id");
    const sourceSelect = document.getElementById("source_side");
    const rows = document.getElementById("rows");
    const refresh = document.getElementById("refresh");

    function esc(value) {{
      return String(value || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    }}

    async function load() {{
      rows.innerHTML = "<tr><td colspan='7'>加载中...</td></tr>";
      const params = new URLSearchParams();
      if (agentInput.value.trim()) params.set("agent_id", agentInput.value.trim());
      if (sourceSelect.value) params.set("source_side", sourceSelect.value);
      params.set("limit", "100");
      const resp = await fetch(`/v1/docs-test/errors?${{params.toString()}}`);
      const body = await resp.json();
      if (!resp.ok || body.error) {{
        rows.innerHTML = `<tr><td colspan="7">${{esc((body.error && body.error.message) || "加载失败")}}</td></tr>`;
        return;
      }}
      const list = body.data || [];
      if (!list.length) {{
        rows.innerHTML = "<tr><td colspan='7'>没有匹配的错误记录</td></tr>";
        return;
      }}
      rows.innerHTML = list.map((item) => `
        <tr>
          <td>${{esc(item.created_at)}}</td>
          <td>${{esc(item.source_side)}}</td>
          <td>${{esc(item.stage)}}</td>
          <td>${{esc(item.category)}}</td>
          <td>${{esc(item.agent_id || "")}}</td>
          <td>${{esc(item.status_code || "")}}</td>
          <td><strong>${{esc(item.summary)}}</strong><pre>${{esc(item.detail || "")}}${{item.payload_json && Object.keys(item.payload_json).length ? "\\n" + esc(JSON.stringify(item.payload_json, null, 2)) : ""}}</pre></td>
        </tr>
      `).join("");
      const url = new URL(window.location.href);
      if (agentInput.value.trim()) url.searchParams.set("agent_id", agentInput.value.trim());
      else url.searchParams.delete("agent_id");
      window.history.replaceState(null, "", url);
    }}

    refresh.addEventListener("click", load);
    agentInput.addEventListener("keydown", (event) => {{
      if (event.key === "Enter") load();
    }});
    load();
  }})();
  </script>
</body>
</html>
"""
    return HTMLResponse(html)


_DOCS_README_HTML_PATH = Path(__file__).resolve().parent / "static" / "docs_readme.html"


@app.get("/docs/readme", include_in_schema=False)
async def docs_readme_page():
    """管理/业务向项目说明：价值、协议、架构与产品规则（非接入排障页）。"""
    html = _DOCS_README_HTML_PATH.read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import uuid
    if any(request.url.path.startswith(prefix) for prefix in ("/v1/agent-link", "/v1/openclaw", "/ws/openclaw", "/agent-link", "/docs-test")):
        from app.services.error_event_service import ErrorEventService
        await ErrorEventService.record_out_of_band(
            source_side="platform",
            stage="unhandled_exception",
            category="server",
            summary="未处理异常",
            request_path=request.url.path,
            status_code=500,
            detail=str(exc),
        )
    logger.error("未处理异常", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"request_id": f"req_{uuid.uuid4().hex[:12]}", "data": None, "error": {"code": "INTERNAL_ERROR", "message": "服务内部错误"}},
    )


def custom_openapi():
    """自定义 OpenAPI schema，统一 Swagger 中的 JWT Bearer 鉴权方案。"""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    components = schema.setdefault("components", {})
    components["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Swagger Authorize 中只需粘贴 JWT token 本体，不要手动加 Bearer 前缀。",
        }
    }

    public_paths = {
        "/health",
        "/v1/openclaw/events/transcript",
        "/v1/openclaw/events/approval",
        "/v1/openclaw/agents/onboarding",
        "/v1/openclaw/agents/bootstrap",
        "/v1/agent-link/manifest",
        "/v1/agent-link/self-register",
        "/agent-link/connect",
        "/agent-link/install/openclaw-aimoo-link.sh",
        "/agent-link/plugins/aimoo-link.tar.gz",
        "/openclaw/agents/connect.md",
        "/v1/rocketchat/webhook",
    }

    # 对需要 JWT 的路径统一绑定 BearerAuth，避免与 FastAPI 默认的 HTTPBearer 名称不一致。
    for path, methods in schema.get("paths", {}).items():
        if path in public_paths:
            continue
        for method, method_info in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}:
                continue
            method_info["security"] = [{"BearerAuth": []}]

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi
