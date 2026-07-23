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
Background task: Workspace cleanup – removes old workspace directories
and orphaned K8s ConfigMaps for completed/failed tickets older than 7 days.
"""

import asyncio
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from database import get_tickets
from config import AGENT_NAMESPACE

log = logging.getLogger("hivemind.cleanup")

WORKSPACE_BASE = Path("/app/workspace")
CLEANUP_AGE_DAYS = 7
CLEANUP_INTERVAL_SECONDS = 1800  # 30 minutes


async def workspace_cleanup_loop():
    """Periodically clean up old workspaces and orphaned ConfigMaps."""
    while True:
        try:
            await _cleanup_workspaces()
            await _cleanup_configmaps()
        except Exception as e:
            log.error(f"Workspace cleanup error: {e}", exc_info=True)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


async def _cleanup_workspaces():
    """Remove workspace directories for tickets completed/failed more than CLEANUP_AGE_DAYS ago."""
    if not WORKSPACE_BASE.exists():
        return

    cutoff = datetime.now() - timedelta(days=CLEANUP_AGE_DAYS)
    tickets = get_tickets()
    old_ticket_ids = set()
    for t in tickets:
        if t.get("status") in ("completed", "failed", "merged", "stopped"):
            completed = t.get("completed_at") or t.get("updated_at") or ""
            if completed and completed < cutoff.isoformat():
                old_ticket_ids.add(t["id"])

    cleaned = 0
    for ws_dir in WORKSPACE_BASE.iterdir():
        if not ws_dir.is_dir():
            continue
        dir_name = ws_dir.name
        ticket_id = dir_name.replace("workspace_", "") if dir_name.startswith("workspace_") else dir_name
        if ticket_id in old_ticket_ids:
            try:
                shutil.rmtree(ws_dir, ignore_errors=True)
                cleaned += 1
                log.info(f"Cleaned up workspace: {ws_dir}")
            except Exception as e:
                log.warning(f"Failed to clean up workspace {ws_dir}: {e}")

    if cleaned > 0:
        log.info(f"Workspace cleanup: removed {cleaned} old workspace directories")


async def _cleanup_configmaps():
    """Remove orphaned ConfigMaps (those not tied to active tickets)."""
    try:
        from k8s_client import get_k8s_api
        api = get_k8s_api()
        if api is None:
            return
    except ImportError:
        return

    tickets = get_tickets()
    active_ticket_ids = {t["id"] for t in tickets if t.get("status") in ("queued", "running")}

    try:
        cms = api.list_namespaced_config_map(
            namespace=AGENT_NAMESPACE,
            label_selector="app=hivemind-agent",
        )
    except Exception as e:
        log.debug(f"Could not list ConfigMaps: {e}")
        return

    cleaned = 0
    for cm in cms.items:
        cm_name = cm.metadata.name
        for tid in active_ticket_ids:
            if tid in cm_name:
                break
        else:
            creation = cm.metadata.creation_timestamp
            if creation and (datetime.now(creation.tzinfo) - creation).days > CLEANUP_AGE_DAYS:
                try:
                    api.delete_namespaced_config_map(name=cm_name, namespace=AGENT_NAMESPACE)
                    cleaned += 1
                    log.info(f"Cleaned up orphaned ConfigMap: {cm_name}")
                except Exception as e:
                    log.debug(f"Failed to delete ConfigMap {cm_name}: {e}")

    if cleaned > 0:
        log.info(f"ConfigMap cleanup: removed {cleaned} orphaned ConfigMaps")


def set_shutdown(shutdown: bool):
    pass  # Not needed for cleanup task