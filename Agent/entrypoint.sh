#!/bin/bash
set -euo pipefail

DRY_RUN="${DRY_RUN:-false}"
COMMENT_POLL_INTERVAL="${COMMENT_POLL_INTERVAL:-30}"
GITLAB_TOKEN="${GITLAB_TOKEN:?GITLAB_TOKEN muss gesetzt sein}"
GITLAB_HOST="${GITLAB_HOST:?GITLAB_HOST muss gesetzt sein}"
GITLAB_USER="${GITLAB_USER:-gitlab-ci-token}"

TASK_FILE="${1:-}"

if [ -z "$TASK_FILE" ]; then
  if [ -d "/workspace" ]; then
    TASK_FILE=$(find /workspace -name assignment.md -path '*/.opencode/*' 2>/dev/null | head -1 || true)
  fi
  if [ -z "$TASK_FILE" ]; then
    TASK_FILE="/etc/task/task.md"
  fi
fi

if [ ! -f "$TASK_FILE" ]; then
  echo "❌ Kein Task-Prompt gefunden. Gesucht: /etc/task/task.md, /workspace/.opencode/assignment.md"
  exit 1
fi

FIRST_LINE=$(head -1 "$TASK_FILE")

TICKET_ID=$(echo "$FIRST_LINE" | sed -n 's/^# .*Aufgabe: \([^ ]*\).*/\1/p')
TICKET_TITLE=$(echo "$FIRST_LINE" | sed -n 's/^# .*Aufgabe: [^ ]* – //p')

if [ -z "$TICKET_ID" ]; then
  TICKET_ID=$(echo "$FIRST_LINE" | sed -n 's/^# Auftrag: \([^ ]*\).*/\1/p')
fi
if [ -z "$TICKET_TITLE" ]; then
  TICKET_TITLE=$(echo "$FIRST_LINE" | sed -n 's/^# Auftrag: [^ ]* – //p')
fi

if [ -z "$TICKET_ID" ]; then
  TICKET_ID=$(grep -m1 -oP '(?<=[* ]ID:\*\* ).*' "$TASK_FILE" | head -1)
fi

if [ -z "$TICKET_TITLE" ]; then
  TICKET_TITLE=$(echo "$FIRST_LINE" | sed 's/^# .*Aufgabe: [^ ]*[-–] //')
fi
if [ -z "$TICKET_TITLE" ]; then
  TICKET_TITLE=$(echo "$FIRST_LINE" | sed 's/^# [^:]*: [^ ]*\( – \| - \|[:]\)//')
fi
if [ -z "$TICKET_TITLE" ]; then
  TICKET_TITLE=$(echo "$FIRST_LINE" | sed 's/^# //')
fi
if [ -z "$TICKET_TITLE" ] || [ "$TICKET_TITLE" = "$FIRST_LINE" ]; then
  TICKET_TITLE=$(grep -m1 -A1 "## Beschreibung" "$TASK_FILE" | tail -1 | xargs)
fi

if [ -z "$TICKET_ID" ] || [ -z "$TICKET_TITLE" ]; then
  echo "❌ Konnte Ticket-ID oder Titel nicht aus dem Prompt extrahieren"
  echo "   First line: $FIRST_LINE"
  exit 1
fi

BRANCH="feature/${TICKET_ID}"

# ── MR-URL Erkennung: Existierenden Branch aus dem MR auschecken ─────
MR_URL=$(grep -oE "https?://${GITLAB_HOST}/[^ ]*merge_requests/[0-9]+" "$TASK_FILE" 2>/dev/null | head -1 || true)
MR_BRANCH=""
if [ -n "$MR_URL" ]; then
  echo "🔗 MR-URL gefunden: $MR_URL"
  MR_PROJECT=$(echo "$MR_URL" | sed -E "s|https?://${GITLAB_HOST}/||;s|/-/merge_requests.*||;s|/merge_requests.*||")
  MR_IID=$(echo "$MR_URL" | grep -oE 'merge_requests/[0-9]+' | grep -oE '[0-9]+')
  if [ -n "$MR_PROJECT" ] && [ -n "$MR_IID" ]; then
    ENCODED_PROJECT=$(echo -n "$MR_PROJECT" | jq -sRr @uri)
    MR_DATA=$(curl -sS -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
      "${GITLAB_API_URL}/projects/${ENCODED_PROJECT}/merge_requests/${MR_IID}" 2>&1 || true)
    MR_BRANCH=$(echo "$MR_DATA" | jq -r '.source_branch // empty' 2>/dev/null || true)
    if [ -n "$MR_BRANCH" ]; then
      BRANCH="$MR_BRANCH"
      echo "🌿 Existierenden MR-Branch verwendet: $BRANCH (MR !${MR_IID})"
    else
      echo "⚠️  Konnte Branch aus MR nicht ermitteln – verwende Default: $BRANCH"
    fi
  fi
fi

echo "📋 Ticket:  $TICKET_ID – $TICKET_TITLE"
echo "🌿 Branch:  $BRANCH"
echo "🧪 Dry-Run: $DRY_RUN"

if [ "$DRY_RUN" = "true" ]; then
  echo ""
  echo "═══════════════════════════════════════════"
  echo "  🧪 DRY RUN – keine echten Änderungen"
  echo "═══════════════════════════════════════════"
  echo ""
  echo "📄 Task-Prompt (erste 20 Zeilen):"
  head -20 "$TASK_FILE"
  echo "..."
  echo ""
  echo "📦 Repos in /workspace:"
  for d in /workspace/*/; do [ -d "$d/.git" ] && echo "  ${d%/}"; done
  echo ""
  echo "🏁 Dry-Run abgeschlossen."
  exit 0
fi

# ── Git Konfiguration (vor Rebase noetig) ──────────────────────────────
git config --global user.email "hivemind-agents@example.com"
git config --global user.name "HiveMind"
git config --global credential.helper store

# ── Merge-Konflikt-Erkennung und automatische Aufloesung ──────────────
MERGE_CONFLICT=false
TARGET_BRANCH="main"
if [ -n "$MR_URL" ] && [ -n "$MR_DATA" ]; then
  MERGE_STATUS=$(echo "$MR_DATA" | jq -r '.merge_status // "unknown"' 2>/dev/null || echo "unknown")
  HAS_CONFLICTS=$(echo "$MR_DATA" | jq -r '.has_conflicts // false' 2>/dev/null || echo "false")
  MR_TARGET=$(echo "$MR_DATA" | jq -r '.target_branch // "main"' 2>/dev/null || echo "main")

  if [ "$MERGE_STATUS" = "cannot_be_merged" ] || [ "$HAS_CONFLICTS" = "true" ]; then
    MERGE_CONFLICT=true
    TARGET_BRANCH="$MR_TARGET"
    echo "⚠️  Merge-Konflikt erkannt (merge_status=$MERGE_STATUS, has_conflicts=$HAS_CONFLICTS)"
    echo "🔀 Ziel-Branch: $TARGET_BRANCH"
  fi
fi

if [ "$MERGE_CONFLICT" = "true" ]; then
  echo "🔄 Versuche automatische Konfiktaufloesung..."
  for dir in /workspace/*/; do
    repo="${dir%/}"
    [ -d "$repo/.git" ] || continue
    cd "$repo" || continue

    echo "📦 $(basename "$repo"): Rebasing auf origin/$TARGET_BRANCH..."

    git fetch origin "$TARGET_BRANCH" 2>&1 || { echo "⚠️  Fetch fehlgeschlagen für $repo"; cd /workspace; continue; }

    CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "")
    if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
      if git show-ref --verify --quiet "refs/heads/$BRANCH" 2>/dev/null; then
        git checkout "$BRANCH" 2>&1 || { echo "⚠️  Checkout fehlgeschlagen für $BRANCH"; cd /workspace; continue; }
      else
        echo "   Branch $BRANCH existiert nicht in $repo – überspringe"
        cd /workspace; continue
      fi
    fi

    echo "   Rebasing $BRANCH auf origin/$TARGET_BRANCH..."
    if git rebase "origin/$TARGET_BRANCH" 2>&1; then
      echo "✅ $(basename "$repo"): Rebase erfolgreich"
    else
      echo "⚠️  $(basename "$repo"): Rebase hat Konflikte – versuche automatische Aufloesung"
      CONFLICT_FILES=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
      CONFLICT_COUNT=$(echo "$CONFLICT_FILES" | grep -c . || echo "0")
      echo "   $CONFLICT_COUNT Konflikt-Dateien erkannt"

      git diff --name-only --diff-filter=U | while read -r conflict_file; do
        if [ -z "$conflict_file" ]; then continue; fi
        echo "   Löse Konflikt in: $conflict_file"
        git checkout --theirs "$conflict_file" 2>/dev/null || \
        git checkout --ours "$conflict_file" 2>/dev/null || true
        git add "$conflict_file" 2>/dev/null || true
      done

      if git rebase --continue 2>&1; then
        echo "✅ $(basename "$repo"): Konflikte aufgelöst, Rebase abgeschlossen"
      else
        echo "❌ $(basename "$repo"): Rebase konnte nicht automatisch aufgelöst werden – abort"
        git rebase --abort 2>/dev/null || true
      fi
    fi

    cd /workspace || true
  done
  echo "🔄 Konfiktaufloesung abgeschlossen"
fi

# ── OpenCode Config ──────────────────────────────────────────────────────
mkdir -p /root/.config/opencode

if [ -z "${OPENCODE_PLUGINS:-}" ]; then
  export OPENCODE_PLUGINS='["opencode-snip","opencode-agent-memory","opencode-handoff"]'
fi

if [ -f /mnt/opencode-config/opencode.json ]; then
  echo "📄 Verwende opencode.json aus ConfigMap (/mnt/opencode-config)"
  cp /mnt/opencode-config/opencode.json /root/.config/opencode/opencode.json
elif [ -f /etc/agent/opencode.json.template ]; then
  echo "📄 Verwende opencode.json.template (Fallback)"
  envsubst '$OPENCODE_MODEL $OLLAMA_BASE_URL $OPENCODE_PLUGINS' < /etc/agent/opencode.json.template > /root/.config/opencode/opencode.json
else
  echo "❌ Keine opencode-Config gefunden"
  exit 1
fi
export OPENCODE_CONFIG=/root/.config/opencode/opencode.json

# ── Agent Memory Blocks ──────────────────────────────────────────────────
mkdir -p /root/.config/opencode/memory
MEMORY_DIR="/root/.config/opencode/memory"

# Restore memory blocks from mounted config if available
if [ -d "/mnt/memory-blocks" ]; then
  echo "📝 Stelle Memory-Blocks aus /mnt/memory-blocks wieder her..."
  cp /mnt/memory-blocks/*.md "$MEMORY_DIR/" 2>/dev/null || true
fi

# Seed default memory blocks if none exist
if [ -z "$(ls -A "$MEMORY_DIR" 2>/dev/null)" ]; then
  echo "📝 Erstelle Default-Memory-Blocks..."
  cat > "$MEMORY_DIR/persona.md" << 'MEMEOF'
---
label: persona
description: Agent-Identitaet und Verhalten
limit: 5000
read_only: false
---
Du bist ein autonomer Software-Entwickler. Arbeite sorgfaeltig und methodisch. Bevorzuge deutsche Kommentare im Code. Halte dich an bestehende Konventionen.
MEMEOF

  cat > "$MEMORY_DIR/human.md" << 'MEMEOF'
---
label: human
description: Praeferenzen des Operators
limit: 5000
read_only: false
---
Bevorzuge deutsche UI-Sprache. Verwende Conventional Commits. Keine Emojis in Commit-Nachrichten. Tests sind Pflicht.
MEMEOF

  cat > "$MEMORY_DIR/project.md" << 'MEMEOF'
---
label: project
description: Projekt-Konventionen und Architektur
limit: 5000
read_only: false
---
Tech-Stack: Vue 3 + TypeScript Frontend, Go Backend.
Tests: pnpm test && vue-tsc --noEmit (Frontend), go test ./... (Backend).
Lint: pnpm lint (Frontend), golangci-lint run (Backend).
Branch: feature/TICKET-ID. Conventional Commits.
MEMEOF
  echo "✅ Default-Memory-Blocks erstellt"
fi

# Agent-memory journal config (optional)
if [ ! -f /root/.config/opencode/agent-memory.json ]; then
  cat > /root/.config/opencode/agent-memory.json << 'JEOF'
{
  "journal": {
    "enabled": true,
    "tags": [
      { "name": "debugging", "description": "Debugging sessions and findings" },
      { "name": "architecture", "description": "Architecture decisions" },
      { "name": "conventions", "description": "Learned codebase conventions" }
    ]
  }
}
JEOF
fi

git config --global user.email "hivemind-agents@example.com"
git config --global user.name "HiveMind"
git config --global credential.helper store

if [ -n "${OLLAMA_CLOUD_API_KEY:-}" ]; then
  export OLLAMA_API_KEY="$OLLAMA_CLOUD_API_KEY"
fi

echo "📝 Ergänze .gitignore in allen Repos..."
GITIGNORE_ENTRIES=".leankg/
leankg.yaml
node_modules/"
for dir in /workspace/*/; do
  repo="${dir%/}"
  [ -d "$repo/.git" ] || continue
  if [ -f "$repo/.gitignore" ]; then
    while IFS= read -r entry; do
      if ! grep -qF "$entry" "$repo/.gitignore" 2>/dev/null; then
        echo "$entry" >> "$repo/.gitignore"
      fi
    done <<< "$GITIGNORE_ENTRIES"
  else
    echo "$GITIGNORE_ENTRIES" > "$repo/.gitignore"
  fi
done

# ── Phase 1: Run opencode in primary repo ────────────────────────────────
PRIMARY_REPO=""
if [ -f /mnt/opencode-config/opencode.json ]; then
  PRIMARY_REPO=$(jq -r '.repositories[] | select(.primary == true) | .name' /mnt/opencode-config/opencode.json 2>/dev/null | head -1 || true)
fi
if [ -n "$PRIMARY_REPO" ] && [ -d "/workspace/$PRIMARY_REPO/.git" ]; then
  PRIMARY_REPO="/workspace/$PRIMARY_REPO"
  echo "📂 Primaeres Repository aus Config: $PRIMARY_REPO"
elif [ -n "$PRIMARY_REPO" ]; then
  echo "⚠️  Primaeres Repository '$PRIMARY_REPO' nicht in /workspace gefunden, suche Fallback..."
  PRIMARY_REPO=""
fi
if [ -z "$PRIMARY_REPO" ]; then
  for dir in /workspace/*/; do
    if [ -d "$dir/.git" ]; then
      PRIMARY_REPO="${dir%/}"
      break
    fi
  done
fi
if [ -z "$PRIMARY_REPO" ]; then
  echo "❌ Kein Git-Repository in /workspace gefunden"
  exit 1
fi
cd "$PRIMARY_REPO"
echo "📂 Arbeitsverzeichnis: $PRIMARY_REPO"

inject_git_credentials() {
  local _saved_pwd="$PWD"
  for dir in /workspace/*/; do
    local repo="${dir%/}"
    [ -d "$repo/.git" ] || continue
    cd "$repo" || continue
    local REMOTE_URL
    REMOTE_URL=$(git remote get-url origin 2>/dev/null || true)
    if echo "$REMOTE_URL" | grep -q "^https://[^@]*@${GITLAB_HOST}"; then
      :
    elif echo "$REMOTE_URL" | grep -q "^https://${GITLAB_HOST}"; then
      git remote set-url origin "https://${GITLAB_USER}:${GITLAB_TOKEN}@${REMOTE_URL#https://}"
    elif echo "$REMOTE_URL" | grep -q "^https://"; then
      local CLEAN_URL
      CLEAN_URL=$(echo "$REMOTE_URL" | sed "s|https://[^@]*@|https://|")
      git remote set-url origin "https://${GITLAB_USER}:${GITLAB_TOKEN}@${CLEAN_URL#https://}"
    elif echo "$REMOTE_URL" | grep -q "^git@"; then
      local CLEAN_PATH
      CLEAN_PATH=$(echo "$REMOTE_URL" | sed 's|git@[^:]*:||;s|\.git$||')
      git remote set-url origin "https://${GITLAB_USER}:${GITLAB_TOKEN}@${GITLAB_HOST}/${CLEAN_PATH}.git"
    fi
    cd "$_saved_pwd" || true
  done
}

echo "https://${GITLAB_USER}:${GITLAB_TOKEN}@${GITLAB_HOST}" > /root/.git-credentials
chmod 600 /root/.git-credentials

inject_git_credentials

TASK_PROMPT="$(cat "$TASK_FILE")"

# ── Fortschritts-Reporting an Orchestrator ──────────────────────────
post_progress() {
  local content="$1"
  local comment_type="${2:-system}"
  if [ -z "${ORCHESTRATOR_URL:-}" ] || [ -z "${TICKET_ID:-}" ]; then
    return
  fi
  if [ "$comment_type" = "progress" ]; then
    return
  fi
  local timestamp
  timestamp=$(date '+%Y-%m-%d %H:%M:%S')
  local body
  body=$(jq -n \
    --arg author "${AGENT_ID:-agent}" \
    --arg type "$comment_type" \
    --arg content "[$timestamp] $content" \
    '{author: $author, comment_type: $type, content: $content}')
  curl -sS -X POST \
    -H "Content-Type: application/json" \
    -d "$body" \
    "${ORCHESTRATOR_URL}/api/tickets/${TICKET_ID}/comments" \
    >/dev/null 2>&1 || true
}

PROGRESS_FIFO="/tmp/opencode_progress_fifo"
mkfifo "$PROGRESS_FIFO" 2>/dev/null || true

progress_monitor() {
  local todo_buffer=""
  local in_todos=false
  while IFS= read -r line; do
    if echo "$line" | grep -qE '^\s*[-*]?\s*\[[ x]\]'; then
      if ! $in_todos; then
        in_todos=true
        todo_buffer=""
      fi
      todo_buffer="${todo_buffer}${line}"$'\n'
    else
      if $in_todos && [ -n "$todo_buffer" ]; then
        in_todos=false
      fi
    fi
  done
}

post_progress "Agent startet — Ticket ${TICKET_ID}: ${TICKET_TITLE}" "system"
OPENCODE_WEB_PASSWORD="${OPENCODE_SERVER_PASSWORD:-}"

echo "🤖 Starte opencode run..."
echo "   Task: [${TICKET_ID}] ${TICKET_TITLE}"

OPENCODE_EXIT=0

# Start opencode web server first so we can attach to it interactively
# This allows the user to send messages via Web UI on port 4096 while opencode runs
export OPENCODE_SERVER_PASSWORD="${OPENCODE_WEB_PASSWORD:-}"
opencode web --hostname 0.0.0.0 --port 4096 &
WEB_PID=$!
unset OPENCODE_SERVER_PASSWORD

# Wait for web server to be ready
sleep 3

# Attach opencode run to the running server
if [ -n "${ORCHESTRATOR_URL:-}" ]; then
  opencode run \
    --attach "http://localhost:4096" \
    ${OPENCODE_WEB_PASSWORD:+--password "$OPENCODE_WEB_PASSWORD"} \
    --title "[${TICKET_ID}] ${TICKET_TITLE}" \
    --dangerously-skip-permissions \
    "$TASK_PROMPT" 2>&1 | progress_monitor &
  MONITOR_PID=$!
else
  opencode run \
    --attach "http://localhost:4096" \
    ${OPENCODE_WEB_PASSWORD:+--password "$OPENCODE_WEB_PASSWORD"} \
    --title "[${TICKET_ID}] ${TICKET_TITLE}" \
    --dangerously-skip-permissions \
    "$TASK_PROMPT" &
  MONITOR_PID=$!
fi

# Wait briefly for opencode to start
sleep 5

wait $MONITOR_PID 2>/dev/null || true
OPENCODE_EXIT=$?

if [ "$OPENCODE_EXIT" -ne 0 ]; then
  echo "❌ opencode run fehlgeschlagen (Exit: $OPENCODE_EXIT)"
  post_progress "❌ opencode fehlgeschlagen (Exit: $OPENCODE_EXIT)" "system"
  exit "$OPENCODE_EXIT"
fi

echo "✅ opencode Task abgeschlossen"
post_progress "✅ opencode Task abgeschlossen – beginne Commit/Push/MR Phase" "system"

echo "🔑 Re-inject Git credentials (opencode may have modified remote URLs)..."
inject_git_credentials

# ── Phase 2: Commit, Push, MR — nur fuer echte Git-Repos ──────────────────

GITLAB_API_URL="https://${GITLAB_HOST}/api/v4"

create_merge_request() {
  local project_path="$1"
  local source_branch="$2"
  local target_branch="$3"
  local title="$4"
  local description="$5"

  local encoded_path
  encoded_path=$(echo -n "$project_path" | jq -sRr @uri)

  # Pruefe ob Projekt verschoben/redirected wurde → verwende neuen Pfad
  local project_info
  project_info=$(curl -sS -H "PRIVATE-TOKEN: $GITLAB_TOKEN" "${GITLAB_API_URL}/projects/${encoded_path}" 2>&1)
  if echo "$project_info" | jq -e '.message' 2>/dev/null | grep -qi "moved"; then
    echo "⚠️  Projekt ${project_path} wurde verschoben, versuche Redirect..."
    local redirected_path
    redirected_path=$(echo "$project_info" | jq -r '."message" // empty' 2>/dev/null | grep -oE 'has been moved to [^ "]+' | sed 's/has been moved to //' | sed 's/\.git$//' | sed 's|/$||')
    if [ -z "$redirected_path" ]; then
      redirected_path=$(echo "$project_info" | jq -r '.redirected_to_path // empty' 2>/dev/null)
    fi
    if [ -n "$redirected_path" ]; then
      echo "   → Redirect: ${project_path} → ${redirected_path}"
      project_path="$redirected_path"
      encoded_path=$(echo -n "$project_path" | jq -sRr @uri)
    else
      echo "   → Konnte Redirect-Ziel nicht ermitteln, versuche trotzdem..."
    fi
  fi

  echo "🔍 Suche existierenden MR für ${project_path} (${source_branch} → ${target_branch})..."
  local existing
  existing=$(curl -sS \
    -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
    "${GITLAB_API_URL}/projects/${encoded_path}/merge_requests?state=opened&source_branch=${source_branch}&target_branch=${target_branch}" 2>&1)

  if echo "$existing" | jq -e '.[0].web_url' >/dev/null 2>&1; then
    echo "✅ Existierenden MR gefunden"
    echo "$existing" | jq -r '.[0].web_url'
    return 0
  fi

  local mr_body
  mr_body=$(jq -n \
    --arg sb "$source_branch" \
    --arg tb "$target_branch" \
    --arg t "$title" \
    --arg d "$description" \
    '{source_branch: $sb, target_branch: $tb, title: $t, description: $d, remove_source_branch: true}')

  echo "📝 Erstelle neuen MR für ${project_path}..."
  local result
  result=$(curl -sS \
    -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$mr_body" \
    "${GITLAB_API_URL}/projects/${encoded_path}/merge_requests" 2>&1)

  if echo "$result" | jq -e '.web_url' >/dev/null 2>&1; then
    echo "$result" | jq -r '.web_url'
    return 0
  else
    echo "❌ MR API Fehler: $(echo "$result" | head -200)" >&2
    return 1
  fi
}

MR_DESCRIPTION="## Zusammenfassung

Dieser MR wurde automatisch durch den HiveMind Agent erstellt.

### Ticket
- **ID:** ${TICKET_ID}
- **Titel:** ${TICKET_TITLE}

### Änderungen

$(SAVED_PWD="$PWD"; for dir in /workspace/*/; do
  repo="${dir%/}"
  [ -d "$repo/.git" ] || continue
  cd "$repo" || continue
  if [ -n "$(git log development..HEAD --oneline 2>/dev/null)" ]; then
    echo "#### $(basename "$repo")"
    echo ""
    git log --oneline development..HEAD 2>/dev/null
    echo ""
    echo "\`\`\`"
    git diff --stat development..HEAD 2>/dev/null
    echo "\`\`\`"
    echo ""
  fi
  cd "$SAVED_PWD" || true
done)"

for dir in /workspace/*/; do
  repo="${dir%/}"
  [ -d "$repo/.git" ] || continue
  cd "$repo" || { echo "❌ Kann nicht in $repo wechseln"; exit 1; }

  echo "📦 $(basename "$repo"): Prüfe Branch/MR-Status..."

  BRANCH_EXISTS_LOCALLY=$(git branch --list "$BRANCH" 2>/dev/null)
  if [ -z "$BRANCH_EXISTS_LOCALLY" ]; then
    if [ -z "$(git status --porcelain)" ]; then
      echo "📦 $(basename "$repo"): Keine Änderungen und kein Branch, überspringe."
      continue
    fi
    git checkout -b "$BRANCH"
  else
    git checkout "$BRANCH"
  fi

  if [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -m "[${TICKET_ID}] ${TICKET_TITLE}" --allow-empty
  fi

  git push -u origin "$BRANCH" 2>&1 | tee /tmp/push_output.txt || git push origin "$BRANCH" 2>&1 | tee -a /tmp/push_output.txt || echo "⚠️  Push bereits vorhanden oder fehlgeschlagen für $(basename "$repo")"

  REMOTE_URL=$(git remote get-url origin 2>/dev/null)
  PROJECT_PATH=$(echo "$REMOTE_URL" | sed -E 's|.*://[^@]*@||;s|\.git$||;s|^.*://||;s|^git@[^:]*:||')

  if grep -q "Please update your Git remote" /tmp/push_output.txt 2>/dev/null; then
    NEW_URL=$(grep "git remote set-url origin" /tmp/push_output.txt | sed 's/.*git remote set-url origin //' | tr -d '[:space:]')
    if [ -n "$NEW_URL" ]; then
      git remote set-url origin "https://${GITLAB_USER}:${GITLAB_TOKEN}@${NEW_URL#https://}"
      PROJECT_PATH=$(echo "$NEW_URL" | sed -E 's|https?://||;s|\.git$||')
    fi
  fi

  PROJECT_HOST="${GITLAB_HOST}"
  PROJECT_PATH=$(echo "$PROJECT_PATH" | sed "s|^${PROJECT_HOST}/||")

  MR_URL=$(create_merge_request \
    "$PROJECT_PATH" \
    "$BRANCH" \
    "main" \
    "[${TICKET_ID}] ${TICKET_TITLE}" \
    "$MR_DESCRIPTION" 2>&1) || true

  if echo "$MR_URL" | grep -q "^http"; then
    echo "🔗 MR erstellt für $(basename "$repo"): $MR_URL"
  else
    echo "⚠️  MR-Erstellung für $(basename "$repo") fehlgeschlagen: $MR_URL"
  fi
done

echo "🏁 Alle Repos verarbeitet."
post_progress "🏁 Ticket ${TICKET_ID} abgeschlossen – Alle Repos verarbeitet." "system"

# ── Phase 3: Comment-Polling — auf User-Feedback reagieren ────────────

if [ -n "${ORCHESTRATOR_URL:-}" ] && [ -n "${TICKET_ID:-}" ]; then
  echo "👂 Starte Comment-Polling (alle ${COMMENT_POLL_INTERVAL}s)..."
  post_progress "👂 Warte auf Kommentare/Fedback..." "system"

  while true; do
    COMMENTS_JSON=$(curl -sS "${ORCHESTRATOR_URL}/api/tickets/${TICKET_ID}/comments" 2>/dev/null || echo "[]")

    PENDING_COMMENTS=$(echo "$COMMENTS_JSON" | jq -r "
      [ .[] | select(
        (.author // \"system\") != \"system\"
        and (.comment_type // \"system\") != \"system\"
        and (.comment_type // \"progress\") != \"progress\"
        and (.id // 0) > $LAST_SEEN_COMMENT_ID
      ) ] | length
    " 2>/dev/null || echo "0")

    if [ "$PENDING_COMMENTS" -gt 0 ]; then
      NEW_IDS=$(echo "$COMMENTS_JSON" | jq -r "
        [ .[] | select(
          (.author // \"system\") != \"system\"
          and (.comment_type // \"system\") != \"system\"
          and (.comment_type // \"progress\") != \"progress\"
          and (.id // 0) > $LAST_SEEN_COMMENT_ID
        ) .id ] | max // 0
      " 2>/dev/null || echo "0")

      COMMENT_BODIES=$(echo "$COMMENTS_JSON" | jq -r "
        [ .[] | select(
          (.author // \"system\") != \"system\"
          and (.comment_type // \"system\") != \"system\"
          and (.comment_type // \"progress\") != \"progress\"
          and (.id // 0) > $LAST_SEEN_COMMENT_ID
        ) | \"[\(.author)] \(.content)\" ] | join(\"\n---\n\")
      " 2>/dev/null || echo "")

      LAST_SEEN_COMMENT_ID=$NEW_IDS

      echo "💬 Neuer User-Kommentar erkannt — starte Follow-up..."
      post_progress "💬 User-Feedback erhalten — verarbeite..." "system"

      FOLLOWUP_PROMPT="# User-Feedback zu Ticket ${TICKET_ID}:

${COMMENT_BODIES}

Bitte beruecksichtige dieses Feedback und passe die Aenderungen entsprechend an.
Commite und pushe die Aenderungen auf denselben Branch."

      unset OPENCODE_SERVER_PASSWORD
      opencode run \
        --title "[${TICKET_ID}] Follow-up: User-Feedback" \
        "$FOLLOWUP_PROMPT" || echo "⚠️  Follow-up opencode run fehlgeschlagen (Exit: $?)"

      echo "🔑 Re-inject Git credentials nach Follow-up..."
      inject_git_credentials

      cd "$PRIMARY_REPO" 2>/dev/null || true
      if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        git add -A
        git commit -m "[${TICKET_ID}] Follow-up: User-Feedback" --allow-empty 2>/dev/null || true
        git push -u origin "$BRANCH" 2>&1 || echo "⚠️  Follow-up Push fehlgeschlagen"
      fi

      post_progress "✅ Follow-up abgeschlossen" "system"
      echo "✅ Follow-up abgeschlossen, weiteres Polling..."
    fi

    sleep "$COMMENT_POLL_INTERVAL"
  done &
  COMMENT_POLL_PID=$!
fi

# opencode web is already running (started before opencode run --attach)
# Web UI stays available for interactive corrections on port 4096
echo "🌐 OpenCode Web UI laeuft auf Port 4096 (PID $WEB_PID) — warte bis sie beendet wird..."
wait $WEB_PID 2>/dev/null || true