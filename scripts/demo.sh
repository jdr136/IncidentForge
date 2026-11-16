#!/usr/bin/env bash
# scripts/demo.sh — Run a full IncidentForge demo scenario end-to-end
# Requires the API server to be running (make dev-api in another terminal).
# Usage: bash scripts/demo.sh [scenario_id] [provider] [model]

set -euo pipefail

API="http://127.0.0.1:8000"
SCENARIO="${1:-k8s-readiness-path}"
PROVIDER="${2:-openai}"
MODEL="${3:-gpt-4o}"
MAX_WAIT=120  # seconds

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

step()  { printf "\n${BOLD}▶ %s${RESET}\n" "$1"; }
ok()    { printf "  ${GREEN}✓${RESET} %s\n" "$1"; }
info()  { printf "  ${CYAN}→${RESET} %s\n" "$1"; }
warn()  { printf "  ${YELLOW}~${RESET} %s\n" "$1"; }
fail()  { printf "  ${RED}✗${RESET} %s\n" "$1"; exit 1; }

# ── Preflight ───────────────────────────────────────────────────────────────
step "Preflight checks"

if ! curl -sf "${API}/health" > /dev/null 2>&1; then
  fail "API not reachable at ${API}. Run 'make dev-api' in another terminal first."
fi
ok "API is running at ${API}"

# Check scenario exists
SCENARIO_CHECK=$(curl -sf "${API}/api/v1/scenarios/${SCENARIO}?version=1" 2>&1 || true)
if echo "$SCENARIO_CHECK" | grep -q '"id"'; then
  ok "Scenario '${SCENARIO}' found"
else
  fail "Scenario '${SCENARIO}' not found. Run 'make dev-api' and check the scenario catalogue."
fi

# ── Start run ───────────────────────────────────────────────────────────────
step "Starting run: scenario='${SCENARIO}' provider='${PROVIDER}' model='${MODEL}'"

RUN_RESPONSE=$(curl -sf -X POST "${API}/api/v1/runs" \
  -H "Content-Type: application/json" \
  -d "{\"scenario_id\":\"${SCENARIO}\",\"scenario_version\":1,\"provider\":\"${PROVIDER}\",\"model\":\"${MODEL}\",\"temperature\":0}" \
  2>&1 || true)

if ! echo "$RUN_RESPONSE" | grep -q '"run_id"'; then
  # 409 LAB_BUSY?
  if echo "$RUN_RESPONSE" | grep -q "LAB_BUSY"; then
    fail "Lab is busy — another run is in progress. Wait for it to complete or cancel it."
  fi
  fail "Failed to start run: ${RUN_RESPONSE}"
fi

RUN_ID=$(echo "$RUN_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
ok "Run started: ${RUN_ID}"
info "Track live: http://localhost:3000/runs/${RUN_ID}"

# ── Poll for progress ────────────────────────────────────────────────────────
step "Watching run progress (max ${MAX_WAIT}s)"

PREV_STATUS=""
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  sleep 2
  ELAPSED=$((ELAPSED + 2))

  STATUS=$(curl -sf "${API}/api/v1/runs/${RUN_ID}" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','UNKNOWN'))" \
    2>/dev/null || echo "UNKNOWN")

  if [ "$STATUS" != "$PREV_STATUS" ]; then
    printf "  ${CYAN}[%3ds]${RESET} %s\n" "$ELAPSED" "$STATUS"
    PREV_STATUS="$STATUS"
  fi

  case "$STATUS" in
    AWAITING_VERIFICATION)
      ok "AI diagnosis complete — awaiting verification"
      break
      ;;
    COMPLETED)
      ok "Run completed"
      break
      ;;
    FAILED|TIMED_OUT|CANCELLED|INTERRUPTED|UNSUPPORTED|INVALID_SCENARIO)
      fail "Run ended with non-success status: ${STATUS}"
      ;;
  esac
done

if [ "$STATUS" = "AWAITING_VERIFICATION" ]; then
  # ── Trigger verification ──────────────────────────────────────────────────
  step "Triggering fix verification in sandbox"
  VERIFY_RESP=$(curl -sf -X POST "${API}/api/v1/runs/${RUN_ID}/verify" \
    -H "Content-Type: application/json" -d '{}' 2>&1 || true)
  if echo "$VERIFY_RESP" | grep -q '"run_id"'; then
    ok "Verification started"
  else
    warn "Verification may have failed: ${VERIFY_RESP}"
  fi

  # Wait for COMPLETED
  for i in $(seq 1 30); do
    sleep 2
    STATUS=$(curl -sf "${API}/api/v1/runs/${RUN_ID}" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','UNKNOWN'))" \
      2>/dev/null || echo "UNKNOWN")
    if [ "$STATUS" = "COMPLETED" ]; then
      ok "Verification complete — status: COMPLETED"
      break
    fi
  done
fi

# ── Print results ────────────────────────────────────────────────────────────
step "Results"

EXPORT=$(curl -sf "${API}/api/v1/runs/${RUN_ID}/export" 2>/dev/null || echo '{}')

# Extract diagnosis from events
DIAG=$(echo "$EXPORT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
events = data.get('events', [])
for ev in reversed(events):
    if ev.get('type') == 'diagnosis_completed':
        d = ev.get('payload', {}).get('diagnosis', {})
        if d:
            print(f\"Root cause:  {d.get('root_cause_id', 'N/A')}\")
            print(f\"Confidence:  {d.get('confidence', 0)*100:.0f}%\")
            print(f\"Summary:     {d.get('summary', '')[:80]}\")
            rem = d.get('remediation') or {}
            if rem:
                print(f\"Fix:         {rem.get('action_type','?')} on {rem.get('target_ref','?')}\")
            break
" 2>/dev/null || echo "  (Could not extract diagnosis)")

echo "$DIAG" | while IFS= read -r line; do info "$line"; done

printf "\n${BOLD}Run ID:${RESET} ${RUN_ID}\n"
printf "${BOLD}Export:${RESET} ${API}/api/v1/runs/${RUN_ID}/export\n"
printf "${BOLD}UI:${RESET}     http://localhost:3000/runs/${RUN_ID}\n"

printf "\n${GREEN}${BOLD}Demo complete.${RESET}\n"
