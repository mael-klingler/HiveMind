#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

step()  { echo -e "\n${BLUE}═══ $1 ═══${NC}"; }
ok()    { echo -e "  ${GREEN}✅ $1${NC}"; }
warn()  { echo -e "  ${YELLOW}⚠️  $1${NC}"; }
fail()  { echo -e "  ${RED}❌ $1${NC}"; exit 1; }
info()  { echo -e "  ${BLUE}ℹ️  $1${NC}"; }

# ── .env laden ──────────────────────────────────────────────
if [ -f .env ]; then
  info "Lade .env..."
  set -a; source .env; set +a
else
  warn "Keine .env gefunden – kopiere .env.example → .env und passe an!"
  if [ -f .env.example ]; then
    cp .env.example .env
    info ".env aus .env.example erstellt. Bitte anpassen!"
  fi
fi

DRY_RUN="${DRY_RUN:-true}"
OPENCODE_MODEL="${OPENCODE_MODEL:-glm-5.1:cloud}"

# ── 1. Check Docker ─────────────────────────────────────────
step "1. Docker-Prüfung"
docker version --format '{{.Server.Version}}' >/dev/null 2>&1 \
  && ok "Docker läuft" \
  || fail "Docker nicht verfügbar – starte Docker Desktop"

# ── 2. SSH Key prüfen ───────────────────────────────────────
step "2. SSH-Key Prüfung"
KEY_DIR="${SSH_KEY_PATH:-$HOME/.ssh}"
if docker run --rm -v "$KEY_DIR:/keys:ro" alpine:3.21 ls /keys/id_rsa >/dev/null 2>&1; then
  ok "SSH-Key gefunden: $KEY_DIR/id_rsa"
elif docker run --rm -v "$KEY_DIR:/keys:ro" alpine:3.21 ls /keys/ssh-privatekey >/dev/null 2>&1; then
  ok "SSH-Key gefunden: $KEY_DIR/ssh-privatekey"
else
  warn "Kein SSH-Key in $KEY_DIR gefunden."
  warn "Falls kein SSH-Zugang zu den GitLab-Repos:"
  warn "  1. Passe die URLs in orchestrator_config.json auf HTTPS um"
  warn "  2. Setze GIT_TOKEN in .env"
fi

# ── 3. Images bauen ─────────────────────────────────────────
step "3. Docker-Images bauen"

info "Baue Orchestrator-Image..."
docker compose -f docker-compose.yaml build orchestrator-process 2>&1 | tail -1
ok "Orchestrator-Image gebaut"

info "Baue Agent-Image..."
docker compose -f docker-compose.yaml build agent 2>&1 | tail -1
ok "Agent-Image gebaut"

# ── 4. Orchestrator: init ───────────────────────────────────
step "4. Orchestrator Init (Repos pullen + indexieren, LeanKG, Docker-Compose)"

if [ "$SKIP_INIT" = "true" ]; then
  info "SKIP_INIT=true → überspringe"
else
  docker compose -f docker-compose.yaml --profile init up orchestrator-init 2>&1
  ok "Init abgeschlossen"
fi

# ── 5. Orchestrator: process ────────────────────────────────
step "5. Orchestrator Process (Ticket analysieren + Workspace bauen)"

docker compose -f docker-compose.yaml --profile process up orchestrator-process 2>&1
ok "Workspace gebaut"

# ── 6. Prüfe ob Assignment existiert ────────────────────────
step "6. Assignment-Prüfung"

TICKET_ID=$(grep -oP '"id":\s*"' ./Orchestrator/example_ticket.json | head -1 | sed 's/"id": "//;s/"//')
info "Ticket-ID: ${TICKET_ID:-?}"
info "Suche assignment.md im Workspace..."

WORKSPACE_DIR="workspace_${TICKET_ID:-PROJ-123}"
ASSIGNMENT_EXISTS=$(docker run --rm -v hivemind_workspace:/ws alpine:3.21 \
  sh -c "find /ws -name 'assignment.md' -path '*${WORKSPACE_DIR}*' 2>/dev/null" || true)

if [ -n "$ASSIGNMENT_EXISTS" ]; then
  ok "Assignment gefunden"
  info "Pfad: $ASSIGNMENT_EXISTS"
else
  warn "Assignment nicht im Volume gefunden – checke Workspace-Struktur:"
  docker run --rm -v hivemind_workspace:/ws alpine:3.21 ls -laR /ws/ 2>/dev/null || true
fi

# ── 7. Agent Dry-Run ────────────────────────────────────────
step "7. Agent (Dry-Run = $DRY_RUN)"

if [ "$DRY_RUN" = "true" ]; then
  info "DRY_RUN=true → opencode wird nicht ausgeführt"
  info "Starte Agent im Dry-Run-Modus..."

  docker compose -f docker-compose.yaml --profile run up agent 2>&1
  ok "Agent Dry-Run abgeschlossen"

  echo ""
  echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
  echo -e "${GREEN}  ✅ Test erfolgreich abgeschlossen!           ${NC}"
  echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
  echo ""
  echo "Nächste Schritte:"
  echo ""
  echo "  1. .env anpassen:"
  echo "     - GITLAB_TOKEN setzen"
  echo "     - SSH_KEY_PATH auf deinen privaten Key zeigen"
  echo ""
  echo "  2. DRY_RUN=false setzen für echte Ausführung:"
  echo "     echo 'DRY_RUN=false' >> .env"
  echo ""
  echo "  3. Erneut testen:"
  echo "     ./test.sh"
  echo ""
  echo "  4. ODER nur den Agent direkt testen (ohne Orchestrator):"
  echo "     docker compose --profile run up agent"
else
  warn "DRY_RUN=false → opencode wird ECHTE Änderungen vornehmen!"
  warn "Stelle sicher dass:"
  warn "  - SSH-Key und GITLAB_TOKEN korrekt sind"
  warn "  - Du in einem Test-Repo arbeitest"
  echo ""
  read -p "Fortfahren? (y/N) " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker compose -f docker-compose.yaml --profile run up agent 2>&1
    ok "Agent-Lauf abgeschlossen"
  else
    info "Abgebrochen."
  fi
fi
