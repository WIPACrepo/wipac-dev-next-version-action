"""A script for determining the next version of a package."""

import enum
import fnmatch
import logging
import os


class BumpType(enum.StrEnum):
    MAJOR = enum.auto()
    MINOR = enum.auto()
    PATCH = enum.auto()


def are_all_files_ignored(changed_files: list[str], ignore_patterns: list[str]) -> bool:
    """Do all the changed files match the ignore_patterns?"""
    for f in changed_files:
        logging.debug(f"Checking if this changed file is ignored: {f}")
        for pat in ignore_patterns:
            if fnmatch.fnmatch(f, pat):
                logging.debug(f"-> COVERED BY IGNORE-PATTERN: {pat}")
                break
            else:
                logging.debug(f"-> not covered by pattern: {pat}")
        else:  # <- if no 'break'
            logging.info(f"Found a changed non-ignored file: {f}")
            return False
    return True


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

    # is a version bump needed?
    if not changed_files:
        return logging.info("No changes detected")
    if are_all_files_ignored(changed_files, ignore_patterns):
        return logging.info("None of the changed files require a version bump.")

    # detect bump
    if "[major]" in commit_log:
        bump = BumpType.MAJOR
    elif "[minor]" in commit_log:
        bump = BumpType.MINOR
    elif ("[patch]" in commit_log) or ("[fix]" in commit_log) or force_patch:
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
