"""ETF cleanser entry point: preflight, explicit step flow, and publication.

Invoked by the project main.py when cleanser mode is selected. Build all
outputs in a private staging directory, validate them, and publish only if
no input failed. Inputs and the previous successful output remain available
when processing fails. Business logic lives in the sibling modules.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import tempfile
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

from . import steps
from .config import EXCHANGES_PATH, Config, DATA_DIR, LOOKUP_PATH, OUTPUT_DIR
from .reporting import emit, detail
from .reporting import log_diagnostics
from .lookup import get_lookup, get_exchange_names
from .models import Job
from .parsing import SUPPORTED_SUFFIXES


# --------------------------------------------------------------- plumbing --
def check_folders(cfg: Config) -> None:
    """Exit with a message if the input folder or lookup file is missing, or the
    input folder is also the output folder."""
    if not cfg.input_dir.is_dir():
        sys.exit(f"ERROR: input folder not found: {cfg.input_dir}")
    needs_lookup = cfg.enable_refresh_exchanges or cfg.enable_refresh_tickers or cfg.enable_refresh_isin
    if needs_lookup and not cfg.lookup_path.is_file():
        sys.exit(f"ERROR: lookup file not found: {cfg.lookup_path}")
    output = cfg.output_dir.resolve()
    source = cfg.input_dir.resolve()
    if output == source or output in source.parents or source in output.parents:
        sys.exit("ERROR: input and output folders must not contain one another")
    for protected in (cfg.lookup_path, cfg.exchanges_path):
        if output == protected.resolve() or output in protected.resolve().parents:
            sys.exit(f"ERROR: output folder contains a reference file: {protected}")
    if cfg.output_dir.is_symlink():
        sys.exit("ERROR: output folder must not be a symbolic link")
    if cfg.output_dir.exists() and not cfg.output_dir.is_dir():
        sys.exit("ERROR: output path is not a directory")
    get_lookup.cache_clear()
    get_exchange_names.cache_clear()
    if needs_lookup:
        get_lookup(str(cfg.lookup_path), str(cfg.exchanges_path))
    elif cfg.enable_unify_exchange_names or cfg.enable_check:
        get_exchange_names(str(cfg.exchanges_path))


def find_input_files(input_dir: Path) -> list[Path]:
    files = []
    for path in sorted(input_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() in SUPPORTED_SUFFIXES:
            files.append(path)
        else:
            detail(f"INPUT SKIPPED | file={path.name} | reason=unsupported type")
    if not files:
        emit(f"INPUT EMPTY | no supported files in {input_dir}", level=logging.WARNING)
    return files


def rename_with_retry(source: Path, destination: Path) -> None:
    """Allow short-lived Windows sharing locks to clear before giving up."""
    for attempt in range(5):
        try:
            source.rename(destination)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2 * 2 ** attempt)


def publish_output(staged: Path, destination: Path, backup: Path) -> None:
    """Replace a completed run, restoring the previous directory on failure."""
    if backup.resolve().parent != destination.resolve().parent or not backup.name.startswith(".cleanser-previous-"):
        raise ValueError("unsafe backup path")
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    if destination.exists():
        rename_with_retry(destination, backup)
    try:
        rename_with_retry(staged, destination)
    except Exception:
        if backup.exists():
            rename_with_retry(backup, destination)
        raise
    if backup.exists():
        # Only remove our unique sibling backup, after publication succeeded.
        try:
            shutil.rmtree(backup)
        except OSError:
            emit(f"CLEANUP | previous output retained at {backup}", level=logging.WARNING)


def run_step(step_fn, jobs: list[Job], cfg: Config, header: bool = True, quiet: bool = False,
             enabled: bool = True) -> None:
    if not enabled:
        emit(f"STEP SKIPPED | {step_fn.__name__} | reason=disabled")
        return
    started = time.perf_counter()
    processed = failures = skipped = 0
    if header:
        emit(f"STEP START | {step_fn.__name__}")
    for job in jobs:
        if job.error:
            skipped += 1
            continue
        try:
            step_fn(job, cfg)
            processed += 1
        except Exception as exc:
            failures += 1
            job.error = f"{type(exc).__name__}: {exc}"
            if not quiet:
                emit(f"STEP FAILED | {step_fn.__name__} | file={job.path.name} | {job.error}",
                     level=logging.ERROR)
            if cfg.verbose:
                logging.getLogger("tasks.etf_cleanser").exception(
                    "Step %s failed for %s", step_fn.__name__, job.path.name)
    emit(f"STEP DONE | {step_fn.__name__} | files_ok={processed} | failed={failures}"
         f" | skipped={skipped} | elapsed={time.perf_counter() - started:.3f}s",
         level=logging.ERROR if failures else logging.INFO)


def print_summary(jobs: list[Job], check_enabled: bool = True) -> int:
    """Log one closing section: counts, then failed files, then files whose checks
    found something to review. Returns the exit code (1 if any file failed)."""
    failed = [j for j in jobs if j.error]
    review = [j for j in jobs if not j.error and j.findings]
    passed = len(jobs) - len(failed) - len(review)

    if check_enabled:
        emit(f"VALIDATION | clean={passed} | review={len(review)} | failed={len(failed)}",
             level=logging.ERROR if failed else logging.WARNING if review else logging.INFO)
    else:
        emit(f"VALIDATION | checks=disabled | processed={len(jobs) - len(failed)} | failed={len(failed)}",
             level=logging.ERROR if failed else logging.INFO)
    for job in failed:
        emit(f"FAILED | file={job.path.name} | {job.error}", level=logging.ERROR)
    for job in review:
        for line in job.findings:
            emit(f"REVIEW | file={job.path.name} | {line.strip()}", level=logging.WARNING)
    return 1 if failed else 0


# ------------------------------------------------------------------- flow --
def run(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Cleanser for raw ETF holdings files")
    parser.add_argument("--input", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--lookup", type=Path, default=LOOKUP_PATH)
    parser.add_argument("--exchanges", type=Path, default=EXCHANGES_PATH)
    parser.add_argument("--verbose", action="store_true",
                        help="include a traceback in the log when a step fails (requires ENABLE_CHECK)")
    args = parser.parse_args(argv)

    cfg = Config(input_dir=args.input, output_dir=args.output, lookup_path=args.lookup,
                 exchanges_path=args.exchanges, verbose=args.verbose)
    started = time.perf_counter()
    emit(f"RUN START | input={cfg.input_dir.resolve()} | output={cfg.output_dir.resolve()}")
    emit(f"REFERENCES | lookup={cfg.lookup_path.resolve()} | exchanges={cfg.exchanges_path.resolve()}")
    try:
        check_folders(cfg)
    except (Exception, SystemExit) as exc:
        emit(f"PREFLIGHT FAILED | {exc}", level=logging.ERROR)
        raise

    files = find_input_files(cfg.input_dir)
    if not files:
        emit(f"RUN COMPLETE | files=0 | publication=skipped (empty input) | elapsed={time.perf_counter() - started:.3f}s")
        print(f"Processed 0/0 files; no supported input files in {cfg.input_dir.resolve()}")
        return
    jobs = [Job(path=path) for path in files]
    stems = [p.stem.casefold() for p in files]
    if len(set(stems)) != len(stems):
        emit("PREFLIGHT FAILED | input files have duplicate output stems", level=logging.ERROR)
        sys.exit("ERROR: input files have duplicate output stems")
    cfg.output_dir.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=".cleanser-", dir=cfg.output_dir.parent)).resolve()
    if work_dir.parent != cfg.output_dir.parent.resolve():
        raise ValueError("staging directory escaped the output parent")
    retain_staged = False
    try:
        # Only this newly created sibling directory belongs to this run.
        staged = work_dir / "output"
        staged.mkdir()
        exit_code = process_jobs(jobs, replace(cfg, output_dir=staged))
        if exit_code == 0:
            backup = cfg.output_dir.parent / f".cleanser-previous-{uuid.uuid4().hex}"
            try:
                publish_output(staged, cfg.output_dir, backup)
            except OSError:
                retain_staged = True
                logging.getLogger("tasks.etf_cleanser").exception(
                    "Could not publish %s; prepared output retained at %s; backup path: %s",
                    cfg.output_dir, staged, backup)
                emit(f"RUN FAILED | publication=failed | prepared={len(jobs)}"
                     f" | elapsed={time.perf_counter() - started:.3f}s", level=logging.ERROR)
                print(f"Processed {len(jobs)}/{len(jobs)} files; could not publish to {cfg.output_dir.resolve()}.")
                print("Close programs using the output folder and retry.")
                print(f"Prepared files retained at: {staged}")
                if backup.exists():
                    print(f"Previous output retained at: {backup}")
                elif cfg.output_dir.exists():
                    print("Previous output retained.")
                sys.exit(1)
            emit(f"RUN COMPLETE | published={len(jobs)}/{len(jobs)} | failed=0"
                 f" | elapsed={time.perf_counter() - started:.3f}s | output={cfg.output_dir.resolve()}")
            print(f"Processed {len(jobs)}/{len(jobs)} files to {cfg.output_dir.resolve()}")
        else:
            failed = [job for job in jobs if job.error]
            emit(f"RUN FAILED | processed={len(jobs) - len(failed)}/{len(jobs)} | failed={len(failed)}"
                 f" | publication=skipped | previous_output=retained"
                 f" | elapsed={time.perf_counter() - started:.3f}s", level=logging.ERROR)
            print(f"Processed {len(jobs) - len(failed)}/{len(jobs)} files; "
                  f"output not published to {cfg.output_dir.resolve()}. Previous output retained.")
            print("These files failed: " + ", ".join(job.path.name for job in failed))
    finally:
        if not retain_staged:
            try:
                shutil.rmtree(work_dir)
            except OSError:
                emit(f"CLEANUP | temporary files retained at {work_dir}", level=logging.WARNING)
    sys.exit(exit_code)


def process_jobs(jobs: list[Job], cfg: Config) -> int:
    """Build and validate one complete run in its private staging directory."""
    run_step(steps.step010_load, jobs, cfg, quiet=True)
    failed = [j for j in jobs if j.error]
    for job in failed:
        emit(f"LOAD FAILED | file={job.path.name} | {job.error}", level=logging.ERROR)
    run_step(steps.step020_last_marker, jobs, cfg)
    run_step(steps.step030_header, jobs, cfg)
    run_step(steps.step040_columns, jobs, cfg)
    run_step(steps.step041_manual_updates, jobs, cfg, enabled=cfg.enable_manual_updates)
    run_step(steps.step090_drop_incomplete, jobs, cfg)
    changed = [j for j in jobs if not j.error and j.dropped_rows]
    emit(f"CLEANUP | files_changed={len(changed)} | rows_removed={sum(j.dropped_rows for j in changed)}")
    run_step(steps.step050_exchange, jobs, cfg, enabled=cfg.enable_refresh_exchanges)
    run_step(steps.step060_unify_exchange_names, jobs, cfg, enabled=cfg.enable_unify_exchange_names)
    run_step(steps.step070_refresh_from_isin, jobs, cfg, enabled=cfg.enable_refresh_tickers)
    run_step(steps.step080_isin_backfill, jobs, cfg, enabled=cfg.enable_refresh_isin)
    run_step(steps.step100_weights, jobs, cfg, enabled=cfg.enable_calc_weights)
    run_step(steps.step110_sort_by_weight, jobs, cfg, enabled=cfg.enable_sort_by_weight)
    run_step(steps.step130_check, jobs, cfg, enabled=cfg.enable_check)
    run_step(steps.step120_write, jobs, cfg, header=False)
    written = sum(1 for j in jobs if not j.error)
    emit(f"OUTPUT PREPARED | files={written}/{len(jobs)}")
    log_diagnostics(jobs)
    return print_summary(jobs, check_enabled=cfg.enable_check)
