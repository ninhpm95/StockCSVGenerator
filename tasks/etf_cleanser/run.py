"""ETF holdings cleanser - entry point.

    python main.py                       # paths from config.py
    python main.py --input root/data/ETFs --output root/data/cleansed_ETFs

Reads raw ETF holdings files (CSV + XLSX) from the input folder and writes
one tidy CSV per file into the output folder, with columns
Ticker, Name, ISIN, Weight(%), Exchange.
Inputs are opened read-only and never modified; the output folder is wiped
and rebuilt on every run.

main() at the bottom is the flow. Every step's real logic lives in steps.py
(and the modules it calls); the plumbing (folder checks, file discovery,
run_step, summary) sits above it in this file. Add, remove or reorder a step
by editing the run_step(...) calls in main().

    010 load            read csv, or the 保有明細 sheet of an xlsx, into one raw grid
    020 last_marker     keep only rows from the last 'Fund Holdings as of' down
    030 header          find header row, drop metadata above it
    040 columns         resolve candidate columns -> Table
    050 refresh_from_isin correct Ticker via existing ISIN + Exchange -> GLOBAL_lookup (<Ticker>.csv only)
    060 isin_backfill   fill missing ISIN from GLOBAL_lookup (ticker+exchange; all files)
    070 exchange        fill missing Exchange from GLOBAL_lookup using Ticker + ISIN
    080 exchange_short_form convert Exchange values to configured short names
    090 drop_incomplete remove rows with empty Code and Name, or a Name containing a SKIP_NAME_TEXTS entry
    100 weights         compute weight when missing
    110 sort_by_weight  sort holdings by descending weight (missing/invalid last)
    120 write           Ticker/Name/ISIN/Weight(%)/Exchange csv into the output folder (tickers starting with 0 get a leading ')
    130 check           log-only sanity checks on tickers / ISINs / exchanges / weights (nothing changed)
"""
from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from pathlib import Path

from . import steps
from .config import EXCHANGES_PATH, Config, DATA_DIR, LOOKUP_PATH, OUTPUT_DIR
from .models import Job
from .parsing import SUPPORTED_SUFFIXES


# --------------------------------------------------------------- plumbing --
def check_folders(cfg: Config) -> None:
    """Exit with a message if the input folder or lookup file is missing, or the
    input folder is also the output folder."""
    if not cfg.input_dir.is_dir():
        sys.exit(f"ERROR: input folder not found: {cfg.input_dir}")
    needs_lookup = cfg.enable_isin_backfill or cfg.enable_refresh_from_isin or cfg.enable_exchange
    if needs_lookup and not cfg.lookup_path.is_file():
        sys.exit(f"ERROR: lookup file not found: {cfg.lookup_path}")
    if cfg.output_dir.resolve() == cfg.input_dir.resolve():
        sys.exit("ERROR: output folder must differ from the input folder")


def find_input_files(input_dir: Path) -> list[Path]:
    files = []
    for path in sorted(input_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() in SUPPORTED_SUFFIXES:
            files.append(path)
        else:
            print(f"ignoring unsupported file: {path.name}")
    if not files:
        print(f"WARNING: no csv/xlsx files in {input_dir}")
    return files


def reset_output_folder(cfg: Config) -> None:
    """Wipe and recreate the output folder, so a run where every file fails
    never leaves stale output from a previous run behind."""
    if cfg.output_dir.exists():
        shutil.rmtree(cfg.output_dir)    # inputs stay untouched
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"output folder reset: {cfg.output_dir}")


def run_step(step_fn, jobs: list[Job], cfg: Config, header: bool = True, quiet: bool = False,
             enabled: bool = True) -> None:
    if not enabled:
        print(f"=== {step_fn.__name__} skipped (disabled) ===")
        return
    if header:
        print(f"=== {step_fn.__name__} ===")
    for job in jobs:
        if job.error:
            continue
        try:
            step_fn(job, cfg)
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            if not quiet:
                print(f"ERROR {job.path.name}: step failed, file will be skipped ({job.error})")
            if cfg.verbose:
                traceback.print_exc()


def print_summary(jobs: list[Job], check_enabled: bool = True) -> int:
    """One closing section: counts, then failed files, then files whose checks
    found something to review. Returns the exit code (1 if any file failed)."""
    failed = [j for j in jobs if j.error]
    review = [j for j in jobs if not j.error and j.findings]
    passed = len(jobs) - len(failed) - len(review)

    if check_enabled:
        print(f"{passed} checks passed, {len(review)} to review, {len(failed)} failed")
    else:
        print(f"{len(jobs) - len(failed)} files processed, {len(failed)} failed (checks disabled)")
    if failed:
        print(f"\n{len(failed)} failed:")
        for job in failed:
            print(f"-- {job.path.name}: {job.error}")
    if review:
        print(f"\n{len(review)} to review:")
        for job in review:
            print(f"-- {job.path.name}")
            for line in job.findings:
                print(f"   {line}")
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

    reset_output_folder(cfg)

    run_step(steps.step010_load, jobs, cfg, quiet=True)
    failed = [j for j in jobs if j.error]
    print(f"Loaded {len(jobs) - len(failed)} files, {len(failed)} failed to load"
        + (":" if failed else ""))
    for job in failed:
        print(f"- {job.path.name}: {job.error}")
    run_step(steps.step020_last_marker, jobs, cfg)
    run_step(steps.step030_header, jobs, cfg)
    run_step(steps.step040_columns, jobs, cfg)
    run_step(steps.step050_refresh_from_isin, jobs, cfg, enabled=cfg.enable_refresh_from_isin)
    run_step(steps.step060_isin_backfill, jobs, cfg, enabled=cfg.enable_isin_backfill)
    run_step(steps.step070_exchange, jobs, cfg, enabled=cfg.enable_exchange)
    run_step(steps.step080_exchange_short_form, jobs, cfg, enabled=cfg.enable_exchange_short_form)
    run_step(steps.step090_drop_incomplete, jobs, cfg)
    changed = [j for j in jobs if not j.error and j.dropped_rows]
    total = len([j for j in jobs if not j.error])
    print(f"{len(changed)}/{total} files had rows dropped, "
          f"{sum(j.dropped_rows for j in changed)} row(s) in total")
    run_step(steps.step100_weights, jobs, cfg, enabled=cfg.enable_weights)
    run_step(steps.step110_sort_by_weight, jobs, cfg, enabled=cfg.enable_sort_by_weight)
    run_step(steps.step120_write, jobs, cfg, header=False)
    written = sum(1 for j in jobs if not j.error)
    print(f"Written {written}/{len(jobs)} files to {cfg.output_dir}")
    run_step(steps.step130_check, jobs, cfg, enabled=cfg.enable_check)

    sys.exit(print_summary(jobs, check_enabled=cfg.enable_check))
