#!/usr/bin/env bash
set -euo pipefail

API=${API:-http://127.0.0.1:8000}
JQ=${JQ:-jq}
RUN_ID=${RUN_ID:-$(date +%m%d%H%M%S)}

provider_agent="openclaw:svc-provider-$RUN_ID"
consumer_agent="openclaw:svc-consumer-$RUN_ID"
provider_owner="owner_svc_provider_$RUN_ID"
consumer_owner="owner_svc_consumer_$RUN_ID"
service_id="svc_thread_$RUN_ID"
reply1="SERVICE_THREAD_ROUND1_OK_$RUN_ID"
reply2="SERVICE_THREAD_ROUND2_OK_$RUN_ID"

require_value() {
  local value=$1
  local name=$2
  if [ -z "$value" ] || [ "$value" = "null" ]; then
    echo "missing $name" >&2
    exit 10
  fi
}

wait_for_thread_reply() {
  local token=$1
  local thread_id=$2
  local expect_text=$3
  local attempts=${4:-10}

  while [ "$attempts" -gt 0 ]; do
    resp=$(curl -sS "$API/v1/service-threads/$thread_id/messages" -H "Authorization: Bearer $token")
    if echo "$resp" | $JQ -e --arg expect "$expect_text" '.data[] | select(.role=="assistant" and (.content_text | contains($expect)))' >/dev/null; then
      echo "$resp"
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done

  echo "thread reply not observed: $expect_text" >&2
  echo "$resp" | $JQ
  exit 11
}

echo "1) register provider agent"
provider_resp=$(curl -sS -X POST "$API/v1/agent-link/self-register" \
  -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"$provider_agent\",\"display_name\":\"$provider_agent\",\"owner_profile\":{\"owner_id\":\"$provider_owner\",\"user_id\":\"provider_user_$RUN_ID\"}}")
echo "$provider_resp" | $JQ
provider_token=$(echo "$provider_resp" | $JQ -r '.data.auth_token')
require_value "$provider_token" "provider_token"

echo "2) register consumer agent"
consumer_resp=$(curl -sS -X POST "$API/v1/agent-link/self-register" \
  -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"$consumer_agent\",\"display_name\":\"$consumer_agent\",\"owner_profile\":{\"owner_id\":\"$consumer_owner\",\"user_id\":\"consumer_user_$RUN_ID\"}}")
echo "$consumer_resp" | $JQ
consumer_token=$(echo "$consumer_resp" | $JQ -r '.data.auth_token')
require_value "$consumer_token" "consumer_token"

echo "3) provider publishes listed service"
service_resp=$(curl -sS -X POST "$API/v1/services" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $provider_token" \
  -d "{\"service_id\":\"$service_id\",\"handler_agent_id\":\"$provider_agent\",\"title\":\"Service Thread $RUN_ID\",\"summary\":\"formal service thread flow\",\"visibility\":\"listed\",\"contact_policy\":\"auto_accept\",\"allow_agent_initiated_chat\":true}")
echo "$service_resp" | $JQ
created_service_id=$(echo "$service_resp" | $JQ -r '.data.service_id')
created_handler=$(echo "$service_resp" | $JQ -r '.data.handler_agent_id')
require_value "$created_service_id" "service_id"
if [ "$created_handler" != "$provider_agent" ]; then
  echo "unexpected handler agent: $created_handler" >&2
  exit 12
fi

echo "4) consumer discovers service from directory"
list_resp=$(curl -sS "$API/v1/services" -H "Authorization: Bearer $consumer_token")
echo "$list_resp" | $JQ
if ! echo "$list_resp" | $JQ -e --arg sid "$service_id" '.data[] | select(.service_id == $sid)' >/dev/null; then
  echo "service not visible in directory" >&2
  exit 13
fi

detail_resp=$(curl -sS "$API/v1/services/$service_id" -H "Authorization: Bearer $consumer_token")
echo "$detail_resp" | $JQ
detail_handler=$(echo "$detail_resp" | $JQ -r '.data.handler_agent_id')
if [ "$detail_handler" != "$provider_agent" ]; then
  echo "unexpected service detail handler: $detail_handler" >&2
  exit 14
fi

echo "5) consumer creates thread with opening message"
thread_resp=$(curl -sS -X POST "$API/v1/services/$service_id/threads" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $consumer_token" \
  -d "{\"initiator_agent_id\":\"$consumer_agent\",\"opening_message\":\"service round1 from $consumer_agent\"}")
echo "$thread_resp" | $JQ
thread_id=$(echo "$thread_resp" | $JQ -r '.data.thread.thread_id')
task1=$(echo "$thread_resp" | $JQ -r '.data.task_id')
thread_initiator=$(echo "$thread_resp" | $JQ -r '.data.thread.initiator_agent_id')
thread_handler=$(echo "$thread_resp" | $JQ -r '.data.thread.handler_agent_id')
require_value "$thread_id" "thread_id"
require_value "$task1" "task1"
if [ "$thread_initiator" != "$consumer_agent" ]; then
  echo "unexpected initiator agent: $thread_initiator" >&2
  exit 15
fi
if [ "$thread_handler" != "$provider_agent" ]; then
  echo "unexpected thread handler: $thread_handler" >&2
  exit 16
fi

echo "6) provider agent returns first task.update reply"
update1_resp=$(curl -sS -X POST "$API/v1/agent-link/messages" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $provider_token" \
  -d "{\"payload\":{\"type\":\"task.update\",\"task_id\":\"$task1\",\"state\":\"COMPLETED\",\"message_text\":\"$reply1\",\"metadata\":{\"source\":\"integration-service-thread\"}}}")
echo "$update1_resp" | $JQ

echo "7) consumer reads mirrored assistant reply"
messages_after_first=$(wait_for_thread_reply "$consumer_token" "$thread_id" "$reply1")
echo "$messages_after_first" | $JQ

echo "8) consumer continues second round"
round2_resp=$(curl -sS -X POST "$API/v1/service-threads/$thread_id/messages" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $consumer_token" \
  -d "{\"initiator_agent_id\":\"$consumer_agent\",\"text\":\"service round2 from $consumer_agent\"}")
echo "$round2_resp" | $JQ
task2=$(echo "$round2_resp" | $JQ -r '.data.task_id')
require_value "$task2" "task2"

echo "9) provider agent returns second task.update reply"
update2_resp=$(curl -sS -X POST "$API/v1/agent-link/messages" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $provider_token" \
  -d "{\"payload\":{\"type\":\"task.update\",\"task_id\":\"$task2\",\"state\":\"COMPLETED\",\"message_text\":\"$reply2\",\"metadata\":{\"source\":\"integration-service-thread\"}}}")
echo "$update2_resp" | $JQ

echo "10) consumer confirms second mirrored reply and ordered thread history"
messages_after_second=$(wait_for_thread_reply "$consumer_token" "$thread_id" "$reply2")
echo "$messages_after_second" | $JQ
assistant_count=$(echo "$messages_after_second" | $JQ '[.data[] | select(.role=="assistant")] | length')
user_count=$(echo "$messages_after_second" | $JQ '[.data[] | select(.role=="user")] | length')
if [ "$assistant_count" -lt 2 ] || [ "$user_count" -lt 2 ]; then
  echo "unexpected message counts: assistant=$assistant_count user=$user_count" >&2
  exit 17
fi

echo "Integration test passed: service discovery, cross-tenant thread creation, and follow-up dialog with provider agent verified. Service:$service_id Thread:$thread_id"
