# src/git_snapshot/core.py
import datetime
import os
import tempfile
from pathlib import Path

import click
import py7zr
from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern

from git_snapshot.exceptions import GitSnapshotException
from git_snapshot.utils import (
    _clear_directory_contents,
    _get_archive_app_name,
    _remove_dir_if_empty,
    _remove_directory_robustly,
    _revert_from_stash,
    _stash_directory_state,
    get_git_root,
    parse_gitignore,
)


def _create_snapshot_logic(source_path: Path, output_dir: Path, verbose: bool = False):
    """
    Core logic to create a .7z snapshot of the Git repository.
    This function finds the Git repository root, applies .gitignore rules
    (including automatically excluding the output directory if it's inside the repo),
    and compresses the relevant files into a .7z archive.

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

    actual_files_to_archive_relative: list[Path] = []

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
        raise GitSnapshotException(
            f"An unexpected error occurred during compression: {e}"
        ) from e


def _restore_snapshot_logic(
    snapshot_filepath: Path,
    output_dir: Path,
    verbose: bool = False,
    keep_venv: bool = False,
):
    """
    Core logic to restore a .7z snapshot to a local directory.
    This function handles stashing the current state in a temporary filesystem,
    clearing the target directory, extracting the snapshot, and attempting to revert on failure.

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
        raise GitSnapshotException(
            f"Error: Snapshot file '{snapshot_filepath}' not found."
        )

    archive_app_name = _get_archive_app_name(snapshot_filepath, verbose)
    target_app_path = (
        resolved_output_dir / archive_app_name
        if archive_app_name
        else resolved_output_dir
    )

    try:
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        if verbose:
            click.echo(f"Ensured output directory exists: {resolved_output_dir}")
    except Exception as e:
        raise GitSnapshotException(
            f"Error: Could not ensure output directory '{resolved_output_dir}': {e}"
        ) from e

    stash_filepath: Path | None = None
    # Use TemporaryDirectory for the stash base directory
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_stash_base_dir = Path(temp_dir_str)
        try:
            if target_app_path.is_dir():
                if verbose:
                    click.echo(
                        f"Stashing existing contents of '{target_app_path}' in temporary location..."
                    )
                # Pass the temporary directory path to _stash_directory_state
                stash_filepath = _stash_directory_state(
                    target_app_path, temp_stash_base_dir, verbose=verbose
                )
                if stash_filepath:
                    if verbose:
                        click.echo(f"Temporary stash created at: {stash_filepath}")
                else:
                    if verbose:
                        click.echo(
                            f"'{target_app_path}' is empty or does not exist, no stash created."
                        )
            else:
                if verbose:
                    click.echo(
                        f"Target application directory '{target_app_path}' does not exist, no stash needed."
                    )

            # Exclusions for clearing should now only include the main snapshots directory
            # as the temporary stash directory is outside the main target directory's scope
            # and managed by `tempfile`.
            exclusions_for_clear = [(Path.cwd() / "snapshots").resolve()]

            if keep_venv:
                venv_path_in_target = target_app_path / ".venv"
                if venv_path_in_target.is_dir():
                    exclusions_for_clear.append(venv_path_in_target.resolve())
                    if verbose:
                        click.echo(
                            f"Keeping existing .venv directory at {venv_path_in_target}"
                        )

            if target_app_path.is_dir():
                if verbose:
                    click.echo(
                        f"Clearing existing contents of '{target_app_path}' before restoration..."
                    )
                _clear_directory_contents(
                    target_app_path, exclusions_for_clear, verbose=verbose
                )
            else:
                if verbose:
                    click.echo(
                        f"'{target_app_path}' does not exist, no need to clear before extraction."
                    )

            target_app_path.mkdir(parents=True, exist_ok=True)

            click.echo(
                f"Restoring snapshot '{snapshot_filepath}' to '{resolved_output_dir}'..."
            )

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
                    click.echo(
                        "Manual intervention may be required to restore previous state.",
                        err=True,
                    )
            else:
                click.echo(
                    "No stash found or stash creation failed, cannot revert automatically.",
                    err=True,
                )

            if target_app_path.is_dir() and target_app_path.exists():
                click.echo(
                    f"Cleaning up partially extracted files at: {target_app_path}",
                    err=True,
                )
                _remove_directory_robustly(target_app_path, verbose=verbose)

            # Re-raise as GitSnapshotException to exit cleanly via Click
            raise GitSnapshotException(
                "Restoration failed, see logs above for details."
            ) from e

    # The temporary directory (temp_stash_base_dir) is automatically cleaned up here
    # when the 'with tempfile.TemporaryDirectory()' block exits, regardless of success or failure.

    # Only clean up the main 'snapshots' directory if it's empty after all operations.
    snapshots_dir = Path.cwd() / "snapshots"
    _remove_dir_if_empty(snapshots_dir, "snapshots", verbose=verbose)
