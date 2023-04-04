import logging as logging
from itertools import islice
import elasticsearch_utils

import numpy as np
import pandas as pd
import tqdm

from elasticsearch.helpers import parallel_bulk

import file_utils
from csv_load_utils import CsvLoadUtils


class PhaseIndexing:
    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.logger = logging.getLogger(__name__)

    def record_action(
        self,
        data,
        id_field,
        num_rows,
    ):
        records = data.to_dict(orient="records")
        for record in islice(records, num_rows):
            doc = {"_id": record[id_field], "_source": record}
            yield doc

    def handle(self):
        self.logger.info(
            "handle() step:%s Phase Handler: %s"
            % (self.one_step, self.__class__.__name__)
        )
        self.logger.debug(self.project_config)
        index_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-config.json",
        )
        self.logger.info("loaded config %s" % str(index_config))

        if index_config:
            elasticsearch_utils.replace_index_with_now_version(index_config)
            self.logger.debug("loaded config %s" % str(index_config))
            csv_loader = CsvLoadUtils(
                self.project,
                self.project_config.dataDir,
                self.one_step,
                index_config.source,
                index_config.num_rows,
                index_config.skip_rows,
            )
            data = csv_loader.load_csv()
            data.replace({np.nan: None}, inplace=True)

            prog_meter = tqdm.tqdm(unit="docs", total=len(data))

            self.logger.info(
                "Indexing %d records into index %s" % (len(data), index_config.index)
            )
            for success, response in parallel_bulk(
                client=self.es,
                thread_count=8,
                index=index_config.index,
                actions=self.record_action(
                    data,
                    index_config.id_field,
                    index_config.num_rows,
                ),
                raise_on_error=False,
                raise_on_exception=False,
            ):
                # every one of these will yield
                prog_meter.update(1)
