# HiveMind Improvement Backlog

## P0 — Sofort beheben (Sicherheit)

### S-1: GitLab Token in Pod-Logs/ConfigMap
- **Datei**: `Orchestrator/pod_builder.py:220`
- **Problem**: `${GIT_USER}:${GITLAB_TOKEN}@` in git clone URL → sichtbar in Pod-Logs & `kubectl describe pod`
- **Fix**: Git credential helper statt Credentials-in-URL; `git config credential.helper store` + `~/.git-credentials`

### S-3: API-Key Timing Attack
- **Datei**: `Orchestrator/middleware.py:84`
- **Problem**: `api_key != HIVEMIND_API_KEY` ist nicht constant-time — Timing-Seitenkanal
- **Fix**: `hmac.compare_digest(api_key, HIVEMIND_API_KEY or "")` (wie bei Webhook-Verify bereits verwendet)

### S-4: Proxy-Password in Query-String
- **Datei**: `Orchestrator/api/proxy.py:39`
- **Problem**: `request.query_params.get("password")` — Passwort landet in Access-Logs, Browser-History, Referrern
- **Fix**: Nur `Authorization: Bearer` Header oder Cookie akzeptieren, Query-Param entfernen

### S-5: Settings API exponiert Secrets
- **Datei**: `Orchestrator/api/settings_api.py:29`
- **Problem**: `GET /api/settings` gibt `git_token`, `gitlab_token`, `ollama_cloud_api_key` etc. im Klartext zurück
- **Fix**: Sensible Keys maskieren (`glpat-****`) oder eigene `/api/settings/public`-Route ohne Secrets

### D-9: GITLAB_HOST fällt auf GIT_TOKEN zurück
- **Datei**: `Orchestrator/server.py:268`, `Orchestrator/api/repos_api.py:63,230`
- **Problem**: `os.getenv("GITLAB_HOST", os.getenv("GIT_TOKEN", ""))` — GITLAB_HOST bekommt Token-Wert als Fallback
- **Fix**: `os.getenv("GITLAB_HOST", "")` ohne GIT_TOKEN-Fallback

---

## P1 — Bald beheben (Sicherheit + Stabilität)

### S-2: OpenAI/Anthropic Keys als Plaintext Env
- **Datei**: `Orchestrator/pod_builder.py:155-164`
- **Problem**: `OPENAI_API_KEY` als `value=` statt `valueFrom: secretKeyRef` → sichtbar in `kubectl describe pod`
- **Fix**: K8s Secrets erstellen (wie bereits für `gitlab-token` und `ollama-cloud-api-key`)

### S-6: Settings API: keine Key-Whitelist
- **Datei**: `Orchestrator/api/settings_api.py:33-38`
- **Problem**: POST akzeptiert beliebige Key/Value-Paare, inkl. `max_agents=1000`
- **Fix**: Whitelist erlaubter Keys + Typ-Validierung

### R-1: Race Condition in Queue Assignment
- **Datei**: `Orchestrator/background/queue_processor.py:114-182`
- **Problem**: `assign_next_queue_item()` weist Ticket zu, danach wird Agent neu ausgewählt — Inkonsistenz bei Crash
- **Fix**: Atomare Zuweisung: Agent-Auswahl vor Claim, oder Transaktion mit `BEGIN IMMEDIATE`

### R-3: Duplicate Ticket IDs bei Concurrency
- **Datei**: `Orchestrator/database/tickets.py:27`
- **Problem**: `TASK-{timestamp_ms}` kann bei gleichzeitigen Requests kollidieren
- **Fix**: UUID4 oder `INSERT OR RETRY` mit UNIQUE-Constraint

### R-7: Secret Creation Errors werden geschluckt
- **Datei**: `Orchestrator/pod_builder.py:404-416`
- **Problem**: `except Exception: pass` — Pod startet ohne Credentials und crashed
- **Fix**: Mindestens `log.error()`, besser: Pod-Creation als fehlgeschlagen markieren

### S-8: Path Traversal Risk in Memory Sync
- **Datei**: `Orchestrator/api/memory_api.py:96-107`
- **Problem**: `memory_dir` kommt aus User-Input, TOCTOU-Race mit Symlinks
- **Fix**: `memory_dir` nur aus `agent_id` ableiten, nicht aus Request-Body

### S-9: Agent Pod mit permissiven Permissions
- **Datei**: `Orchestrator/pod_builder.py:125-128`
- **Problem**: `PERMISSION_WRITE=allow`, `PERMISSION_BASH=allow`, `PERMISSION_DOOM_LOOP=allow`
- **Fix**: `DOOM_LOOP` mindestens in Produktion deaktivieren; Permissions granularer konfigurierbar machen

### S-10: GIT_SSL_NO_VERIFY=1 deaktiviert Zertifikats-Validierung
- **Datei**: `Orchestrator/pod_builder.py:95`, `Agent/entrypoint.sh:12`
- **Fix**: Nur in Dev-Umgebung setzen; in Produktion richtige CA-Certs mounten

---

## P2 — Nächster Sprint (Zuverlässigkeit + UX)

### R-5: Agent State Transitions nicht validiert
- **Datei**: `Orchestrator/api/agents.py:170-278`
- **Problem**: `VALID_AGENT_TRANSITIONS` existiert, wird aber nie verwendet
- **Fix**: `_validate_transition()` vor jedem Statuswechsel aufrufen

### R-6: `assign_queue_item` ohne Transaction Isolation
- **Datei**: `Orchestrator/database/queue.py:83-91`
- **Problem**: Kein `BEGIN IMMEDIATE` wie bei `assign_next_queue_item`
- **Fix**: `BEGIN IMMEDIATE` hinzufügen

### R-8: Kein Graceful Shutdown für Background Tasks
- **Datei**: `Orchestrator/server.py:412-422`
- **Problem**: Deprecated `@app.on_event("shutdown")`, Background-Tasks werden nicht awaited
- **Fix**: `lifespan` Context Manager + Task-Tracking mit Timeout

### R-12: K8s Pod Delete+Recreate Race
- **Datei**: `Orchestrator/k8s_client.py:397-401`
- **Problem**: `time.sleep(2)` ist fragil
- **Fix**: Poll-Loop mit `get_pod()` bis Pod gelöscht ist, oder `propagationPolicy=Foreground`

### U-1: Inkonsistente API-Error-Responses
- **Datei**: Multiple API-Files
- **Problem**: Manche `{"ok": False}` mit HTTP 200, andere `HTTPException` mit korrektem Status
- **Fix**: Einheitlich `HTTPException` mit korrekten Status-Codes

### U-3: `GET /api/agents/{id}` erstellt neuen Agent
- **Datei**: `Orchestrator/api/agents.py:120-123`
- **Problem**: GET-Endpoint ruft `get_or_create_agent()` auf — nicht idempotent
- **Fix**: `get_agent()` nutzen, 404 wenn nicht gefunden

### U-6: Keine Ticket-Input-Validation
- **Datei**: `Orchestrator/api/tickets.py:129-139`
- **Problem**: POST akzeptiert beliebiges JSON, Pflichtfelder fehlen
- **Fix**: Pydantic-Model für Ticket-Creation mit Validierung

### D-4: Kein Health Check für Orchestrator
- **Datei**: `docker-compose.yaml`
- **Fix**: `healthcheck:` mit `curl -f http://localhost:8080/healthz`

### D-6: Orchestrator `depends_on: {}` leer
- **Datei**: `docker-compose.yaml:149`
- **Fix**: `depends_on` mit Redis + PostgreSQL + Health Conditions

---

## P3 — Wenn Zeit (Performance + DevOps)

### P-1: N+1 Queries bei Agent Profiles
- **Datei**: `Orchestrator/database/agents.py:249-255`
- **Problem**: 10 Agents = 40+ DB-Connections (4 pro Agent)
- **Fix**: JOIN-Query statt N+1 einzelne Queries

### P-4: `/metrics` lädt ALLE Tickets
- **Datei**: `Orchestrator/server.py:89-177`
- **Problem**: `get_tickets(status=None)` + mehrfache Iteration
- **Fix**: Dedizierte COUNT-Queries + Caching (TTL 10-30s)

### P-5: `_serve_spa` liest Datei bei jedem Request
- **Datei**: `Orchestrator/server.py:177-179`
- **Problem**: Blockiert Event Loop, kein Caching
- **Fix**: HTML einmal in Memory laden + bei Bedarf refreshen

### P-2: Fehlende DB-Indizes
- **Datei**: `Orchestrator/database/init_db.py`
- **Problem**: `team_channel_messages.group_id` und `ticket_comments(ticket_id, created_at)` ohne Index
- **Fix**: `CREATE INDEX IF NOT EXISTS` hinzufügen

### P-3: `/api/repos/{name}/branches` — Zwei langsame Calls
- **Datei**: `Orchestrator/api/repos_api.py:54-111`
- **Fix**: Caching mit TTL + `asyncio.gather` für parallele Calls

### U-2: Keine Pagination
- **Datei**: Multiple Endpoints
- **Problem**: `get_tickets()`, `get_all_agents()` etc. laden immer alles
- **Fix**: `skip` + `limit` Query-Parameter mit Defaults

### U-5: SPA HTML nicht gecacht
- **Datei**: `Orchestrator/server.py:177-179`
- **Fix**: Inhalt beim Start laden, periodisch refreshen oder File-Watcher

### U-7: CORS `*` Default in Docker Compose
- **Datei**: `docker-compose.yaml:142`
- **Fix**: Restriktiven Default setzen, explizite Konfiguration für Produktion

### R-2: Global `_worker = None` Mutation
- **Datei**: `Orchestrator/api/repos_api.py` (8 Stellen)
- **Fix**: Thread-safe Reset-Methode auf `WorkspaceBuilder`

### R-4: SQLite: Open/Close pro Operation
- **Datei**: `Orchestrator/database/sqlite_backend.py:27-54`
- **Fix**: Connection-Pool oder Request-Scoped Connection

### R-9: Orphan Recovery mit fragilem Pod-Name-Parsing
- **Datei**: `Orchestrator/server.py:241-242`
- **Fix**: Label-basiertes Matching (`ticket-id` Label) statt String-Replacement

### R-10: Webhook Dedup wächst unbegrenzt
- **Datei**: `Orchestrator/api/webhooks.py:47-48`
- **Fix**: Größe capen oder Redis nutzen

### R-11: `eval` von TEST_COMMAND
- **Datei**: `Agent/entrypoint.sh:483`
- **Fix**: Allowlist oder Array-basierte Ausführung statt `eval`

### D-1: Dockerfile kopiert einzeln
- **Datei**: `Orchestrator/Dockerfile:18-39`
- **Fix**: `COPY . .` mit `.dockerignore`

### D-2: Agent `@latest` Tag
- **Datei**: `Agent/Dockerfile:17`
- **Fix**: Pin auf spezifische Version

### D-3: Hardcoded DB-Passwords
- **Datei**: `docker-compose.yaml:95-97,156-159`
- **Fix**: `${VAR}`-Substitution mit `.env`

### D-5: Keine Resource Limits
- **Datei**: `docker-compose.yaml`
- **Fix**: `mem_limit` und `cpus` für jeden Service

### D-7: Data-Dir Permissions
- **Datei**: `Orchestrator/Dockerfile:47`
- **Fix**: `mkdir -p /app/data && chown 1000:1000 /app/data`

### D-8: Kein `.dockerignore`
- **Fix**: `.dockerignore` erstellen (`.git/`, `__pycache__/`, `tests/`, `.idea/`, `.env`)

### S-7: Unrestricted `/api/restart`
- **Datei**: `Orchestrator/api/repos_api.py:363-366`
- **Fix**: Confirmation-Token oder Admin-only Restriction