# tests/test_h_root_dir.py
# find_root_dir() must pick the right root directory in seven worktree, submodule and
# stray-.gitmodules layouts, and fall back to the highest .gitmodules outside git.
import os
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from git_fleximod import cli

GITMODULES_TEMPLATE = """[submodule "{name}"]
    path = {path}
    url = {url}
"""

LAYOUT_IDS = [
    # The root is the cwd itself.
    "worktree-beside",  # linked worktree next to the main checkout
    "worktree-inside",  # linked worktree created inside the main checkout
    "stray-parent-gitmodules",  # clone below a non-repo directory that has its own .gitmodules
    "copied-in-unlisted",  # clone in a repo's gitignored libs/ that its .gitmodules does not list
    # The root is an ancestor: the parent is the superproject.
    "in-submodule",  # cwd in a submodule of the main checkout
    "in-submodule-of-worktree",  # cwd in a submodule of a linked worktree
    # The root is an ancestor: the parent lists the cwd as a submodule.
    "listed-unindexed",  # clone in libs/ that the parent's .gitmodules lists but git never indexed
]


class Layout(NamedTuple):
    """The directory find_root_dir() runs from and the root it must return."""

    cwd: Path
    expected_root: Path


def _git(*args, cwd):
    """Run git hermetically (no user config), allowing local-path submodule clones."""
    env = dict(os.environ)
    env.update(
        # Ignore the developer's ~/.gitconfig and /etc/gitconfig (signing, hooks,
        # init.defaultBranch, ...) so the layouts are identical on every machine.
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_PARAMETERS="'protocol.file.allow=always'",
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.com",
    )
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


# Note only this module needs hermetic git setup: the other tests drive GitInterface
# against network clones.
def _init_repo(path):
    path.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=path)


def _commit_all(path, message="commit"):
    _git("add", "-A", cwd=path)
    _git("commit", "-q", "-m", message, cwd=path)


@pytest.fixture(scope="module", name="layouts")
def fixture_layouts(tmp_path_factory):
    """Build all seven layouts; return {id: Layout(cwd, expected_root)}."""
    layouts = {}
    base = Path(os.path.realpath(tmp_path_factory.mktemp("rootdir")))

    # A small library repo, used as the submodule source.
    lib = base / "lib"
    _init_repo(lib)
    (lib / "README").write_text("lib\n")
    _commit_all(lib)

    # The main checkout, with one submodule "sub" and its checked-out directory.
    main = base / "main"
    _init_repo(main)
    (main / "README").write_text("main\n")
    _commit_all(main)
    _git("submodule", "add", "-q", str(lib), "sub", cwd=main)
    _commit_all(main, "add sub")
    layouts["in-submodule"] = Layout(main / "sub", main)

    # 1. worktree beside the checkout, 4. its submodule
    wt_beside = base / "wt_beside"
    _git("worktree", "add", "-q", str(wt_beside), cwd=main)
    _git("submodule", "update", "--init", cwd=wt_beside)
    layouts["worktree-beside"] = Layout(wt_beside, wt_beside)
    layouts["in-submodule-of-worktree"] = Layout(wt_beside / "sub", wt_beside)

    # 2. worktree inside the checkout
    wt_inside = main / "wt_nested"
    _git("worktree", "add", "-q", str(wt_inside), cwd=main)
    layouts["worktree-inside"] = Layout(wt_inside, wt_inside)

    # 5. stray .gitmodules in a parent that is not a git repo, clone beneath
    stray = base / "stray"
    stray.mkdir()
    (stray / ".gitmodules").write_text(
        GITMODULES_TEMPLATE.format(name="elsewhere", path="elsewhere", url=lib)
    )
    stray_clone = stray / "clone"
    _git("clone", "-q", str(main), str(stray_clone), cwd=base)
    layouts["stray-parent-gitmodules"] = Layout(stray_clone, stray_clone)

    # 6./7. A parent repo that gitignores its libs/ dir, like a checkout whose external
    # components are cloned in and never added to its index. libs/c is a plain clone
    # of main (any repo would do; only its location matters). The two parents differ
    # in whether their .gitmodules lists libs/c: "listed-unindexed" (6) does, so the root is the
    # parent; "copied-in-unlisted" (7) lists libs/other, so libs/c is an unrelated copy.
    for tag, listed_path in (("listed", "libs/c"), ("unlisted", "libs/other")):
        parent = base / f"parent_{tag}"
        _init_repo(parent)
        (parent / ".gitignore").write_text("/libs\n")
        (parent / ".gitmodules").write_text(
            GITMODULES_TEMPLATE.format(name="c", path=listed_path, url=lib)
        )
        _commit_all(parent)
        (parent / "libs").mkdir()
        clone = parent / "libs" / "c"
        _git("clone", "-q", str(main), str(clone), cwd=base)
        if tag == "listed":
            layouts["listed-unindexed"] = Layout(clone, parent)
        else:
            layouts["copied-in-unlisted"] = Layout(clone, clone)

    return layouts


@pytest.mark.parametrize("layout_id", LAYOUT_IDS)
def test_find_root_dir(layouts, layout_id, monkeypatch):
    layout = layouts[layout_id]
    # find_root_dir() reads the process cwd; chdir is undone when the test ends.
    monkeypatch.chdir(layout.cwd)
    assert cli.find_root_dir() == str(layout.expected_root)


def test_find_root_dir_outside_git(tmp_path, monkeypatch):
    # Outside a git repository there is no superproject chain, so the highest
    # directory with a .gitmodules wins.
    top = Path(
        os.path.realpath(tmp_path)
    )  # macOS tmp dirs are symlinks; cwd is resolved
    leaf = top / "mid" / "leaf"
    leaf.mkdir(parents=True)
    (top / ".gitmodules").write_text("")
    (top / "mid" / ".gitmodules").write_text("")
    monkeypatch.chdir(leaf)
    assert cli.find_root_dir() == str(top)
