"""Relay Command Line Interface."""

import argparse
import sys


def main(args: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="relay",
        description="Relay: Crash-resilient, deterministic customer support AI agent",
    )
    parser.add_argument("--version", action="version", version="relay 0.1.0")
    subparsers = parser.add_subparsers(dest="command")

    # worker command
    subparsers.add_parser("worker", help="Start the execution runtime worker")

    # sink command
    subparsers.add_parser("sink", help="Start the external deduplicating sink service")

    parsed_args = parser.parse_args(args)

    if parsed_args.command is None:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
