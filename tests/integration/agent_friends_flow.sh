#!/usr/bin/env bash
set -euo pipefail

API=${API:-http://127.0.0.1:8000}
JQ=${JQ:-jq}

echo "1) register alice"
alice_resp=$(curl -sS -X POST "$API/v1/agent-link/self-register" -H 'Content-Type: application/json' -d '{"agent_id":"openclaw:alice","display_name":"alice","owner_profile":{"owner_id":"owner_alice","user_id":"alice_user"}}')
echo "$alice_resp" | $JQ
alice_token=$(echo "$alice_resp" | $JQ -r '.data.auth_token')

echo "2) register bob"
bob_resp=$(curl -sS -X POST "$API/v1/agent-link/self-register" -H 'Content-Type: application/json' -d '{"agent_id":"openclaw:bob","display_name":"bob","owner_profile":{"owner_id":"owner_bob","user_id":"bob_user"}}')
echo "$bob_resp" | $JQ
bob_token=$(echo "$bob_resp" | $JQ -r '.data.auth_token')

echo "3) create friend request as alice"
create_resp=$(curl -sS -X POST "$API/v1/agents/openclaw:alice/friends" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $alice_token" \
  -d '{"target_agent_id":"openclaw:bob","message":"hi bob"}')
echo "$create_resp" | $JQ
friend_id=$(echo "$create_resp" | $JQ -r '.data.id')

if [ -z "$friend_id" ] || [ "$friend_id" = "null" ]; then
  echo "failed to create friend" >&2
  exit 2
fi

echo "4) bob accepts friend request"
accept_resp=$(curl -sS -X PATCH "$API/v1/agents/openclaw:bob/friends/$friend_id" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $bob_token" \
  -d '{"status":"accepted"}')
echo "$accept_resp" | $JQ
bob_context_id=$(echo "$accept_resp" | $JQ -r '.data.context_id')

if [ -z "$bob_context_id" ] || [ "$bob_context_id" = "null" ]; then
  echo "bob did not get a usable context_id" >&2
  exit 3
fi

echo "5) alice sends message to bob via formal agent-link"
send_resp=$(curl -sS -X POST "$API/v1/agent-link/messages/send" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $alice_token" \
  -d '{"target_agent_id":"openclaw:bob","parts":[{"type":"text/plain","text":"hello bob from alice via agent-link"}]}' )
echo "$send_resp" | $JQ
task_id=$(echo "$send_resp" | $JQ -r '.data.task_id')

if [ -z "$task_id" ] || [ "$task_id" = "null" ]; then
  echo "failed to create task" >&2
  exit 4
fi

echo "6) verify bob received the inbound task and message"
bob_task_resp=$(curl -sS "$API/v1/tasks/$task_id" -H "Authorization: Bearer $bob_token")
echo "$bob_task_resp" | $JQ
bob_task_target=$(echo "$bob_task_resp" | $JQ -r '.data.target_agent_id')
if [ "$bob_task_target" != "openclaw:bob" ]; then
  echo "unexpected bob task target: $bob_task_target" >&2
  exit 5
fi

task_messages_resp=$(curl -sS "$API/v1/tasks/$task_id/messages" -H "Authorization: Bearer $bob_token")
echo "$task_messages_resp" | $JQ
source_agent=$(echo "$task_messages_resp" | $JQ -r '.data[0].source_agent_id')
if [ "$source_agent" != "openclaw:alice" ]; then
  echo "unexpected source agent in bob task: $source_agent" >&2
  exit 6
fi

echo "7) bob replies to alice via formal agent-link"
reply_resp=$(curl -sS -X POST "$API/v1/agent-link/messages/send" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $bob_token" \
  -d '{"target_agent_id":"openclaw:alice","parts":[{"type":"text/plain","text":"hello alice from bob via agent-link"}]}' )
echo "$reply_resp" | $JQ
reply_task_id=$(echo "$reply_resp" | $JQ -r '.data.task_id')

if [ -z "$reply_task_id" ] || [ "$reply_task_id" = "null" ]; then
  echo "failed to create alice inbound task" >&2
  exit 7
fi

echo "8) verify alice received bob's reply task"
alice_task_resp=$(curl -sS "$API/v1/tasks/$reply_task_id" -H "Authorization: Bearer $alice_token")
echo "$alice_task_resp" | $JQ
alice_task_target=$(echo "$alice_task_resp" | $JQ -r '.data.target_agent_id')
if [ "$alice_task_target" != "openclaw:alice" ]; then
  echo "unexpected alice task target: $alice_task_target" >&2
  exit 8
fi

echo "Integration test passed: formal friend flow and bidirectional messaging verified. Friend:$friend_id BobTask:$task_id AliceTask:$reply_task_id"
