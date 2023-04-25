import logging as logging
from itertools import islice
import utils.elasticsearch_utils as elasticsearch_utils

import numpy as np
import pandas as pd
import tqdm

from elasticsearch.helpers import parallel_bulk

import utils.file_utils as file_utils
from utils.csv_load_utils import CsvLoadUtils


class PhaseIndexingPopulate:
    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.logger = logging.getLogger(__name__)

    def record_action(
        self,
        data,
        ingestion_pipeline,
        id_field,
        num_rows,
    ):
        records = data.to_dict(orient="records")
        for record in islice(records, num_rows):
            # support _id being specified and not, pipeline used and not
            # seems cumbersome but is clear
            try:
                doc = {"_id": record[id_field], "_source": record}
                if ingestion_pipeline:
                    doc = {
                        "_id": record[id_field],
                        "_source": record,
                        "pipeline": ingestion_pipeline,
                    }
                # self.logger.debug("Ingestion doc: {}".format(doc))
                yield doc
            except KeyError:
                doc = {"_source": record}
                if ingestion_pipeline:
                    doc = {
                        "_source": record,
                        "pipeline": ingestion_pipeline,
                    }
                # self.logger.debug("Ingestion doc: {}".format(doc))
                yield doc

    def handle(self):
        self.logger.info(
            "Initiating step:{} Phase Handler: {}".format(
                self.one_step, self.__class__.__name__
            )
        )
        self.logger.debug(self.project_config)
        index_config = file_utils.load_from_project_file(
            self.project,
            self.project_config.configurationDir,
            self.one_step,
            "index-config.json",
        )
        self.logger.debug("loaded config {}".format(index_config))

        if index_config:
            elasticsearch_utils.replace_index_with_now_version(index_config)
            self.logger.debug("loaded config {}".format(index_config))
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
                "Indexing {} records into index {}".format(
                    len(data), index_config.index
                )
            )
            pipeline = None
            try:
                pipeline = index_config.pipeline
                self.logger.info("Ingesting with pipeline {}".format(pipeline))
            except AttributeError:
                # leave pipeline as None
                pass

            id_field = None
            try:
                id_field = index_config.id_field
            except AttributeError:
                # auto generate the id_field
                pass

            num_rows = None
            try:
                num_rows = index_config.num_rows
            except AttributeError:
                # all rows
                pass

            for success, response in parallel_bulk(
                client=self.es,
                thread_count=8,
                index=index_config.index,
                actions=self.record_action(
                    data,
                    pipeline,
                    id_field,
                    num_rows,
                ),
                raise_on_error=False,
                raise_on_exception=False,
            ):
                # every one of these will yield
                prog_meter.update(1)
