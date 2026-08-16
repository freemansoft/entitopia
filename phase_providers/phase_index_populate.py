import contextlib
import logging
from itertools import islice

import numpy as np
import tqdm
from elasticsearch.helpers import parallel_bulk

from utils import elasticsearch_utils, file_utils, id_utils
from utils.csv_load_utils import CsvLoadUtils

MAX_LOGGED_FAILURES = 20


class PhaseIndexingPopulate:
    def __init__(self, es, project, one_step, project_config):
        self.es = es
        self.project = project
        self.one_step = one_step
        self.project_config = project_config
        self.logger = logging.getLogger(__name__)

    def compute_id(self, record, id_field):
        # id_field as a list builds a composite key by joining the named
        # fields' values; a KeyError here is handled the same as the
        # single-field case, falling back to an ES auto-generated _id
        return id_utils.compute_id(record, id_field)

    def record_action(
        self,
        data,
        ingestion_pipeline,
        id_field,
        num_rows,
        blank_keys=None,
    ):
        """Yield one bulk action per CSV row.

        `blank_keys` is a mutable counter the caller reads after the load,
        rather than a return value, because this is a generator consumed by
        parallel_bulk — there is no other moment at which the count and the
        caller are both in scope. It exists so a keyless row is reported once
        with a total instead of either being silent (the original bug) or
        emitting a log line per row on a dataset where the column is mostly
        empty.
        """
        records = data.to_dict(orient="records")
        for record in islice(records, num_rows):
            # support _id being specified and not, pipeline used and not
            # seems cumbersome but is clear
            try:
                doc_id = self.compute_id(record, id_field)
                if blank_keys is not None and str(doc_id).startswith(
                    id_utils.BLANK_KEY_PREFIX
                ):
                    blank_keys["count"] += 1
                doc = {"_id": doc_id, "_source": record}
                if ingestion_pipeline:
                    doc = {
                        "_id": doc_id,
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
            # A --num-rows on the command line wins over the config file, so
            # one checkout can run against a small sample or the full download
            # without editing anything committed. Resolved before the loader is
            # built so the cap also limits what pandas reads, rather than
            # pulling millions of rows into memory and discarding most of them.
            num_rows = getattr(index_config, "num_rows", None)
            override = getattr(self.project_config, "num_rows_override", None)
            if override is not None:
                self.logger.info(
                    "Row cap override in effect: {} rows (config said {})".format(
                        override, num_rows
                    )
                )
                num_rows = override

            csv_loader = CsvLoadUtils(
                self.project,
                self.project_config.dataDir,
                self.one_step,
                index_config.source,
                num_rows,
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
            # auto generate the id_field if not present
            with contextlib.suppress(AttributeError):
                id_field = index_config.id_field

            failure_count = 0
            blank_keys = {"count": 0}
            for success, response in parallel_bulk(
                client=self.es,
                thread_count=8,
                index=index_config.index,
                actions=self.record_action(
                    data,
                    pipeline,
                    id_field,
                    num_rows,
                    blank_keys,
                ),
                raise_on_error=False,
                raise_on_exception=False,
            ):
                if not success:
                    failure_count += 1
                    if failure_count <= MAX_LOGGED_FAILURES:
                        self.logger.error("Failed to index document: {}".format(response))
                prog_meter.update(1)

            # Warned rather than raised: the fallback id is deterministic, so
            # the row is loaded correctly and a reload overwrites it. What the
            # operator still has to decide is whether a keyless row belongs in
            # the source at all, and that needs the count and the field name.
            if blank_keys["count"]:
                self.logger.warning(
                    "id_field {} was blank on {} of {} rows loaded into {}; those rows "
                    "were keyed by a hash of the whole row ('{}' prefix) so a reload "
                    "overwrites them instead of appending a fresh copy each "
                    "run. Byte-identical keyless rows collapse onto one "
                    "document.".format(
                        id_field,
                        blank_keys["count"],
                        len(data),
                        index_config.index,
                        id_utils.BLANK_KEY_PREFIX,
                    )
                )

            if failure_count:
                self.logger.error(
                    "{} of {} documents failed to index into {}{}".format(
                        failure_count,
                        len(data),
                        index_config.index,
                        " (showing first {} failures above)".format(MAX_LOGGED_FAILURES)
                        if failure_count > MAX_LOGGED_FAILURES
                        else "",
                    )
                )
