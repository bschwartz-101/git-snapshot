import datetime
import os
import shutil
import stat
import time
import sys
from pathlib import Path

import click
from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern

# Ensure py7zr is installed for compression
try:
    import py7zr
except ImportError:
    click.echo(
        "Error: 'py7zr' is not installed. Please install it using 'uv pip install py7zr'.",
        err=True,
    )
    sys.exit(1)

# Initialize the Click group
@click.group(
    help="Create a .7z snapshot of a local Git repository, respecting .gitignore rules, or restore one."
)
def cli():
    pass


def get_git_root(path: Path) -> Path | None:
    """
    Finds the root of the Git repository from the given path.
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
            f"Error reading .gitignore at {gitignore_path}: {e}. Proceeding without exclusions."
        )
        return []


def _handle_remove_read_only(func, path, exc_info):
    """
    Error handler for shutil.rmtree. If a file is read-only or permission denied,
    attempts to change its permissions and retry the operation.
    """
    # Check if the error is an OSError (often PermissionError) and if the path exists
    if issubclass(exc_info[0], OSError) and os.path.exists(path):
        try:
            # Change permissions to make the file/directory writable
            os.chmod(path, stat.S_IWRITE)
            func(path)  # Retry the operation that failed (e.g., os.remove or os.rmdir)
        except Exception:
            # If changing permissions and retrying still fails, re-raise the original exception
            raise exc_info[1]
    else:
        # Re-raise the exception if it's not a permission error or cannot be handled
        raise exc_info[1]


def _remove_directory_robustly(path: Path, retries: int = 5, delay: float = 0.1, verbose: bool = False):
    """
    Attempts to remove a directory robustly, handling PermissionError by changing permissions
    and implementing a retry mechanism.
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
            click.echo(f"Error removing {path}: {e}", err=True)
            raise  # Re-raise if it's not a permission error or after retries

    click.echo(
        f"Failed to remove directory {path} after {retries} attempts due to PermissionError.",
        err=True,
    )
    raise PermissionError(
        f"Could not remove directory {path} after multiple attempts. Manual intervention may be required."
    )


def _clear_directory_contents(target_dir: Path, exclusions: list[Path], verbose: bool = False):
    """
    Clears the contents of target_dir, excluding paths in the exclusions list.
    Exclusions should be resolved paths.
    """
    if not target_dir.is_dir():
        return

    # Create a set of resolved exclusion paths for quick lookup
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
            _remove_directory_robustly(
                item, verbose=verbose
            )  # Recursively remove subdirectories robustly


def _create_snapshot_logic(source_path: Path, output_dir: Path, verbose: bool = False):
    """
    Core logic to create a .7z snapshot of the Git repository.
    Factored out for use by the Click command.
    """
    repo_root = get_git_root(source_path)
    if not repo_root:
        raise ValueError(
            f"'{source_path}' is not a valid Git repository or not within one."
        )

    if verbose:
        click.echo(f"Detected Git repository root: {repo_root}")

    # Determine app_name from the repository root directory name
    app_name = repo_root.name

    # Get initial .gitignore patterns
    gitignore_patterns = parse_gitignore(repo_root, verbose=verbose)
    # Create a mutable list to hold all patterns for the PathSpec
    all_patterns_for_spec = list(gitignore_patterns)

    # Automatically exclude the output directory if it's within the repository
    try:
        abs_output_dir = output_dir.resolve()
        # Check if output_dir is within repo_root
        if abs_output_dir.is_relative_to(repo_root):
            # Get the path of the output directory relative to the repository root
            relative_output_path_str = str(abs_output_dir.relative_to(repo_root))
            # Add a pattern to exclude the directory itself and its contents
            # The leading '/' ensures it matches from the root of the repository
            exclusion_pattern = f"/{relative_output_path_str}/"
            # Add only if not already present
            if exclusion_pattern not in all_patterns_for_spec:
                all_patterns_for_spec.append(exclusion_pattern)
                if verbose:
                    click.echo(
                        f"Automatically excluding output directory '{relative_output_path_str}' from snapshot."
                    )
    except ValueError:  # output_dir is not relative to repo_root
        pass  # No need to exclude if it's outside the repository

    # Create PathSpec from the combined patterns
    spec = PathSpec.from_lines(GitWildMatchPattern, all_patterns_for_spec)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{app_name}_snapshot_{timestamp}.7z"
    output_filepath = output_dir / output_filename

    # Ensure the output directory exists before attempting to write the file
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        click.echo(
            f"Error: Could not create output directory '{output_dir}': {e}", err=True
        )
        sys.exit(1)

    actual_files_to_archive_relative = []

    # First, explicitly add all contents of the .git directory
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

    # Now, walk through the rest of the repository root and filter files based on .gitignore and dynamic exclusions
    # Ensure not to re-walk .git as it was already handled
    for root, dirs, files in os.walk(repo_root):
        current_path = Path(root)

        # Filter out .git from main walk if it was at repo_root level
        if current_path == repo_root and ".git" in dirs:
            dirs.remove(".git")

        # Filter directories to not descend into ignored ones
        dirs_to_remove = []
        for d in dirs:
            dir_path_abs = current_path / d
            relative_dir_path = dir_path_abs.relative_to(repo_root)
            # gitignore patterns can apply to directories as well.
            # If a directory itself is matched, its contents should be ignored.
            # The trailing os.sep is important for matching directory patterns like 'my_dir/'
            if spec.match_file(str(relative_dir_path) + os.sep) or spec.match_file(
                str(relative_dir_path)
            ):
                dirs_to_remove.append(d)

        for d_remove in dirs_to_remove:
            dirs.remove(
                d_remove
            )  # Modify dirs in-place for os.walk to skip ignored directories

        for f in files:
            file_path_abs = current_path / f
            relative_file_path = file_path_abs.relative_to(repo_root)
            # Check if the file is ignored by .gitignore or dynamic exclusions
            if not spec.match_file(str(relative_file_path)):
                actual_files_to_archive_relative.append(relative_file_path)

    if not actual_files_to_archive_relative:
        click.echo("No files found to compress after applying .gitignore rules.")
        return

    click.echo(
        f"Compressing {len(actual_files_to_archive_relative)} files into {output_filepath}..."
    )
    try:
        # Create the 7z archive by directly writing files from their original locations
        with py7zr.SevenZipFile(output_filepath, "w") as archive:
            for relative_file in actual_files_to_archive_relative:
                full_path = repo_root / relative_file
                # Use app_name as prefix for path inside archive to match user's request for nested structure
                archive.write(full_path, arcname=str(app_name / relative_file))
        click.echo(f"Successfully created snapshot: {output_filepath}")

    except py7zr.Bad7zFile as e:
        click.echo(
            f"Error creating 7z archive: {e}. The file might be corrupted or there was an issue with compression.",
            err=True,
        )
        if output_filepath.exists():
            output_filepath.unlink()  # Clean up incomplete archive
        sys.exit(1)
    except PermissionError:
        click.echo(
            f"Permission denied: Cannot write to {output_filepath}. Check directory permissions.",
            err=True,
        )
        sys.exit(1)
    except Exception as e:
        click.echo(f"An unexpected error occurred during compression: {e}", err=True)
        if output_filepath.exists():
            output_filepath.unlink()  # Clean up incomplete archive
        sys.exit(1)


def _stash_directory_state(directory_to_stash: Path, stash_base_dir: Path, verbose: bool = False) -> Path:
    """
    Creates a temporary 7z snapshot (stash) of the given directory's current state.
    Returns the path to the created stash file.
    """
    if verbose:
        click.echo(f"Creating a temporary stash of '{directory_to_stash}'...")
    try:
        stash_base_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        click.echo(f"Error creating stash directory '{stash_base_dir}': {e}", err=True)
        raise

    # Generate a unique stash filename
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    stash_filename = f"restore_stash_{timestamp}.7z"
    stash_filepath = stash_base_dir / stash_filename

    try:
        # Check if the directory to stash exists and is not empty
        if not directory_to_stash.is_dir() or not any(directory_to_stash.iterdir()):
            if verbose:
                click.echo(f"Directory '{directory_to_stash}' is empty or does not exist. No stash created.")
            return None # Return None if no stash was created

        with py7zr.SevenZipFile(stash_filepath, "w") as archive:
            # Iterate through the directory to stash and add its contents
            for item in directory_to_stash.iterdir():
                # Avoid stashing the stash directory itself if it's inside the target_dir,
                # or the main snapshots directory.
                if item.resolve() == stash_base_dir.resolve() or \
                   item.resolve() == (Path.cwd() / "snapshots").resolve():
                    continue

                # Walk subdirectory and add its contents relative to the original item
                if item.is_file():
                    archive.write(
                        item, arcname=item.name
                    )  # Add file directly to root of archive
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
        click.echo(f"Error creating stash for '{directory_to_stash}': {e}", err=True)
        if stash_filepath and stash_filepath.exists(): # Check if it was partially created
            stash_filepath.unlink()  # Clean up incomplete stash
        raise


def _revert_from_stash(stash_filepath: Path, target_dir: Path, verbose: bool = False):
    """
    Reverts the target directory to the state saved in the stash file.
    This attempts to robustly clear the target_dir and then extract the stash.
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
            (Path.cwd() / "snapshots").resolve(), # Ensure this is absolute
        ]
        _clear_directory_contents(target_dir, exclusions_for_clear, verbose=verbose)

        # Recreate the empty target_dir if needed
        target_dir.mkdir(parents=True, exist_ok=True)

        # Extract stash
        with py7zr.SevenZipFile(stash_filepath, mode="r") as z:
            z.extractall(path=target_dir)
        click.echo(f"Successfully reverted '{target_dir}' from stash.")
    except Exception as e:
        click.echo(f"Error reverting from stash: {e}", err=True)
        click.echo("Manual intervention may be required.", err=True)
        raise  # Re-raise to indicate revert failure


def _remove_dir_if_empty(path: Path, description: str, verbose: bool = False):
    """Removes a directory if it's empty, with a descriptive message."""
    if path.is_dir():
        try:
            # Check if directory is truly empty (contains no files or subdirectories)
            if not any(path.iterdir()):
                path.rmdir()
                if verbose:
                    click.echo(f"Cleaned up empty {description} directory: {path}")
            else:
                if verbose:
                    click.echo(f"{description} directory '{path}' is not empty, skipping removal.")
        except OSError as e:
            click.echo(f"Warning: Could not remove empty {description} directory '{path}': {e}", err=True)
    # No action if path is not a directory or doesn't exist


def _restore_snapshot_logic(snapshot_filepath: Path, output_dir: Path, verbose: bool = False, keep_venv: bool = False):
    resolved_output_dir = output_dir.resolve()

    if not snapshot_filepath.is_file():
        click.echo(f"Error: Snapshot file '{snapshot_filepath}' not found.", err=True)
        sys.exit(1)

    archive_app_name = None
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
                raise ValueError("Snapshot appears to be empty or does not contain any entries.")
            if not archive_app_name:
                click.echo("Warning: Could not determine top-level directory name in snapshot. Contents will be extracted directly into the output directory.")
                archive_app_name = ""
        if verbose and archive_app_name:
            click.echo(f"Detected top-level directory '{archive_app_name}' within snapshot.")
    except Exception as e:
        click.echo(f"Error inspecting snapshot: {e}", err=True)
        click.echo(
            "This might be due to a corrupted snapshot or an outdated 'py7zr' library. Consider updating 'py7zr'.",
            err=True,
        )
        sys.exit(1)

    target_app_path = resolved_output_dir / archive_app_name if archive_app_name else resolved_output_dir

    try:
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        if verbose:
            click.echo(f"Ensured output directory exists: {resolved_output_dir}")
    except Exception as e:
        click.echo(f"Error: Could not ensure output directory '{resolved_output_dir}': {e}", err=True)
        sys.exit(1)

    temp_stashes_base_dir = Path.cwd() / "snapshots/.temp_stashes"
    try:
        temp_stashes_base_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        click.echo(f"Error creating temporary stash directory '{temp_stashes_base_dir}': {e}", err=True)
        sys.exit(1)

    stash_filepath = None
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

        # Add .venv to exclusions if --keep-venv is specified
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
             shutil.rmtree(target_app_path, ignore_errors=True)

        sys.exit(1)

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
    """
    try:
        _create_snapshot_logic(source, output, verbose)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"An unexpected error occurred: {e}", err=True)
        sys.exit(1)


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
    """
    try:
        _restore_snapshot_logic(snapshot_file, output, verbose, keep_venv)
    except click.exceptions.Exit:
        raise
    except Exception as e:
        click.echo(f"An unexpected error occurred during restore: {e}", err=True)
        sys.exit(1)


def main():
    cli()


if __name__ == "__main__":
    main()