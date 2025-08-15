"""A script for determining the next version of a package."""

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


def parse_bump_from_commit_titles(commit_titles: list[str]) -> Optional[BumpType]:
    """Determine the bump type based on the commit log."""
    commit_titles = [t.lower() for t in commit_titles]  # so token matching is forgiving

    for bump in [BumpType.MAJOR, BumpType.MINOR, BumpType.PATCH]:  # order matters
        # just one appearance will suffice
        if any(_has_bump_token(bump, t) for t in commit_titles):
            return bump

    # for 'no-bump', every commit must indicate that it's 'no-bump'
    if all(_has_bump_token(BumpType.NO_BUMP, t) for t in commit_titles):
        return BumpType.NO_BUMP

    return None


def are_all_files_ignored(changed_files: list[str], patterns: list[str]) -> bool:
    """Do all the changed files match the patterns?"""
    if not changed_files:
        return True  # think: git commit --allow-empty -m "Trigger CI pipeline"

    for f in changed_files:
        logging.debug(f"Checking if this changed file is ignored: {f}")
        for pat in patterns:
            if fnmatch.fnmatch(f, pat):
                logging.debug(f"-> COVERED BY IGNORE-PATTERN: {pat}")
                break
            else:
                logging.debug(f"-> nope: {pat}")
        else:  # <- if no 'break'
            logging.info(f"Found a changed non-ignored file: {f}")
            return False
    return True


def get_commit_titles(first_commit: str) -> list[str]:
    """Get only commit titles (no change descriptions)."""
    result = subprocess.run(
        ["git", "log", f"{first_commit}..HEAD", "--pretty=%s"],
        capture_output=True,
        text=True,
        check=True,
    )
    titles = [msg.strip() for msg in result.stdout.split("\n") if msg.strip()]

    logging.info(f"Found {len(titles)} commits")
    logging.info("<start>")
    for t in titles:
        logging.info(t)
    logging.info("<end>")

    return titles


def major_bump(major: int) -> tuple[int, int, int]:
    major += 1
    minor = 0
    patch = 0
    return major, minor, patch


def minor_bump(major: int, minor: int) -> tuple[int, int, int]:
    minor += 1
    patch = 0
    return major, minor, patch


def patch_bump(major: int, minor: int, patch: int) -> tuple[int, int, int]:
    patch += 1
    return major, minor, patch


def main(
    tag: str,
    first_commit: str,
    changed_files: list[str],
    ignore_path_patterns: list[str],
    force_patch: bool,
    version_style: str,
) -> None:
    """Print the next version of a package; if there's no print, then's no new version."""
    logging.info(f"{tag=}")
    logging.info(f"{first_commit=}")
    logging.info(f"{changed_files=}")
    logging.info(f"{ignore_path_patterns=}")
    logging.info(f"{force_patch=}")
    logging.info(f"{version_style=}")

    # NOTE: we don't actually care if there are any changed files --
    #       think: git commit --allow-empty -m "Trigger CI pipeline [bump]"

    # detect bump
    bump = parse_bump_from_commit_titles(get_commit_titles(first_commit))
    if bump == BumpType.NO_BUMP:
        return logging.info("All commit log(s) explicitly signify no version bump.")
    elif not bump:
        # okay, user didn't include a bump string in their commit, what's the back-up plan?

        # all the changed files are ignored, so no bump
        if are_all_files_ignored(changed_files, ignore_path_patterns):
            return logging.info("None of the changed files require a version bump.")

        # default to a patch bump?
        if force_patch:
            bump = BumpType.PATCH
        else:
            return logging.info("Commit log(s) don't signify a version bump.")

    # increment
    major, minor, patch = map(int, tag.split("."))
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
        # X.Y -> a patch bump is equivalent to a minor bump
        elif version_style == VERSION_STYLE_X_Y:
            major, minor, patch = minor_bump(major, minor)
            # ^^^ 'patch' value will be discarded in the end
        else:
            raise ValueError(f"Invalid version style: {version_style}")
    else:
        raise ValueError(f"Bump type not supported: {bump}")

    # print the next version
    if version_style == VERSION_STYLE_X_Y_Z:
        print(f"{major}.{minor}.{patch}")
    elif version_style == VERSION_STYLE_X_Y:
        print(f"{major}.{minor}")
    else:
        raise ValueError(f"Invalid version style: {version_style}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    main(
        tag=os.environ["LATEST_SEMVER_TAG_NO_V"],
        first_commit=os.environ["FIRST_COMMIT"],
        changed_files=os.environ["CHANGED_FILES"].splitlines(),
        ignore_path_patterns=os.environ.get("IGNORE_PATHS", "").strip().splitlines(),
        force_patch=(
            os.environ.get("FORCE_PATCH_IF_NO_COMMIT_TOKEN", "false").lower() == "true"
        ),
        version_style=os.environ.get("VERSION_STYLE", VERSION_STYLE_X_Y_Z).upper(),
    )
