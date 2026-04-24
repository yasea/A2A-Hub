"""
原 integrations 路由已拆分为以下模块：
- _shared.py: 共享工具函数
- routes_agent_link.py: Agent Link 核心端点
- routes_openclaw.py: OpenClaw 注册和管理端点（含 Rocket.Chat webhook）
- routes_approvals.py: 审批管理
- routes_deliveries.py: 投递管理
- routes_docs_test.py: docs-test 联调接口
- routes_events.py: SSE 和计量

此文件仅保留空路由模块以向后兼容，所有端点已迁出。
"""