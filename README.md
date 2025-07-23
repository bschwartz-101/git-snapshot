# Git Snapshot CLI

A command-line interface (CLI) application designed to create clean snapshots of local Git repositories, respecting `.gitignore` rules and packaging the relevant files into a `.7z` archive. This tool also provides the ability to restore these snapshots, offering a robust solution for managing portable repository states.

## Features

* **Repository Snapshot Creation**: Generates a `.7z` archive of the current local Git repository.
* **Repository Restore Functionality**: Restores a `.7z` snapshot to a specified local directory.
* **Automatic Stash and Reroll**: Before restoration, the tool automatically stashes the current state of the target directory. If the restoration fails, it attempts to revert to this stashed state to prevent data loss.
* **Intelligent `.git` Directory Handling**: Automatically includes the `.git` directory in snapshots and ensures existing `.git` directories in the target restore location are handled for a clean restoration.
* **`.gitignore` Compliance**: Reads and respects the **root** `.gitignore` file to exclude specified paths, ensuring only relevant files are included in snapshots.
* **Dynamic Naming Convention**: Snapshots are automatically named `[repository_name]_snapshot_[YYYYMMDD]_[HHMMSS].7z`.
* **Command Line Interface**: Easy to use via the command line with dedicated subcommands for `create` and `restore` operations.
* **Efficient Compression**: Utilizes `.7z` compression for efficient storage and transfer.
* **Root Directory Detection in Snapshots**: Intelligently identifies the top-level directory name within the `.7z` archive for correct extraction, placing the repository content as expected.
* **Verbose Output Option**: Control the level of detail in the console output.
* **Option to Keep Virtual Environment**: During restoration, an option is available to prevent the removal of the `.venv` directory.

## Installation

This application uses `uv` for dependency management, which simplifies installation.

1.  **Install `uv`**: If you don't have `uv` installed, you can get it with pip:

    ```bash
    pip install uv
    ```

    Or refer to the official `uv` documentation for other installation methods.

2.  **Install `git-snapshot`**: Navigate to the root directory of the `git-snapshot` project (where `pyproject.toml` is located) and run:

    ```bash
    uv tool install -e .
    ```

    This command installs the `git-snapshot` tool in editable mode, making it available globally via the `git-snapshot` command.

## Usage

The `git-snapshot` CLI uses subcommands for `create` and `restore` operations:

```bash
git-snapshot <command> [arguments]
```

### Commands

  * `create`: Create a .7z snapshot of a local Git repository.
  * `restore`: Restore a .7z snapshot to a local directory.

### Create Command Arguments (`git-snapshot create`)

  * `--source <path>` / `-s <path>`: (Optional) Path to the local Git repository you want to snapshot. Defaults to the current working directory (`.`).
  * `--output <path>` / `-o <path>`: (Optional) Directory where the generated `.7z` file will be saved. Defaults to `./snapshots/`.
  * `--verbose` / `-v`: (Optional) Enable verbose output, showing more details about the operation.

### Restore Command Arguments (`git-snapshot restore`)

  * `<snapshot_file>`: (Required) Path to the `.7z` snapshot file to restore. This is a positional argument.
  * `--output <path>` / `-o <path>`: (Optional) Directory to restore the snapshot to. Defaults to the current working directory (`.`). The content of the snapshot (e.g., `my-repo/`) will be extracted into this directory.
  * `--verbose` / `-v`: (Optional) Enable verbose output, showing more details about the operation.
  * `--keep-venv`: (Optional) Do not remove the `.venv` directory during restoration. **Use with caution as this may lead to dependency mismatches** if the restored code's requirements differ from the existing environment.

### Examples

1.  **Create a snapshot of the current directory**:

    ```bash
    git-snapshot create
    ```

    This will create a `.7z` archive of the Git repository in your current working directory and save it to the `./snapshots/` directory.

2.  **Create a snapshot of a specific repository and save to a different location**:

    ```bash
    git-snapshot create --source /path/to/my/awesome-project --output /tmp/backups
    ```

    This will create a snapshot of `awesome-project` and save it to `/tmp/backups`.

3.  **Restore a snapshot to the current directory**:

    ```bash
    git-snapshot restore ./snapshots/my_repo_snapshot_20250720_143000.7z
    ```

    This will restore the contents of `my_repo_snapshot_20250720_143000.7z` into a directory (e.g., `my_repo/`) inside your current working directory.

4.  **Restore a snapshot to a specific output directory**:

    ```bash
    git-snapshot restore /path/to/backup/my_app_snapshot_20250720_143000.7z --output /new/restore/location
    ```

    This will restore the `my_app` repository into `/new/restore/location/my_app/`.

5.  **Restore a snapshot with verbose output**:

    ```bash
    git-snapshot restore ./snapshots/my_repo_snapshot_20250720_143000.7z --verbose
    ```

6.  **Restore a snapshot, keeping the existing virtual environment**:

    ```bash
    git-snapshot restore ./snapshots/my_repo_snapshot_20250720_143000.7z --keep-venv
    ```

## .gitignore Compliance

The `create` command reads the `.gitignore` file located in the **root** of the Git repository. Files and directories matching the patterns specified in this `.gitignore` will be excluded from the snapshot. If no `.gitignore` file is found in the repository root, the snapshot will include all files.

## Output Naming Convention (for `create` command)

The generated `.7z` file will follow this format:

`[repository_name]_snapshot_[YYYYMMDD]_[HHMMSS].7z`

  * `[repository_name]`: The name of the root directory of the Git repository.
  * `[YYYYMMDD]`: The current date (e.g., `20250720`).
  * `[HHMMSS]`: The current time (e.g., `143000`).

**Example**: If your repository is named `my_cool_repo`, a snapshot might be named `my_cool_repo_snapshot_20250720_143000.7z`.

## Error Handling

The application includes robust error handling for common scenarios:

  * **Invalid Repository Paths**: If the `--source` path for `create` is not a valid Git repository, an error will be reported.
  * **Missing Snapshot File**: If the specified `snapshot_file` for `restore` does not exist, an error will be reported.
  * **Corrupted Snapshot Detection**: Checks for issues with the `.7z` snapshot file during inspection or extraction for `restore`.
  * **Failed Restoration Revert**: If the `restore` process fails, the tool attempts to automatically revert the target directory to its state before the restoration began, helping to prevent data corruption.
  * **Missing `.gitignore`**: If the root `.gitignore` file is missing, the `create` process will continue without applying any exclusions.
  * **Permission Issues**: Errors related to file permissions (e.g., inability to read files, write the archive, or modify directories during restore) will be reported.
  * **Insufficient Disk Space**: While not explicitly checked, system errors for disk space will propagate.

## Future Considerations (Post-MVP)

  * Support for additional compression formats (e.g., `.zip`, `.tar.gz`).
  * Interactive mode for guiding users through snapshot creation/restoration.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
