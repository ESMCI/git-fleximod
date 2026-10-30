from pathlib import Path
import argparse, os, subprocess, sys

__version__ = "1.1.1"


class CustomArgumentParser(argparse.ArgumentParser):
    def print_help(self, file=None):
        # First print the default help message
        super().print_help(file)

        # Then append the contents of README.md
        candidate_paths = [
            Path(sys.prefix) / "share" / "git_fleximod" / "README.md",
            Path(__file__).resolve().parent.parent / "README.md",  # fallback for dev
        ]
        for path in candidate_paths:
            if os.path.exists(path):
                with open(path) as f:
                    print(f.read(), file=file)
                    return
        print("README.md not found.", file=file)


def _git_output(*args, cwd=None):
    """Return the stdout of a git command, or None if it fails or git is missing."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _lists_submodule(parent, child, filename):
    """True if `parent`/`filename` has a submodule entry whose path is `child`."""
    gitmodules = os.path.join(parent, filename)
    if not os.path.isfile(gitmodules):
        return False
    # git parses the indented lines of a .gitmodules file; configparser would not.
    entries = _git_output(
        "config",
        "--file",
        gitmodules,
        "--null",
        "--get-regexp",
        r"^submodule\..*\.path$",
    )
    rel = os.path.relpath(child, parent)
    # --null output is one "<key>\n<value>\0" piece per entry.
    for entry in (entries or "").split("\0"):
        # Take the path value after the key; the empty piece after the last \0, or an
        # entry whose path has no value, leaves it empty.
        _, _, path = entry.partition("\n")
        if not path:
            continue
        # .gitmodules may spell the path "libs/./c/"; normalise it before comparing
        # with the child's path relative to the parent.
        if os.path.normpath(path) == rel:
            return True
    return False


def find_root_dir(filename=".gitmodules"):
    """
    Finds the root directory git-fleximod should operate on: the directory containing
    a file called `filename`, at the top of the chain of superprojects above the current
    directory.

    Starts at the top level of the current git repository (or worktree) and climbs
    while either holds:

    - git reports a superproject;
    - an ancestor directory's `filename` lists the current directory as a submodule path
      (a clone that git does not know as a submodule, such as one in a gitignored
      directory).

    Returns None if the
    resulting directory has no `filename`. Outside any git repository, returns the
    highest directory containing `filename`.
    """
    d = _git_output("rev-parse", "--show-toplevel")
    if d is None:
        # Not in a git repository (tarball extract, or git is not installed), so
        # there is no superproject chain; use the highest directory with `filename`.
        for dl in reversed([Path.cwd(), *Path.cwd().parents]):
            if (dl / filename).is_file():
                return str(dl)
        return None
    d = d.strip()
    while True:
        # 1. The parent is the superproject: git reports it when d is a submodule.
        parent = (
            _git_output("rev-parse", "--show-superproject-working-tree", cwd=d) or ""
        ).strip()
        if not parent:
            # 2. The parent lists d as a submodule: a clone that git does not report as
            # a submodule (e.g. under a gitignored directory). The nearest ancestor
            # listing d wins; parent stays empty if none does.
            for anc in Path(d).parents:
                if _lists_submodule(anc, d, filename):
                    parent = str(anc)
                    break
        if not parent:
            break
        d = parent
    # d holds the root but there may be nothing to do: d lacks `filename` if it is a
    # repository with no submodules (the loop never climbed), or if git reports it as a
    # superproject of a gitlink but it has no `filename`; in either case, there is
    # nothing for git-fleximod to operate on.
    return d if os.path.isfile(os.path.join(d, filename)) else None


def get_parser():
    description = """
    %(prog)s manages checking out groups of gitsubmodules with additional support for Earth System Models
    """
    parser = CustomArgumentParser(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter
    )

    #
    # user options
    #
    choices = ["update", "status", "test"]
    parser.add_argument(
        "action",
        choices=choices,
        default="update",
        help=f"Subcommand of git-fleximod, choices are {choices[:-1]}",
    )

    parser.add_argument(
        "components",
        nargs="*",
        help="Specific component(s) to checkout. By default, "
        "all required submodules are checked out.",
    )

    parser.add_argument(
        "-C",
        "--path",
        default=find_root_dir(),
        help="Toplevel repository directory.  Defaults to top git directory relative to current.",
    )

    parser.add_argument(
        "-g",
        "--gitmodules",
        nargs="?",
        default=".gitmodules",
        help="The submodule description filename. " "Default: %(default)s.",
    )

    parser.add_argument(
        "-x",
        "--exclude",
        nargs="*",
        help="Component(s) listed in the gitmodules file which should be ignored.",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        default=False,
        help="Override cautions and update or checkout over locally modified repository.",
    )

    parser.add_argument(
        "-o",
        "--optional",
        action="store_true",
        default=False,
        help="By default only the required submodules "
        "are checked out. This flag will also checkout the "
        "optional submodules relative to the toplevel directory.",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Output additional information to "
        "the screen and log file. This flag can be "
        "used up to two times, increasing the "
        "verbosity level each time.",
    )

    parser.add_argument(
        "--no-mods-details",
        action="store_true",
        default=False,
        help="Suppress details on local mods in status output.",
    )

    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print version and exit.",
    )

    #
    # developer options
    #
    parser.add_argument(
        "--backtrace",
        action="store_true",
        help="DEVELOPER: show exception backtraces as extra " "debugging output",
    )

    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        default=False,
        help="DEVELOPER: output additional debugging "
        "information to the screen and log file.",
    )

    return parser
