# git_snapshot.py
import argparse
import datetime
import os
from pathlib import Path

# Using pathspec for .gitignore parsing as it's robust and efficient.
# For 7z compression, py7zr is a suitable and common choice.
from pathspec import PathSpec
from pathspec.patterns import GitWildMatchPattern

# Ensure py7zr is installed for compression
try:
    import py7zr
except ImportError:
    print(
        "Error: 'py7zr' is not installed. Please install it using 'uv pip install py7zr'."
    )
    exit(1)


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


def parse_gitignore(repo_root: Path) -> PathSpec:
    """
    Parses the root .gitignore file and returns a PathSpec object.
    If .gitignore is not found, returns an empty PathSpec.
    """
    gitignore_path = repo_root / ".gitignore"
    if not gitignore_path.is_file():
        print(
            f"Warning: .gitignore not found at {gitignore_path}. Proceeding without exclusions."
        )
        return PathSpec([])

    try:
        with open(gitignore_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return PathSpec.from_lines(GitWildMatchPattern, lines)
    except Exception as e:
        print(
            f"Error reading .gitignore at {gitignore_path}: {e}. Proceeding without exclusions."
        )
        return PathSpec([])


def create_snapshot(source_path: Path, output_dir: Path):
    """
    Creates a .7z snapshot of the Git repository at source_path,
    respecting .gitignore rules, and saves it to output_dir.
    """
    repo_root = get_git_root(source_path)
    if not repo_root:
        raise ValueError(
            f"'{source_path}' is not a valid Git repository or not within one."
        )

    print(f"Detected Git repository root: {repo_root}")

    # Determine app_name from the repository root directory name
    app_name = repo_root.name

    # Parse .gitignore
    spec = parse_gitignore(repo_root)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{app_name}_snapshot_{timestamp}.7z"
    output_filepath = output_dir / output_filename

    actual_files_to_archive_relative = []

    # Walk through the repository root and filter files based on .gitignore
    for root, dirs, files in os.walk(repo_root):
        current_path = Path(root)

        # Filter directories to not descend into ignored ones
        # We iterate a copy of dirs because we're modifying the list in place
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
            # Check if the file is ignored
            if not spec.match_file(str(relative_file_path)):
                actual_files_to_archive_relative.append(relative_file_path)

    if not actual_files_to_archive_relative:
        print("No files found to compress after applying .gitignore rules.")
        return

    print(
        f"Compressing {len(actual_files_to_archive_relative)} files into {output_filepath}..."
    )
    try:
        # Create the 7z archive by directly writing files from their original locations
        with py7zr.SevenZipFile(output_filepath, "w") as archive:
            for relative_file in actual_files_to_archive_relative:
                full_path = repo_root / relative_file
                # archive.write reads the file from full_path and adds it to the archive
                # with the name specified by arcname (the relative path within the archive)
                archive.write(full_path, arcname=str(relative_file))
        print(f"Successfully created snapshot: {output_filepath}")

    except py7zr.Bad7zFile as e:
        print(
            f"Error creating 7z archive: {e}. The file might be corrupted or there was an issue with compression."
        )
        if output_filepath.exists():
            output_filepath.unlink()  # Clean up incomplete archive
    except PermissionError:
        print(
            f"Permission denied: Cannot write to {output_filepath}. Check directory permissions."
        )
    except Exception as e:
        print(f"An unexpected error occurred during compression: {e}")
        if output_filepath.exists():
            output_filepath.unlink()  # Clean up incomplete archive


def main():
    parser = argparse.ArgumentParser(
        description="Create a .7z snapshot of a local Git repository, respecting .gitignore rules."
    )
    parser.add_argument(
        "--source",
        type=str,
        default=".",
        help="Path to the local Git repository. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=".",
        help="Directory to save the generated .7z file. Defaults to the current working directory.",
    )

    args = parser.parse_args()

    source_path = Path(args.source)
    output_dir = Path(args.output)

    # Validate paths
    if not source_path.is_dir():
        print(f"Error: Source path '{source_path}' is not a valid directory.")
        exit(1)

    if not output_dir.is_dir():
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            print(f"Created output directory: {output_dir}")
        except Exception as e:
            print(f"Error: Could not create output directory '{output_dir}': {e}")
            exit(1)

    try:
        create_snapshot(source_path, output_dir)
    except ValueError as e:
        print(f"Error: {e}")
        exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        exit(1)


if __name__ == "__main__":
    main()
