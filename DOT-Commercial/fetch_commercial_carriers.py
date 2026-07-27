import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils.file_utils as file_utils


def compute_where_clause(date_field, window_months, now):
    """
    Returns a Socrata $where clause filtering date_field to values after
    (now - window_months), formatted as YYYYMMDD text to match the
    dataset's native date field format. Returns None if no filtering
    is configured.
    """
    if not date_field or not window_months:
        return None
    cutoff = now - timedelta(days=30 * window_months)
    cutoff_str = cutoff.strftime("%Y%m%d")
    return "{} > '{}'".format(date_field, cutoff_str)


def fetch_dataset(
    session,
    base_url,
    dataset_id,
    output_path,
    date_field=None,
    window_months=None,
    page_size=50000,
    app_token=None,
    now=None,
):
    """
    Pages through a Socrata dataset's CSV export and writes the full
    result to output_path. Returns the total number of data rows written.
    """
    logger = logging.getLogger(__name__)
    if now is None:
        now = datetime.now()
    where_clause = compute_where_clause(date_field, window_months, now)

    tmp_path = output_path + ".tmp"
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    offset = 0
    total_rows = 0
    header_written = False

    with open(tmp_path, "w", newline="", encoding="utf-8") as out_file:
        while True:
            params = {"$limit": page_size, "$offset": offset}
            if where_clause:
                params["$where"] = where_clause
            if app_token:
                params["$$app_token"] = app_token

            url = "{}/resource/{}.csv".format(base_url, dataset_id)
            response = session.get(url, params=params, timeout=60)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if "text/csv" not in content_type:
                raise ValueError(
                    "Expected text/csv from {} but got Content-Type: {}".format(
                        url, content_type
                    )
                )

            lines = response.text.splitlines(keepends=True)
            if not lines:
                break

            data_lines = lines[1:]

            if not header_written:
                out_file.write(lines[0])
                header_written = True
            out_file.writelines(data_lines)

            page_row_count = len(data_lines)
            total_rows += page_row_count
            logger.info(
                "Fetched {} rows (offset {}) for dataset {}".format(
                    page_row_count, offset, dataset_id
                )
            )

            if page_row_count < page_size:
                break
            offset += page_size

    os.replace(tmp_path, output_path)
    logger.info("Wrote {} total rows to {}".format(total_rows, output_path))
    return total_rows


FETCH_CONFIG_FILE_NAME = "configuration/fetch-config.json"


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        required=False,
        default=None,
        help="Fetch a single dataset by name (e.g. carriers, crashes, inspections). Fetches all configured datasets if omitted.",
    )
    args = parser.parse_args()

    config = file_utils.load_from_file(FETCH_CONFIG_FILE_NAME)
    if not config:
        logger.critical("Could not load {}".format(FETCH_CONFIG_FILE_NAME))
        sys.exit(1)

    app_token = os.environ.get(config.app_token_env_var)

    dataset_names = list(vars(config.datasets).keys())
    if args.dataset:
        if args.dataset not in dataset_names:
            logger.critical(
                "Unknown dataset: {}. Known datasets: {}".format(
                    args.dataset, dataset_names
                )
            )
            sys.exit(1)
        dataset_names = [args.dataset]

    session = requests.Session()
    for name in dataset_names:
        dataset_config = getattr(config.datasets, name)
        logger.info("Fetching dataset: {}".format(name))
        total = fetch_dataset(
            session=session,
            base_url=config.base_url,
            dataset_id=dataset_config.dataset_id,
            output_path=dataset_config.output,
            date_field=dataset_config.date_field,
            window_months=dataset_config.window_months,
            page_size=config.page_size,
            app_token=app_token,
        )
        logger.info("Fetched {} rows for {}".format(total, name))


if __name__ == "__main__":
    main()
