"""Structure of the FDIC institutions ingest pipeline.

Guards a silent failure: the API's 12/31/9999 "not ended" sentinel loading as a
real shutdown date in year 9999. Loaded as a date it makes every active bank
"end in 9999" and places 22 inactive banks in nonsense windows, with no error
anywhere and every downstream sweep still reporting success. The pipeline must
remove the sentinel before the date processor converts the field.

This reads pipelines.json only. It does not run the pipeline, so the
on_failure drop of an unparseable date is not covered here.
"""

import json
from pathlib import Path

_PIPELINES = (
    Path(__file__).parent.parent
    / "FDIC-Banks"
    / "configuration"
    / "institutions-ingestion-setup"
    / "pipelines.json"
)


def test_pipeline_drops_end_date_sentinel_before_converting():
    """The 12/31/9999 sentinel must be removed before the date processor.

    Loaded as a date it makes active banks "end in 9999" and places 22 inactive
    banks in nonsense windows, with no error anywhere.
    """
    processors = json.loads(_PIPELINES.read_text())["processors"]
    kinds = [next(iter(p)) for p in processors]
    removal = next(
        p["remove"]
        for p in processors
        if "remove" in p and "12/31/9999" in p["remove"].get("if", "")
    )
    assert removal["field"] == "ENDEFYMD"
    end_date = next(
        i for i, p in enumerate(processors) if p.get("date", {}).get("field") == "ENDEFYMD"
    )
    assert kinds.index("remove") < end_date
