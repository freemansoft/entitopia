"""Guards the GLEIF sweeps' population and declared rules against silent drift.

A duplicate registration read as a succession inflates recall with pairs that
are one entity: GLEIF marks the second registration of one legal entity
`DUPLICATE` and has it name the surviving LEI as its successor, so the two
share a name and score as the easiest recall there is. A fund or a branch read
as a predecessor floods the sweep with entities that have no successor in the
corporate sense. A registered agent's address on a legal-address signal links
every client of that agent, because tens of thousands of US entities carry one
filing service's street as their legal address. None of these shows in a run's
log: the sweep completes and its counts look plausible.

A third guard covers retrieval. The reappearance sweep's recall gain at R3
rests on `hq-street-seed`, an `address` signal at weight 0 that exists only to
seed candidates on street words. It seeds because `CandidateFinder` selects
seed signals by signal type, not by name. Seeding by name, or deleting the
apparently unused zero-weight signal, would leave the sweep running and
logging success with its tuned recall quietly gone.

The German sweeps (`lei-reappearance-de`, `lei-absorption-de`) measure how the
US-tuned configuration carries to another country, so they must stay the US
configuration with only the country changed. A German config tuned or edited
on its own would make the README's US-against-German comparison measure two
configurations instead of two countries, and nothing in a run would show it.

The expected exclusions are written here from the decision the README records
("Decisions from the profile"), never read from the config under test, so a
config edited to drop one fails rather than redefining what is expected.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from matching.candidates import CandidateFinder
from matching.population import PopulationSelector
from matching.signals import build_signal
from utils import file_utils

# Each sweep and the LEGAL_COUNTRY its population is drawn from.
SWEEP_COUNTRIES = {
    "lei-reappearance": "US",
    "lei-absorption": "US",
    "lei-reappearance-de": "DE",
    "lei-absorption-de": "DE",
}
SWEEPS = tuple(SWEEP_COUNTRIES)

# From GLEIF-Entities/README.md, "Decisions from the profile": FUND and BRANCH
# are 25.5% of US inactive records, and 6,527 records are DUPLICATE
# registrations naming the LEI they duplicate as their successor.
EXPECTED_EXCLUSIONS = {
    ("CATEGORY", "FUND"),
    ("CATEGORY", "BRANCH"),
    ("REGISTRATION_STATUS", "DUPLICATE"),
}


# Fields carrying the legal (registered) address. A registered agent's street
# sits on tens of thousands of US legal addresses ("Shared legal addresses" in
# the README); the headquarters fields are what the address signal reads.
LEGAL_ADDRESS_FIELDS = {"LEGAL_STREET", "LEGAL_LOCAL", "LEGAL_CITY", "LEGAL_POSTAL"}


def _load(step):
    path = Path("GLEIF-Entities") / "configuration" / step / "entity-match.json"
    return file_utils.load_from_file(str(path))


def _terms(query, negated=False):
    """(field, value, negated) for every term clause, however deeply nested.

    Walked rather than compared to a literal so the test pins what the
    population means (which values are required and which excluded), leaving
    the exact nesting to test_population's shipped-query pins.
    """
    if isinstance(query, dict):
        for key, value in query.items():
            if key == "term":
                (field, term_value), = value.items()
                yield field, term_value, negated
            elif key == "must_not":
                for clause in value:
                    yield from _terms(clause, not negated)
            else:
                yield from _terms(value, negated)
    elif isinstance(query, list):
        for item in query:
            yield from _terms(item, negated)


@pytest.mark.parametrize("step", SWEEPS)
def test_population_excludes_what_profiling_found(step):
    config = _load(step)
    query = PopulationSelector(None, "entities-000001", config.population).build_query()
    terms = list(_terms(query))
    required = {(f, v) for f, v, negated in terms if not negated}
    excluded = {(f, v) for f, v, negated in terms if negated}
    assert required == {("ENTITY_STATUS", "INACTIVE"), ("LEGAL_COUNTRY", SWEEP_COUNTRIES[step])}
    assert excluded == EXPECTED_EXCLUSIONS


@pytest.mark.parametrize("step", SWEEPS)
def test_only_truth_rule_is_the_successor(step):
    block = _load(step).declared_links
    truth = [r for r in block.rules if r.role == "truth"]
    assert len(truth) == 1
    (rule,) = truth
    assert rule.kind == "key"
    assert rule.field == "SUCCESSOR_LEI"
    assert not getattr(rule, "reverse", False)
    # GLEIF records a successor only when the filer reports one; an unlinked
    # pair may still be a real succession, so precision must not count it as
    # a false match.
    assert block.complete_truth is False


@pytest.mark.parametrize("step", SWEEPS)
def test_legal_address_is_rarity_limited(step):
    """A legal-address signal must carry a shared-value limit that is enforced.

    The limit is only enforced for fields a signal reports through
    exact_evidence_fields, which the corpus frequency scan reads. The address
    signal reports none today, so a `max_shared_records` entry beside it would
    be accepted by the schema and do nothing. Both conditions are checked so
    a no-op entry cannot satisfy this guard.
    """
    config = _load(step)
    declared = getattr(config, "max_shared_records", None)
    limits = vars(declared) if declared is not None else {}
    unguarded = []
    for signal_config in config.signals:
        legal = LEGAL_ADDRESS_FIELDS & set(getattr(signal_config, "fields", []))
        if not legal:
            continue
        enforced = {path for path, _ in build_signal(signal_config).exact_evidence_fields()}
        for field in sorted(legal):
            if field not in limits or field not in enforced:
                unguarded.append((signal_config.type, field))
    assert not unguarded, (
        "signals reading a legal address without an enforced max_shared_records "
        "limit: {}. Today no signal reads a LEGAL_* address field, so this test "
        "passes vacuously until one is added".format(unguarded)
    )


def test_hq_street_seed_retrieves_on_street_tokens_by_signal_type():
    """The zero-weight `hq-street-seed` must still seed, and on HQ street tokens.

    Built through the real finder rather than by calling the signal, so the
    test fails if seeding stops selecting by signal type (a name-based
    selection would not see a signal the config lists only by type) as well as
    if the signal is deleted. The `address` signal seeds on its exact subfield,
    here `clean`; only the seed signal seeds on `tokens`, so a clause on
    `HQ_LOCAL.tokens` proves it, not the scored `address` signal, did the work.
    """
    config = _load("lei-reappearance")
    seeds = [s for s in config.signals if getattr(s, "name", None) == "hq-street-seed"]
    assert len(seeds) == 1, "hq-street-seed signal is missing from the config"
    assert seeds[0].weight == 0.0, "the seed must not change any score"

    finder = CandidateFinder(
        es=SimpleNamespace(),
        source_index=config.source_index,
        candidates_config=config.candidates,
        signal_configs=config.signals,
        entity_config=config.entity,
        lifecycle=config.lifecycle,
    )
    clauses = finder._seed_clauses({"HQ_LOCAL": "100 MAIN ST"})
    assert {"match": {"HQ_LOCAL.tokens": {"query": "100 MAIN ST"}}} in clauses


@pytest.mark.parametrize("us_step", ["lei-reappearance", "lei-absorption"])
def test_german_sweep_is_the_us_sweep_with_another_country(us_step):
    """Everything but the population must equal the US sweep's, and the population
    must build the US query with only the country swapped.

    Compared on the raw JSON rather than on chosen keys, so a signal, weight,
    window, cap or declared rule added to one side alone fails here.
    """
    base = Path("GLEIF-Entities") / "configuration"
    us = json.loads((base / us_step / "entity-match.json").read_text())
    de = json.loads((base / f"{us_step}-de" / "entity-match.json").read_text())
    us.pop("population")
    de.pop("population")
    assert de == us

    def built(step):
        return PopulationSelector(None, "entities-000001", _load(step).population).build_query()

    us_query = json.dumps(built(us_step)).replace('"LEGAL_COUNTRY": "US"', '"LEGAL_COUNTRY": "DE"')
    assert json.loads(us_query) == built(f"{us_step}-de")
