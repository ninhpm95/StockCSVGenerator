from constants import FILE_NAME, SPEED, MODE, UPDATE_FINANCIALS, FILTER_ETFS
from tasks.financials_update.run import run as run_financials_update
from tasks.etfs_screening.run import run as run_etfs_screening


def main():
  if MODE == UPDATE_FINANCIALS:
    run_financials_update(FILE_NAME, SPEED, MODE)
    return
  
  if MODE == FILTER_ETFS:
    run_etfs_screening()
    return


if __name__ == "__main__":
  main()
