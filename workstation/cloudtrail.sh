#!/usr/bin/env bash
# 【工作机】CloudTrail：最近这个 workshop 角色调的 kms:Sign 是谁发起的（事件通常要 5～15 分钟才出现）
set -euo pipefail
source "$(dirname "$0")/../config.sh"
aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=Sign \
  --max-results 30 --query 'Events[].CloudTrailEvent' --output text | tr '\t' '\n' | ROLE="$ROLE_NAME" python3 -c '
import sys, json, os
for line in sys.stdin:
    if line.strip():
        e = json.loads(line)
        who = e["userIdentity"]["arn"].split("/", 1)[1]
        if who.startswith(os.environ["ROLE"] + "/"):
            print(e["eventTime"], e["sourceIPAddress"], who, e.get("errorCode", "成功"))'
