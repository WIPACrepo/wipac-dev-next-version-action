"""A script for determining the next version of a package."""

import dataclasses as dc
import enum
import fnmatch
import logging
import os
import subprocess
from typing import Optional

# **************************************************************************************
# NOTE!
#
# THIS MUST BE COMPATIBLE WITH THE LOWEST SUPPORTED PYTHON VERSION (py 3.9 as of 2025)
# -> FANCINESS WILL HAVE TO WAIT
# **************************************************************************************

# version styles -- could be a StrEnum but that is py 3.11+
VERSION_STYLE_X_Y_Z = "X.Y.Z"  # ex: 1.12.3
VERSION_STYLE_X_Y = "X.Y"  # ex: 0.51


class InvalidVersionStyle(RuntimeError):
    """Raised when the version style is invalid."""

    def __init__(self, version_style: str):
        super().__init__(f"Invalid version style: {version_style}")


@dc.dataclass
class Commit:
    """Useful things to know about a commit."""

    sha: str
    title_lower: str
    changed_files: list[str]

    def __post_init__(self):
        self.title_lower = self.title_lower.lower()


class BumpType(enum.Enum):
    MAJOR = enum.auto()
    MINOR = enum.auto()
    PATCH = enum.auto()
    NO_BUMP = enum.auto()


BUMP_TOKENS = {
    BumpType.MAJOR: ["[major]"],
    BumpType.MINOR: ["[minor]"],
    BumpType.PATCH: ["[patch]", "[fix]", "[bump]"],
    BumpType.NO_BUMP: ["[no-bump]", "[no_bump]", "[nobump]"],
}


def _has_bump_token(bump: BumpType, string: str) -> bool:
    """Does the string have any of the bump type's tokens?"""
    return any(x in string for x in BUMP_TOKENS[bump])


def are_all_files_ignored(changed_files: list[str], patterns: list[str]) -> bool:
    """Do all the changed files match the patterns (aggregate)?"""
    if not changed_files:
        return True  # think: git commit --allow-empty -m "Trigger CI pipeline"

    for f in changed_files:
        logging.debug(f"Checking if this changed file is ignored: {f}")
        matched = False
        for pat in patterns:
            if fnmatch.fnmatch(f, pat):
                logging.debug(f"-> COVERED BY IGNORE-PATTERN: {pat}")
                matched = True
                break
        if not matched:
            logging.info(f"Found a changed non-ignored file: {f}")
            return False
    return True


def _commit_has_non_ignored_changes(files: list[str], patterns: list[str]) -> bool:
    """Does this commit change any non-ignored file?"""
    if not files:
        return False  # empty changeset -> treat as ignored-only here
    for f in files:
        if not any(fnmatch.fnmatch(f, pat) for pat in patterns):
            return True
    return False


def get_commits_with_changes(first_commit: str) -> list[Commit]:
    """Return a list of (sha, title, files[]) for commits in FIRST..HEAD.

    We use a record separator between commits and a unit separator between sha/title.
    """
    # --pretty uses:
    #   %H = commit sha
    #   %s = subject (title)
    #   %x1f = unit separator (between sha and title)
    #   %x1e = record separator (between commits)
    result = subprocess.run(
        [
            "git",
            "log",
            f"{first_commit}..HEAD",
            "--pretty=format:%H%x1f%s%x1e",
            "--name-only",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    blob = result.stdout
    commits: list[Commit] = []
    for rec in blob.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        header, *rest = rec.split("\n", 1)
        sha, title = header.split("\x1f", 1)
        files = []
        if rest:
            files = [ln.strip() for ln in rest[0].splitlines() if ln.strip()]
        commits.append(Commit(sha, title.strip(), files))
    logging.info(f"Found {len(commits)} commits")
    logging.info("<start>")
    for _, title, _files in commits:
        logging.info(title)
    logging.info("<end>")
    return commits


def major_bump(major: int) -> tuple[int, int, int]:
    """Increment for a major bump."""
    major += 1
    minor = 0
    patch = 0
    return major, minor, patch


def minor_bump(major: int, minor: int) -> tuple[int, int, int]:
    """Increment for a minor bump."""
    minor += 1
    patch = 0
    return major, minor, patch


def patch_bump(major: int, minor: int, patch: int) -> tuple[int, int, int]:
    """Increment for a patch bump."""
    patch += 1
    return major, minor, patch


def increment_bump(version_tag: str, bump: BumpType, version_style: str) -> str:
    """Figure the next version and return as a string."""
    # get the starting version segments
    try:
        if version_style == VERSION_STYLE_X_Y_Z:
            major, minor, patch = map(int, version_tag.split("."))
        elif version_style == VERSION_STYLE_X_Y:
            major, minor = map(int, version_tag.split("."))
            patch = 0  # ignored for X.Y
        else:
            raise InvalidVersionStyle(version_style)
    except ValueError as e:
        raise ValueError(
            f"Could not parse version from {version_tag=} for {version_style=}"
        ) from e

    # MAJOR bump
    if bump == BumpType.MAJOR:
        major, minor, patch = major_bump(major)
    # MINOR bump
    elif bump == BumpType.MINOR:
        major, minor, patch = minor_bump(major, minor)
    # PATCH bump
    elif bump == BumpType.PATCH:
        # X.Y.Z -> normal
        if version_style == VERSION_STYLE_X_Y_Z:
            major, minor, patch = patch_bump(major, minor, patch)
        # X.Y -> a PATCH bump is equivalent to a MINOR bump
        elif version_style == VERSION_STYLE_X_Y:
            major, minor, patch = minor_bump(major, minor)
            # 'patch' value ^^^ will be ignored in the end
        else:
            raise InvalidVersionStyle(version_style)
    else:
        raise ValueError(f"Bump type not supported: {bump}")

    # stringify the next version
    if version_style == VERSION_STYLE_X_Y_Z:
        return f"{major}.{minor}.{patch}"
    elif version_style == VERSION_STYLE_X_Y:
        return f"{major}.{minor}"  # no patch
    else:
        raise InvalidVersionStyle(version_style)


def _decide_bump_from_commits(
    commits: list[Commit],
    ignore_path_patterns: list[str],
    force_patch: bool,
) -> Optional[BumpType]:
    """Decide bump type considering per-commit titles and per-commit files.

    Precedence:
      1) Any [major] anywhere -> MAJOR
      2) Else any [minor] anywhere -> MINOR
      3) Else any [patch]/[fix]/[bump] anywhere -> PATCH
      4) Else if any [no-bump] present AND every other commit without [no-bump]
         has only ignored-file changes -> NO_BUMP
      5) Else if any tokenless commit has non-ignored changes:
           -> PATCH if force_patch else None
      6) Else -> None
    """

    # Explicit bumps win, in order
    for bump in (BumpType.MAJOR, BumpType.MINOR, BumpType.PATCH):
        if any(_has_bump_token(bump, t) for t in [c.title_lower for c in commits]):
            return bump

    any_no_bump = False
    any_tokenless_with_effective_change = False

    for commit in commits:
        has_no_bump = _has_bump_token(BumpType.NO_BUMP, commit.title_lower)
        has_any_token = (
            _has_bump_token(BumpType.MAJOR, commit.title_lower)
            or _has_bump_token(BumpType.MINOR, commit.title_lower)
            or _has_bump_token(BumpType.PATCH, commit.title_lower)
            or has_no_bump
        )
        if has_no_bump:
            any_no_bump = True
            continue  # doesn't matter what files are in a no-bump commit
        if not has_any_token:
            if _commit_has_non_ignored_changes(
                commit.changed_files, ignore_path_patterns
            ):
                any_tokenless_with_effective_change = True

    if any_no_bump and not any_tokenless_with_effective_change:
        return BumpType.NO_BUMP

    if any_tokenless_with_effective_change:
        return BumpType.PATCH if force_patch else None

    return None


def work(
    version_tag: str,
    first_commit: str,
    changed_files: list[str],
    ignore_path_patterns: list[str],
    force_patch: bool,
    version_style: str,
) -> None:
    """Core behavior: detect bump and print the next version (or nothing)."""
    logging.info(f"{version_tag=}")
    logging.info(f"{first_commit=}")
    logging.info(f"{changed_files=}")
    logging.info(f"{ignore_path_patterns=}")
    logging.info(f"{force_patch=}")
    logging.info(f"{version_style=}")

    # Pull commits with their own changed files
    commits = get_commits_with_changes(first_commit)

    # Decide bump (per-commit aware)
    bump = _decide_bump_from_commits(commits, ignore_path_patterns, force_patch)

    if bump == BumpType.NO_BUMP:
        return logging.info("Commit set signifies no version bump.")
    elif not bump:
        # Fallback: if aggregate changes are all ignored -> no bump
        if are_all_files_ignored(changed_files, ignore_path_patterns):
            return logging.info("None of the changed files require a version bump.")
        # Otherwise: still no explicit token; if force_patch is False, do nothing
        return logging.info("Commit log(s) don't signify a version bump.")

    # increment bump
    next_version = increment_bump(version_tag, bump, version_style)
    print(next_version)


def main() -> None:
    """Parse environment variables, configure logging, and run work()."""
    logging.basicConfig(level=logging.DEBUG)

    work(
        version_tag=os.environ["LATEST_VERSION_TAG"].lower().lstrip("v"),
        first_commit=os.environ["FIRST_COMMIT"],
        changed_files=os.environ["CHANGED_FILES"].splitlines(),
        ignore_path_patterns=os.environ.get("IGNORE_PATHS", "").strip().splitlines(),
        force_patch=(
            os.environ.get("FORCE_PATCH_IF_NO_COMMIT_TOKEN", "false").lower() == "true"
        ),
        version_style=os.environ.get("VERSION_STYLE", VERSION_STYLE_X_Y_Z).upper(),
    )


if __name__ == "__main__":
    main()
