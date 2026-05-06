from .base import VCSProvider
from .gitlab import GitLabProvider
from .github import GitHubProvider


def get_vcs_provider() -> VCSProvider:
    import os
    vcs_type = os.getenv("VCS_PROVIDER", "gitlab").lower()
    if vcs_type == "github":
        return GitHubProvider()
    return GitLabProvider()