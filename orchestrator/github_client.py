"""GitHub integration — branches, commits, PRs."""

import base64
import logging
from typing import Optional

from github import Github, GithubException
from github.Repository import Repository

from orchestrator.settings import settings

logger = logging.getLogger(__name__)

_gh: Optional[Github] = None
_repo: Optional[Repository] = None


def get_repo() -> Repository:
    """Get the GitHub repo instance (cached)."""
    global _gh, _repo
    if _repo is None:
        _gh = Github(settings.github_token)
        _repo = _gh.get_repo(settings.github_repo)
    return _repo


def create_branch(branch_name: str) -> str:
    """Create a new branch from the default branch. Returns branch name."""
    repo = get_repo()
    default = repo.get_branch(settings.github_default_branch)
    try:
        repo.create_git_ref(
            ref=f"refs/heads/{branch_name}",
            sha=default.commit.sha,
        )
        logger.info(f"Created branch: {branch_name}")
    except GithubException as e:
        if e.status == 422:  # Branch already exists
            logger.info(f"Branch already exists: {branch_name}")
        else:
            raise
    return branch_name


def get_file_content(path: str, branch: Optional[str] = None) -> Optional[str]:
    """Read a file from the repo. Returns None if not found."""
    repo = get_repo()
    ref = branch or settings.github_default_branch
    try:
        content = repo.get_contents(path, ref=ref)
        if isinstance(content, list):
            return None  # It's a directory
        return content.decoded_content.decode("utf-8")
    except GithubException as e:
        if e.status == 404:
            return None
        raise


def get_directory_listing(path: str, branch: Optional[str] = None) -> list[str]:
    """List files in a directory. Returns file paths."""
    repo = get_repo()
    ref = branch or settings.github_default_branch
    try:
        contents = repo.get_contents(path, ref=ref)
        if not isinstance(contents, list):
            return [contents.path]
        return [c.path for c in contents]
    except GithubException:
        return []


def commit_file(
    path: str,
    content: str,
    message: str,
    branch: str,
) -> str:
    """Create or update a file on a branch. Returns commit SHA."""
    repo = get_repo()
    try:
        # Try to get existing file (for update)
        existing = repo.get_contents(path, ref=branch)
        result = repo.update_file(
            path=path,
            message=message,
            content=content,
            sha=existing.sha,
            branch=branch,
        )
    except GithubException as e:
        if e.status == 404:
            # New file
            result = repo.create_file(
                path=path,
                message=message,
                content=content,
                branch=branch,
            )
        else:
            raise

    sha = result["commit"].sha
    logger.info(f"Committed {path} to {branch} ({sha[:8]})")
    return sha


def delete_file(path: str, message: str, branch: str) -> str:
    """Delete a file on a branch. Returns commit SHA."""
    repo = get_repo()
    existing = repo.get_contents(path, ref=branch)
    result = repo.delete_file(
        path=path,
        message=message,
        sha=existing.sha,
        branch=branch,
    )
    return result["commit"].sha


def create_pull_request(
    branch: str,
    title: str,
    body: str,
    auto_merge: bool = False,
) -> dict:
    """Create a PR from branch to default branch. Returns PR info."""
    repo = get_repo()
    pr = repo.create_pull(
        title=title,
        body=body,
        head=branch,
        base=settings.github_default_branch,
    )
    logger.info(f"Created PR #{pr.number}: {title}")

    result = {
        "number": pr.number,
        "url": pr.html_url,
        "title": pr.title,
    }

    if auto_merge:
        try:
            pr.merge(merge_method="squash")
            result["merged"] = True
            logger.info(f"Auto-merged PR #{pr.number}")
        except GithubException as e:
            result["merged"] = False
            result["merge_error"] = str(e)
            logger.warning(f"Auto-merge failed for PR #{pr.number}: {e}")

    return result


def get_tree_paths(path: str = "", branch: Optional[str] = None, max_depth: int = 3) -> list[str]:
    """Recursively get all file paths under a directory."""
    repo = get_repo()
    ref = branch or settings.github_default_branch

    paths = []

    def _walk(current_path: str, depth: int):
        if depth > max_depth:
            return
        try:
            contents = repo.get_contents(current_path, ref=ref)
            if not isinstance(contents, list):
                paths.append(contents.path)
                return
            for item in contents:
                if item.type == "dir":
                    _walk(item.path, depth + 1)
                else:
                    paths.append(item.path)
        except GithubException:
            pass

    _walk(path, 0)
    return paths
