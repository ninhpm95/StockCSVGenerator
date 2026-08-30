from constants import FILE_NAME, SPEED, MODE, UPDATE_FINANCIALS, SCREEN_ETFS, AGGREGATE_ETFS, CLEANSE_ETFS, ENRICH_STOCKS, EXTRACT_TICKERS
from tasks.financials_update.run import run as run_financials_update
from tasks.etf_screening.run import run as run_etf_screening
from tasks.etf_aggregator.run import run as run_etf_aggregator
from tasks.etf_holdings_cleanser.run import run as run_etf_holdings_cleanser
from tasks.stock_enriching.run import run as run_stock_enriching
from tasks.ticker_extractor.run import run as run_extract_tickers


def main():
    if MODE == UPDATE_FINANCIALS:
        run_financials_update(FILE_NAME, SPEED, MODE)
        return
    
    if MODE == SCREEN_ETFS:
        run_etf_screening()
        return

    if MODE == AGGREGATE_ETFS:
        run_etf_aggregator()
        return

    if MODE == CLEANSE_ETFS:
        run_etf_holdings_cleanser()
        return

    if MODE == ENRICH_STOCKS:
        run_stock_enriching()
        return

    if MODE == EXTRACT_TICKERS:
        run_extract_tickers()
        return


if __name__ == "__main__":
    main()
