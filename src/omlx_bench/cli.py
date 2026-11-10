"""CLI entry point: `uv run omlx-bench <command>`. Commands are wired in later commits."""
import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog="omlx-bench", description=__doc__)


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
