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

MONITORING_DEFAULT="n"
MONITORING_VAL=$(env_val ENABLE_MONITORING)
if [[ "$MONITORING_VAL" == "true" || "$MONITORING_VAL" == "y" ]]; then
  MONITORING_DEFAULT="y"
fi
ENABLE_MONITORING=$(prompt_val "ENABLE_MONITORING" "Prometheus + Grafana installieren? (y/n)" "$MONITORING_DEFAULT")

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
echo -e "${BOLD}  Konfiguration${NC}"
echo "  ───────────────────────────────────────────────────────────"
echo "  Werte aus .env werden als Defaults vorgeschlagen."
echo "  Leere Eingabe uebernimmt den Default-Wert."
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

# ── Monitoring (Prometheus + Grafana) ────────────────────────────────────────
if [[ "$ENABLE_MONITORING" == "y" || "$ENABLE_MONITORING" == "true" ]]; then
  echo ""
  echo -e "${BOLD}  Monitoring (Prometheus + Grafana)${NC}"
  echo "  ───────────────────────────────────────────────────────────"

  MONITORING_YAML="$SCRIPT_DIR/Orchestrator/k8s/monitoring.yaml"
  if [[ -f "$MONITORING_YAML" ]]; then
    kubectl apply -f "$MONITORING_YAML"
    ok "Prometheus + Grafana Manifests angewendet"

    echo "  Warte auf Grafana-Start..."
    for i in $(seq 1 30); do
      if kubectl get pods -n "$NAMESPACE" -l app=grafana -o jsonpath='{.items[0].status.phase}' 2>/dev/null | grep -q "Running"; then
        break
      fi
      sleep 2
    done

    GF_POD=$(kubectl get pods -n "$NAMESPACE" -l app=grafana -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    PROM_POD=$(kubectl get pods -n "$NAMESPACE" -l app=prometheus -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

    ok "Grafana Pod:  ${GF_POD:-pending...}"
    ok "Prometheus Pod: ${PROM_POD:-pending...}"
    echo ""
    info "Port-Forward:"
    echo "    Grafana:     kubectl port-forward -n $NAMESPACE svc/grafana 3000:3000"
    echo "    Prometheus:  kubectl port-forward -n $NAMESPACE svc/prometheus 9090:9090"
    echo "    Dashboard:   http://localhost:3000  (admin/hivemind)"
  else
    warn "monitoring.yaml nicht gefunden – ueberspringe"
  fi

  update_env_line "ENABLE_MONITORING" "true"
else
  update_env_line "ENABLE_MONITORING" "false"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}Setup abgeschlossen${NC}"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Secret:     $SECRET_NAME (namespace: $NAMESPACE)"
if [[ "$ENABLE_MONITORING" == "y" || "$ENABLE_MONITORING" == "true" ]]; then
echo "  Monitoring: Prometheus + Grafana (namespace: $NAMESPACE)"
echo "    Grafana:     kubectl port-forward -n $NAMESPACE svc/grafana 3000:3000"
echo "    Prometheus:  kubectl port-forward -n $NAMESPACE svc/prometheus 9090:9090"
echo "    Dashboard:   http://localhost:3000  (admin/hivemind)"
fi
echo "  Naechster Schritt:  ./redeploy.sh"
echo ""