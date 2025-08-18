"""Test compute_next_version.py"""

import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

# Ensure the module path includes the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import compute_next_version as mod  # noqa: E402


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _mock_git_repo(records):
    """
    Create a patchable subprocess.run that emulates a git repo for:
      - git rev-list <range>
      - git show -s --format=%s <sha>
      - git diff-tree --no-commit-id --name-only -r <sha>

    `records` is a list of tuples: (title, [files]) in newest-first order.
    We fabricate deterministic SHAs from Peanuts names for fun.
    """

    shas = []
    titles = {}
    files_map = {}

    for i, (title, files) in enumerate(records):
        base = str(i).encode("utf-8").hex()
        sha = base[:7]
        shas.append(sha)
        titles[sha] = title
        files_map[sha] = list(files or [])

    # rev-list returns newest-first by default here
    rev_list_out = "\n".join(shas) + ("\n" if shas else "")

    def _runner(cmd, capture_output=False, text=False, check=False):
        assert isinstance(cmd, list), f"Command must be a list, got {cmd!r}"

        if cmd[:2] == ["git", "rev-list"]:
            # ["git", "rev-list", "<range>"]
            return CompletedProcess(cmd, 0, stdout=rev_list_out, stderr="")

        if cmd[:4] == ["git", "show", "-s", "--format=%s"]:
            # ["git", "show", "-s", "--format=%s", sha]
            sha = cmd[4]
            out = titles.get(sha, "")
            if text:
                return CompletedProcess(cmd, 0, stdout=f"{out}\n", stderr="")
            else:
                return CompletedProcess(
                    cmd, 0, stdout=(out + "\n").encode(), stderr=b""
                )

        if cmd[:5] == ["git", "diff-tree", "--no-commit-id", "--name-only", "-r"]:
            # ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", <sha>]
            sha = cmd[5]
            paths = files_map.get(sha, [])
            out = "\n".join(paths) + ("\n" if paths else "")
            if text:
                return CompletedProcess(cmd, 0, stdout=out, stderr="")
            else:
                return CompletedProcess(cmd, 0, stdout=out.encode(), stderr=b"")

        assert False, f"Unexpected command: {cmd}"

    return _runner


def _set_env(ignore_paths: list[str], force_patch: bool):
    """Swap in a new EnvConfig for the module (since the real one is frozen)."""
    mod.ENV = mod.EnvConfig(
        IGNORE_PATHS=list(ignore_paths),
        FORCE_PATCH_IF_NO_COMMIT_TOKEN=bool(force_patch),
    )


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
# work() integration
# -----------------------------------------------------------------------------


def test_300_work_semver_patch_from_token(monkeypatch, capsys):
    """v1.2.3 with an explicit [patch] token -> 1.2.4 printed."""
    _set_env(ignore_paths=[], force_patch=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("fix: squashed a bug [patch]", ["src/a.py", "README.md"]),  # bump
            ]
        ),
    )

    mod.work(
        version_tag="1.2.3",
        first_commit="abc123",
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == "1.2.4"


def test_310_work_no_tokens_all_files_ignored_no_output(monkeypatch, capsys):
    """No tokens + all files ignored -> no print (no bump)."""
    _set_env(ignore_paths=["docs/**", "*.md"], force_patch=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("docs: update readme", ["docs/README.md"]),  # non bump
                ("chore: ci tweak", ["notes.md"]),  # non bump
            ]
        ),
    )

    mod.work(
        version_tag="2.3.4",
        first_commit="abc123",
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == ""


def test_320_work_force_patch_when_no_token_semver(monkeypatch, capsys):
    """No tokens + a non-ignored change + force_patch=True -> patch bump."""
    _set_env(ignore_paths=["*.md"], force_patch=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("refactor: cleanup modules", ["src/core.py", "README.md"]),  # bump
            ]
        ),
    )

    mod.work(
        version_tag="0.9.9",
        first_commit="abc123",
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == "0.9.10"


def test_330_work_patch_token_behaves_as_minor_in_xy(monkeypatch, capsys):
    """In X.Y mode, [patch] acts like MINOR; 1.2 -> 1.3."""
    _set_env(ignore_paths=[], force_patch=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("fix: small bug [patch]", ["src/a.py"]),  # bump
            ]
        ),
    )

    mod.work(
        version_tag="1.2",
        first_commit="abc123",
        version_style=mod.VERSION_STYLE_X_Y,
    )
    out = capsys.readouterr().out.strip()
    assert out == "1.3"


def test_340_work_all_commits_no_bump_explicit(monkeypatch, capsys):
    """All titles marked [no-bump] -> no output (they are disqualified inside Commit)."""
    _set_env(ignore_paths=[], force_patch=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("chore: x [no-bump]", ["src/a.py"]),  # non bump
                ("docs: y [nobump]", ["docs/README.md"]),  # non bump
            ]
        ),
    )

    mod.work(
        version_tag="3.4.5",
        first_commit="abc123",
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == ""


def test_350_work_bad_tag_shape_raises(monkeypatch):
    """Bad tag for selected style should raise in increment_bump path."""
    _set_env(ignore_paths=[], force_patch=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("fix: z [patch]", ["src/a.py"]),  # bump
            ]
        ),
    )

    with pytest.raises(ValueError):
        mod.work(
            version_tag="1.2",  # invalid for X.Y.Z
            first_commit="abc123",
            version_style=mod.VERSION_STYLE_X_Y_Z,
        )


def test_360_work_no_tokens_some_ignored_some_not_force_patch_false(
    monkeypatch, capsys
):
    """Tokenless changes with a non-ignored file but force_patch=False -> no bump."""
    _set_env(ignore_paths=["docs/**"], force_patch=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("chore: x", ["docs/README.md"]),  # non bump
                ("refactor: y", ["src/kite_eating_tree.py"]),  # no b/c force_patch=Fals
            ]
        ),
    )

    mod.work(
        version_tag="4.5.6",
        first_commit="abc123",
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == ""


def test_370_work_explicit_bump(monkeypatch, capsys):
    """Test."""
    _set_env(ignore_paths=["docs/**"], force_patch=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("chore: x [major]", ["docs/README.md"]),  # bump b/c explicit
                ("refactor: y [no-bump]", ["src/kite_eating_tree.py"]),
            ]
        ),
    )

    mod.work(
        version_tag="4.5.6",
        first_commit="abc123",
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == "5.0.0"


def test_380_work_(monkeypatch, capsys):
    """Test."""
    _set_env(ignore_paths=["docs/**"], force_patch=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("chore: x", ["docs/snoopy.md"]),  # non bump
                ("refactor: y [no-bump]", ["snoopy.py"]),  # non bump
            ]
        ),
    )

    mod.work(
        version_tag="4.5.6",
        first_commit="abc123",
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == ""


def test_390_work_(monkeypatch, capsys):
    """Test."""
    _set_env(ignore_paths=["docs/**"], force_patch=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("chore: x [minor]", []),  # bump
            ]
        ),
    )

    mod.work(
        version_tag="4.5.6",
        first_commit="abc123",
        version_style=mod.VERSION_STYLE_X_Y_Z,
    )
    out = capsys.readouterr().out.strip()
    assert out == "4.6.0"


# -----------------------------------------------------------------------------
# main() env handling integration
# -----------------------------------------------------------------------------


def test_400_main_reads_env_and_strips_v(monkeypatch, capsys):
    """main() should parse env, lower().lstrip('v'), and print bumped version."""
    _set_env(ignore_paths=[], force_patch=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("fix: z [patch]", ["src/x.py"]),
            ]
        ),
    )

    env = {
        "LATEST_VERSION_TAG": "V1.2.3",  # covered by lower().lstrip("v") -> "1.2.3"
        "FIRST_COMMIT": "abc123",
        "VERSION_STYLE": mod.VERSION_STYLE_X_Y_Z,
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    mod.main()
    out = capsys.readouterr().out.strip()
    assert out == "1.2.4"
