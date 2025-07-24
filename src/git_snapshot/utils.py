# src/git_snapshot/utils.py
import os
import shutil
import stat
import time
from pathlib import Path

import click
import py7zr

from git_snapshot.exceptions import GitSnapshotException


def get_git_root(path: Path) -> Path | None:
    """
    Finds the root of the Git repository from the given path.
    It does this by searching for the '.git' directory in the provided path
    and its parent directories.

    Args:
        path (Path): The starting path to search for the Git repository root.

    Returns:
        Path | None: The path to the Git repository root if found, otherwise None.
    """
    current_path = path.resolve()
    while current_path != current_path.parent:
        if (current_path / ".git").is_dir():
            return current_path
        current_path = current_path.parent
    return None


def parse_gitignore(repo_root: Path, verbose: bool = False) -> list[str]:
    """
    Parses the root .gitignore file and returns a list of pattern strings.
    If .gitignore is not found, returns an empty list.

    Args:
        repo_root (Path): The root directory of the Git repository.
        verbose (bool): If True, print warnings if .gitignore is not found or cannot be read.

    Returns:
        list[str]: A list of .gitignore patterns.
    """
    gitignore_path = repo_root / ".gitignore"
    if not gitignore_path.is_file():
        if verbose:
            click.echo("Warning: .gitignore not found. Proceeding without exclusions.")
        return []

    try:
        with open(gitignore_path, "r", encoding="utf-8") as f:
            lines = [
                line.strip()
                for line in f.readlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        return lines
    except Exception as e:
        click.echo(
            f"Error reading .gitignore at {gitignore_path}: {e}. Proceeding without exclusions.",
            err=True,
        )
        return []


def _handle_remove_read_only(func, path: str, exc_info: tuple):
    """
    Error handler for shutil.rmtree. If a file is read-only or permission denied,
    attempts to change its permissions and retry the operation. This function is
    designed to be passed as the `onerror` argument to `shutil.rmtree`.

    Args:
        func (callable): The function that failed (e.g., os.remove, os.rmdir).
        path (str): The path to the file/directory that caused the error.
        exc_info (tuple): A tuple containing (exception type, exception value, traceback).
    """
    if issubclass(exc_info[0], OSError) and os.path.exists(path):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            raise exc_info[1]
    else:
        raise exc_info[1]


def _remove_directory_robustly(
    path: Path, retries: int = 5, delay: float = 0.1, verbose: bool = False
):
    """
    Attempts to remove a directory robustly, handling PermissionError by changing permissions
    and implementing a retry mechanism with exponential backoff.

    Args:
        path (Path): The path to the directory to remove.
        retries (int): The maximum number of retry attempts.
        delay (float): The initial delay in seconds between retries.
        verbose (bool): If True, print verbose messages during retries.

    Raises:
        GitSnapshotException: If the directory cannot be removed after multiple attempts due to permission issues,
                              or for any other unexpected errors during removal.
    """
    if not path.exists():
        return

    for i in range(retries):
        try:
            shutil.rmtree(path, onerror=_handle_remove_read_only)
            return
        except PermissionError:
            if verbose:
                click.echo(
                    f"Permission denied during removal of {path} (Attempt {i + 1}/{retries}). Retrying in {delay}s...",
                    err=True,
                )
            time.sleep(delay)
            delay *= 1.5
        except Exception as e:
            raise GitSnapshotException(f"Error removing {path}: {e}") from e

    raise GitSnapshotException(
        f"Failed to remove directory {path} after {retries} attempts due to PermissionError. Manual intervention may be required."
    )


def _clear_directory_contents(
    target_dir: Path, exclusions: list[Path], verbose: bool = False
):
    """
    Clears the contents of target_dir, excluding paths in the exclusions list.
    Exclusions should be resolved paths.

    Args:
        target_dir (Path): The directory whose contents need to be cleared.
        exclusions (list[Path]): A list of resolved Path objects to exclude from clearing.
        verbose (bool): If True, print verbose messages about skipped items.
    """
    if not target_dir.is_dir():
        return

    resolved_exclusions = {p.resolve() for p in exclusions}

    for item in target_dir.iterdir():
        if item.resolve() in resolved_exclusions:
            if verbose:
                click.echo(
                    f"Skipping removal of protected directory/file: '{item.name}'"
                )
            continue

        if item.is_file():
            try:
                item.unlink()
            except Exception as file_e:
                click.echo(
                    f"Warning: Could not remove file '{item}': {file_e}", err=True
                )
        elif item.is_dir():
            _remove_directory_robustly(item, verbose=verbose)


def _stash_directory_state(
    directory_to_stash: Path, stash_base_dir: Path, verbose: bool = False
) -> Path | None:
    """
    Creates a temporary 7z snapshot (stash) of the given directory's current state.
    This is used during restoration to provide a rollback point if restoration fails.
    The stash is created within the provided `stash_base_dir`, which is typically
    a temporary directory managed by `tempfile.TemporaryDirectory`.

    Args:
        directory_to_stash (Path): The directory whose contents need to be stashed.
        stash_base_dir (Path): The base directory where temporary stashes will be stored.
                               This should ideally be a path within a `tempfile.TemporaryDirectory`.
        verbose (bool): If True, print verbose messages.

    Returns:
        Path | None: The path to the created stash file, or None if no stash was created
                     (e.g., if the directory was empty).

    Raises:
        GitSnapshotException: If an error occurs during stash creation.
    """
    if verbose:
        click.echo(f"Creating a temporary stash of '{directory_to_stash}'...")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    stash_filename = f"restore_stash_{timestamp}.7z"
    stash_filepath = stash_base_dir / stash_filename

    try:
        if not directory_to_stash.is_dir() or not any(directory_to_stash.iterdir()):
            if verbose:
                click.echo(
                    f"Directory '{directory_to_stash}' is empty or does not exist. No stash created."
                )
            return None

        with py7zr.SevenZipFile(stash_filepath, "w") as archive:
            for item in directory_to_stash.iterdir():
                if item.is_file():
                    archive.write(item, arcname=item.name)
                elif item.is_dir():
                    for root, _, files in os.walk(item):
                        current_root_path = Path(root)
                        for f in files:
                            full_path = current_root_path / f
                            relative_path_in_stash = full_path.relative_to(
                                directory_to_stash
                            )
                            archive.write(
                                full_path, arcname=str(relative_path_in_stash)
                            )
        return stash_filepath
    except Exception as e:
        if stash_filepath.exists():
            stash_filepath.unlink()
        raise GitSnapshotException(
            f"Error creating stash for '{directory_to_stash}': {e}"
        ) from e


def _revert_from_stash(stash_filepath: Path, target_dir: Path, verbose: bool = False):
    """
    Reverts the target directory to the state saved in the stash file.
    This attempts to robustly clear the target_dir and then extract the stash.

    Args:
        stash_filepath (Path): The path to the stash file to revert from.
        target_dir (Path): The directory to revert to the stashed state.
        verbose (bool): If True, print verbose messages.

    Raises:
        GitSnapshotException: If the stash file is invalid or reversion fails.
    """
    if not stash_filepath or not stash_filepath.is_file():
        if verbose:
            click.echo("No valid stash file found, cannot revert.", err=True)
        return

    click.echo(f"Attempting to revert '{target_dir}' from stash.")
    try:
        exclusions_for_clear: list[Path] = []
        _clear_directory_contents(target_dir, exclusions_for_clear, verbose=verbose)

        target_dir.mkdir(parents=True, exist_ok=True)

        with py7zr.SevenZipFile(stash_filepath, mode="r") as z:
            z.extractall(path=target_dir)
        click.echo(f"Successfully reverted '{target_dir}' from stash.")
    except Exception as e:
        raise GitSnapshotException(
            f"Error reverting from stash: {e}. Manual intervention may be required."
        ) from e


def _remove_dir_if_empty(path: Path, description: str, verbose: bool = False):
    """
    Removes a directory if it's empty, with a descriptive message.

    Args:
        path (Path): The path to the directory to remove.
        description (str): A description of the directory (e.g., "temporary stash").
        verbose (bool): If True, print verbose messages about removal or why it's skipped.
    """
    if path.is_dir():
        try:
            if not any(path.iterdir()):
                path.rmdir()
                if verbose:
                    click.echo(f"Cleaned up empty {description} directory: {path}")
            else:
                if verbose:
                    click.echo(
                        f"{description} directory '{path}' is not empty, skipping removal."
                    )
        except OSError as e:
            click.echo(
                f"Warning: Could not remove empty {description} directory '{path}': {e}",
                err=True,
            )


def _get_archive_app_name(snapshot_filepath: Path, verbose: bool) -> str:
    """
    Inspects the 7z archive to determine the top-level directory name.
    Snapshots created by `git-snapshot` are expected to have a single top-level directory
    matching the repository name.

    Args:
        snapshot_filepath (Path): The path to the snapshot file.
        verbose (bool): If True, print verbose messages.

    Returns:
        str: The detected top-level directory name or an empty string if not found
             or if the archive is not structured with a single top-level directory.

    Raises:
        GitSnapshotException: If there's an error inspecting the archive or it's empty.
    """
    archive_app_name = ""
    try:
        with py7zr.SevenZipFile(snapshot_filepath, mode="r") as z:
            found_any_item = False
            for item_info in z.list():
                found_any_item = True
                parts = item_info.filename.split("/")
                if parts and parts[0]:
                    archive_app_name = parts[0]
                    break
            if not found_any_item:
                raise GitSnapshotException(
                    "Snapshot appears to be empty or does not contain any entries."
                )
            if not archive_app_name:
                click.echo(
                    "Warning: Could not determine top-level directory name in snapshot. Contents will be extracted directly into the output directory."
                )
        if verbose and archive_app_name:
            click.echo(
                f"Detected top-level directory '{archive_app_name}' within snapshot."
            )
        return archive_app_name
    except Exception as e:
        raise GitSnapshotException(
            f"Error inspecting snapshot: {e}. This might be due to a corrupted snapshot or an outdated 'py7zr' library. Consider updating 'py7zr'."
        ) from e
