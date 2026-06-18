#!/bin/bash
TOKEN=$(curl -s -X POST "https://multiragapi.snaic.net/auth/token" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"sLjvv#CUTV"}' | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

jq -n --rawfile p /tmp/prompt.txt '{system_prompt: $p}' | \
curl -s -X PUT "https://multiragapi.snaic.net/admin/system-prompt" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @- | grep -o '"updated_at":"[^"]*"'
