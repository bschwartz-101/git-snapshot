# src/git_snapshot/exceptions.py
import click

class GitSnapshotException(click.ClickException):
    """
    Custom exception for controlled exit with an error message within the Click CLI.
    This allows Click to handle the error message display and exit code gracefully.
    """
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message