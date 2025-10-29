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
# are_all_files_ignored() — gitignore semantics via pathspec
# -----------------------------------------------------------------------------


def test_000_workflows_ignore_except_one():
    """Ignore .github/workflows/** but not image-publish.yml (pathspec behavior)."""
    _set_env(
        ignore_paths=[
            ".github/workflows/**",
            "!.github/workflows/image-publish.yml",  # unignore only the file
        ],
        force_patch=False,
    )
    # image-publish.yml should be NOT ignored; cicd.yml should be ignored.
    assert not mod.are_all_files_ignored([".github/workflows/image-publish.yml"])
    assert mod.are_all_files_ignored([".github/workflows/cicd.yml"])


def test_010_unignore_child_requires_parent_directory_unignore():
    """Child negation works without parent re-include under pathspec; parent re-include also works."""
    # Under pathspec, this DOES unignore the child file.
    _set_env(
        ignore_paths=[
            "build/",
            "!build/keep.txt",
        ],
        force_patch=False,
    )
    assert not mod.are_all_files_ignored(["build/keep.txt"])

    # Also works (and mirrors git docs) if you re-include the parent explicitly.
    _set_env(
        ignore_paths=[
            "build/",
            "!build/",
            "!build/keep.txt",
        ],
        force_patch=False,
    )
    assert not mod.are_all_files_ignored(["build/keep.txt"])


def test_020_basename_patterns_apply_anywhere():
    """A pattern with no '/' matches basenames anywhere; negation likewise."""
    _set_env(
        ignore_paths=[
            "*.md",  # ignore all markdown
            "!README.md",  # unignore any README.md anywhere
        ],
        force_patch=False,
    )
    # README.md anywhere is unignored; other .md are ignored.
    assert not mod.are_all_files_ignored(["README.md"])
    assert not mod.are_all_files_ignored(["docs/README.md"])
    assert mod.are_all_files_ignored(["docs/guide.md"])
    assert mod.are_all_files_ignored(["notes.md"])


def test_030_single_star_does_not_cross_slash():
    """docs/*.md should not match files in subdirectories (no slash crossing)."""
    _set_env(
        ignore_paths=[
            "docs/*.md",
        ],
        force_patch=False,
    )
    assert mod.are_all_files_ignored(["docs/a.md"])
    assert not mod.are_all_files_ignored(["docs/sub/a.md"])  # not matched by docs/*.md
    assert not mod.are_all_files_ignored(["src/a.md"])


def test_040_double_star_crosses_slashes_recursively():
    """docs/**/*.md should match recursively under docs/."""
    _set_env(
        ignore_paths=[
            "docs/**/*.md",
        ],
        force_patch=False,
    )
    assert mod.are_all_files_ignored(["docs/a.md"])
    assert mod.are_all_files_ignored(["docs/sub/a.md"])
    assert mod.are_all_files_ignored(["docs/sub/deep/a.md"])
    assert not mod.are_all_files_ignored(["docs/a.txt"])


def test_050_trailing_slash_directory_pattern():
    """A trailing slash pattern ignores everything under that dir (file paths)."""
    _set_env(
        ignore_paths=[
            "vendor/",
        ],
        force_patch=False,
    )
    # Focus on file paths, which is what git diff-tree yields.
    assert mod.are_all_files_ignored(["vendor/lib/a.py"])
    # Sibling path containing 'vendor' later is not affected
    assert not mod.are_all_files_ignored(["src/vendor/lib.py"])


def test_060_order_last_rule_wins():
    """Later rules override earlier matches (last rule wins)."""
    _set_env(
        ignore_paths=[
            "*.log",
            "!debug.log",
            "debug.log",
            "!debug.log",  # final: unignore
        ],
        force_patch=False,
    )
    assert mod.are_all_files_ignored(["app.log"])
    assert not mod.are_all_files_ignored(["debug.log"])
    # basename rule applies to nested path's basename
    assert not mod.are_all_files_ignored(["logs/debug.log"])


def test_070_leading_dot_slash_normalization():
    """Leading './' is ignored when matching paths (pathspec normalization)."""
    _set_env(
        ignore_paths=[
            "./dist/",
            "!./dist/keep.whl",
        ],
        force_patch=False,
    )
    assert not mod.are_all_files_ignored(["./dist/keep.whl"])
    assert mod.are_all_files_ignored(["dist/drop.whl"])


def test_080_empty_changed_list_treated_as_all_ignored():
    """No changed files → treated as all ignored (allows empty commits to no-op)."""
    _set_env(ignore_paths=["*"], force_patch=False)
    assert mod.are_all_files_ignored([]) is True


def test_090_mixed_subtree_unignore_then_reignore_last_rule_wins():
    """Subtree unignore followed by a re-ignore should leave the final path ignored."""
    _set_env(
        ignore_paths=[
            "data/**",  # ignore all data
            "!data/images/**",  # unignore images subtree
            "!data/images/private/**",  # unignore private subtree
            "data/images/private/**",  # re-ignore private subtree (last wins)
        ],
        force_patch=False,
    )
    assert mod.are_all_files_ignored(["data/a.bin"])
    assert not mod.are_all_files_ignored(["data/images/a.png"])
    assert mod.are_all_files_ignored(["data/images/private/secret.png"])


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
                ("x [no-bump]", ["src/a.py"]),  # non bump
                ("y [nobump]", ["src/a.py"]),  # non bump
                ("<bot> z1", ["src/a.py"]),  # non bump
                ("z2 <bot>", ["src/a.py"]),  # non bump
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


def test_398_work_workflows_ignore_except_one_affects_bump(monkeypatch, capsys):
    """Ignoring all workflows except one file: touching that file still triggers a bump (force_patch=True)."""
    _set_env(
        ignore_paths=[
            ".github/workflows/**",
            "!.github/",
            "!.github/workflows/",
            "!.github/workflows/image-publish.yml",
        ],
        force_patch=True,  # allow bump when a non-ignored file changes without tokens
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                (
                    "ci: tweak image publish",
                    [".github/workflows/image-publish.yml"],
                ),  # NOT ignored
                ("ci: tweak other", [".github/workflows/cicd.yml"]),  # ignored
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
