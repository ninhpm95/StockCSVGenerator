"""ETF cleanser entry point: preflight, explicit step flow, and publication.

Invoked by the project main.py when cleanser mode is selected. Build all
outputs in a private staging directory, validate them, and publish only if
no input failed. Inputs and the previous successful output remain available
when processing fails. Business logic lives in the sibling modules.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
import sys
import traceback
import uuid
from dataclasses import replace
from pathlib import Path

from . import steps
from .config import EXCHANGES_PATH, Config, DATA_DIR, LOOKUP_PATH, OUTPUT_DIR
from .reporting import emit
from .reporting import write_diagnostics
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
            emit(f"ignoring unsupported file: {path.name}")
    if not files:
        emit(f"WARNING: no csv/xlsx files in {input_dir}")
    return files


def publish_output(staged: Path, destination: Path, backup: Path) -> None:
    """Replace a completed run, restoring the previous directory on failure."""
    if backup.resolve().parent != destination.resolve().parent or not backup.name.startswith(".cleanser-previous-"):
        raise ValueError("unsafe backup path")
    if backup.exists():
        raise FileExistsError(f"backup already exists: {backup}")
    if destination.exists():
        destination.rename(backup)
    try:
        staged.rename(destination)
    except Exception:
        if backup.exists():
            backup.rename(destination)
        raise
    if backup.exists():
        # Only remove our unique sibling backup, after publication succeeded.
        try:
            shutil.rmtree(backup)
        except OSError:
            emit(f"Previous output retained for recovery at {backup}")


def run_step(step_fn, jobs: list[Job], cfg: Config, header: bool = True, quiet: bool = False,
             enabled: bool = True) -> None:
    if not enabled:
        emit(f"=== {step_fn.__name__} skipped (disabled) ===")
        return
    if header:
        emit(f"=== {step_fn.__name__} ===")
    for job in jobs:
        if job.error:
            continue
        try:
            step_fn(job, cfg)
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            if not quiet:
                emit(f"ERROR {job.path.name}: step failed, file will be skipped ({job.error})")
            if cfg.verbose:
                traceback.print_exc()


def print_summary(jobs: list[Job], check_enabled: bool = True) -> int:
    """One closing section: counts, then failed files, then files whose checks
    found something to review. Returns the exit code (1 if any file failed)."""
    failed = [j for j in jobs if j.error]
    review = [j for j in jobs if not j.error and j.findings]
    passed = len(jobs) - len(failed) - len(review)

    if check_enabled:
        emit(f"{passed} files without validation findings, {len(review)} to review, {len(failed)} failed")
    else:
        emit(f"{len(jobs) - len(failed)} files processed, {len(failed)} failed (checks disabled)")
    if failed:
        emit(f"\n{len(failed)} failed:")
        for job in failed:
            emit(f"-- {job.path.name}: {job.error}")
    if review:
        emit(f"\n{len(review)} to review:")
        for job in review:
            emit(f"-- {job.path.name}")
            for line in job.findings:
                emit(f"   {line}")
    return 1 if failed else 0


# ------------------------------------------------------------------- flow --
def run(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Cleanser for raw ETF holdings files")
    parser.add_argument("--input", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--lookup", type=Path, default=LOOKUP_PATH)
    parser.add_argument("--exchanges", type=Path, default=EXCHANGES_PATH)
    parser.add_argument("--verbose", action="store_true",
                        help="print a traceback when a step fails")
    args = parser.parse_args(argv)

    cfg = Config(input_dir=args.input, output_dir=args.output, lookup_path=args.lookup,
                 exchanges_path=args.exchanges, verbose=args.verbose)
    check_folders(cfg)

    files = find_input_files(cfg.input_dir)
    if not files:
        return
    jobs = [Job(path=path) for path in files]
    stems = [p.stem.casefold() for p in files]
    if len(set(stems)) != len(stems):
        sys.exit("ERROR: input files have duplicate output stems")
    cfg.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cleanser-", dir=cfg.output_dir.parent) as work:
        work_dir = Path(work).resolve()
        # TemporaryDirectory owns only this newly created sibling directory.
        if work_dir.parent != cfg.output_dir.parent.resolve():
            raise ValueError("staging directory escaped the output parent")
        staged = work_dir / "output"
        staged.mkdir()
        exit_code = process_jobs(jobs, replace(cfg, output_dir=staged))
        if exit_code == 0:
            backup = cfg.output_dir.parent / f".cleanser-previous-{uuid.uuid4().hex}"
            publish_output(staged, cfg.output_dir, backup)
            emit(f"Published {len(jobs)} files to {cfg.output_dir}")
            emit(f"Diagnostics: {cfg.output_dir / 'diagnostics'}")
        else:
            emit("Run failed; the previous output was retained and this run was not published")
    sys.exit(exit_code)


def process_jobs(jobs: list[Job], cfg: Config) -> int:
    """Build and validate one complete run in its private staging directory."""
    run_step(steps.step010_load, jobs, cfg, quiet=True)
    failed = [j for j in jobs if j.error]
    emit(f"Loaded {len(jobs) - len(failed)} files, {len(failed)} failed to load"
        + (":" if failed else ""))
    for job in failed:
        emit(f"- {job.path.name}: {job.error}")
    run_step(steps.step020_last_marker, jobs, cfg)
    run_step(steps.step030_header, jobs, cfg)
    run_step(steps.step040_columns, jobs, cfg)
    run_step(steps.step041_manual_updates, jobs, cfg, enabled=cfg.enable_manual_updates)
    run_step(steps.step090_drop_incomplete, jobs, cfg)
    run_step(steps.step050_exchange, jobs, cfg, enabled=cfg.enable_refresh_exchanges)
    run_step(steps.step060_unify_exchange_names, jobs, cfg, enabled=cfg.enable_unify_exchange_names)
    run_step(steps.step070_refresh_from_isin, jobs, cfg, enabled=cfg.enable_refresh_tickers)
    run_step(steps.step080_isin_backfill, jobs, cfg, enabled=cfg.enable_refresh_isin)
    changed = [j for j in jobs if not j.error and j.dropped_rows]
    total = len([j for j in jobs if not j.error])
    emit(f"{len(changed)}/{total} files had rows dropped, "
          f"{sum(j.dropped_rows for j in changed)} row(s) in total")
    run_step(steps.step100_weights, jobs, cfg, enabled=cfg.enable_calc_weights)
    run_step(steps.step110_sort_by_weight, jobs, cfg, enabled=cfg.enable_sort_by_weight)
    run_step(steps.step130_check, jobs, cfg, enabled=cfg.enable_check)
    run_step(steps.step120_write, jobs, cfg, header=False)
    written = sum(1 for j in jobs if not j.error)
    emit(f"Prepared {written}/{len(jobs)} output files")
    write_diagnostics(jobs, cfg.output_dir)
    return print_summary(jobs, check_enabled=cfg.enable_check)
