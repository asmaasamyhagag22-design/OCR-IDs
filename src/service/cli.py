"""The competition CLI: an image folder in, a CSV out.

    python -m src.service.cli INPUT_DIR --output readings.csv [--workers N]

Columns: image_name, reading, margin, admitted, fixes_triggered, reason_code.

- The pipeline is the service's (src/service/recognizer.py), byte for byte.
- It always writes a reading when one exists, and never abstains for low
  confidence. A reading that F1v's validity gate refused (structurally
  impossible, so certainly wrong) is still written, with admitted false.
- Every regular file under INPUT_DIR is read, whatever its extension: the format
  comes from the bytes. Files are sorted by their relative path, and the output
  follows that order whatever the worker count.
- --workers N runs a process pool, one model bundle per process. With N > 1,
  TensorFlow, Paddle, OpenCV and OpenMP threads are each capped at
  max(1, cpu_count // N), so the CPUs are not oversubscribed.
- Each worker runs the golden self-test before its first image, and the run
  stops if it fails: a model that does not reproduce is not used.
- One image failing never stops the run: its row gets reason_code
  UNDECODABLE, IMAGE_TOO_LARGE (only with --max-megapixels) or PROCESSING_ERROR.
  A worker that crashes is replaced; an image that crashes a worker twice gets
  PROCESSING_ERROR and the run goes on.
- Core dumps are off, so a native crash writes no image or reading to disk.
- Nothing is logged about an image but counts: no names, no readings.
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing
import os
import sys
import time
from collections.abc import Iterator
from concurrent.futures import Executor
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .recognizer import Recognition, Recognizer

COLUMNS = ("image_name", "reading", "margin", "admitted", "fixes_triggered", "reason_code")
REASON_UNDECODABLE = "UNDECODABLE"
REASON_PROCESSING_ERROR = "PROCESSING_ERROR"
REASON_IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
#: crash rows written in a row with no image processed in between: beyond this the workers
#: cannot start at all (a failing golden self-test), and the run stops instead of marking every row
MAX_CONSECUTIVE_CRASH_ROWS = 2

_RECOGNIZER: Recognizer | None = None


def list_images(root: Path) -> list[tuple[str, Path]]:
    """(relative posix name, path) for every regular, non-hidden file under root, sorted by name."""
    root = Path(root)
    found = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in filenames:
            if name.startswith("."):
                continue
            path = Path(current) / name
            if path.is_file():
                found.append((path.relative_to(root).as_posix(), path))
    return sorted(found, key=lambda item: item[0])


def threads_for(workers: int, cpus: int | None = None) -> int | None:
    """None (library defaults) for one worker; otherwise an even share of the CPUs."""
    if workers <= 1:
        return None
    return max(1, (cpus or os.cpu_count() or 1) // workers)


def init_worker(threads: int | None, self_test: bool = True, max_megapixels: float | None = None) -> None:
    """Load the models once in this process, then prove they reproduce."""
    global _RECOGNIZER
    from .hardening import no_core_dumps
    from .runtime import configure_threads

    no_core_dumps()
    configure_threads(threads)
    from .recognizer import Recognizer

    recognizer = Recognizer(max_megapixels=max_megapixels)
    recognizer.load()
    if self_test:
        from . import golden

        golden.run(recognizer.runtime)
    _RECOGNIZER = recognizer


def row_for(name: str, recognition: Recognition) -> dict[str, str]:
    """The output row for one image. For a card F1v's gate kept out, it writes the refused reading
    and its margin with admitted=false (the service never returns that reading)."""
    reading, margin = recognition.reading, recognition.margin
    if not recognition.admitted and recognition.gated_reading:
        reading, margin = recognition.gated_reading, recognition.gated_margin
    return {
        "image_name": name,
        "reading": reading or "",
        "margin": "" if margin is None or not reading else f"{float(margin):.6f}",
        "admitted": "true" if recognition.admitted else "false",
        "fixes_triggered": ";".join(recognition.fixes),
        "reason_code": recognition.reason_code or "",
    }


def empty_row(name: str, reason: str) -> dict[str, str]:
    """The output row for an image that could not be read at all, with its reason code."""
    return {
        "image_name": name,
        "reading": "",
        "margin": "",
        "admitted": "false",
        "fixes_triggered": "",
        "reason_code": reason,
    }


def process_one(item: tuple[str, str]) -> dict[str, str]:
    """Read one (name, path) item in a worker. One image's failure is a row, never a stopped run;
    a governance refusal is raised, and stops the run."""
    name, path = item
    from src.governance import guard

    from .recognizer import ImageTooLarge, UndecodableImage

    try:
        guard.check_card_read(path)
        guard.check_card_read(Path(path).resolve())  # a renamed symlink to a blocked card is refused too
        payload = Path(path).read_bytes()
        return row_for(name, cast("Recognizer", _RECOGNIZER).recognize(payload))
    except UndecodableImage:
        return empty_row(name, REASON_UNDECODABLE)
    except ImageTooLarge:
        return empty_row(name, REASON_IMAGE_TOO_LARGE)
    except guard.GovernanceViolation:
        raise
    except Exception:  # noqa: BLE001 - one image never stops the run
        return empty_row(name, REASON_PROCESSING_ERROR)


def _kill(executor: Executor) -> None:
    """Stop a pool now: cancel what is queued and terminate what is running."""
    executor.shutdown(wait=False, cancel_futures=True)
    # `_processes` is ProcessPoolExecutor's private map: the standard library has no public way to
    # terminate a running worker. getattr keeps this safe if the name ever goes.
    for process in list(getattr(executor, "_processes", {}).values()):
        process.terminate()


def ordered_rows(
    items: list[tuple[str, str]], workers: int, threads: int | None, max_megapixels: float | None
) -> Iterator[dict[str, str]]:
    """Rows in input order. A worker that dies (a native crash) is replaced and the run goes on.

    When the pool breaks, it restarts at the first row not yet written. If the pool
    breaks again with that same row first in line, that row is written as
    PROCESSING_ERROR (a crash row) and the run moves past it. If a third crash row
    would follow two others with no image processed in between, the workers cannot
    start at all (a failing golden self-test, say), and the run stops.
    """
    if workers <= 1:
        init_worker(threads, True, max_megapixels)
        yield from map(process_one, items)
        return
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool

    context = multiprocessing.get_context("spawn")
    start, broke_at, crash_rows = 0, None, 0
    while start < len(items):
        executor = ProcessPoolExecutor(
            workers, mp_context=context, initializer=init_worker, initargs=(threads, True, max_megapixels)
        )
        try:
            for row in executor.map(process_one, items[start:], chunksize=1):
                yield row
                start += 1
                crash_rows = 0  # an image went through: the workers do start
            executor.shutdown(wait=True)
        except BrokenProcessPool:
            _kill(executor)
            if broke_at != start:
                broke_at = start  # first break with this row in front: retry it
                continue
            if crash_rows >= MAX_CONSECUTIVE_CRASH_ROWS:
                raise RuntimeError("the worker pool fails before any image: see the workers' logs") from None
            yield empty_row(items[start][0], REASON_PROCESSING_ERROR)
            start += 1
            crash_rows += 1
            broke_at = None
        except BaseException:
            _kill(executor)
            raise


def _peak_rss_mb() -> dict[str, float]:
    try:
        import resource
    except ImportError:  # Windows
        return {}
    return {
        "peak_rss_mb_this_process": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "peak_rss_mb_largest_worker": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1),
    }


def check_output_path(output: Path) -> None:
    """Readings are card-level material: inside this repository they belong under the ignored artifacts/."""
    resolved = Path(output).resolve()
    if PROJECT_ROOT in resolved.parents and (PROJECT_ROOT / "artifacts") not in resolved.parents:
        raise SystemExit(
            "--output is inside the repository but outside artifacts/: write readings "
            "under artifacts/ or outside the repository"
        )


def run(
    input_dir: Path,
    output: Path,
    workers: int = 1,
    threads: int | None = None,
    cycle_to: int | None = None,
    progress_every: int = 100,
    max_megapixels: float | None = None,
) -> dict[str, object]:
    """Read every image in `input_dir` into the CSV `output`; return the run's aggregate summary."""
    check_output_path(output)
    items = [(name, str(path)) for name, path in list_images(input_dir)]
    if cycle_to:
        # benchmarking only: repeat the sorted list to `cycle_to` entries, each named <name>#<round>
        base, items = items, []
        for k in range(cycle_to):
            name, path = base[k % len(base)]
            items.append((f"{name}#{k // len(base)}" if k >= len(base) else name, path))
    if threads is None:
        threads = threads_for(workers)
    if workers > 1:
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            os.environ[name] = str(threads)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    counts = {
        "images": 0,
        "admitted": 0,
        "with_reading": 0,
        REASON_UNDECODABLE: 0,
        REASON_PROCESSING_ERROR: 0,
        REASON_IMAGE_TOO_LARGE: 0,
    }
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in ordered_rows(items, workers, threads, max_megapixels):
            writer.writerow(row)
            counts["images"] += 1
            counts["admitted"] += row["admitted"] == "true"
            counts["with_reading"] += bool(row["reading"])
            if row["reason_code"] in counts:
                counts[row["reason_code"]] += 1
            if progress_every and counts["images"] % progress_every == 0:
                handle.flush()
                print(
                    json.dumps(
                        {
                            "progress": counts["images"],
                            "of": len(items),
                            "elapsed_s": round(time.perf_counter() - started, 1),
                        }
                    ),
                    file=sys.stderr,
                )
    elapsed = time.perf_counter() - started
    return {
        **counts,
        "workers": workers,
        "threads_per_worker": threads,
        "elapsed_s": round(elapsed, 2),
        "images_per_s": round(counts["images"] / elapsed, 4) if elapsed else None,
        "note_elapsed": "includes model loading and each worker's golden self-test",
        **_peak_rss_mb(),
    }


def main(argv: list[str] | None = None) -> int:
    """`python -m src.service.cli`: parse the arguments, run, print the summary."""
    from .hardening import no_core_dumps

    no_core_dumps()
    parser = argparse.ArgumentParser(
        prog="python -m src.service.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--workers", "-w", type=int, default=1)
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="threads per worker (default: library defaults for 1 worker, else cpu_count // N)",
    )
    parser.add_argument(
        "--max-megapixels",
        type=float,
        default=None,
        help="refuse images that decode to more pixels than this (default: no cap, D-051)",
    )
    parser.add_argument("--cycle-to", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        parser.error("INPUT_DIR is not a folder")
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    summary = run(
        args.input_dir, args.output, args.workers, args.threads, args.cycle_to, max_megapixels=args.max_megapixels
    )
    print(json.dumps(summary, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
