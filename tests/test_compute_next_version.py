"""Test compute_next_version.py"""

import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import compute_next_version as mod  # noqa: E402


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _mock_git_log_run(titles: list[str]):
    """Return a callable to patch subprocess.run for `git log` only."""
    stdout = ("\n".join(titles)).rstrip() + ("\n" if titles else "")

    def _runner(cmd, capture_output=False, text=False, check=False):
        assert isinstance(cmd, list)
        assert cmd[:2] == ["git", "log"], f"Unexpected command: {cmd}"
        return CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return _runner


# -----------------------------------------------------------------------------
# parse_bump_from_commit_titles
# -----------------------------------------------------------------------------


def test_000_parse_bump_detects_major_minor_patch():
    """Tokens [major], [minor], [patch]/[fix]/[bump] are detected with precedence."""
    assert (
        mod.parse_bump_from_commit_titles(
            [
                "feat: add X [minor]",
                "fix: small bug [patch]",
                "chore: stuff",
                "BREAKING: api [major]",
            ]
        )
        == mod.BumpType.MAJOR
    )

    assert (
        mod.parse_bump_from_commit_titles(
            [
                "feat: add X [minor]",
                "fix: typo [patch]",
            ]
        )
        == mod.BumpType.MINOR
    )

    assert (
        mod.parse_bump_from_commit_titles(
            [
                "fix: bug [fix]",
            ]
        )
        == mod.BumpType.PATCH
    )


def test_010_parse_bump_no_bump_requires_all_no_bump():
    """Only if every title includes a no-bump token do we return NO_BUMP."""
    assert (
        mod.parse_bump_from_commit_titles(
            [
                "chore: foo [no-bump]",
                "docs: bar [no_bump]",
            ]
        )
        == mod.BumpType.NO_BUMP
    )

    assert (
        mod.parse_bump_from_commit_titles(
            [
                "chore: foo [no-bump]",
                "feat: bar",
            ]
        )
        is None
    )


def test_020_parse_bump_none_when_no_tokens():
    """No tokens yields None."""
    assert (
        mod.parse_bump_from_commit_titles(
            [
                "chore: foo",
                "docs: bar",
            ]
        )
        is None
    )


# -----------------------------------------------------------------------------
# are_all_files_ignored
# -----------------------------------------------------------------------------


def test_100_ignored_true_with_no_changed_files():
    """Empty change list is treated as all-ignored (allow-empty commits)."""
    assert mod.are_all_files_ignored([], ["**/*.md"]) is True


def test_110_ignored_false_if_any_file_not_matching_patterns():
    """If any file doesn't match ignore patterns, function returns False."""
    changed = ["docs/README.md", "src/app.py"]
    patterns = ["docs/**", "*.txt"]  # src/app.py not matched
    assert mod.are_all_files_ignored(changed, patterns) is False


def test_120_ignored_true_if_all_files_match_some_pattern():
    """All files matching at least one pattern -> True."""
    changed = ["docs/README.md", "docs/usage.txt"]
    patterns = ["docs/**", "*.txt"]
    assert mod.are_all_files_ignored(changed, patterns) is True


# -----------------------------------------------------------------------------
# increment_bump (pure bump math)
# -----------------------------------------------------------------------------


def test_200_increment_semver_bumps():
    """MAJOR/MINOR/PATCH bump behavior for X.Y.Z."""
    assert (
        mod.increment_bump("1.2.3", mod.BumpType.MAJOR, mod.VERSION_STYLE_X_Y_Z)
        == "2.0.0"
    )
    assert (
        mod.increment_bump("1.2.3", mod.BumpType.MINOR, mod.VERSION_STYLE_X_Y_Z)
        == "1.3.0"
    )
    assert (
        mod.increment_bump("1.2.3", mod.BumpType.PATCH, mod.VERSION_STYLE_X_Y_Z)
        == "1.2.4"
    )


def test_210_increment_majmin_patch_collapses_to_minor():
    """In X.Y projects, PATCH is treated as MINOR."""
    assert mod.increment_bump("1.2", mod.BumpType.MAJOR, mod.VERSION_STYLE_X_Y) == "2.0"
    assert mod.increment_bump("1.2", mod.BumpType.MINOR, mod.VERSION_STYLE_X_Y) == "1.3"
    assert mod.increment_bump("1.2", mod.BumpType.PATCH, mod.VERSION_STYLE_X_Y) == "1.3"


def test_220_increment_invalid_style_raises():
    """Unknown version style raises."""
    with pytest.raises(mod.InvalidVersionStyle):
        mod.increment_bump("1.2.3", mod.BumpType.PATCH, "X")


def test_230_increment_bad_tag_shape_raises():
    """Bad tag formatting for the given style raises ValueError."""
    with pytest.raises(ValueError):
        mod.increment_bump("1.2", mod.BumpType.PATCH, mod.VERSION_STYLE_X_Y_Z)
    with pytest.raises(ValueError):
        mod.increment_bump("1.2.3", mod.BumpType.PATCH, mod.VERSION_STYLE_X_Y)


# -----------------------------------------------------------------------------
# work() integration-ish (git log stub + direct args)
# -----------------------------------------------------------------------------


def test_300_work_semver_patch_from_token(monkeypatch, capsys):
    """End-to-end via work(): v1.2.3 with [patch] token -> 1.2.4 printed."""
    monkeypatch.setattr(
        subprocess, "run", _mock_git_log_run(["fix: squashed a bug [patch]"])
    )

    mod.work(
        version_tag="1.2.3",
        first_commit="charliebrown",
        changed_files=["src/a.py", "README.md"],
        ignore_path_patterns=["docs/**", "*.md"],
        force_patch=False,
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == "1.2.4"


def test_310_work_no_tokens_all_files_ignored_no_output(monkeypatch, capsys):
    """No tokens + all files ignored -> no print (no bump)."""
    monkeypatch.setattr(
        subprocess, "run", _mock_git_log_run(["docs: update readme", "chore: ci tweak"])
    )

    mod.work(
        version_tag="2.3.4",
        first_commit="snoopy",
        changed_files=["docs/README.md", "notes.txt"],
        ignore_path_patterns=["docs/**", "*.txt"],
        force_patch=False,
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == ""


def test_320_work_force_patch_when_no_token_semver(monkeypatch, capsys):
    """No tokens + not all ignored + force_patch=True -> patch bump."""
    monkeypatch.setattr(
        subprocess, "run", _mock_git_log_run(["refactor: cleanup modules"])
    )

    mod.work(
        version_tag="0.9.9",
        first_commit="linus",
        changed_files=["src/core.py", "README.md"],  # src/core.py not ignored
        ignore_path_patterns=["*.md"],
        force_patch=True,
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == "0.9.10"


def test_330_work_majmin_patch_token_behaves_as_minor(monkeypatch, capsys):
    """In X.Y mode, [patch] acts like MINOR; 1.2 -> 1.3."""
    monkeypatch.setattr(
        subprocess, "run", _mock_git_log_run(["fix: small bug [patch]"])
    )

    mod.work(
        version_tag="1.2",
        first_commit="peppermintpatty",
        changed_files=["src/a.py"],
        ignore_path_patterns=[],
        force_patch=False,
        version_style=mod.VERSION_STYLE_X_Y,
    )
    out = capsys.readouterr().out.strip()
    assert out == "1.3"


def test_340_work_no_bump_token_explicit(monkeypatch, capsys):
    """All titles marked [no-bump] -> no output."""
    monkeypatch.setattr(
        subprocess, "run", _mock_git_log_run(["chore: x [no-bump]", "docs: y [nobump]"])
    )

    mod.work(
        version_tag="3.4.5",
        first_commit="woodstock",
        changed_files=["src/a.py"],
        ignore_path_patterns=[],
        force_patch=False,
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == ""


def test_350_work_bad_tag_shape_raises(monkeypatch):
    """Bad tag for selected style should raise in increment_bump path."""
    monkeypatch.setattr(subprocess, "run", _mock_git_log_run(["fix: z [patch]"]))
    with pytest.raises(ValueError):
        mod.work(
            version_tag="1.2",  # invalid for X.Y.Z
            first_commit="schroeder",
            changed_files=["src/a.py"],
            ignore_path_patterns=[],
            force_patch=False,
            version_style=mod.VERSION_STYLE_X_Y_Z,
        )


# -----------------------------------------------------------------------------
# main() env handling integration
# -----------------------------------------------------------------------------


def test_400_main_reads_env_and_strips_v(monkeypatch, capsys):
    """main() should parse env, lower().lstrip('v'), and print bumped version."""
    monkeypatch.setattr(subprocess, "run", _mock_git_log_run(["fix: z [patch]"]))

    env = {
        "LATEST_VERSION_TAG": "V1.2.3",  # covered by lower().lstrip("v") -> "1.2.3"
        "FIRST_COMMIT": "lucy",
        "CHANGED_FILES": "src/x.py\n",
        "IGNORE_PATHS": "",
        "FORCE_PATCH_IF_NO_COMMIT_TOKEN": "false",
        "VERSION_STYLE": mod.VERSION_STYLE_X_Y_Z,
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    mod.main()
    out = capsys.readouterr().out.strip()
    assert out == "1.2.4"
