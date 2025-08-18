"""A script for determining the next version of a package."""

import dataclasses as dc
import enum
import fnmatch
import logging
import os
import pprint
import subprocess
from collections import OrderedDict

from wipac_dev_tools import from_environment_as_dataclass


# **************************************************************************************
# NOTE!
#
# THIS MUST BE COMPATIBLE WITH THE LOWEST SUPPORTED PYTHON VERSION (py 3.9 as of 2025)
# -> FANCINESS WILL HAVE TO WAIT
# **************************************************************************************


@dc.dataclass(frozen=True)
class EnvConfig:
    """For storing environment variables, typed."""

    IGNORE_PATHS: list[str] = dc.field(default_factory=list)
    FORCE_PATCH_IF_NO_COMMIT_TOKEN: bool = False


ENV = from_environment_as_dataclass(EnvConfig)

# version styles -- could be a StrEnum but that is py 3.11+
VERSION_STYLE_X_Y_Z = "X.Y.Z"  # ex: 1.12.3
VERSION_STYLE_X_Y = "X.Y"  # ex: 0.51


class InvalidVersionStyle(RuntimeError):
    """Raised when the version style is invalid."""

    def __init__(self, version_style: str):
        super().__init__(f"Invalid version style: {version_style}")


class BumpType(enum.Enum):
    MAJOR = enum.auto()
    MINOR = enum.auto()
    PATCH = enum.auto()
    NO_BUMP = enum.auto()


BUMP_TOKENS = OrderedDict(  # ordered by precedence
    {
        BumpType.MAJOR: ["[major]"],
        BumpType.MINOR: ["[minor]"],
        BumpType.PATCH: ["[patch]", "[fix]", "[bump]"],
        BumpType.NO_BUMP: ["[no-bump]", "[no_bump]", "[nobump]"],
    }
)


def _has_bump_token(bump: BumpType, string: str) -> bool:
    """Does the string have any of the bump type's tokens?"""
    return any(x in string for x in BUMP_TOKENS[bump])


def are_all_files_ignored(changed_files: list[str]) -> bool:
    """Do all the changed files match the patterns (aggregate)?"""
    if not changed_files:
        return True  # think: git commit --allow-empty -m "Trigger CI pipeline"

    for f in changed_files:
        logging.debug(f"Checking if this changed file is ignored: {f}")
        matched = False
        for pat in ENV.IGNORE_PATHS:
            if fnmatch.fnmatch(f, pat):
                logging.debug(f"-> COVERED BY IGNORE-PATTERN: {pat}")
                matched = True
                break
        if not matched:
            logging.info(f"Found a changed non-ignored file: {f}")
            return False
    return True


class DisqualifiedCommit(RuntimeError):
    """Raised when a commit is disqualified."""


@dc.dataclass
class BumpableCommit:
    """Useful things to know about a commit which qualifies as bumpable."""

    sha: str
    title: str  # original title (preserved for logs)
    changed_files: list[str]

    # derived
    title_lower: str = dc.field(init=False)
    bump_type: BumpType = dc.field(init=False)

    def __post_init__(self):
        # keep 'title' for logs, derive a lowercased view for token matching
        self.title_lower = self.title.lower()

        self.bump_type = self._figure_bump_type(self.title_lower, self.changed_files)

    @staticmethod
    def _figure_bump_type(title_lower: str, changed_files: list[str]) -> BumpType:

        # look for a commit bump token in the title
        for bump in BUMP_TOKENS.keys():
            if _has_bump_token(bump, title_lower):
                if bump == BumpType.NO_BUMP:
                    raise DisqualifiedCommit("explicitly has 'no-bump' commit title")
                else:
                    return bump

        # so, commit title did not have a token...

        # only changed ignored files?
        if are_all_files_ignored(changed_files):
            raise DisqualifiedCommit("only changed ignored files")
        # so, it changed non-ignored files...
        elif ENV.FORCE_PATCH_IF_NO_COMMIT_TOKEN:
            return BumpType.PATCH
        else:
            raise DisqualifiedCommit(
                "did not contain a bump token (force-patching is off)"
            )


def get_bumpable_commits(first_commit: str) -> list[BumpableCommit]:
    """Return a list of Commit objects for commits in FIRST..HEAD which warrant bumping."""

    # 1) SHAs in FIRST..HEAD (newest first to match your prior behavior)
    rev_list = subprocess.run(
        ["git", "rev-list", f"{first_commit}..HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    shas = [ln.strip() for ln in rev_list.stdout.splitlines() if ln.strip()]
    commits: list[BumpableCommit] = []

    for sha in shas:

        # 2a) title (subject line only)
        show_title = subprocess.run(
            ["git", "show", "-s", "--format=%s", sha],
            capture_output=True,
            text=True,
            check=True,
        )
        title = show_title.stdout.strip()

        # 2b) files changed in that commit
        diff_tree = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
            capture_output=True,
            text=True,
            check=True,
        )
        files = [ln.strip() for ln in diff_tree.stdout.splitlines() if ln.strip()]

        try:
            commits.append(BumpableCommit(sha=sha, title=title, changed_files=files))
        except DisqualifiedCommit as e:
            # Skip commits that intentionally have no effect on versioning
            logging.info(f"Commit is disqualified: {sha=} {title=} reason='{e}'")
            continue

    # log & return
    logging.info(
        f"Found {len(shas)} commits "
        f"({len(commits)} qualified, {len(shas)-len(commits)} disqualified)"
    )
    logging.info("<start> (qualified commits)")
    for c in commits:
        logging.info(pprint.pformat(dc.asdict(c), indent=4))
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
    """Figure the next version and return as a string.

    Bump math:
      - MAJOR: (M, N, P) -> (M+1, 0, 0)
      - MINOR: (M, N, P) -> (M, N+1, 0)
      - PATCH: (M, N, P) -> (M, N, P+1)   (but for X.Y style PATCH behaves like MINOR)
    """
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


def work(
    version_tag: str,
    first_commit: str,
    version_style: str,
) -> None:
    """Core behavior: detect bump and print the next version (or nothing)."""
    logging.info(f"{version_tag=}")
    logging.info(f"{first_commit=}")
    logging.info(f"{version_style=}")

    # Pull commits which warrant bumping
    commits = get_bumpable_commits(first_commit)
    if not commits:
        return logging.info("Commit log(s) don't signify a version bump.")

    # Decide bump
    max_bump = max(
        [c.bump_type for c in commits],
        key=lambda x: -1 * list(BUMP_TOKENS.keys()).index(x),  # -1 makes it a max
    )
    if max_bump == BumpType.NO_BUMP:
        raise RuntimeError("detected [no-bump] after commit filtering")

    # Increment bump
    next_version = increment_bump(version_tag, max_bump, version_style)
    print(next_version)


def main() -> None:
    """Parse environment variables, configure logging, and run work()."""
    logging.basicConfig(level=logging.DEBUG)

    work(
        version_tag=os.environ["LATEST_VERSION_TAG"].lower().lstrip("v"),
        first_commit=os.environ["FIRST_COMMIT"],
        version_style=os.environ.get("VERSION_STYLE", VERSION_STYLE_X_Y_Z).upper(),
    )


if __name__ == "__main__":
    main()
