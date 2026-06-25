"""wiki-status CLI entry point: argument parsing and dispatch."""
import sys

from .status import cmd_status


def main():
    args = sys.argv[1:]
    project_dir = None
    for a in args:
        if a in ("-h", "--help", "help"):
            print("wiki-status — Conductor wiki health metrics (read-only)")
            print()
            print("Usage: wiki-status [project-dir]")
            print("       project-dir defaults to the current working directory.")
            sys.exit(0)
        if not a.startswith("--"):
            project_dir = a

    cmd_status(project_dir or ".")
