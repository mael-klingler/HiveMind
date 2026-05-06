"""
Git credential configuration.
"""

import logging
import os
import subprocess
from pathlib import Path

from database import get_all_repos, get_setting as _gs

log = logging.getLogger("hivemind.main")


def configure_git_credentials():
    token = os.getenv("GITLAB_TOKEN") or os.getenv("GIT_TOKEN") or ""
    git_user = _gs("git_user") or os.getenv("GIT_USER", "gitlab-ci-token")
    hosts = set()
    default_host = os.getenv("GITLAB_HOST") or ""
    if default_host:
        hosts.add(default_host)
    try:
        repos = get_all_repos()
        for r in repos:
            url = r.get("url", "") if isinstance(r, dict) else getattr(r, "url", "")
            if url and "://" in url:
                host = url.split("://")[1].split("/")[0].split(":")[0]
                hosts.add(host)
    except Exception:
        pass
    git_dir = Path.home() / ".git-credentials"
    if token and hosts:
        lines = [f"https://{git_user}:{token}@{h}\n" for h in sorted(hosts)]
        git_dir.write_text("".join(lines), encoding="utf-8")
        subprocess.run(["git", "config", "--global", "credential.helper", "store"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "hivemind-agents@example.com"], check=False)
        subprocess.run(["git", "config", "--global", "user.name", "HiveMind"], check=False)
        log.info(f"Git credentials set for {', '.join(sorted(hosts))}")
    else:
        log.warning("GITLAB_TOKEN not set – possible clone errors")


__all__ = ["configure_git_credentials"]