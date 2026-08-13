import logging

import pandas as pd


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
        # dtype=str, so the index mapping decides every field's type and this
        # loader cannot decide one for it. Inference here is not a convenience
        # but a corruption: a column that happens to be uniformly numeric
        # becomes int64 and its leading zeros are gone before Elasticsearch is
        # reached, which a `keyword` mapping is powerless to undo -- CMS
        # `Facility ID` loaded `010001` as `10001` in both `_source` and, being
        # the id_field, `_id`. Elasticsearch coerces strings into numeric
        # fields, so nothing is lost by deferring the decision.
        #
        # Blank cells still arrive as NaN rather than as a string, which
        # phase_index_populate relies on to turn them into JSON null; a literal
        # '' or 'nan' would make every "not evaluable" signal test see a
        # present value and let two empty records appear to agree.
        results = pd.read_csv(
            file_path,
            encoding="utf-8",
            nrows=self.num_rows,
            header=0,
            skiprows=skip_rows,
            dtype=str,
        )
        number_of_docs = len(results)

        self.logger.info(
            "Read {} records from data file {}".format(number_of_docs, file_path)
        )

        return results
