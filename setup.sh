#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NAMESPACE="hivemind"
SECRET_NAME="orchestrator-env"
ENV_FILE="$SCRIPT_DIR/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}  $1${NC}"; }
ok()    { echo -e "${GREEN}  $1${NC}"; }
warn()  { echo -e "${YELLOW}  $1${NC}"; }
err()   { echo -e "${RED}  $1${NC}"; }

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo -e "  ${BOLD}HiveMind – Setup${NC}"
echo "═══════════════════════════════════════════════════════════════"
echo ""

if ! command -v kubectl &>/dev/null; then
  err "kubectl nicht gefunden. Bitte installieren: https://kubernetes.io/docs/tasks/tools/"
  exit 1
fi

if ! kubectl cluster-info &>/dev/null 2>&1; then
  err "Kein K8s-Cluster erreichbar. Ist kubectl korrekt konfiguriert?"
  exit 1
fi

kubectl get namespace "$NAMESPACE" &>/dev/null 2>&1 || {
  info "Namespace '$NAMESPACE' nicht gefunden – erstelle..."
  kubectl create namespace "$NAMESPACE"
  ok "Namespace erstellt"
}

env_val() {
  local key="$1"
  if [[ -f "$ENV_FILE" ]]; then
    local val
    val=$(grep "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)
    echo "$val"
  fi
}

prompt_val() {
  local key="$1"
  local label="$2"
  local default="$3"
  local is_secret="${4:-false}"

  if [[ -n "$default" ]]; then
    if $is_secret; then
      printf "  ${CYAN}${label}${NC} [*****]: " >&2
    else
      printf "  ${CYAN}${label}${NC} [${default}]: " >&2
    fi
  else
    printf "  ${CYAN}${label}${NC}: " >&2
  fi
  local user_val
  read -r user_val

  if [[ -n "$user_val" ]]; then
    echo "$user_val"
  else
    echo "$default"
  fi
}

echo -e "${BOLD}  Konfiguration${NC}"
echo "  ───────────────────────────────────────────────────────────"
echo "  Werte aus .env werden als Defaults vorgeschlagen."
echo "  Leere Eingabe uebernimmt den Default-Wert."
echo ""

if [[ ! -f "$ENV_FILE" ]]; then
  warn ".env nicht gefunden – kopiere .env.example"
  cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
fi

GITLAB_HOST=$(prompt_val "GITLAB_HOST" "GitLab Host" "$(env_val GITLAB_HOST)")
GIT_USER=$(prompt_val "GIT_USER" "Git User" "$(env_val GIT_USER)")
GITLAB_TOKEN=$(prompt_val "GITLAB_TOKEN" "GitLab Token (PAT)" "$(env_val GITLAB_TOKEN)" true)
OLLAMA_HOST=$(prompt_val "OLLAMA_HOST" "Ollama Host" "$(env_val OLLAMA_HOST)")
OLLAMA_BASE_URL=$(prompt_val "OLLAMA_BASE_URL" "Ollama Base URL (/v1)" "$(env_val OLLAMA_BASE_URL)")
OLLAMA_MODEL=$(prompt_val "OLLAMA_MODEL" "Ollama Model" "$(env_val OLLAMA_MODEL)")
OPENCODE_MODEL=$(prompt_val "OPENCODE_MODEL" "OpenCode Model" "$(env_val OPENCODE_MODEL)")
OLLAMA_CLOUD_API_KEY=$(prompt_val "OLLAMA_CLOUD_API_KEY" "Ollama Cloud API Key (optional)" "$(env_val OLLAMA_CLOUD_API_KEY)" true)
AGENT_IMAGE=$(prompt_val "AGENT_IMAGE" "Agent Image" "$(env_val AGENT_IMAGE)")
BRANCH_FALLBACK_ORDER=$(prompt_val "BRANCH_FALLBACK_ORDER" "Branch Fallback Order" "$(env_val BRANCH_FALLBACK_ORDER)")
DRY_RUN=$(prompt_val "DRY_RUN" "Dry Run (true/false)" "$(env_val DRY_RUN)")

MISSING=""
[[ -z "$GITLAB_HOST" ]] && MISSING="$MISSING GITLAB_HOST"
[[ -z "$OLLAMA_HOST" ]] && MISSING="$MISSING OLLAMA_HOST"
[[ -z "$OLLAMA_MODEL" ]] && MISSING="$MISSING OLLAMA_MODEL"
[[ -z "$OPENCODE_MODEL" ]] && MISSING="$MISSING OPENCODE_MODEL"

if [[ -n "$MISSING" ]]; then
  err "Pflichtwerte fehlen:$MISSING"
  exit 1
fi

echo ""
echo -e "${BOLD}  K8s Secret erstellen${NC}"
echo "  ───────────────────────────────────────────────────────────"

SECRET_ARGS=(
  --from-literal=GIT_HOST="$GITLAB_HOST"
  --from-literal=GIT_USER="$GIT_USER"
  --from-literal=GIT_TOKEN="$GITLAB_TOKEN"
  --from-literal=OLLAMA_HOST="$OLLAMA_HOST"
  --from-literal=OLLAMA_BASE_URL="$OLLAMA_BASE_URL"
  --from-literal=OLLAMA_MODEL="$OLLAMA_MODEL"
  --from-literal=OPENCODE_MODEL="$OPENCODE_MODEL"
  --from-literal=AGENT_IMAGE="$AGENT_IMAGE"
  --from-literal=BRANCH_FALLBACK_ORDER="$BRANCH_FALLBACK_ORDER"
  --from-literal=DRY_RUN="$DRY_RUN"
)

if [[ -n "$OLLAMA_CLOUD_API_KEY" ]]; then
  SECRET_ARGS+=(--from-literal=OLLAMA_CLOUD_API_KEY="$OLLAMA_CLOUD_API_KEY")
fi

if kubectl get secret "$SECRET_NAME" -n "$NAMESPACE" &>/dev/null 2>&1; then
  warn "Secret '$SECRET_NAME' existiert bereits – wird ersetzt"
  kubectl delete secret "$SECRET_NAME" -n "$NAMESPACE"
fi

kubectl create secret generic "$SECRET_NAME" -n "$NAMESPACE" "${SECRET_ARGS[@]}"
ok "Secret '$SECRET_NAME' erstellt"

echo ""

update_env_line() {
  local key="$1"
  local val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i '' "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

info "Sync .env mit eingegebenen Werten..."
update_env_line "GITLAB_HOST" "$GITLAB_HOST"
update_env_line "GIT_USER" "$GIT_USER"
update_env_line "GITLAB_TOKEN" "$GITLAB_TOKEN"
update_env_line "OLLAMA_HOST" "$OLLAMA_HOST"
update_env_line "OLLAMA_BASE_URL" "$OLLAMA_BASE_URL"
update_env_line "OLLAMA_MODEL" "$OLLAMA_MODEL"
update_env_line "OPENCODE_MODEL" "$OPENCODE_MODEL"
update_env_line "OLLAMA_CLOUD_API_KEY" "$OLLAMA_CLOUD_API_KEY"
update_env_line "AGENT_IMAGE" "$AGENT_IMAGE"
update_env_line "BRANCH_FALLBACK_ORDER" "$BRANCH_FALLBACK_ORDER"
update_env_line "DRY_RUN" "$DRY_RUN"
ok ".env aktualisiert"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}Setup abgeschlossen${NC}"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Secret:     $SECRET_NAME (namespace: $NAMESPACE)"
echo "  Naechster Schritt:  ./redeploy.sh"
echo ""