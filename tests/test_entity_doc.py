"""EntityDoc's key is configuration, not a field name baked into the framework.

The premise of this project is that a new dataset is onboarded by writing JSON.
A hardcoded `dot_number` attribute meant every project's records had to pretend
to be FMCSA carriers, so these pin the key as data — including the absence of a
`dot_number` alias, which would let the vocabulary back in.
"""

from matching.candidates import to_entity_doc
from matching.documents import EntityDoc


def test_entity_doc_exposes_its_key_generically():
    doc = EntityDoc(entity_key="12345", source={"dot_number": "12345"}, tokens={})
    assert doc.entity_key == "12345"


def test_entity_doc_has_no_dot_number_attribute():
    # An alias would give new code two names for one value with no rule about
    # which to reach for, which is how the vocabulary got into framework code.
    doc = EntityDoc(entity_key="12345", source={}, tokens={})
    assert not hasattr(doc, "dot_number")


def test_to_entity_doc_reads_the_configured_key_field():
    hit = {"_id": "abc", "_source": {"Facility ID": "010001", "dot_number": "999"}}
    doc = to_entity_doc(hit, tokens={}, key_field="Facility ID")
    assert doc.entity_key == "010001"


def test_to_entity_doc_falls_back_to_the_es_id():
    # The near-empty dev index carries probe documents with no key field; this
    # keeps a sweep usable against one rather than raising on test data.
    hit = {"_id": "abc", "_source": {}}
    doc = to_entity_doc(hit, tokens={}, key_field="Facility ID")
    assert doc.entity_key == "abc"


def test_to_entity_doc_stringifies_a_numeric_key():
    # The same logical key arrives as a JSON integer from some indexes and a
    # string from others, and a pair keyed on it must not depend on which.
    hit = {"_id": "abc", "_source": {"dot_number": 23680}}
    doc = to_entity_doc(hit, tokens={}, key_field="dot_number")
    assert doc.entity_key == "23680"


def test_token_set_is_unchanged_by_the_rename():
    doc = EntityDoc(
        entity_key="1", source={}, tokens={"legal_name.phonetic": {"AKM", "HLNK"}}
    )
    assert doc.token_set("legal_name", "phonetic") == {"AKM", "HLNK"}
    assert doc.token_set("legal_name", "never_indexed") == set()
