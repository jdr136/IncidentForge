#!/usr/bin/env bash
# scripts/doctor.sh — Check all prerequisites for IncidentForge
# Called by `make doctor`
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { printf "  ${GREEN}✓${NC} %s\n" "$1"; }
fail() { printf "  ${RED}✗${NC} %s\n" "$1"; FAILED=1; }
warn() { printf "  ${YELLOW}~${NC} %s\n" "$1"; }

FAILED=0

echo ""
echo "IncidentForge — prerequisite check"
echo "────────────────────────────────────"

# Docker
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    pass "docker ($(docker --version | cut -d' ' -f3 | tr -d ','))"
  else
    warn "docker installed but daemon not running — start Docker Desktop"
  fi
else
  fail "docker not found — install Docker Desktop: https://www.docker.com/products/docker-desktop"
fi

# kubectl
if command -v kubectl >/dev/null 2>&1; then
  pass "kubectl ($(kubectl version --client --short 2>/dev/null | head -1 || kubectl version --client 2>/dev/null | grep 'Client Version' | awk '{print $3}'))"
else
  fail "kubectl not found — brew install kubectl"
fi

# kind
if command -v kind >/dev/null 2>&1; then
  pass "kind ($(kind version | awk '{print $2}'))"
else
  fail "kind not found — brew install kind"
fi

# uv
if command -v uv >/dev/null 2>&1; then
  pass "uv ($(uv --version | awk '{print $2}'))"
else
  fail "uv not found — brew install uv"
fi

# Node
if command -v node >/dev/null 2>&1; then
  NODE_VER=$(node --version | tr -d 'v')
  NODE_MAJOR=$(echo "$NODE_VER" | cut -d. -f1)
  if [ "$NODE_MAJOR" -ge 20 ]; then
    pass "node (v$NODE_VER)"
  else
    warn "node v$NODE_VER found — v20+ recommended"
  fi
else
  fail "node not found — brew install node"
fi

# npm
if command -v npm >/dev/null 2>&1; then
  pass "npm ($(npm --version))"
else
  fail "npm not found — install with node"
fi

# Python (via uv)
PYTHON_OK=0
if command -v uv >/dev/null 2>&1; then
  PYTHON_VER=$(uv run python --version 2>/dev/null | awk '{print $2}' || true)
  if [ -n "$PYTHON_VER" ]; then
    pass "python $PYTHON_VER (via uv)"
    PYTHON_OK=1
  fi
fi
if [ "$PYTHON_OK" -eq 0 ]; then
  warn "python not yet installed via uv — run: make install-python"
fi

echo ""
echo "Port availability"
echo "────────────────────────────────────"

check_port() {
  local port="$1"
  local name="$2"
  if lsof -i ":$port" >/dev/null 2>&1; then
    warn "port $port in use — $name may fail to start"
  else
    pass "port $port free ($name)"
  fi
}

check_port 8000 "FastAPI"
check_port 3000 "Next.js"
check_port 9297 "AWS Emulator"

echo ""

if [ "$FAILED" -eq 0 ]; then
  printf "${GREEN}All required tools present.${NC} Run: make bootstrap\n\n"
else
  printf "${RED}Some prerequisites are missing.${NC} Install them and re-run: make doctor\n\n"
  exit 1
fi
