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

"""
Pipeline phase definitions, transition validation, and role mapping.

Phases:
  work    → developer/loganalyst implements the change
  test    → tester writes and runs tests
  review  → reviewer performs code review + creates MR
  ship    → developer/reviewer merges and cleans up

Roles:
  developer   — implements features, fixes bugs
  tester       — writes and runs tests
  reviewer     — code review, MR creation
  loganalyst   — log analysis, root cause investigation
  general      — can perform any phase (backward compatible)
"""

from typing import Dict, List, Optional, Tuple

PHASE_ORDER = ["work", "test", "review", "ship"]

PHASE_LABELS = {
    "work": "Work",
    "test": "Test",
    "review": "Review",
    "ship": "Ship",
}

PHASE_DESCRIPTIONS = {
    "work": "Implement the change — write code, fix bugs, add features",
    "test": "Write and run tests — unit tests, integration tests, validate",
    "review": "Code review — review changes, create MR, address feedback",
    "ship": "Ship — merge MR, clean up branches, finalize",
}

VALID_PHASE_TRANSITIONS: Dict[str, List[str]] = {
    "work": ["test", "review"],
    "test": ["review", "work"],
    "review": ["ship", "work"],
    "ship": [],
}

ROLE_REQUIRED_PHASES: Dict[str, List[str]] = {
    "developer": ["work", "ship"],
    "tester": ["test"],
    "reviewer": ["review"],
    "loganalyst": ["work"],
    "general": PHASE_ORDER,
}

PHASE_PREFERRED_ROLES: Dict[str, List[str]] = {
    "work": ["loganalyst", "developer", "general"],
    "test": ["tester", "general"],
    "review": ["reviewer", "general"],
    "ship": ["developer", "reviewer", "general"],
}

ROLE_DISPLAY_NAMES = {
    "developer": "Developer",
    "tester": "Tester",
    "reviewer": "Reviewer",
    "loganalyst": "Log Analyst",
    "general": "General Purpose",
}

DEFAULT_ROLE_INSTRUCTIONS = {
    "developer": """You are a senior software developer. Your job is to implement changes, fix bugs, and add features.
- Write clean, well-structured, maintainable code
- Follow existing code patterns and conventions in the repository
- Include appropriate error handling and logging
- Ensure your changes are minimal and focused on the task at hand
- When done, report completion with a summary of changes made""",

    "tester": """You are a test engineer. Your job is to write and run tests to validate changes.
- Write comprehensive unit and integration tests
- Test edge cases, error conditions, and boundary values
- Use the existing test framework and patterns in the repository
- If tests fail, provide clear descriptions of what failed and why
- If all tests pass, report success with coverage summary""",

    "reviewer": """You are a code reviewer. Your job is to review code changes and create merge requests.
- Review for correctness, performance, security, and maintainability
- Check for edge cases, error handling, and test coverage
- Create a clear, well-described merge request
- If you find issues, describe them clearly with suggested fixes
- If the code looks good, approve and create the MR""",

    "loganalyst": """You are a log analyst and root cause investigator. Your job is to analyze logs and errors to find root causes.
- Examine error messages, stack traces, and log patterns
- Identify the root cause of issues
- Propose targeted fixes with clear explanations
- Document your findings for the team
- When done, report your analysis and proposed fix""",

    "general": """You are a general-purpose AI agent. You can handle any phase of the development pipeline.
- Adapt your approach based on the current phase (work, test, review, or ship)
- Follow the specific guidelines for each phase
- Communicate clearly with team members via messages
- When done, report completion with a summary""",
}


def get_next_phase(current_phase: str) -> Optional[str]:
    if current_phase not in PHASE_ORDER:
        return None
    idx = PHASE_ORDER.index(current_phase)
    if idx + 1 < len(PHASE_ORDER):
        return PHASE_ORDER[idx + 1]
    return None


def validate_phase_transition(from_phase: str, to_phase: str) -> bool:
    allowed = VALID_PHASE_TRANSITIONS.get(from_phase, [])
    return to_phase in allowed


def get_preferred_roles_for_phase(phase: str) -> List[str]:
    return PHASE_PREFERRED_ROLES.get(phase, ["general"])


def can_role_handle_phase(role: str, phase: str) -> bool:
    phases = ROLE_REQUIRED_PHASES.get(role, PHASE_ORDER)
    return phase in phases


def select_agent_for_phase(phase: str, idle_agents: List[Dict]) -> Optional[Dict]:
    preferred_roles = get_preferred_roles_for_phase(phase)
    for role in preferred_roles:
        for agent in idle_agents:
            agent_role = agent.get("role", "general")
            if agent_role == role and can_role_handle_phase(agent_role, phase):
                return agent
    for agent in idle_agents:
        agent_role = agent.get("role", "general")
        if can_role_handle_phase(agent_role, phase):
            return agent
    return idle_agents[0] if idle_agents else None


def get_phase_from_ticket(ticket_data: Dict) -> str:
    return ticket_data.get("current_phase", "work") or "work"


def get_initial_phase(ticket_data: Optional[Dict] = None) -> str:
    if ticket_data:
        issue_type = (ticket_data.get("issue_type") or "").lower()
        labels = []
        raw_labels = ticket_data.get("labels", "[]")
        if isinstance(raw_labels, str):
            import json
            try:
                labels = [l.lower() for l in json.loads(raw_labels)]
            except (json.JSONDecodeError, TypeError):
                labels = []
        if "bug" in labels or issue_type == "bug":
            return "work"
        if "test" in labels or issue_type == "test":
            return "test"
        if "review" in labels or issue_type == "review":
            return "review"
    return "work"


from datetime import datetime
from typing import Dict as DictType


def create_pipeline_step(ticket_id: str, phase: str, agent_id: str, group_id: Optional[str] = None) -> DictType:
    from database.sqlite_backend import get_db
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute(
        "INSERT INTO pipeline_steps (ticket_id, group_id, phase, agent_id, status, started_at, created_at) VALUES (?, ?, ?, ?, 'running', ?, ?)",
        (ticket_id, group_id, phase, agent_id, now, now),
    )
    step_id = c.lastrowid
    conn.commit()
    conn.close()
    return {"id": step_id, "ticket_id": ticket_id, "group_id": group_id, "phase": phase, "agent_id": agent_id, "status": "running"}


def complete_pipeline_step(step_id: int, result: str = ""):
    from database.sqlite_backend import get_db
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE pipeline_steps SET status = 'completed', result = ?, completed_at = ? WHERE id = ?", (result, now, step_id))
    conn.commit()
    conn.close()


def fail_pipeline_step(step_id: int, result: str = ""):
    from database.sqlite_backend import get_db
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE pipeline_steps SET status = 'failed', result = ?, completed_at = ? WHERE id = ?", (result, now, step_id))
    conn.commit()
    conn.close()


def get_pipeline_steps(ticket_id: str) -> List[DictType]:
    from database.sqlite_backend import get_db
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM pipeline_steps WHERE ticket_id = ? ORDER BY created_at ASC", (ticket_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]