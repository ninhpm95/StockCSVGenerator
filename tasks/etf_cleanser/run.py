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
    050 weights         compute weight when missing
    060 isin_backfill   fill missing ISIN from GLOBAL_lookup (ticker+exchange); not for <Ticker>.csv files
    070 refresh_from_isin <Ticker>.csv files only: fix Ticker via ISIN -> GLOBAL_lookup
    080 exchange        fill Exchange from GLOBAL_lookup by ISIN where the row has none
    090 write           Ticker/Name/ISIN/Weight(%)/Exchange csv into the output folder (tickers starting with 0 get a leading ')
    100 check           log-only sanity checks on tickers / ISINs / exchanges / weights (nothing changed)
"""
from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from pathlib import Path

from . import steps
from .config import Config, DATA_DIR, LOOKUP_PATH, OUTPUT_DIR
from .models import Job
from .parsing import SUPPORTED_SUFFIXES


# --------------------------------------------------------------- plumbing --
def check_folders(cfg: Config) -> None:
    """Exit with a message if the input folder or lookup file is missing, or the
    input folder is also the output folder."""
    if not cfg.input_dir.is_dir():
        sys.exit(f"ERROR: input folder not found: {cfg.input_dir}")
    if not cfg.lookup_path.is_file():
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


def run_step(step_fn, jobs: list[Job], cfg: Config) -> None:
    """Run one step over every job that hasn't already failed. A failing
    step marks that job errored; later steps skip it, other files continue."""
    print(f"=== {step_fn.__name__} ===")
    for job in jobs:
        if job.error:
            continue
        try:
            step_fn(job, cfg)
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            print(f"ERROR {job.path.name}: step failed, file will be skipped ({job.error})")
            if cfg.verbose:
                traceback.print_exc()


def print_summary(jobs: list[Job]) -> int:
    """Print one line per file; return the process exit code (1 if any failed)."""
    print("---- summary ----")
    for job in jobs:
        if job.error:
            print(f"{job.path.name}: FAILED ({job.error})")
        else:
            n = len(job.table.rows) if job.table else 0
            print(f"{job.path.name}: OK, {n} holding row(s)")
    return 1 if any(job.error for job in jobs) else 0


# ------------------------------------------------------------------- flow --
def run(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Cleanser for raw ETF holdings files")
    parser.add_argument("--input", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--lookup", type=Path, default=LOOKUP_PATH)
    parser.add_argument("--verbose", action="store_true",
                        help="print a traceback when a step fails")
    args = parser.parse_args(argv)

    cfg = Config(input_dir=args.input, output_dir=args.output, lookup_path=args.lookup,
                 verbose=args.verbose)
    check_folders(cfg)

    files = find_input_files(cfg.input_dir)
    if not files:
        return
    jobs = [Job(path=path) for path in files]

    reset_output_folder(cfg)

    run_step(steps.step010_load, jobs, cfg)
    run_step(steps.step020_last_marker, jobs, cfg)
    run_step(steps.step030_header, jobs, cfg)
    run_step(steps.step040_columns, jobs, cfg)
    run_step(steps.step050_weights, jobs, cfg)
    run_step(steps.step060_isin_backfill, jobs, cfg)
    run_step(steps.step070_refresh_from_isin, jobs, cfg)
    run_step(steps.step080_exchange, jobs, cfg)
    run_step(steps.step090_write, jobs, cfg)
    run_step(steps.step100_check, jobs, cfg)

    sys.exit(print_summary(jobs))
