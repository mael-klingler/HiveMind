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
SQLite/PostgreSQL adapter – delegates to the existing database/ submodule 
functions based on DATABASE_URL env var.

When DATABASE_URL starts with 'postgresql://', uses database.postgres_backend.
Otherwise, uses the modular SQLite subpackage (database.sqlite_backend + 
database.tickets, etc.).

This provides backward compatibility while the Supabase adapter is available
via SUPABASE_URL + SUPABASE_SERVICE_KEY.
"""

import os

USE_POSTGRES = os.getenv("DATABASE_URL", "").startswith("postgresql")

if USE_POSTGRES:
    from database.postgres_backend import *  # noqa: F401,F403
    from database.postgres_backend import get_db
else:
    from database.sqlite_backend import get_db, _add_column_if_not_exists
    from database.init_db import init_db
    from database.tickets import (
        create_ticket, get_tickets, get_ticket, update_ticket_status,
        stop_ticket, get_tickets_with_queue, update_ticket_review,
        set_ticket_mr_url, set_ticket_workspace, set_ticket_ai_planning,
        update_ticket_description, requeue_ticket, reopen_ticket,
        get_failed_tickets, get_open_mr_tickets, update_ticket_mr_tracking,
    )
    from database.metrics import (
        record_metric_event, get_metric_events, get_metrics_summary,
        update_ticket_phase_timestamp, update_ticket_llm_usage,
        increment_review_cycle_count, set_ticket_first_pipeline_status,
        set_ticket_completed_at, set_ticket_primary_repo,
    )
    from database.agents import (
        get_agent, get_or_create_agent, set_agent_status,
        get_all_agents, get_idle_agents, get_max_agents, set_max_agents,
        ensure_agent_pool, create_agent, update_agent_profile,
        set_agent_role, delete_agent, set_agent_skills, set_agent_instruction_assignments,
        get_agent_with_profile, get_all_agents_with_profiles,
        get_agent_mcp_servers, get_agent_assigned_instructions,
    )
    from database.queue import (
        get_next_queue_item, assign_next_queue_item, assign_queue_item,
        complete_queue_item, fail_queue_item, get_queue, init_queue_extensions,
    )
    from database.steps import add_step, get_steps, get_all_steps
    from database.settings import (
        ensure_config_defaults, get_all_settings, get_setting,
        set_setting, import_settings_from_env,
    )
    from database.mcp_servers import (
        get_mcp_servers, get_enabled_mcp_servers, add_mcp_server,
        update_mcp_server, delete_mcp_server,
    )
    from database.instructions import (
        get_agent_instructions, get_enabled_agent_instructions,
        add_agent_instruction, update_agent_instruction, delete_agent_instruction,
    )
    from database.plugins import (
        get_opencode_plugins, get_enabled_opencode_plugins,
        get_enabled_plugin_names, add_opencode_plugin,
        update_opencode_plugin, delete_opencode_plugin,
    )
    from database.memory import (
        get_agent_memory_blocks, set_agent_memory_block,
        get_agent_memory_as_markdown, delete_agent_memory_block,
        seed_default_memory_blocks,
    )
    from database.repos import (
        get_all_repos, get_repo, add_repo, delete_repo, update_repo,
        import_repos_from_config, set_repo_active,
        get_agent_repo_affinities, set_agent_repo_affinities,
        get_all_repo_names, find_best_agent_for_repo, score_agent_for_repo,
    )
    from database.comments import add_ticket_comment, get_ticket_comments
    from database.groups import (
        create_ticket_group, get_ticket_group,
        add_team_message, get_team_messages,
    )

    try:
        from database.metrics import set_ticket_line_stats
    except ImportError:
        def set_ticket_line_stats(ticket_id, lines_added=0, lines_removed=0, files_changed=0):
            conn = get_db()
            c = conn.cursor()
            from datetime import datetime
            now = datetime.now().isoformat()
            c.execute("UPDATE tickets SET lines_added = ?, lines_removed = ?, files_changed = ?, updated_at = ? WHERE id = ?",
                      (lines_added, lines_removed, files_changed, now, ticket_id))
            conn.commit()
            conn.close()

    # Auto-init on import (for SQLite backend)
    try:
        init_db()
        ensure_config_defaults()
        import_settings_from_env()
    except Exception:
        pass