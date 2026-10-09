
"""
Collect five live traffic snapshots at five-minute intervals.

Forecasting code runs from main.
Production outputs are published to the live-data branch.

Requires the GitHub Actions workflow to prepare a separate
live-data worktree at .live-data before this script runs.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_WORKTREE = PROJECT_ROOT / ".live-data"

INTERVAL_SECONDS = 300
SNAPSHOTS = 5

OUTPUT_FILES = [
    "data/production/live_history.parquet",
    "data/production/live_forecasts.parquet",
    "docs/data/latest_forecasts.json",
]


def git(*args, cwd=DATA_WORKTREE, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
    )


def synchronize_history():
    """
    Load the latest production history from live-data.

    This is called before each collection so that a new runner
    starts with the previously accumulated observations.
    """

    git("fetch", "origin", "live-data")

    # The worktree is dedicated to generated files.
    # Reset it to the latest published production snapshot.
    git("reset", "--hard", "origin/live-data")

    source = DATA_WORKTREE / "data/production/live_history.parquet"
    destination = PROJECT_ROOT / "data/production/live_history.parquet"

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def publish_outputs():
    """
    Copy generated outputs into the live-data worktree
    and publish a new production snapshot.
    """

    for relative_path in OUTPUT_FILES:
        source = PROJECT_ROOT / relative_path
        destination = DATA_WORKTREE / relative_path

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    git("add", *OUTPUT_FILES)

    changes = git(
        "diff", "--cached", "--quiet",
        check=False,
    )

    if changes.returncode == 0:
        print("No production changes to publish.", flush=True)
        return

    if changes.returncode != 1:
        raise RuntimeError("Could not inspect staged changes.")

    git("commit", "-m", "Update live Canberra forecasts")

    # The workflow uses a single concurrency group, so
    # competing workflow runs should not publish simultaneously.
    git("push", "origin", "HEAD:live-data")

    print("Published snapshot to live-data.", flush=True)


def main():
    if not (DATA_WORKTREE / ".git").exists():
        raise RuntimeError(
            "Missing .live-data worktree. "
            "The workflow must prepare it before collection."
        )

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

        synchronize_history()

        # Remove outputs from any previous attempt.
        # The forecasting script must regenerate both files successfully.
        for relative_path in OUTPUT_FILES[1:]:
            output_path = PROJECT_ROOT / relative_path
            output_path.unlink(missing_ok=True)

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

        publish_outputs()
        successful += 1

    print(
        f"\nBurst complete: {successful}/{SNAPSHOTS} "
        "successful collections.",
        flush=True,
    )

    if successful == 0:
        raise RuntimeError("No snapshots collected.")


if __name__ == "__main__":
    main()


