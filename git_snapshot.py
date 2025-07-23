import datetime
import os
import shutil
import stat
import time
from pathlib import Path

import click
import py7zr
from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern


# Custom exception for Click to handle gracefully
class GitSnapshotException(click.ClickException):
    """Custom exception for controlled exit with an error message."""
    pass


def _check_py7zr_installed():
    """Checks if py7zr is installed and raises GitSnapshotException if not."""
    try:
        # Attempt to import py7zr to check for installation
        import py7zr
        # Minimal check for functionality, e.g., accessing a known attribute
        _ = py7zr.SevenZipFile
    except ImportError:
        raise GitSnapshotException(
            "Error: 'py7zr' is not installed. Please install it using 'uv pip install py7zr'."
        )
    except AttributeError:
        # This might catch issues where py7zr is installed but not correctly structured
        raise GitSnapshotException(
            "Error: 'py7zr' is installed but appears to be corrupted or incomplete. "
            "Please try reinstalling it using 'uv pip install py7zr'."
        )


# Initialize the Click group
@click.group(
    help="Create a .7z snapshot of a local Git repository, respecting .gitignore rules, or restore one."
)
def cli():
    """
    Main entry point for the git-snapshot CLI application.
    This function serves as the Click group for subcommands.
    """
    # Perform initial checks here, before any subcommands are executed
    _check_py7zr_installed()


def get_git_root(path: Path) -> Path | None:
    """
    Finds the root of the Git repository from the given path.

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
            click.echo(
                "Warning: .gitignore not found. Proceeding without exclusions."
            )
        return []

    try:
        with open(gitignore_path, "r", encoding="utf-8") as f:
            # Strip whitespace and filter out empty lines or comments
            lines = [
                line.strip()
                for line in f.readlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        return lines
    except Exception as e:
        click.echo(
            f"Error reading .gitignore at {gitignore_path}: {e}. Proceeding without exclusions.",
            err=True
        )
        return []


def _handle_remove_read_only(func, path, exc_info):
    """
    Error handler for shutil.rmtree. If a file is read-only or permission denied,
    attempts to change its permissions and retry the operation.

    Args:
        func (callable): The function that failed (e.g., os.remove, os.rmdir).
        path (str): The path to the file/directory that caused the error.
        exc_info (tuple): A tuple containing (exception type, exception value, traceback).
    """
    # Check if the error is an OSError (often PermissionError) and if the path exists
    if issubclass(exc_info[0], OSError) and os.path.exists(path):
        try:
            # Change permissions to make the file/directory writable
            os.chmod(path, stat.S_IWRITE)
            func(path)  # Retry the operation that failed
        except Exception:
            # If changing permissions and retrying still fails, re-raise the original exception
            raise exc_info[1]
    else:
        # Re-raise the exception if it's not a permission error or cannot be handled
        raise exc_info[1]


def _remove_directory_robustly(path: Path, retries: int = 5, delay: float = 0.1, verbose: bool = False):
    """
    Attempts to remove a directory robustly, handling PermissionError by changing permissions
    and implementing a retry mechanism with exponential backoff.

    Args:
        path (Path): The path to the directory to remove.
        retries (int): The maximum number of retry attempts.
        delay (float): The initial delay in seconds between retries.
        verbose (bool): If True, print verbose messages during retries.

    Raises:
        PermissionError: If the directory cannot be removed after multiple attempts due to permission issues.
        Exception: For any other unexpected errors during removal.
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
            delay *= 1.5  # Exponential backoff
        except Exception as e:
            raise GitSnapshotException(f"Error removing {path}: {e}") from e

    raise GitSnapshotException(
        f"Failed to remove directory {path} after {retries} attempts due to PermissionError. Manual intervention may be required."
    )


def _clear_directory_contents(target_dir: Path, exclusions: list[Path], verbose: bool = False):
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
                click.echo(f"Skipping removal of protected directory/file: '{item.name}'")
            continue

        if item.is_file():
            try:
                item.unlink()
            except Exception as file_e:
                click.echo(
                    f"Warning: Could not remove file '{item}': {file_e}", err=True
                )
        elif item.is_dir():
            _remove_directory_robustly(item, verbose=verbose)  # Recursively remove subdirectories robustly


def _create_snapshot_logic(source_path: Path, output_dir: Path, verbose: bool = False):
    """
    Core logic to create a .7z snapshot of the Git repository.

    Args:
        source_path (Path): The path to the local Git repository to snapshot.
        output_dir (Path): The directory to save the generated .7z file.
        verbose (bool): If True, enable verbose output.

    Raises:
        GitSnapshotException: If the source path is not a Git repository, output directory issues occur,
                              or compression fails.
    """
    repo_root = get_git_root(source_path)
    if not repo_root:
        raise GitSnapshotException(
            f"'{source_path}' is not a valid Git repository or not within one."
        )

    if verbose:
        click.echo(f"Detected Git repository root: {repo_root}")

    app_name = repo_root.name
    gitignore_patterns = parse_gitignore(repo_root, verbose=verbose)
    all_patterns_for_spec = list(gitignore_patterns)

    # Automatically exclude the output directory if it's within the repository
    abs_output_dir = output_dir.resolve()
    try:
        if abs_output_dir.is_relative_to(repo_root):
            relative_output_path_str = str(abs_output_dir.relative_to(repo_root))
            exclusion_pattern = f"/{relative_output_path_str}/"
            if exclusion_pattern not in all_patterns_for_spec:
                all_patterns_for_spec.append(exclusion_pattern)
                if verbose:
                    click.echo(
                        f"Automatically excluding output directory '{relative_output_path_str}' from snapshot."
                    )
    except ValueError:  # output_dir is not relative to repo_root
        pass  # No need to exclude if it's outside the repository

    spec = PathSpec.from_lines(GitWildMatchPattern, all_patterns_for_spec)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{app_name}_snapshot_{timestamp}.7z"
    output_filepath = output_dir / output_filename

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise GitSnapshotException(
            f"Error: Could not create output directory '{output_dir}': {e}"
        ) from e

    actual_files_to_archive_relative = []

    # Explicitly add all contents of the .git directory
    git_path_in_repo = repo_root / ".git"
    if git_path_in_repo.is_dir():
        if verbose:
            click.echo("Including .git directory contents in the snapshot.")
        for root, _, files in os.walk(git_path_in_repo):
            current_root_path = Path(root)
            for f in files:
                full_path = current_root_path / f
                relative_path = full_path.relative_to(repo_root)
                actual_files_to_archive_relative.append(relative_path)

    # Walk through the rest of the repository root and filter files
    for root, dirs, files in os.walk(repo_root):
        current_path = Path(root)

        # Filter out .git from main walk if it's at repo_root level
        if current_path == repo_root and ".git" in dirs:
            dirs.remove(".git")

        # Filter directories to not descend into ignored ones
        dirs_to_remove = []
        for d in dirs:
            dir_path_abs = current_path / d
            relative_dir_path = dir_path_abs.relative_to(repo_root)
            # gitignore patterns can apply to directories.
            # The trailing os.sep is important for matching directory patterns like 'my_dir/'
            if spec.match_file(str(relative_dir_path) + os.sep) or spec.match_file(str(relative_dir_path)):
                dirs_to_remove.append(d)

        for d_remove in dirs_to_remove:
            dirs.remove(d_remove)  # Modify dirs in-place for os.walk

        for f in files:
            file_path_abs = current_path / f
            relative_file_path = file_path_abs.relative_to(repo_root)
            if not spec.match_file(str(relative_file_path)):
                actual_files_to_archive_relative.append(relative_file_path)

    if not actual_files_to_archive_relative:
        click.echo("No files found to compress after applying .gitignore rules.")
        return

    click.echo(
        f"Compressing {len(actual_files_to_archive_relative)} files into {output_filepath}..."
    )
    try:
        with py7zr.SevenZipFile(output_filepath, "w") as archive:
            for relative_file in actual_files_to_archive_relative:
                full_path = repo_root / relative_file
                # Use app_name as prefix for path inside archive
                archive.write(full_path, arcname=str(app_name / relative_file))
        click.echo(f"Successfully created snapshot: {output_filepath}")

    except py7zr.Bad7zFile as e:
        if output_filepath.exists():
            output_filepath.unlink()  # Clean up incomplete archive
        raise GitSnapshotException(
            f"Error creating 7z archive: {e}. The file might be corrupted or there was an issue with compression."
        ) from e
    except PermissionError as e:
        if output_filepath.exists():
            output_filepath.unlink()  # Clean up incomplete archive
        raise GitSnapshotException(
            f"Permission denied: Cannot write to {output_filepath}. Check directory permissions."
        ) from e
    except Exception as e:
        if output_filepath.exists():
            output_filepath.unlink()  # Clean up incomplete archive
        raise GitSnapshotException(f"An unexpected error occurred during compression: {e}") from e


def _stash_directory_state(directory_to_stash: Path, stash_base_dir: Path, verbose: bool = False) -> Path | None:
    """
    Creates a temporary 7z snapshot (stash) of the given directory's current state.

    Args:
        directory_to_stash (Path): The directory whose contents need to be stashed.
        stash_base_dir (Path): The base directory where temporary stashes will be stored.
        verbose (bool): If True, print verbose messages.

    Returns:
        Path | None: The path to the created stash file, or None if no stash was created
                     (e.g., if the directory was empty).

    Raises:
        GitSnapshotException: If an error occurs during stash creation.
    """
    if verbose:
        click.echo(f"Creating a temporary stash of '{directory_to_stash}'...")
    try:
        stash_base_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise GitSnapshotException(f"Error creating stash directory '{stash_base_dir}': {e}") from e

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    stash_filename = f"restore_stash_{timestamp}.7z"
    stash_filepath = stash_base_dir / stash_filename

    try:
        if not directory_to_stash.is_dir() or not any(directory_to_stash.iterdir()):
            if verbose:
                click.echo(f"Directory '{directory_to_stash}' is empty or does not exist. No stash created.")
            return None

        with py7zr.SevenZipFile(stash_filepath, "w") as archive:
            for item in directory_to_stash.iterdir():
                # Avoid stashing the stash directory itself or the main snapshots directory.
                if item.resolve() == stash_base_dir.resolve() or \
                   item.resolve() == (Path.cwd() / "snapshots").resolve():
                    continue

                if item.is_file():
                    archive.write(item, arcname=item.name)
                elif item.is_dir():
                    for root, _, files in os.walk(item):
                        current_root_path = Path(root)
                        for f in files:
                            full_path = current_root_path / f
                            relative_path_in_stash = full_path.relative_to(directory_to_stash)
                            archive.write(full_path, arcname=str(relative_path_in_stash))
        return stash_filepath
    except Exception as e:
        if stash_filepath.exists(): # Check if it was partially created
            stash_filepath.unlink()  # Clean up incomplete stash
        raise GitSnapshotException(f"Error creating stash for '{directory_to_stash}': {e}") from e


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
        # Define paths to exclude from clearing
        # The stash_filepath.parent is the .temp_stashes directory
        # Path("./snapshots").resolve() is the main snapshots directory
        exclusions_for_clear = [
            stash_filepath.parent.resolve(),
            (Path.cwd() / "snapshots").resolve(),  # Ensure this is absolute
        ]
        _clear_directory_contents(target_dir, exclusions_for_clear, verbose=verbose)

        # Recreate the empty target_dir if needed
        target_dir.mkdir(parents=True, exist_ok=True)

        # Extract stash
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
            if not any(path.iterdir()):  # Check if directory is truly empty
                path.rmdir()
                if verbose:
                    click.echo(f"Cleaned up empty {description} directory: {path}")
            else:
                if verbose:
                    click.echo(f"{description} directory '{path}' is not empty, skipping removal.")
        except OSError as e:
            click.echo(f"Warning: Could not remove empty {description} directory '{path}': {e}", err=True)


def _get_archive_app_name(snapshot_filepath: Path, verbose: bool) -> str:
    """
    Inspects the 7z archive to determine the top-level directory name.

    Args:
        snapshot_filepath (Path): The path to the snapshot file.
        verbose (bool): If True, print verbose messages.

    Returns:
        str: The detected top-level directory name or an empty string if not found.

    Raises:
        GitSnapshotException: If there's an error inspecting the archive or it's empty.
    """
    archive_app_name = ""
    try:
        with py7zr.SevenZipFile(snapshot_filepath, mode="r") as z:
            found_any_item = False
            for item_info in z.list():
                found_any_item = True
                parts = item_info.filename.split('/')
                if parts and parts[0]:
                    archive_app_name = parts[0]
                    break
            if not found_any_item:
                raise GitSnapshotException("Snapshot appears to be empty or does not contain any entries.")
            if not archive_app_name:
                click.echo("Warning: Could not determine top-level directory name in snapshot. Contents will be extracted directly into the output directory.")
        if verbose and archive_app_name:
            click.echo(f"Detected top-level directory '{archive_app_name}' within snapshot.")
        return archive_app_name
    except Exception as e:
        raise GitSnapshotException(
            f"Error inspecting snapshot: {e}. This might be due to a corrupted snapshot or an outdated 'py7zr' library. Consider updating 'py7zr'."
        ) from e


def _restore_snapshot_logic(snapshot_filepath: Path, output_dir: Path, verbose: bool = False, keep_venv: bool = False):
    """
    Core logic to restore a .7z snapshot to a local directory.

    Args:
        snapshot_filepath (Path): The path to the snapshot file to restore.
        output_dir (Path): The directory to restore the snapshot to.
        verbose (bool): If True, enable verbose output.
        keep_venv (bool): If True, do not remove the .venv directory during restoration.

    Raises:
        GitSnapshotException: If the snapshot file is not found, extraction fails, or other issues occur.
    """
    resolved_output_dir = output_dir.resolve()

    if not snapshot_filepath.is_file():
        raise GitSnapshotException(f"Error: Snapshot file '{snapshot_filepath}' not found.")

    archive_app_name = _get_archive_app_name(snapshot_filepath, verbose)
    target_app_path = resolved_output_dir / archive_app_name if archive_app_name else resolved_output_dir

    try:
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        if verbose:
            click.echo(f"Ensured output directory exists: {resolved_output_dir}")
    except Exception as e:
        raise GitSnapshotException(f"Error: Could not ensure output directory '{resolved_output_dir}': {e}") from e

    temp_stashes_base_dir = Path.cwd() / "snapshots/.temp_stashes"
    try:
        temp_stashes_base_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise GitSnapshotException(f"Error creating temporary stash directory '{temp_stashes_base_dir}': {e}") from e

    stash_filepath: Path | None = None
    try:
        if target_app_path.is_dir():
            if verbose:
                click.echo(f"Stashing existing contents of '{target_app_path}'...")
            stash_filepath = _stash_directory_state(target_app_path, temp_stashes_base_dir, verbose=verbose)
            if stash_filepath:
                if verbose:
                    click.echo(f"Temporary stash created at: {stash_filepath}")
            else:
                if verbose:
                    click.echo(f"'{target_app_path}' is empty or does not exist, no stash created.")
        else:
            if verbose:
                click.echo(f"Target application directory '{target_app_path}' does not exist, no stash needed.")

        exclusions_for_clear = [
            temp_stashes_base_dir.resolve(),
            (Path.cwd() / "snapshots").resolve(),
        ]

        if keep_venv:
            venv_path_in_target = target_app_path / ".venv"
            if venv_path_in_target.is_dir():
                exclusions_for_clear.append(venv_path_in_target.resolve())
                if verbose:
                    click.echo(f"Keeping existing .venv directory at {venv_path_in_target}")

        if target_app_path.is_dir():
            if verbose:
                click.echo(f"Clearing existing contents of '{target_app_path}' before restoration...")
            _clear_directory_contents(target_app_path, exclusions_for_clear, verbose=verbose)
        else:
            if verbose:
                click.echo(f"'{target_app_path}' does not exist, no need to clear before extraction.")

        target_app_path.mkdir(parents=True, exist_ok=True)

        click.echo(f"Restoring snapshot '{snapshot_filepath}' to '{resolved_output_dir}'...")

        with py7zr.SevenZipFile(snapshot_filepath, mode="r") as z:
            z.extractall(path=resolved_output_dir)

        click.echo(f"Snapshot restored successfully to {target_app_path}")

    except Exception as e:
        click.echo(f"Restoration failed: {e}", err=True)
        if stash_filepath and stash_filepath.exists():
            click.echo("Attempting to automatically revert to previous state...")
            try:
                _revert_from_stash(stash_filepath, target_app_path, verbose=verbose)
            except Exception as revert_e:
                click.echo(f"Automatic revert also failed: {revert_e}", err=True)
                click.echo("Manual intervention may be required to restore previous state.", err=True)
        else:
            click.echo("No stash found or stash creation failed, cannot revert automatically.", err=True)

        if target_app_path.is_dir() and target_app_path.exists():
            click.echo(f"Cleaning up partially extracted files at: {target_app_path}", err=True)
            # Use _remove_directory_robustly for cleanup here too
            _remove_directory_robustly(target_app_path, verbose=verbose)

        # Re-raise as GitSnapshotException to exit cleanly via Click
        raise GitSnapshotException("Restoration failed, see logs above for details.") from e

    finally:
        if stash_filepath and stash_filepath.exists():
            try:
                stash_filepath.unlink()
                if verbose:
                    click.echo(f"Stash file '{stash_filepath}' deleted.")
            except Exception as e:
                click.echo(f"Warning: Could not delete stash file '{stash_filepath}': {e}", err=True)

        _remove_dir_if_empty(temp_stashes_base_dir, "temporary stash", verbose=verbose)
        snapshots_dir = Path.cwd() / "snapshots"
        _remove_dir_if_empty(snapshots_dir, "snapshots", verbose=verbose)


@cli.command("create")
@click.option(
    "-s",
    "--source",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, readable=True, path_type=Path),
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
@click.argument("snapshot_file", type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True, path_type=Path))
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
    help="Do not remove the .venv directory during restoration. Use with caution as this may lead to dependency mismatches."
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
    Handles top-level error exceptions from Click commands.
    """
    try:
        cli()
    except GitSnapshotException as e:
        click.echo(f"Error: {e.message}", err=True)
        # Click handles exiting with status code 1 for ClickExceptions
    except Exception as e:
        click.echo(f"An unexpected fatal error occurred: {e}", err=True)
        # For truly unexpected errors not caught by GitSnapshotException
        click.Abort() # Abort the Click program, which exits with 1


if __name__ == "__main__":
    main()