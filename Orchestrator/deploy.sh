#!/usr/bin/env bash
# K8s Deployment Script fuer HiveMind Orchestrator
# Nutzung: ./deploy.sh [METHOD] [ENV|EXTRA-ARGS]
#   METHOD: helm | kustomize | plain
#   ENV:    dev | prod (Default: dev)
#
# Beispiele:
#   ./deploy.sh helm dev
#   ./deploy.sh kustomize prod
#   ./deploy.sh plain
#
# Vor der Ausfuehrung:
#   1. GIT_TOKEN setzen (z.B. export GIT_TOKEN="glciy-xxx")
#   2. kubectl mit dem Cluster verbunden
#   3. Helm installiert (falls --helm genutzt wird)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NAMESPACE="hivemind"
METHOD="${1:-helm}"
ENV="${2:-dev}"
IMAGE="example/orchestrator:latest"

# ── Farben ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'

log()     { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()      { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()     { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Voraussetzungen pruefen ──────────────────────────────
check_prereqs() {
  log "Pruefe Voraussetzungen..."
  command -v kubectl >/dev/null || err "kubectl nicht installiert"
  kubectl cluster-info >/dev/null 2>&1 || err "kubectl nicht mit Cluster verbunden"

  case "$METHOD" in
    helm)
      command -v helm >/dev/null || err "helm nicht installiert"
      ;;
    kustomize)
      command -v kustomize >/dev/null || {
        log "Versuche kustomize ueber kubectl kustomize..."
        kubectl kustomize --help >/dev/null || err "kustomize / kubectl kustomize nicht verfuegbar"
      }
      ;;
  esac

  ok "Voraussetzungen erfuellt"
}

# ── Namespace ─────────────────────────────────────────────
ensure_namespace() {
  if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    log "Erstelle namespace: $NAMESPACE"
    kubectl create namespace "$NAMESPACE"
  fi
}

# ── GitLab-Token Secret ──────────────────────────────────
ensure_git_token() {
  if [ -z "${GIT_TOKEN:-}" ]; then
    warn "GIT_TOKEN nicht gesetzt! Bitte export GIT_TOKEN=\"glciy-xxx\" setzen"
    read -rp "Token manuell eingeben (oder Enter fuer Skip): " GIT_TOKEN_INPUT
    if [ -n "${GIT_TOKEN_INPUT:-}" ]; then
      GIT_TOKEN="$GIT_TOKEN_INPUT"
    else
      warn "Kein Token gesetzt – git clone wird fehlschlagen!"
    fi
  fi

  if kubectl get secret orchestrator-git-token -n "$NAMESPACE" >/dev/null 2>&1; then
    log "Git-Token Secret existiert bereits"
  else
    log "Erstelle Git-Token Secret..."
    kubectl create secret generic orchestrator-git-token \
      --from-literal=GIT_TOKEN="${GIT_TOKEN:-}" \
      --namespace "$NAMESPACE"
    ok "Secret erstellt"
  fi
}

# ── Docker Image bauen + pushen ─────────────────────────────
build_image() {
  log "Baue Docker Image: $IMAGE"
  docker build -t "$IMAGE" .
  docker push "$IMAGE"
  ok "Image gebaut und gepusht"
}

# ── Helm Deploy ────────────────────────────────────────────
deploy_helm() {
  log "Deploye via Helm (Env: $ENV)..."

  local values_file="$SCRIPT_DIR/helm/orchestrator/values.yaml"
  local extra_args=()

  if [ -n "${GIT_TOKEN:-}" ]; then
    extra_args+=(--set="env.gitToken=$GIT_TOKEN")
  fi

  helm upgrade --install orchestrator "$SCRIPT_DIR/helm/orchestrator" \
    --namespace "$NAMESPACE" \
    --values "$values_file" \
    --create-namespace \
    "${extra_args[@]:-}"

  ok "Helm Deployment abgeschlossen"
}

# ── Kustomize Deploy ─────────────────────────────────────
deploy_kustomize() {
  log "Deploye via Kustomize (Env: $ENV)..."

  local overlay="$SCRIPT_DIR/kustomize/overlays/$ENV"

  if [ ! -d "$overlay" ]; then
    err "Overlay nicht gefunden: $overlay"
  fi

  # Kustomize mit kubectl
  kubectl apply -k "$overlay"

  ok "Kustomize Deployment abgeschlossen"
}

# ── Plain K8s Manifeste ──────────────────────────────────
deploy_plain() {
  log "Deploye via plain K8s Manifeste..."

  kubectl apply -f "$SCRIPT_DIR/k8s/"

  ok "Plain Deployment abgeschlossen"
}

# ── Warten auf Pod ────────────────────────────────────────
wait_for_pod() {
  log "Warte auf Pod..."
  kubectl wait --for=condition=ready pod \
    -l app.kubernetes.io/name=orchestrator \
    -n "$NAMESPACE" \
    --timeout=300s 2>/dev/null || warn "Timeout beim Warten"
}

# ── Port-Forward ──────────────────────────────────────────
port_forward() {
  echo ""
  log "Starte Port-Forward auf http://localhost:8080"
  log "Strg+C zum Beenden"
  kubectl port-forward -n "$NAMESPACE" svc/orchestrator 8080:8080
}

# ── Main ──────────────────────────────────────────────────
main() {
  echo "============================================"
  echo " 🚀 HiveMind Orchestrator"
  echo "============================================"
  echo ""

  check_prereqs
  ensure_namespace
  ensure_git_token

  case "$METHOD" in
    helm)
      deploy_helm
      wait_for_pod
      log "Fertig! UI: kubectl port-forward -n $NAMESPACE svc/orchestrator 8080:8080"
      ;;
    kustomize)
      deploy_kustomize
      wait_for_pod
      log "Fertig! UI: kubectl port-forward -n $NAMESPACE svc/orchestrator 8080:8080"
      ;;
    plain)
      deploy_plain
      wait_for_pod
      log "Fertig! UI: kubectl port-forward -n $NAMESPACE svc/orchestrator 8080:8080"
      ;;
    *)
      err "Unbekannte Methode: $METHOD. Nutze: helm | kustomize | plain"
      ;;
  esac

  echo ""
  echo "============================================"
    ok "Deployment erfolgreich!"
  echo "============================================"
}

# Starte Port-Forward falls "--pf" als letztes Argument
if [[ "${3:-}" == "--pf" ]]; then
  port_forward
  exit 0
fi

main
