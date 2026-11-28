"""``newb install-shell-completion`` / ``newb print-shell-completion``.

SciTeX CLI audit §1a requires every CLI to ship these two commands so
``newb <TAB>`` produces sensible completions. We implement them natively
over Click's built-in completion classes, so newb gains no scitex-dev
dependency. The install logic is a minimal copy of
``scitex_dev._cli._completion`` (the reference implementation,
scitex-dev#1127); keep the two in step.

Install contract:

* It NEVER modifies a shell rc file (``~/.bashrc``, ``~/.zshrc``, ...). On
  many machines the rc file is a symlink into a version-controlled dotfiles
  checkout, so appending to it dirties a tracked file on every host. Earlier
  versions appended ``eval "$(_NEWB_COMPLETE=bash_source newb)"``.
* It writes one drop-in file per console script (``newb``, ``scitex-newb``)
  at the location the shell's completion framework already scans::

    bash  ${BASH_COMPLETION_USER_DIR:-${XDG_DATA_HOME:-~/.local/share}/bash-completion}/completions/<prog>
    zsh   ${XDG_DATA_HOME:-~/.local/share}/zsh/site-functions/_<prog>
    fish  ${XDG_CONFIG_HOME:-~/.config}/fish/completions/<prog>.fish

* It is idempotent (an unchanged script is not rewritten) and atomic.
* It refuses to write when a target (or a directory on its way, resolved
  through symlinks) lies inside a git work tree.

The script is generated in-process with this interpreter's Click against the
``newb`` group; no ``newb`` binary is executed from ``PATH``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import click
from click.shell_completion import get_completion_class

_SHELLS = ("bash", "zsh", "fish")

# Every console script in pyproject.toml bound to the ``newb`` group. Click
# keys completion on argv[0], so each name gets its own drop-in file.
_PROG_NAMES = ("newb", "scitex-newb")


def _env_var(prog_name: str) -> str:
    """Click's autocompletion env var format: ``_<UPPER_PROG>_COMPLETE``."""
    return "_" + prog_name.upper().replace("-", "_") + "_COMPLETE"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    return Path(raw) if raw else _home() / ".local" / "share"


def _xdg_config_home() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME")
    return Path(raw) if raw else _home() / ".config"


def completion_dropin_path(shell: str, prog_name: str) -> Path:
    """Where ``install-shell-completion`` writes ``prog_name``'s script."""
    if shell == "bash":
        # bash-completion 2.x: BASH_COMPLETION_USER_DIR may list several
        # colon-separated roots; the first one is the user's primary.
        user_dirs = os.environ.get("BASH_COMPLETION_USER_DIR", "")
        first = user_dirs.split(":")[0] if user_dirs else ""
        root = Path(first) if first else _xdg_data_home() / "bash-completion"
        return root / "completions" / prog_name
    if shell == "zsh":
        return _xdg_data_home() / "zsh" / "site-functions" / f"_{prog_name}"
    if shell == "fish":
        return _xdg_config_home() / "fish" / "completions" / f"{prog_name}.fish"
    raise click.ClickException(f"Unsupported completion shell: {shell}.")


def _root_group() -> click.Command:
    # Imported lazily: ``newb._cli`` imports this module while building the group.
    from ._try import main

    return main


def generate_completion_script(shell: str, prog_name: str = "newb") -> str:
    """Generate with this interpreter's Click, without executing PATH entries."""
    completion_class = get_completion_class(shell)
    if completion_class is None:
        raise click.ClickException(f"Unsupported completion shell: {shell}.")
    script = (
        completion_class(_root_group(), {}, prog_name, _env_var(prog_name))
        .source()
        .strip()
    )
    if not script:
        raise click.ClickException(
            f"Failed to generate {shell} completion script for {prog_name}."
        )
    return script + "\n"


def _enclosing_git_work_tree(path: Path) -> Path | None:
    """The nearest ancestor of ``path`` (inclusive) holding a ``.git`` entry."""
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _refuse_if_versioned(target: Path) -> None:
    """Refuse to write a completion file into version-controlled territory."""
    places = [target.parent.resolve()]
    if target.is_symlink():
        places.append(target.resolve().parent)
    for place in places:
        repo = _enclosing_git_work_tree(place)
        if repo is not None:
            raise click.ClickException(
                f"refusing to write {target}: it resolves into the git work "
                f"tree {repo}. Writing there would modify a version-controlled "
                f"checkout. Point the shell at another directory (e.g. set "
                f"XDG_DATA_HOME / BASH_COMPLETION_USER_DIR), or save the "
                f"script yourself with `newb print-shell-completion`."
            )


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` via a same-directory temp file and ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def install_completion(
    shell: str, prog_names: tuple[str, ...] = _PROG_NAMES, *, dry_run: bool = False
) -> list[tuple[str, Path, str]]:
    """Write one drop-in per prog name; never touches a shell rc file.

    Returns ``(prog_name, path, status)`` with status ``"written"``,
    ``"unchanged"`` or ``"would-write"``. Raises ``click.ClickException``
    (before any write) when a target would land inside a git work tree.
    """
    plans = []
    for prog_name in prog_names:
        target = completion_dropin_path(shell, prog_name)
        _refuse_if_versioned(target)
        plans.append((prog_name, target))

    outcomes = []
    for prog_name, target in plans:
        if dry_run:
            outcomes.append((prog_name, target, "would-write"))
            continue
        script = generate_completion_script(shell, prog_name)
        current = (
            target.read_text(encoding="utf-8")
            if target.is_file() and not target.is_symlink()
            else None
        )
        if current == script:
            outcomes.append((prog_name, target, "unchanged"))
            continue
        _atomic_write(target, script)
        outcomes.append((prog_name, target, "written"))
    return outcomes


def legacy_rc_lines(shell: str) -> list[tuple[Path, str]]:
    """Lines an earlier ``newb install-shell-completion`` appended (read-only).

    Covers both the ``eval "$(_NEWB_COMPLETE=<shell>_source newb)"`` line and
    the ``# Added by `newb install-shell-completion``` comment above it.
    """
    rc_name = {"bash": ".bashrc", "zsh": ".zshrc"}.get(shell)
    if rc_name is None:
        return []
    rc = _home() / rc_name
    try:
        text = rc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    needles = (
        f"_NEWB_COMPLETE={shell}_source newb",
        "# Added by `newb install-shell-completion`",
    )
    return [(rc, line) for line in text.splitlines() if any(n in line for n in needles)]


def _activation_hint(shell: str, outcomes: list[tuple[str, Path, str]]) -> str:
    if shell == "bash":
        return (
            "bash-completion (>= 2) loads it on the first <TAB> in a new "
            "shell; no rc edit needed."
        )
    if shell == "zsh":
        directory = outcomes[0][1].parent if outcomes else ""
        return (
            f"zsh loads it from fpath: if {directory} is not on your fpath "
            f"yet, add `fpath=({directory} $fpath)` before `compinit` in "
            f"your zsh config (this command never edits it)."
        )
    return "fish loads it automatically in a new shell."


@click.command("print-shell-completion")
@click.argument("shell", type=click.Choice(_SHELLS), required=False)
def print_shell_completion(shell: str | None):
    """Print the shell-completion script for SHELL to stdout.

    \b
    Example:
      $ newb print-shell-completion bash
      $ newb print-shell-completion zsh > ~/.zsh-newb-completion
      $ newb print-shell-completion          # auto-detect from $SHELL

    SHELL is one of: bash, zsh, fish. Auto-detected when omitted.
    """
    shell = shell or _detect_shell()
    click.echo(generate_completion_script(shell), nl=False)


@click.command("install-shell-completion")
@click.argument("shell", type=click.Choice(_SHELLS), required=False)
@click.option(
    "--rc-path",
    default=None,
    hidden=True,
    help="Removed: rc files are no longer edited.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the target drop-in path(s) without writing.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip the interactive confirmation prompt.",
)
def install_shell_completion(
    shell: str | None, rc_path: str | None, dry_run: bool, yes: bool
):
    """Write TAB-completion drop-in files for SHELL; never edits rc files.

    \b
    Example:
      $ newb install-shell-completion bash --yes
      $ newb install-shell-completion --yes    # auto-detect
      $ newb install-shell-completion zsh --dry-run

    \b
    Writes one file per console script (newb, scitex-newb) into the
    per-user directory the shell's completion framework scans:
      bash  ~/.local/share/bash-completion/completions/
      zsh   ~/.local/share/zsh/site-functions/
      fish  ~/.config/fish/completions/
    Idempotent; refuses to write into a git work tree.
    """
    if rc_path is not None:
        raise click.ClickException(
            "--rc-path was removed: install-shell-completion no longer edits "
            "shell rc files. It writes drop-in files the shell scans on its "
            "own; set XDG_DATA_HOME / XDG_CONFIG_HOME / BASH_COMPLETION_USER_DIR "
            "to move them, or save the script yourself with "
            "`newb print-shell-completion SHELL > FILE`."
        )
    shell = shell or _detect_shell()
    if not dry_run and not yes:
        click.echo(
            "refusing to write completion files without --yes/-y "
            "(or use --dry-run to preview).",
            err=True,
        )
        return
    outcomes = install_completion(shell, dry_run=dry_run)
    for _prog, path, status in outcomes:
        label = {
            "would-write": "Would write",
            "written": "Wrote",
            "unchanged": "Up to date",
        }[status]
        click.echo(f"newb: {label}: {path}")
    for rc, line in legacy_rc_lines(shell):
        click.echo(
            f"note: {rc} still carries a line an older installer "
            f"appended; it is no longer needed and can be deleted:\n"
            f"  {line}",
            err=True,
        )
    if not dry_run:
        click.echo(_activation_hint(shell, outcomes))


def _detect_shell() -> str:
    sh = os.environ.get("SHELL", "").rsplit("/", 1)[-1]
    if sh in _SHELLS:
        return sh
    raise click.ClickException(
        f"could not auto-detect shell from $SHELL={os.environ.get('SHELL', '')!r}; "
        f"pass one of {_SHELLS} explicitly."
    )


# EOF
