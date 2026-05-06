# Copyright 2026 Mael Klingler
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
from datetime import datetime
from typing import Dict, List, Optional

from database.sqlite_backend import get_db


def record_metric_event(event_type: str, ticket_id: str = None, agent_id: str = None,
                         phase: str = None, duration_seconds: float = None,
                         labels: dict = None, value: float = None):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""INSERT INTO metric_events (event_type, ticket_id, agent_id, phase, duration_seconds, labels, value, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
              (event_type, ticket_id, agent_id, phase, duration_seconds,
               json.dumps(labels) if labels else None, value, now))
    conn.commit()
    conn.close()


def get_metric_events(event_type: str = None, ticket_id: str = None,
                       agent_id: str = None, phase: str = None,
                       since: str = None, limit: int = 1000) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    conditions = []
    params = []
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)
    if ticket_id:
        conditions.append("ticket_id = ?")
        params.append(ticket_id)
    if agent_id:
        conditions.append("agent_id = ?")
        params.append(agent_id)
    if phase:
        conditions.append("phase = ?")
        params.append(phase)
    if since:
        conditions.append("created_at >= ?")
        params.append(since)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    c.execute(f"SELECT * FROM metric_events {where} ORDER BY created_at DESC LIMIT ?", params + [limit])
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_metrics_summary(since: str = None) -> Dict:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()

    tickets_since = f"AND created_at >= '{since}'" if since else ""

    total = c.execute(f"SELECT COUNT(*) FROM tickets WHERE 1=1 {tickets_since}").fetchone()[0]
    merged = c.execute(f"SELECT COUNT(*) FROM tickets WHERE status = 'merged' {tickets_since}").fetchone()[0]
    completed = c.execute(f"SELECT COUNT(*) FROM tickets WHERE status = 'completed' {tickets_since}").fetchone()[0]
    failed = c.execute(f"SELECT COUNT(*) FROM tickets WHERE status = 'failed' {tickets_since}").fetchone()[0]

    avg_retries_row = c.execute(f"SELECT AVG(CAST(retry_count AS REAL)) FROM tickets WHERE retry_count > 0 {tickets_since}").fetchone()
    avg_retries = float(avg_retries_row[0]) if avg_retries_row and avg_retries_row[0] else 0
    total_retries_row = c.execute(f"SELECT SUM(retry_count) FROM tickets WHERE 1=1 {tickets_since}").fetchone()
    total_retries = int(total_retries_row[0]) if total_retries_row and total_retries_row[0] else 0

    first_pipeline_passes = c.execute(f"SELECT COUNT(*) FROM tickets WHERE first_pipeline_status = 'passed' {tickets_since}").fetchone()[0]
    first_pipeline_total = c.execute(f"SELECT COUNT(*) FROM tickets WHERE first_pipeline_status != 'unknown' AND first_pipeline_status IS NOT NULL {tickets_since}").fetchone()[0]

    avg_review_row = c.execute(f"SELECT AVG(CAST(review_cycle_count AS REAL)) FROM tickets WHERE review_cycle_count > 0 {tickets_since}").fetchone()
    avg_review_cycles = float(avg_review_row[0]) if avg_review_row and avg_review_row[0] else 0

    avg_llm_row = c.execute(f"SELECT AVG(llm_total_cost_usd) FROM tickets WHERE llm_total_cost_usd > 0 {tickets_since}").fetchone()
    avg_llm_cost = float(avg_llm_row[0]) if avg_llm_row and avg_llm_row[0] else 0
    total_llm_row = c.execute(f"SELECT SUM(llm_total_cost_usd) FROM tickets WHERE 1=1 {tickets_since}").fetchone()
    total_llm_cost = float(total_llm_row[0]) if total_llm_row and total_llm_row[0] else 0.0
    total_prompt_row = c.execute(f"SELECT SUM(llm_prompt_tokens) FROM tickets WHERE 1=1 {tickets_since}").fetchone()
    total_prompt_tokens = int(total_prompt_row[0]) if total_prompt_row and total_prompt_row[0] else 0
    total_completion_row = c.execute(f"SELECT SUM(llm_completion_tokens) FROM tickets WHERE 1=1 {tickets_since}").fetchone()
    total_completion_tokens = int(total_completion_row[0]) if total_completion_row and total_completion_row[0] else 0

    conn.close()

    return {
        "timestamp": now,
        "success_rate": (merged / total * 100) if total > 0 else 0,
        "merge_rate": (merged / total * 100) if total > 0 else 0,
        "failure_rate": (failed / total * 100) if total > 0 else 0,
        "total_tickets": total,
        "merged_tickets": merged,
        "completed_tickets": completed,
        "failed_tickets": failed,
        "avg_retries": round(avg_retries, 2),
        "total_retries": total_retries,
        "first_pipeline_pass_rate": (first_pipeline_passes / first_pipeline_total * 100) if first_pipeline_total > 0 else 0,
        "avg_review_cycles": round(avg_review_cycles, 2),
        "avg_llm_cost_usd": round(avg_llm_cost, 4),
        "total_llm_cost_usd": round(total_llm_cost, 4),
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
    }


def update_ticket_phase_timestamp(ticket_id: str, phase: str):
    phase_columns = {
        "work": "phase_work_started_at",
        "test": "phase_test_started_at",
        "ship": "phase_ship_started_at",
        "listen": "phase_listen_started_at",
    }
    col = phase_columns.get(phase)
    if not col:
        return
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute(f"UPDATE tickets SET {col} = ?, updated_at = ? WHERE id = ? AND {col} IS NULL", (now, now, ticket_id))
    conn.commit()
    conn.close()


def update_ticket_llm_usage(ticket_id: str, prompt_tokens: int = 0, completion_tokens: int = 0, cost_usd: float = 0.0, model: str = ""):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    sets = ["llm_prompt_tokens = llm_prompt_tokens + ?", "llm_completion_tokens = llm_completion_tokens + ?",
            "llm_total_cost_usd = llm_total_cost_usd + ?", "updated_at = ?"]
    params = [prompt_tokens, completion_tokens, cost_usd, now]
    if model:
        sets.append("model_used = ?")
        params.append(model)
    params.append(ticket_id)
    c.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    conn.close()


def increment_review_cycle_count(ticket_id: str):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE tickets SET review_cycle_count = review_cycle_count + 1, updated_at = ? WHERE id = ?", (now, ticket_id))
    conn.commit()
    conn.close()


def set_ticket_first_pipeline_status(ticket_id: str, status: str):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE tickets SET first_pipeline_status = ?, updated_at = ? WHERE id = ? AND (first_pipeline_status IS NULL OR first_pipeline_status = 'unknown')",
              (status, now, ticket_id))
    conn.commit()
    conn.close()


def set_ticket_completed_at(ticket_id: str, status: str = None):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    col = "merged_at" if status == "merged" else "completed_at"
    c.execute(f"UPDATE tickets SET {col} = ?, updated_at = ? WHERE id = ?", (now, now, ticket_id))
    conn.commit()
    conn.close()


def set_ticket_primary_repo(ticket_id: str, primary_repo: str):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE tickets SET primary_repo = ?, updated_at = ? WHERE id = ?", (primary_repo, now, ticket_id))
    conn.commit()
    conn.close()