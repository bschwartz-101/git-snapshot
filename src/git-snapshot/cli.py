# src/git_snapshot/cli.py
from pathlib import Path

import click

from git_snapshot.core import _create_snapshot_logic, _restore_snapshot_logic
from git_snapshot.exceptions import GitSnapshotException
from git_snapshot.utils import _check_py7zr_installed


# Initialize the Click group
@click.group(
    help="Create a .7z snapshot of a local Git repository, respecting .gitignore rules, or restore one."
)
def cli():
    """
    Main entry point for the git-snapshot CLI application.
    This function serves as the Click group for subcommands.
    Performs initial checks, such as verifying 'py7zr' installation.
    """
    _check_py7zr_installed()


@cli.command("create")
@click.option(
    "-s",
    "--source",
    type=click.Path(
        exists=True, file_okay=False, dir_okay=True, readable=True, path_type=Path
    ),
    default=Path("."),
    help="Path to the local Git repository to snapshot. Defaults to the current working directory.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, dir_okay=True, writable=True, path_type=Path),
    default=Path("./snapshots"),
    help="Directory to save the generated .7z file. Defaults to './snapshots/'.",
)
@click.option(
    "-v", "--verbose", is_flag=True, default=False, help="Enable verbose output."
)
def create_command(source: Path, output: Path, verbose: bool):
    """
    Create a .7z snapshot of a local Git repository, respecting .gitignore rules.
    Automatically excludes the output directory if it's within the repository.

    Args:
        source (Path): Path to the local Git repository.
        output (Path): Directory to save the generated .7z file.
        verbose (bool): Enable verbose output.
    """
    _create_snapshot_logic(source, output, verbose)


@cli.command("restore")
@click.argument(
    "snapshot_file",
    type=click.Path(
        exists=True, file_okay=True, dir_okay=False, readable=True, path_type=Path
    ),
)
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, dir_okay=True, writable=True, path_type=Path),
    default=Path("."),
    help="Directory to restore the snapshot to. Defaults to the current working directory (`.`).",
)
@click.option(
    "-v", "--verbose", is_flag=True, default=False, help="Enable verbose output."
)
@click.option(
    "--keep-venv",
    is_flag=True,
    default=False,
    help="Do not remove the .venv directory during restoration. Use with caution as this may lead to dependency mismatches.",
)
def restore_command(snapshot_file: Path, output: Path, verbose: bool, keep_venv: bool):
    """
    Restore a .7z snapshot to a local directory, with automatic stash and reroll on failure.

    Args:
        snapshot_file (Path): Path to the .7z snapshot file to restore.
        output (Path): Directory to restore the snapshot to.
        verbose (bool): Enable verbose output.
        keep_venv (bool): Flag to keep the .venv directory during restoration.
    """
    _restore_snapshot_logic(snapshot_file, output, verbose, keep_venv)


def main():
    """
    Entry point for the command-line interface.
    Handles top-level error exceptions from Click commands, particularly GitSnapshotException.
    """
    try:
        cli()
    except GitSnapshotException as e:
        click.echo(f"Error: {e.message}", err=True)
        # Click handles exiting with status code 1 for ClickExceptions
    except Exception as e:
        # Catch any other unexpected, unhandled exceptions
        click.echo(f"An unexpected fatal error occurred: {e}", err=True)
        click.Abort()  # Abort the Click program, which exits with 1


if __name__ == "__main__":
    main()
