"""
API routes: Agent Memory Blocks
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from database import (
    delete_agent_memory_block,
    get_agent_memory_blocks,
    seed_default_memory_blocks,
    set_agent_memory_block,
)
from logging_setup import log

router = APIRouter()


@router.get("/api/agent-memory/{agent_id}")
def api_get_agent_memory(agent_id: str, repo_name: Optional[str] = None):
    return get_agent_memory_blocks(agent_id, repo_name)


@router.post("/api/agent-memory/{agent_id}")
async def api_set_agent_memory(agent_id: str, req: Request):
    data = await req.json()
    label = data.get("label", "")
    content = data.get("content", "")
    repo_name = data.get("repo_name", "_global")
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    row_id = set_agent_memory_block(
        agent_id, repo_name, label, content,
        description=data.get("description", ""),
        block_limit=data.get("block_limit", 5000),
        read_only=data.get("read_only", False),
    )
    return {"ok": True, "id": row_id}


@router.delete("/api/agent-memory/{agent_id}/{block_id}")
def api_delete_agent_memory(agent_id: str, block_id: int):
    delete_agent_memory_block(block_id)
    return {"ok": True}


@router.post("/api/agent-memory/{agent_id}/seed-defaults")
def api_seed_agent_memory(agent_id: str):
    seed_default_memory_blocks(agent_id)
    return {"ok": True, "agent_id": agent_id}


@router.post("/api/agent-memory/{agent_id}/sync")
async def api_agent_memory_sync(agent_id: str, req: Request):
    data = await req.json()
    blocks = data.get("blocks", [])
    if not blocks:
        return {"ok": True, "synced": 0}
    synced = 0
    for block in blocks:
        label = block.get("label", "")
        content = block.get("content", "")
        if not label or not content:
            continue
        repo_name = block.get("repo_name", "_global")
        description = block.get("description", "")
        block_limit = block.get("block_limit", 5000)
        read_only = block.get("read_only", False)
        set_agent_memory_block(
            agent_id, repo_name, label, content,
            description=description, block_limit=block_limit, read_only=read_only
        )
        synced += 1
    log.info(f"Agent {agent_id}: {synced} memory blocks synced back")
    return {"ok": True, "synced": synced}


@router.post("/api/agent-memory/{agent_id}/sync-filesystem")
async def api_agent_memory_sync_filesystem(agent_id: str, req: Request):
    data = await req.json()
    memory_dir = data.get("memory_dir", "/home/hivemind/.config/opencode/memory")
    repo_name = data.get("repo_name", "_global")
    synced = 0
    import glob as _glob
    for md_file in _glob.glob(f"{memory_dir}/*.md"):
        try:
            content = Path(md_file).read_text(encoding="utf-8")
            lines = content.split("\n")
            label = Path(md_file).stem
            description = ""
            block_limit = 5000
            read_only = False
            if lines[0].strip() == "---":
                end_front = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), -1)
                if end_front > 0:
                    for fl in lines[1:end_front]:
                        if ":" in fl:
                            fk, fv = fl.split(":", 1)
                            fk = fk.strip().lower()
                            fv = fv.strip()
                            if fk == "label":
                                label = fv
                            elif fk == "description":
                                description = fv
                            elif fk == "limit":
                                try:
                                    block_limit = int(fv)
                                except ValueError:
                                    pass
                            elif fk == "read_only":
                                read_only = fv.lower() in ("true", "yes", "1")
                    content = "\n".join(lines[end_front + 1:])
            set_agent_memory_block(
                agent_id, repo_name, label, content,
                description=description, block_limit=block_limit, read_only=read_only
            )
            synced += 1
        except Exception as e:
            log.warning(f"Memory sync failed for {md_file}: {e}")
    log.info(f"Agent {agent_id}: {synced} memory blocks synced from filesystem")
    return {"ok": True, "synced": synced}