# HiveMind Optimization & Bugfix Plan

Comprehensive analysis of the HiveMind project with prioritized fixes.

## Summary

~130+ issues found across security, bugs, performance, DevOps, and code quality.

| Category      | Critical | High | Medium | Low |
|---------------|----------|------|--------|-----|
| Bugs          | 5        | 12   | 15     | 10  |
| Security      | 3        | 8    | 10     | 3   |
| Performance   | 0        | 5    | 5      | 2   |
| DevOps/Infra  | 3        | 6    | 8      | 5   |
| Tests         | 0        | 2    | 4      | 5   |

---

## Phase 1: Critical Security & Bug Fixes

### 1.1 SQL Injection in metrics.py and database_pg.py
**Severity:** CRITICAL  
**Files:** `Orchestrator/database/metrics.py:70`, `Orchestrator/database_pg.py:1683`

The `since` parameter is interpolated via f-string directly into SQL queries:
```python
tickets_since = f"AND created_at >= '{since}'" if since else ""
```

**Fix:** Use parameterized queries:
```python
# metrics.py (SQLite - use ? placeholders)
since_clause = "AND created_at >= ?" if since else ""
params = [since] if since else []
c.execute(f"SELECT COUNT(*) FROM tickets WHERE 1=1 {since_clause}", params)
```

```python
# database_pg.py (PostgreSQL - use %s placeholders)
since_clause = "AND created_at >= %s" if since else ""
params = [since] if since else []
c.execute(f"SELECT COUNT(*) FROM tickets WHERE 1=1 {since_clause}", params)
```

This affects ALL 12 queries in `get_metrics_summary()` in both files.

---

### 1.2 Undefined Functions in review_monitor.py
**Severity:** CRITICAL  
**File:** `Orchestrator/background/review_monitor.py`

Functions called but never imported:
- Line 156: `set_ticket_completed_at` — not in `from database import ...`
- Line 158: `record_metric_event` — not imported
- Line 166,200: `increment_review_cycle_count` — not imported
- Line 225,238: `set_ticket_first_pipeline_status` — not imported
- Line 179: `update_ticket_phase_timestamp` — not imported

**Fix:** Add missing imports to line 28-31:
```python
from database import (
    get_open_mr_tickets, get_ticket, update_ticket_status,
    update_ticket_review, set_ticket_mr_url, add_ticket_comment,
    update_ticket_mr_tracking, requeue_ticket,
    set_ticket_completed_at, record_metric_event,
    increment_review_cycle_count, set_ticket_first_pipeline_status,
    update_ticket_phase_timestamp, get_queue,
)
```

---

### 1.3 FastAPI Body Parsing — `data: dict` Not Working
**Severity:** HIGH  
**Files:** `Orchestrator/api/agent_profiles.py:42,62`, `Orchestrator/api/tickets.py:196`

FastAPI cannot parse `data: dict` as a request body in sync endpoints. It needs either a Pydantic model or explicit `Request` object.

**Fix for agent_profiles.py:**
```python
from fastapi import Request

@router.post("/api/agent-profiles")
async def api_create_agent_profile(req: Request):
    data = await req.json()
    # ... rest same

@router.patch("/api/agent-profiles/{agent_id}")
async def api_update_agent_profile(agent_id: str, req: Request):
    data = await req.json()
    # ... rest same
```

**Fix for tickets.py:196:**
```python
@router.post("/api/tickets/{ticket_id}/comments")
async def api_add_ticket_comment(ticket_id: str, req: Request):
    data = await req.json()
    # ... rest same
```

---

### 1.4 Path Traversal in memory_api.py
**Severity:** CRITICAL  
**File:** `Orchestrator/api/memory_api.py:97-101`

`memory_dir` is user-controlled and passed directly to `_glob.glob()`.

**Fix:** Validate path against allowed base directory:
```python
import os

ALLOWED_MEMORY_DIRS = {"/home/hivemind/.config/opencode/memory", "/workspace"}

@router.post("/api/agent-memory/{agent_id}/sync-filesystem")
async def api_agent_memory_sync_filesystem(agent_id: str, req: Request):
    data = await req.json()
    memory_dir = data.get("memory_dir", "/home/hivemind/.config/opencode/memory")
    real_dir = os.path.realpath(memory_dir)
    if not any(real_dir.startswith(allowed) for allowed in ALLOWED_MEMORY_DIRS):
        raise HTTPException(status_code=400, detail="Invalid memory_dir path")
    # ... rest same
```

---

### 1.5 Git Credentials File Security
**Severity:** HIGH  
**File:** `Orchestrator/git_credentials.py:45-48`

Credentials written to `~/.git-credentials` without proper file permissions.

**Fix:**
```python
git_dir = Path.home() / ".git-credentials"
git_dir.write_text("".join(lines), encoding="utf-8")
git_dir.chmod(0o600)
```

---

### 1.6 Webhook Verification Bugs
**Severity:** HIGH  
**File:** `Orchestrator/api/webhooks.py:52-60` and `Orchestrator/middleware.py`

**GitLab webhook:** The `verify_gitlab_webhook()` function computes HMAC-SHA256 of the body and compares it to `X-Gitlab-Token`. But GitLab sends the webhook secret as a plain token in `X-Gitlab-Token`, not as an HMAC. The verification will ALWAYS fail.

**Fix:** Compare the token directly:
```python
def verify_gitlab_webhook(body: bytes, signature: str) -> bool:
    if not GITLAB_WEBHOOK_SECRET:
        return True
    if not signature:
        return False
    return hmac.compare_digest(signature, GITLAB_WEBHOOK_SECRET)
```

**GitHub webhook (line 261):** When `github_webhook_secret` is set but `signature` header is missing, verification is skipped entirely.

**Fix:** Reject requests missing the signature when the secret is configured:
```python
github_webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
if github_webhook_secret:
    if not signature:
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    expected = "sha256=" + hmac.new(github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
```

---

### 1.7 Race Conditions in Queue Assignment
**Severity:** HIGH  
**Files:** `Orchestrator/api/agents.py:97-154`, `Orchestrator/background/queue_processor.py:138-152`

Between `get_idle_agents()` and `assign_queue_item()`, another request could assign the same agent.

**Fix (PostgreSQL):** Use `SELECT ... FOR UPDATE SKIP LOCKED` in `get_next_queue_item()`:
```python
def get_next_queue_item():
    conn = get_db()
    with conn.cursor() as c:
        c.execute("""
            UPDATE queue SET status = 'running', assigned_agent_id = %s
            WHERE id = (
                SELECT id FROM queue
                WHERE status = 'waiting'
                ORDER BY position ASC, created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING *
        """, (agent_id,))
        item = c.fetchone()
    conn.commit()
    conn.close()
    return item
```

**Fix (SQLite):** Add a threading lock around queue operations, or use `BEGIN IMMEDIATE` transactions.

---

### 1.8 Missing Functions in PostgreSQL Backend
**Severity:** HIGH  
**File:** `Orchestrator/database_pg.py`

Missing functions that exist in SQLite but not in PostgreSQL:
- `update_ticket_description()`
- `set_repo_active()`
- `get_steps()` (only `get_all_steps()` exists)
- `active` column missing from `repos` table SELECT
- `metric_events` table not created in `init_db()`
- Multiple ticket columns missing from `tickets` table

**Fix:** Add all missing functions and columns to `database_pg.py`. This requires adding:
1. `update_ticket_description(ticket_id, description)`
2. `set_repo_active(repo_name, active)` 
3. `get_steps(ticket_id=None, agent_id=None, queue_id=None)` alias
4. Add `active` to `repos` SELECT statements
5. Add `metric_events` CREATE TABLE to `init_db()`
6. Add missing `tickets` columns: `phase_work_started_at`, `phase_test_started_at`, `phase_ship_started_at`, `phase_listen_started_at`, `completed_at`, `merged_at`, `first_pipeline_status`, `review_cycle_count`, `model_used`, `llm_prompt_tokens`, `llm_completion_tokens`, `llm_total_cost_usd`, `primary_repo`, `lines_added`, `lines_removed`, `files_changed`

---

### 1.9 `ensure_agent_pool` Hardcodes 3
**Severity:** HIGH  
**Files:** `Orchestrator/database/agents.py:111`, `Orchestrator/database_pg.py:680`

```python
max_agents = get_max_agents()  # retrieved but unused!
for i in range(3):  # hardcoded!
```

**Fix:**
```python
max_agents = get_max_agents()
for i in range(max_agents):
```

---

### 1.10 `delete_agent` Missing Cascade for `agent_repo_affinities`
**Severity:** MEDIUM  
**Files:** All three database backends

`delete_agent()` deletes from `agent_skills` and `agent_instruction_assignments` but not `agent_repo_affinities`.

**Fix:** Add to all three backends:
```python
c.execute("DELETE FROM agent_repo_affinities WHERE agent_id = ?", (agent_id,))
```

---

## Phase 2: Performance Fixes

### 2.1 Connection Pooling (PostgreSQL)
**File:** `Orchestrator/database_pg.py:37-39`

Every operation creates a new `psycopg2.connect()`. Add connection pooling:
```python
from psycopg2 import pool

_pool = None

def _init_pool():
    global _pool
    if _pool is None:
        _pool = pool.ThreadedConnectionPool(1, 20, DB_URL)

def get_db():
    _init_pool()
    return _pool.getconn()

def put_db(conn):
    _pool.putconn(conn)
```

### 2.2 Database Indexes
**File:** `Orchestrator/database/init_db.py`, `Orchestrator/database_pg.py`

Add to `init_db()`:
```sql
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_agent_id ON tickets(agent_id);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_ticket_id ON queue(ticket_id);
CREATE INDEX IF NOT EXISTS idx_steps_ticket_id ON steps(ticket_id);
CREATE INDEX IF NOT EXISTS idx_steps_agent_id ON steps(agent_id);
CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket_id ON ticket_comments(ticket_id);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_agent_skills_agent_id ON agent_skills(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_repo_affinities_agent_id ON agent_repo_affinities(agent_id);
```

### 2.3 Blocking Subprocess in Async Context
**Files:** `Orchestrator/api/repos_api.py:93-95`, `Orchestrator/background/agent_monitor.py:51,79,295`

Replace `subprocess.run()` with `asyncio.to_thread()` or `asyncio.create_subprocess_exec()`:
```python
import asyncio

async def api_repo_branches(...):
    result = await asyncio.to_thread(subprocess.run, ["git", ...], capture_output=True, text=True, timeout=15)
```

---

## Phase 3: API Design & Code Quality

### 3.1 HTTP 404 Not Working (FastAPI Tuple Return)
**File:** `Orchestrator/api/tickets.py:56-58`

```python
# WRONG: FastAPI doesn't interpret (body, status_code) tuples
return {"error": "Not found"}, 404

# FIX:
raise HTTPException(status_code=404, detail="Ticket not found")
```

### 3.2 GET Endpoint Creates Data
**File:** `Orchestrator/api/agents.py:48-51`

`GET /api/agents/{agent_id}` calls `get_or_create_agent()` which creates a new agent if not found.

**Fix:** Separate into GET (404 if not found) and POST/PUT for creation.

### 3.3 Ticket Status Parameter Ignored
**File:** `Orchestrator/api/tickets.py:206-208`

`GET /api/tickets/status/{status}` ignores the `status` path parameter.

**Fix:** Pass `status` to `get_tickets_with_queue(status=status)`.

### 3.4 `_shutdown_requested` Import by Value
**File:** `Orchestrator/background/review_monitor.py:38`

```python
from background.queue_processor import _shutdown_requested
```
This imports the boolean value, not a reference. If `set_shutdown(True)` is called later, the local `_shutdown_requested` still holds `False`.

**Fix:**
```python
import background.queue_processor as _qp
# Then use: while not _qp._shutdown_requested:
```

### 3.5 Duplicate `tickets` Table Creation in init_db
**File:** `Orchestrator/database/init_db.py`

Two `CREATE TABLE IF NOT EXISTS tickets` blocks (lines 24-43 and 62-81). Remove the duplicate.

### 3.6 `_ai_enrich_repo` Silently Swallows Exceptions
**File:** `Orchestrator/main.py:164-181`

```python
except Exception: pass
```

**Fix:** Log the error:
```python
except Exception as e:
    _struct_log.warning(f"AI enrich failed for repo: {e}")
```

### 3.7 Rate Limiter Memory Leak
**File:** `Orchestrator/middleware.py:70-97`

`_rate_limit_store` grows indefinitely. Add TTL-based eviction or use a bounded dict.

### 3.8 CORS Default Allows All Origins
**File:** `Orchestrator/config.py:46`

```python
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
```

**Fix:** Default to `http://localhost:8080` instead of `*`.

---

## Phase 4: DevOps & Infrastructure

### 4.1 Docker Compose Port Conflict
**File:** `docker-compose.yaml:117,142`

Both `ticketservice` (port 8080) and `orchestrator-serve` (port 8080) use the same host port.

**Fix:** Change ticketservice to `8081:8080`.

### 4.2 Docker Compose Malformed YAML
**File:** `docker-compose.yaml:202-205`

```yaml
  ticketservice-db:
        condition: service_healthy
    profiles:
      - tickets
```

This is a remnant of a `depends_on` block separated from its parent. Fix by reattaching to `ticketservice` service.

### 4.3 K8s Missing GITLAB_HOST Secret Key
**File:** `Orchestrator/k8s/orchestrator.yaml:137-139`

References `secretKeyRef: {key: GITLAB_HOST}` but the Secret only has `ssh-privatekey` and `GITLAB_TOKEN`.

**Fix:** Add `GITLAB_HOST` to the Secret or change to a ConfigMap/env var.

### 4.4 No Resource Limits or Health Probes in Kustomize
**File:** `Orchestrator/kustomize/base/deployment.yaml`

Add `resources.requests` and `resources.limits`, plus `livenessProbe` and `readinessProbe` for `/healthz` and `/readyz`.

### 4.5 Kustomize Invalid JSON
**File:** `Orchestrator/kustomize/overlays/dev/configmap-patch.yaml:10`

`"track_branch.*"main"` should be `"track_branch": "main"`.

### 4.6 Agent Dockerfile `curl | sh` Supply Chain Risk
**File:** `Agent/Dockerfile:38`

Pin the version and verify checksum, or copy the install script locally.

### 4.7 Helm Chart Secrets in Plaintext
**File:** `Orchestrator/helm/orchestrator/templates/deployment.yaml:38,51,55,59,78,100,109,114,117,123,131,140`

All secrets are passed as plain `value:` template strings. Move to `secretKeyRef`.

### 4.8 `redeploy.sh` Secret in Command-Line Args
**File:** `redeploy.sh:290-313`

`kubectl create secret ... --from-literal=GIT_TOKEN="${GIT_TOKEN}"` exposes the token in process list.

**Fix:** Use `--from-env-file` or pipe through stdin.

---

## Phase 5: Test Improvements

### 5.1 Trivial Tests
**File:** `Orchestrator/tests/test_unit.py`

Tests like `3 < 3 == False` prove nothing. Replace with actual function tests from the application.

### 5.2 `os.environ.clear()` in Tests
**File:** `Orchestrator/tests/test_hivemind.py:28-33`

**Fix:** Use `unittest.mock.patch.dict(os.environ, ...)` instead of global mutation.

### 5.3 Real kubectl Calls in Unit Tests
**File:** `Orchestrator/tests/test_unit.py:153-175`

**Fix:** Mock `subprocess.run` for unit tests. Mark kubectl tests as integration tests.

---

## Implementation Order

1. **SQL Injection** (1.1) — Critical, immediate
2. **Undefined Functions** (1.2) — Critical, immediate  
3. **Path Traversal** (1.4) — Critical, immediate
4. **Webhook Verification** (1.6) — High, security
5. **FastAPI Body Parsing** (1.3) — High, API broken
6. **Git Credentials Security** (1.5) — High, security
7. **ensure_agent_pool fix** (1.9) — High, logic bug
8. **Missing PG Functions** (1.8) — High, runtime errors
9. **Race Conditions** (1.7) — High, data corruption
10. **delete_agent cascade** (1.10) — Medium, data integrity
11. **HTTP 404 Fix** (3.1) — Medium, API correctness
12. **Connection Pooling** (2.1) — Medium, performance
13. **DB Indexes** (2.2) — Medium, performance
14. **Docker Compose fixes** (4.1, 4.2) — Medium, deployment
15. **K8s fixes** (4.3-4.5) — Medium, deployment
16. **Test improvements** (5.1-5.3) — Low, quality
17. **Remaining items** — Low priority