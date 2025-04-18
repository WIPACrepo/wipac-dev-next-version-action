"""A script for determining the next version of a package."""

import enum
import logging
import os
from fnmatch import fnmatch


class BumpType(enum.StrEnum):
    MAJOR = enum.auto()
    MINOR = enum.auto()
    PATCH = enum.auto()


def main(
    tag: str,
    changed_files: list[str],
    commit_log: str,
    ignore_patterns: list[str],
    force_patch: bool,
) -> None:
    """Print the next version of a package; if there's no print, then's no new version."""
    logging.info(f"{tag=}")
    logging.info(f"{changed_files=}")
    logging.info(f"{commit_log=}")
    logging.info(f"{ignore_patterns=}")
    logging.info(f"{force_patch=}")

    # is a release needed?
    if not changed_files:
        return logging.info("No changes detected")
    if all(any(fnmatch(f, pat) for pat in ignore_patterns) for f in changed_files):
        return logging.info("None of the changed files require a release.")

    # detect bump
    if "[major]" in commit_log:
        bump = BumpType.MAJOR
    elif "[minor]" in commit_log:
        bump = BumpType.MINOR
    elif ("[patch]" in commit_log) or ("[fix]" in commit_log):
        bump = BumpType.PATCH
    elif force_patch:
        bump = BumpType.PATCH
    else:
        return logging.info("Commit log doesn't signify a version bump.")

    # increment
    major, minor, patch = map(int, tag.split("."))
    match bump:
        case BumpType.MAJOR:
            major += 1
            minor = patch = 0
        case BumpType.MINOR:
            minor += 1
            patch = 0
        case BumpType.PATCH:
            patch += 1
        case _:
            raise ValueError(f"Bump type not supported: {bump}")

    # print the next version
    print(f"{major}.{minor}.{patch}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    main(
        tag=os.environ["LATEST_SEMVER_TAG_NO_V"],
        changed_files=os.environ["CHANGED_FILES"].splitlines(),
        commit_log=os.environ["COMMIT_LOG"].lower(),
        ignore_patterns=os.environ.get("IGNORE_PATHS", "").strip().splitlines(),
        force_patch=(
            os.environ.get("FORCE_PATCH_IF_NO_COMMIT_TOKEN", "false").lower() == "true"
        ),
    )
