"""What a dataset's config promises versus what its CSV actually contains.

Exists because profiling the data and reading the mappings were separate acts,
and nothing compared them. That gap let 66 fields across three DOT-Commercial
datasets change type silently when the loader stopped inferring types: each one
was simply absent from its index-mappings.json, so it became `text` and nobody
found out until a mapping diff was run by hand after the fact.

The comparison is pure so it can be tested against literals; reading CSV
headers and walking a project's configuration is the CLI's job.
"""

from utils.mapping_coverage import compare, looks_like_date, recommend_type


def test_columns_missing_from_the_mapping_are_reported():
    result = compare(columns=["a", "b", "c"], pinned=["a"])
    assert result.unpinned == ["b", "c"]


def test_pinned_fields_absent_from_the_csv_are_reported_as_dead_config():
    """A pin for a column the extract no longer has is stale config.

    Worth surfacing separately from an unpinned column: it means the source
    changed shape underneath the configuration, which usually means other
    assumptions about that file are stale too.
    """
    result = compare(columns=["a"], pinned=["a", "removed_last_year"])
    assert result.dead == ["removed_last_year"]
    assert result.unpinned == []


def test_a_fully_pinned_dataset_reports_nothing():
    """The auth-history shape: every column pinned, so a loader change is inert."""
    result = compare(columns=["a", "b"], pinned=["b", "a"])
    assert result.unpinned == [] and result.dead == []
    assert result.covered is True


def test_enrichment_targets_are_not_mistaken_for_dead_config():
    """Enriched objects are pinned but never appear as CSV columns.

    carriers pins `inspections`, `crashes` and friends as the targets enrich
    processors write into. Reporting those as stale would make the check cry
    wolf on the one project that uses enrichment at all.
    """
    result = compare(columns=["dot_number"], pinned=["dot_number", "crashes"], enriched=["crashes"])
    assert result.dead == []


def test_zero_padded_values_are_recommended_as_keyword():
    """The defect the loader fix exists to prevent, caught at config time."""
    assert recommend_type(["010001", "010005", "000123"]) == "keyword"


def test_plain_integers_are_recommended_as_long():
    assert recommend_type(["42", "7", "1200"]) == "long"


def test_fractional_values_are_recommended_as_double_never_float():
    """`float` is 32-bit and rounds integers above 2^24 to even.

    Measured on the live cluster before this check existed: `final_status_date`
    was mapped float, so `term ...=20250919` and `=20250920` returned the same
    39,400 documents. Recommending `float` would hand someone that defect.
    """
    assert recommend_type(["1.5", "2.25"]) == "double"


def test_boolean_values_are_recommended_as_boolean():
    assert recommend_type(["true", "false", "true"]) == "boolean"


def test_free_text_gets_no_recommendation():
    """None means "text is genuinely right", not "could not decide"."""
    assert recommend_type(["ACME HAULING", "12 MAIN ST"]) is None


def test_blank_values_do_not_drive_the_recommendation():
    """Blank is absence of evidence, the same distinction the scorer draws.

    A column that is mostly empty must be classified on the values it does
    carry, or one stray blank would downgrade a zero-padded key to text.
    """
    assert recommend_type(["", "010001", None, "010005"]) == "keyword"


def test_a_column_with_no_values_at_all_gets_no_recommendation():
    assert recommend_type(["", None]) is None


def test_mixed_numeric_and_text_gets_no_recommendation():
    """The shape that used to be blamed for dropped documents.

    It loads as strings and is safe; `text` versus `keyword` here is a query
    decision for a human, not something this check should assert.
    """
    assert recommend_type(["12345", "NONE", "PA"]) is None


def test_a_non_iso_date_column_is_called_out_rather_than_left_as_text():
    """Dates are the one "not numeric" case where text is the wrong answer.

    FMCSA ships Oracle-style `01-JUN-74`, which Elasticsearch will not
    auto-detect, so the field lands as text and no range query works. Telling
    someone "text is probably right" there hands them the README's own
    hazard 2, so the classifier has to distinguish it from free text.
    """
    assert looks_like_date(["01-JUN-74", "15-MAR-02"]) is True
    assert looks_like_date(["3/15/2002", "12/1/1998"]) is True


def test_free_text_is_not_mistaken_for_a_date():
    assert looks_like_date(["ACME HAULING", "12 MAIN ST"]) is False
    assert looks_like_date(["42", "7"]) is False
