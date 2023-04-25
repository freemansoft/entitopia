import pandas as pd
import logging as logging


class CsvLoadUtils:
    def __init__(self, project, data_dir, step, filename, num_rows, skip_rows):
        # project is the root of the data
        # one_step is a directory inside project/data and project/configuration
        self.project = project
        self.data_dir = data_dir
        self.one_step = step
        self.filename = filename
        self.num_rows = num_rows
        self.skip_rows = skip_rows
        self.logger = logging.getLogger(__name__)

    def load_csv(self):
        file_path = "{}/{}/{}/{}".format(
            self.project,
            self.data_dir,
            self.one_step,
            self.filename,
        )
        skip_rows = self.skip_rows
        # TODO: handle None
        if skip_rows != 0:
            skip_rows = range(1, skip_rows)
        results = pd.read_csv(
            file_path,
            encoding="windows-1252",
            nrows=self.num_rows,
            header=0,
            skiprows=skip_rows,
        )
        number_of_docs = len(results)

        self.logger.info(
            "Read {} records from data file {}".format(number_of_docs, file_path)
        )

        return results
