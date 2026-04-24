#!/bin/sh
"exec" "python3" "$0" "$@"
"""
远端部署基础检查。

检查内容：
1. /health 是否可访问
2. /v1/openclaw/agents/onboarding 是否返回当前 Agent Link 配置
3. 如果显式提供 SERVICE_ACCOUNT_ISSUER_SECRET，则检查 service account token 是否可以签发
"""

import argparse
import json
import os
import sys

  

from remote_api_common import (
    ApiClient,
    默认平台地址,
    默认租户,
    打印分隔,
    打印提示,
    打印成功,
    签发服务账号令牌,
)
 
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查远端 A2A Hub 基础服务是否可用")

    default_api_base = os.getenv("API_BASE") or os.getenv("API") or 默认平台地址
    default_tenant = os.getenv("TENANT_ID") or 默认租户
    default_secret = os.getenv("SERVICE_ACCOUNT_ISSUER_SECRET") or ""

    parser.add_argument("--api-base", default=default_api_base)
    parser.add_argument("--tenant-id", default=default_tenant)
    parser.add_argument("--issuer-secret", default=default_secret)

    parser.add_argument(
        "--verify-tls",
        action="store_true",
        help="校验 TLS 证书。HTTP 或自签证书场景不需要开启"
    )

    return parser.parse_args()

def main() -> int:
    args = parse_args()
    client = ApiClient(args.api_base, verify_tls=args.verify_tls)

    打印分隔("步骤 1：检查健康接口")
    health = client.get("/health")
    print(json.dumps(health, ensure_ascii=False, indent=2))
    if health.get("status") != "ok":
        raise RuntimeError("/health 未返回 ok")
    打印成功("平台 API 正常")

    打印分隔("步骤 2：检查 Agent Link 对外配置")
    onboarding = client.get("/v1/openclaw/agents/onboarding")["data"]
    print(json.dumps(onboarding, ensure_ascii=False, indent=2))
    打印成功("Agent Link onboarding 可访问")

    打印分隔("步骤 3：检查 service account token 签发")
    if not args.issuer_secret:
        打印提示("未提供 SERVICE_ACCOUNT_ISSUER_SECRET，跳过 token 签发检查；如需检查请设置该环境变量或传 --issuer-secret")
        return 0

    token = 签发服务账号令牌(
        api_base=args.api_base,
        issuer_secret=args.issuer_secret,
        tenant_id=args.tenant_id,
        verify_tls=args.verify_tls,
    )
    print(f"token ：{token}")
    打印成功("service account token 签发成功")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        raise
