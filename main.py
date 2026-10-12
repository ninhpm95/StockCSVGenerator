from constants import MODE, UPDATE_FINANCIALS, AGGREGATE_ETFS, SCREEN_ETFS, CLEANSE_ETFS, ENRICH_STOCKS, EXTRACT_TICKERS, FILE_NAME, SPEED
from logging_config import configure_logging

# Switch for tasks other than the ETF cleanser. True writes one timestamped log file per
# run (see logging_config.py) capturing INFO+ records from whichever task
# actually runs. Off by default -- each task's own terminal summary is
# normally enough; flip this on only while debugging. The cleanser uses
# ENABLE_CHECK in tasks/etf_cleanser/config.py instead.
ENABLE_LOG_FILE = False


# Each runner imports its task module lazily, inside the function body,
# rather than at the top of the file. Only one task ever runs per
# invocation (see MODE in constants.py), so importing all six up front
# would mean every run pays the import cost -- and needs the
# dependencies -- of the five tasks that don't run.
def _run_update_financials():
    from tasks.financials_update.run import run
    run(FILE_NAME, SPEED)


def _run_aggregate_etfs():
    from tasks.etf_aggregator.run import run
    run()


def _run_screen_etfs():
    from tasks.etf_screening.run import run
    run()


def _run_cleanse_etfs():
    from tasks.etf_cleanser.run import run
    run()


def _run_enrich_stocks():
    from tasks.stock_enriching.run import run
    run()


def _run_extract_tickers():
    from tasks.ticker_extractor.run import run
    run()


TASK_RUNNERS = {
    UPDATE_FINANCIALS: _run_update_financials,
    AGGREGATE_ETFS: _run_aggregate_etfs,
    SCREEN_ETFS: _run_screen_etfs,
    CLEANSE_ETFS: _run_cleanse_etfs,
    ENRICH_STOCKS: _run_enrich_stocks,
    EXTRACT_TICKERS: _run_extract_tickers,
}


def main():
    enable_log_file = ENABLE_LOG_FILE
    log_dir = None
    if MODE == CLEANSE_ETFS:
        from tasks.etf_cleanser.config import ENABLE_CHECK, LOGS_DIR
        enable_log_file = ENABLE_CHECK
        log_dir = LOGS_DIR
    log_path = configure_logging(enable_log_file, log_dir=log_dir, detailed=MODE == CLEANSE_ETFS)
    try:
        try:
            runner = TASK_RUNNERS[MODE]
        except KeyError:
            raise ValueError(f"Unrecognized MODE: {MODE!r}. Check constants.py / fields.py.") from None
        runner()
    finally:
        # log_path is returned unconditionally when file logging is on,
        # but the FileHandler uses delay=True so the file itself is only
        # created the moment something is actually logged. Checking
        # .exists() here (after the task has run) avoids announcing a log
        # file for tasks that end up logging nothing.
        if log_path is not None and log_path.exists():
            print(f"Full log: {log_path.resolve()}")


if __name__ == "__main__":
    main()
