from constants import FILE_NAME, SPEED, MODE, UPDATE_FINANCIALS, FILTER_ETFS
from tasks.financials.financials_update import run as run_financials_update
from tasks.screening.etf_filter import run as run_etfs_screening


def main():
  if MODE == UPDATE_FINANCIALS:
    run_financials_update(FILE_NAME, SPEED, MODE)
    return
  if MODE == FILTER_ETFS:
    run_etfs_screening()
    return

if __name__ == "__main__":
  main()
