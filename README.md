# Git Snapshot CLI

A command-line interface (CLI) application designed to create clean snapshots of local Git repositories, respecting `.gitignore` rules and packaging the relevant files into a `.7z` archive. This tool aims to reduce the manual effort involved in creating portable repository snapshots.

## Features

  * **Repository Snapshot Creation**: Generates a `.7z` archive of the current local Git repository.

  * **`.gitignore` Compliance**: Reads and respects the **root** `.gitignore` file to exclude specified paths, ensuring only relevant files are included.

  * **Dynamic Naming Convention**: Snapshots are automatically named `[repository_name]_snapshot_[YYYYMMDD]_[HHMMSS].7z`.

  * **Command Line Interface**: Easy to use via the command line with optional arguments for source and output directories.

  * **Efficient Compression**: Utilizes `.7z` compression for efficient storage and transfer.

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

The `git-snapshot` command can be invoked with optional arguments:

```bash
git-snapshot [arguments]
```

### Arguments

  * `--source <path>`: (Optional) Path to the local Git repository you want to snapshot. Defaults to the current working directory (`.`).

  * `--output <path>`: (Optional) Directory where the generated `.7z` file will be saved. Defaults to the current working directory (`.`).

### Examples

1.  **Snapshot the current directory**:

    ```bash
    git-snapshot
    ```

    This will create a `.7z` archive of the Git repository in your current working directory and save it to the same directory.

2.  **Snapshot a specific repository**:

    ```bash
    git-snapshot --source /path/to/my/awesome-project
    ```

    This will create a snapshot of `awesome-project` and save it in your current working directory.

3.  **Snapshot and save to a different location**:

    ```bash
    git-snapshot --source /path/to/my/awesome-project --output /tmp/backups
    ```

    This will create a snapshot of `awesome-project` and save it to `/tmp/backups`.

## .gitignore Compliance

The tool reads the `.gitignore` file located in the **root** of the Git repository. Files and directories matching the patterns specified in this `.gitignore` will be excluded from the snapshot. If no `.gitignore` file is found in the repository root, the snapshot will include all files.

## Output Naming Convention

The generated `.7z` file will follow this format:

`[repository_name]_snapshot_[YYYYMMDD]_[HHMMSS].7z`

  * `[repository_name]`: The name of the root directory of the Git repository.

  * `[YYYYMMDD]`: The current date (e.g., `20250720`).

  * `[HHMMSS]`: The current time (e.g., `143000`).

**Example**: If your repository is named `my_cool_repo`, a snapshot might be named `my_cool_repo_snapshot_20250720_143000.7z`.

## Error Handling

The application includes robust error handling for common scenarios:

  * **Invalid Repository Paths**: If the `--source` path is not a valid Git repository, an error will be reported.

  * **Missing `.gitignore`**: If the root `.gitignore` file is missing, the process will continue without applying any exclusions.

  * **Permission Issues**: Errors related to file permissions (e.g., inability to read files or write the archive) will be reported.

  * **Insufficient Disk Space**: While not explicitly checked, system errors for disk space will propagate.

## Future Considerations (Post-MVP)

  * Support for additional compression formats (e.g., `.zip`, `.tar.gz`).

  * Ability to include specific files/directories not covered by the `.gitignore`.

  * Interactive mode for guiding users through snapshot creation.

  * Pre-commit hook integration for automated snapshot creation.

## License

This project is licensed under the MIT License - see the LICENSE file for details.