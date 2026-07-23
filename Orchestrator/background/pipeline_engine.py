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
Pipeline Engine — orchestrates multi-phase ticket processing.

Each ticket flows through phases: work → test → review → ship.
Different agent roles handle different phases:
  - developer/loganalyst → work phase
  - tester → test phase
  - reviewer → review phase
  - developer/reviewer → ship phase

The pipeline engine:
  1. Determines the current phase of a ticket
  2. Selects the best agent for that phase based on role affinity
  3. Creates pipeline_step records to track progress
  4. Handles phase transitions (advance, retry, fail)
  5. Sends team messages to share context between phase transitions
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from database.phases import (
    PHASE_ORDER, PHASE_LABELS, VALID_PHASE_TRANSITIONS,
    get_next_phase, validate_phase_transition, get_preferred_roles_for_phase,
    can_role_handle_phase, select_agent_for_phase, get_phase_from_ticket,
    get_initial_phase, create_pipeline_step, complete_pipeline_step,
    fail_pipeline_step, get_pipeline_steps, DEFAULT_ROLE_INSTRUCTIONS,
)

log = logging.getLogger("hivemind")


def advance_ticket_phase(ticket_id: str, current_phase: str, result: str = "") -> Optional[str]:
    """Advance a ticket to the next phase in the pipeline.
    Returns the next phase name, or None if the pipeline is complete.
    """
    from database import update_ticket_status, add_ticket_comment

    next_phase = get_next_phase(current_phase)
    if not next_phase:
        return None

    if not validate_phase_transition(current_phase, next_phase):
        log.warning(f"Invalid phase transition: {current_phase} → {next_phase} for ticket {ticket_id}")
        return None

    update_ticket_status(ticket_id, "running")
    log.info(f"Ticket {ticket_id} advanced from {current_phase} to {next_phase}", extra={
        "ticket_id": ticket_id, "event": "phase_advanced",
        "from_phase": current_phase, "to_phase": next_phase,
    })
    return next_phase


def fail_ticket_phase(ticket_id: str, current_phase: str, error: str = "") -> str:
    """Handle a failed phase. Returns the phase to retry (usually 'work')."""
    from database import update_ticket_status

    if current_phase == "work":
        log.warning(f"Ticket {ticket_id} failed in work phase: {error}", extra={
            "ticket_id": ticket_id, "event": "phase_failed", "phase": current_phase,
        })
        return "work"

    retry_phase = "work"
    log.info(f"Ticket {ticket_id} phase {current_phase} failed, reverting to {retry_phase}: {error}", extra={
        "ticket_id": ticket_id, "event": "phase_retry", "from_phase": current_phase, "to_phase": retry_phase,
    })
    return retry_phase


def get_role_instruction(role: str) -> str:
    """Get the default instruction text for a given role."""
    return DEFAULT_ROLE_INSTRUCTIONS.get(role, DEFAULT_ROLE_INSTRUCTIONS["general"])


def build_phase_context(ticket_data: Dict, phase: str, pipeline_steps: List[Dict] = None) -> str:
    """Build context string for an agent starting a new phase.
    Includes previous phase results and team messages.
    """
    lines = [
        f"## Pipeline Phase: {PHASE_LABELS.get(phase, phase)}",
        f"You are working on phase '{phase}' of this ticket.",
        "",
    ]

    if pipeline_steps:
        lines.append("### Previous Phases:")
        for step in pipeline_steps:
            if step.get("status") == "completed":
                lines.append(f"- **{step['phase'].title()}** (agent: {step.get('agent_id', 'unknown')}): ✅ Completed")
                if step.get("result"):
                    lines.append(f"  Result: {step['result'][:500]}")
            elif step.get("status") == "failed":
                lines.append(f"- **{step['phase'].title()}** (agent: {step.get('agent_id', 'unknown')}): ❌ Failed")
                if step.get("result"):
                    lines.append(f"  Error: {step['result'][:500]}")
        lines.append("")

    if phase == "test":
        lines.append("Your task is to write and run tests for the changes made in the Work phase.")
        lines.append("Focus on validating correctness, edge cases, and error handling.")
    elif phase == "review":
        lines.append("Your task is to review the code changes and create a merge request.")
        lines.append("Focus on correctness, performance, security, and maintainability.")
    elif phase == "ship":
        lines.append("Your task is to merge the MR and clean up.")
        lines.append("Ensure the MR is approved and the pipeline is green before merging.")

    return "\n".join(lines)


def send_team_message(group_id: str, sender_agent_id: str, message_type: str, content: str):
    """Send a message to the team channel for a ticket group."""
    from database import add_team_message
    add_team_message(group_id, sender_agent_id, content, message_type=message_type)


def get_team_context(group_id: str, limit: int = 10) -> str:
    """Get recent team messages as context for an agent."""
    from database import get_team_messages
    messages = get_team_messages(group_id, limit=limit)
    if not messages:
        return ""
    lines = ["### Recent Team Messages:"]
    for msg in messages:
        sender = msg.get("sender_agent_id", "unknown")
        msg_type = msg.get("message_type", "info")
        content = msg.get("content", "")
        lines.append(f"- [{msg_type}] {sender}: {content[:300]}")
    return "\n".join(lines)


def ensure_pipeline_group(ticket_id: str, ticket_data: Dict) -> str:
    """Ensure a ticket has a pipeline group for team messages. Returns group_id."""
    from database import get_ticket_group, create_ticket_group

    group_id = ticket_data.get("pipeline_group_id")
    if group_id:
        existing = get_ticket_group(group_id)
        if existing:
            return group_id

    group_id = f"pipeline-{ticket_id}"
    create_ticket_group(group_id, ticket_id, title=f"Pipeline for {ticket_data.get('title', ticket_id)}")
    return group_id