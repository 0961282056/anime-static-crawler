"""Verify the public site without crawler, image or write credentials."""

import argparse
import os
from pathlib import Path

from services.production_verification import wait_for_production
from services.settings import ProjectPaths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("audit-output/production.json")
    )
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 900:
        parser.error("timeout must be between 1 and 900 seconds")
    result = wait_for_production(
        ProjectPaths.from_environment(),
        args.expected_commit,
        args.output,
        timeout=args.timeout,
    )
    print(
        f"Production: {result['status']} ({result.get('deployed_commit', 'unknown')})"
    )
    if os.getenv("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
            handle.write(f"status={result['status']}\n")


if __name__ == "__main__":
    main()
