"""Test compute_next_version.py"""

import re
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
    We fabricate deterministic SHAs from indexes for stability.
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
# are_all_files_ignored() — gitignore-like semantics via pathspec
# -----------------------------------------------------------------------------


def test_000_workflows_ignore_except_one():
    """Ignore .github/workflows/** but not image-publish.yml (pathspec behavior)."""
    _set_env(
        ignore_paths=[
            ".github/workflows/**",
            "!.github/workflows/image-publish.yml",
        ],
        force_patch=False,
    )
    # image-publish.yml should be NOT ignored; cicd.yml should be ignored.
    assert not mod.are_all_files_ignored([".github/workflows/image-publish.yml"])
    assert mod.are_all_files_ignored([".github/workflows/cicd.yml"])


def test_010_child_negation_without_parent_reinclude_allowed_by_pathspec():
    """Under pathspec, a child negation can work without parent re-include (no trailing '/')."""
    _set_env(
        ignore_paths=[
            "build/**",  # ignore all under build
            "!build/keep.txt",  # unignore specific file
        ],
        force_patch=False,
    )
    assert not mod.are_all_files_ignored(["build/keep.txt"])  # allowed by pathspec

    # With an explicit (non-trailing-slash) allow list for a different file
    _set_env(
        ignore_paths=[
            "build/**",
            "!build/other.txt",
        ],
        force_patch=False,
    )
    assert mod.are_all_files_ignored(["build/keep.txt"])  # not unignored here


def test_020_basename_patterns_apply_anywhere():
    """A pattern with no '/' matches basenames anywhere; negation likewise."""
    _set_env(
        ignore_paths=[
            "*.md",  # ignore all markdown
            "!README.md",  # unignore any README.md anywhere
        ],
        force_patch=False,
    )
    assert not mod.are_all_files_ignored(["README.md"])  # at repo root
    assert not mod.are_all_files_ignored(["docs/README.md"])  # nested
    assert mod.are_all_files_ignored(["docs/guide.md"])  # other md still ignored
    assert mod.are_all_files_ignored(["notes.md"])  # other md still ignored


def test_030_single_star_does_not_cross_slash():
    """docs/*.md should not match files in subdirectories (no slash crossing)."""
    _set_env(
        ignore_paths=[
            "docs/*.md",
        ],
        force_patch=False,
    )
    assert mod.are_all_files_ignored(["docs/a.md"])  # matches
    assert not mod.are_all_files_ignored(["docs/sub/a.md"])  # does not cross '/'
    assert not mod.are_all_files_ignored(["src/a.md"])  # different dir


def test_040_double_star_crosses_slashes_recursively():
    """docs/**/*.md should match recursively under docs/."""
    _set_env(
        ignore_paths=[
            "docs/**/*.md",
        ],
        force_patch=False,
    )
    assert mod.are_all_files_ignored(["docs/a.md"])  # matches
    assert mod.are_all_files_ignored(["docs/sub/a.md"])  # matches
    assert mod.are_all_files_ignored(["docs/sub/deep/a.md"])  # matches
    assert not mod.are_all_files_ignored(["docs/a.txt"])  # different ext


def test_050_directory_tree_pattern_any_depth():
    """A directory tree pattern matches at any depth when not anchored with '/'."""
    _set_env(
        ignore_paths=[
            "**/vendor/**",  # any 'vendor' subtree at any depth
        ],
        force_patch=False,
    )
    assert mod.are_all_files_ignored(["vendor/lib/a.py"])  # top-level vendor
    assert mod.are_all_files_ignored(["src/vendor/lib.py"])  # nested vendor


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
    assert mod.are_all_files_ignored(["app.log"])  # ignored by *.log
    assert not mod.are_all_files_ignored(["debug.log"])  # last rule wins (unignored)
    assert not mod.are_all_files_ignored(["logs/debug.log"])  # basename match


def test_070_dist_tree_and_specific_file():
    """Use 'dist/**' and negate a specific file (no trailing-slash directory patterns)."""
    _set_env(
        ignore_paths=[
            "dist/**",
            "!dist/keep.whl",
        ],
        force_patch=False,
    )
    assert not mod.are_all_files_ignored(["dist/keep.whl"])  # unignored
    assert mod.are_all_files_ignored(["dist/drop.whl"])  # still ignored


def test_080_empty_changed_list_treated_as_all_ignored():
    """No changed files are treated as all ignored (empty commits result in no bump)."""
    _set_env(ignore_paths=["*"], force_patch=False)
    assert mod.are_all_files_ignored([]) is True


def test_090_mixed_subtree_unignore_then_reignore_last_rule_wins():
    """Subtree unignore followed by a re-ignore results in the path being ignored again."""
    _set_env(
        ignore_paths=[
            "data/**",  # ignore all data
            "!data/images/**",  # unignore images subtree
            "!data/images/private/**",  # unignore private subtree
            "data/images/private/**",  # re-ignore private subtree (last wins)
        ],
        force_patch=False,
    )
    assert mod.are_all_files_ignored(["data/a.bin"])  # ignored
    assert not mod.are_all_files_ignored(["data/images/a.png"])  # unignored
    assert mod.are_all_files_ignored(["data/images/private/secret.png"])  # re-ignored


# -----------------------------------------------------------------------------
# EnvConfig validation / leading-slash handling
# -----------------------------------------------------------------------------


def test_100_envconfig_allows_leading_slash_workflow_patterns():
    """Leading '/' patterns are accepted and behave as expected."""
    _set_env(
        ignore_paths=[
            ".github/*",
            "!/.github/workflows/*",
            "/.github/workflows/cicd.yml",
        ],
        force_patch=False,
    )
    assert mod.are_all_files_ignored([".github/workflows/cicd.yml"])
    assert not mod.are_all_files_ignored([".github/workflows/image-publish.yml"])
    assert not mod.are_all_files_ignored([".github/workflows/subdir/nested.yml"])
    assert mod.are_all_files_ignored([".github/foo.txt"])


@pytest.mark.parametrize(
    "pattern",
    [
        "./foo.txt",
        "../foo.txt",
        "!./foo.txt",
        "!../foo.txt",
    ],
)
def test_110_envconfig_rejects_relative_patterns(pattern: str):
    """Relative ignore patterns are rejected, including negated ones."""
    with pytest.raises(
        ValueError,
        match=re.escape("ignore-path cannot be relative"),
    ):
        mod.EnvConfig(
            IGNORE_PATHS=[pattern],
            FORCE_PATCH_IF_NO_COMMIT_TOKEN=False,
        )


@pytest.mark.parametrize(
    "pattern",
    [
        "foo/",
        "!foo/",
        ".github/",
        "!/.github/workflows/",
    ],
)
def test_120_envconfig_rejects_trailing_slash_patterns(pattern: str):
    """Trailing-slash directory patterns are rejected."""
    with pytest.raises(
        ValueError,
        match=re.escape("ignore-path cannot end with '/'"),
    ):
        mod.EnvConfig(
            IGNORE_PATHS=[pattern],
            FORCE_PATCH_IF_NO_COMMIT_TOKEN=False,
        )


def test_130_leading_slash_and_nonleading_slash_rules_match_the_same_here():
    """Leading-slash and non-leading-slash forms match the same repo-relative paths here."""
    env_a = mod.EnvConfig(
        IGNORE_PATHS=[
            ".github/*",
            "!.github/workflows/*",
            ".github/workflows/cicd.yml",
        ],
        FORCE_PATCH_IF_NO_COMMIT_TOKEN=False,
    )
    env_b = mod.EnvConfig(
        IGNORE_PATHS=[
            ".github/*",
            "!/.github/workflows/*",
            "/.github/workflows/cicd.yml",
        ],
        FORCE_PATCH_IF_NO_COMMIT_TOKEN=False,
    )

    paths = [
        ".github/workflows/cicd.yml",
        ".github/workflows/image-publish.yml",
        ".github/workflows/tag-and-release.yml",
        ".github/workflows/other.yml",
        ".github/workflows/subdir/nested.yml",
        ".github/foo.txt",
        "README.md",
    ]

    for path in paths:
        assert bool(env_a.GITIGNOREISH_SPEC.match_file(path)) == bool(
            env_b.GITIGNOREISH_SPEC.match_file(path)
        ), path


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
                ("chore: x", ["docs/README.md"]),  # non bump (ignored)
                (
                    "refactor: y",
                    ["src/kite_eating_tree.py"],
                ),  # non-ignored, but no token
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
    """Explicit [major] token wins even if files are under ignored patterns."""
    _set_env(ignore_paths=["docs/**"], force_patch=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("chore: x [major]", ["docs/README.md"]),  # bump b/c explicit token
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


def test_380_work_all_ignored_then_no_bump(monkeypatch, capsys):
    """All changes ignored and no tokens -> no bump output."""
    _set_env(ignore_paths=["docs/**"], force_patch=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("chore: x", ["docs/snoopy.md"]),  # ignored
                ("refactor: y [no-bump]", ["snoopy.py"]),  # explicit no-bump
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


def test_390_work_explicit_minor_no_files(monkeypatch, capsys):
    """Explicit [minor] bump even if commit changed no files (edge case)."""
    _set_env(ignore_paths=["docs/**"], force_patch=True)
    monkeypatch.setattr(
        subprocess,
        "run",
        _mock_git_repo(
            [
                ("chore: x [minor]", []),  # explicit minor bump
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
    """Touching the allowed workflow file triggers a bump when force_patch=True."""
    _set_env(
        ignore_paths=[
            ".github/workflows/**",
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
                    [".github/workflows/image-publish.yml"],  # NOT ignored
                ),
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
