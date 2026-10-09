"""
Collect five live traffic snapshots at five-minute intervals.

Each successful snapshot:
- updates the rolling history
- generates M1 forecasts
- commits the refreshed production files

The final snapshot occurs approximately 20 minutes
after the first.
"""

import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INTERVAL_SECONDS = 300
SNAPSHOTS = 5


def commit_outputs():

    subprocess.run(
        [
            "git", "add",
            "data/production/live_history.parquet",
            "data/production/live_forecasts.parquet",
            "docs/data/latest_forecasts.json",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )

    changes = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=PROJECT_ROOT,
    )

    if changes.returncode == 0:
        print("No changes to commit.", flush=True)
        return

    if changes.returncode != 1:
        raise RuntimeError("Could not inspect staged changes.")

    subprocess.run(
        [
            "git", "commit",
            "-m", "Update live Canberra forecasts",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )

    subprocess.run(
        ["git", "push"],
        cwd=PROJECT_ROOT,
        check=True,
    )


def main():

    start = time.monotonic()
    successful = 0

    for i in range(SNAPSHOTS):

        target = start + i * INTERVAL_SECONDS
        wait = max(0, target - time.monotonic())

        if wait:
            print(
                f"Waiting {wait:.0f}s for next collection...",
                flush=True,
            )
            time.sleep(wait)

        print(
            f"\n{'=' * 50}\n"
            f"SNAPSHOT {i + 1}/{SNAPSHOTS}\n"
            f"{'=' * 50}",
            flush=True,
        )

        result = subprocess.run(
            [
                sys.executable,
                "-u",
                "src/live/update_forecasts.py",
            ],
            cwd=PROJECT_ROOT,
        )

        if result.returncode != 0:
            print(
                "Collection failed; continuing to next slot.",
                flush=True,
            )
            continue

        successful += 1

        try:
            commit_outputs()
        except subprocess.CalledProcessError as exc:
            print(
                f"Git commit/push failed: {exc}",
                flush=True,
            )
            raise

    print(
        f"\nBurst complete: {successful}/{SNAPSHOTS} "
        "successful collections.",
        flush=True,
    )

    if successful == 0:
        raise RuntimeError("No snapshots collected.")


if __name__ == "__main__":
    main()

  