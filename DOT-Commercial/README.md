# DOT Commercial

A reference implementation of the [entitopia framework](../README.md) over FMCSA commercial trucking data. It is the **complex** case: seven datasets, six enrichment policies, a two-level enrichment chain, ingestion pipelines, and the project's first `entity-match` step.

Its goal is detecting **chameleon carriers** — trucking companies shut down for safety or insurance reasons that reopen under a new DOT number while reusing the same addresses, phones, trucks, and near-identical names.

Framework concepts (steps, phases, configuration layout) and the data-loading hazards common to any dataset are in the [top-level README](../README.md). This README covers what is specific to the FMCSA data.

> **Counts here are point-in-time**, measured against the **July 2026** FMCSA
> extract unless stated otherwise. FMCSA republishes continuously: carriers are
> added and shut down daily, so row counts, the predecessor population, the
> placeholder values in `ignore_values`, and every match count below will differ
> on your own download. Sweep results depend on the extract twice over — once
> through the data and once through thresholds tuned against it. Treat these as
> evidence of magnitude and shape, not as figures to reproduce.

On the DOT Site <https://data.transportation.gov/Trucking-and-Motorcoaches/>

## Open items

Dataset-specific. Framework-level items are in the [top-level README](../README.md).

1. **`insp_carrier_state_id` is not pinned in `inspections/index-mappings.json`**, so inspections ingestion silently drops ~0.65% of documents (36,788 of 5,647,567 on a full run). Elasticsearch dynamically infers `float` from whichever value it sees first under `parallel_bulk`'s concurrency, but the source column mixes numeric and non-numeric strings (`'NONE'`, `'S00000030887'`), so every non-conforming row fails with `document_parsing_exception`. Deterministic and lossy on every full run; which rows drop varies with thread ordering. Fix by pinning it to `keyword`, mirroring `dot_number` / `inspection_id`.
1. **`entity-match` thresholds are still uncalibrated, though it has now run against production data.** A full sweep over the July 2026 extract (2,085,534 carriers, 48,540 predecessors, 500 candidates each) emitted **421,846 pairs** from 22,316,105 candidates examined, of which 80.1% score below 0.50 — that band is noise. Applying the triage filter — re-registered within a year of shutdown, scoring ≥ 0.70, and sharing a VIN or phone/email — leaves **906 pairs**, of which 436 reuse the _byte-identical_ legal name. Measured 2026-08-06 against a carriers index whose stamped analysis fingerprint (`0595ca890d9ec6fb`) equals the one computed from the shipped config, so every figure here is re-derivable rather than historical.

   **State the triage filter exactly, because its prose reading and its query disagree.** As run, it is `gap_days <= 365`, `total_score >= 0.70`, and `matched_on` containing `vin-overlap` or `exact-identifier` — bounded above only. It therefore admits pairs whose successor registered _before_ the predecessor's shutdown, sometimes by years, which is not what "re-registered within a year of shutdown" describes. Adding the obvious lower bound (`gap_days >= 0`) cuts the same run from **906 to 186**. Both are recorded because 906 is what every earlier revision of this item measured and 186 is what its sentence meant; the filter differs, not the data. Prefer 186 when the question is "who reappeared after being shut down."

   That also resolves the "roughly 195 pairs" an earlier revision cited and could not reproduce. It is the 2026-08-01 run under the lower-bounded filter — re-measured directly as **195**, against the **957** the same run yields unbounded. So the three figures that looked irreconcilable (195, 906, 957) are two filters over two runs, not a lost measurement. Nothing here is unaccounted for any more.

   The scores remain **uncalibrated confidence, not probability.** `total_score` is a weighted mean of evaluable signals renormalized over their weights; nothing has been fitted against known outcomes, so 0.9 does not mean 90% likely. Turning it into a probability needs labelled FMCSA enforcement results, which the project does not have. Until then, treat the ranking as triage order and the per-signal `matched_on` / contributions as the reason to act.

   Separately reviewable: **675 pairs share a vehicle and nothing else** — no name, address, or phone overlap. These are unreachable without VIN seeding and score ~0.11, so they surface only because `vin-overlap` is marked `conclusive`; they will never appear in a score-ranked list. Triage them by `gap_days`, not by score. Shape of the strongest example measured: `<CARRIER-A>` → `<CARRIER-B>`, unrelated legal names, same city, re-registered 14 days after shutdown, operating a truck with the same VIN.

   Sanity anchors from that run, useful when re-tuning: the top of the list is dominated by carriers re-registering under a **byte-identical legal name** at the same address and phone within days of shutdown — the strongest measured pair reused its name exactly and re-registered **one day** after being placed out of service, scoring 0.9998 on name, address, phone and timing together. If a config change stops surfacing that shape, the change is wrong. Re-checked against the 2026-08-06 run and unchanged, which is the check this paragraph exists to demand.

   **The controlled comparison an earlier revision called for has now been run, and the stop-list correction barely moved anything.** Two sweeps over the same July 2026 extract, differing only in the `unit_designator_stop` fix — seven spelled-out words (`space`, `office`, `pier`, `trailer`, `hangar`, `building`, `slip`) removed, `spc` added — with no change to `entity-match.json` or anything under `matching/` in between:

   |                             |  before |                      after |
   | --------------------------- | ------: | -------------------------: |
   | pairs emitted               | 421,613 | **421,846** (+233, +0.06%) |
   | scoring below 0.50          |  80.14% |                     80.06% |
   | triage set (906 filter)     |     906 |                    **906** |
   | ↳ byte-identical legal name |     436 |                    **436** |
   | vehicle-only pairs          |     675 |                    **675** |

   The correction perturbs the noise band and leaves the reviewable set untouched — the expected outcome for a change affecting roughly 2,800 `phy_street` values out of 2,085,534, and a useful negative result: street-token changes of this size are not what is holding calibration back. Weighting is (see the next item).

   **The calibration question above finally has an answer against reality, not just against itself, and it is recorded here in full because the theory-of-operation explainer's [§9](docs/chameleon-pipeline-explainer.md#9-does-the-score-actually-predict-anything) only summarizes it.** Two validations, primary then secondary per the design's own framing, both measured 2026-08-08 against the same `carriers-000001` / `chameleon-candidates-000001` aliases (fingerprint `0595ca890d9ec6fb`, unchanged from the sweep above) and persisted to the `chameleon-validation-000001` alias, so either run is re-derivable by `run_id` rather than only quotable from this paragraph.

   Primary — does the score find chameleon-shaped pairs, checked directly against the README's own definition ("shut down... that reopen under a new DOT number"), no proxy or external data needed: `.venv/bin/python scripts/measure_chameleon_shape.py`. Of the **1,729 pairs scoring ≥ 0.70**, the `gap_days` distribution is 728 registered more than 180 days _before_ the predecessor's shutdown (42.1%), 161 within the `temporal` signal's own 180-day pre-positioning window (9.3%), 435 within a year after (25.2%), and 405 more than a year after (23.4%) — **only 34.5% (596 of 1,729) is temporally coherent** within the scorer's own window. Mean `total_score` separates pre-shutdown pairs (0.4425 over 306,401) from post-shutdown pairs (0.4520 over 115,445) by just **0.0095**. `TemporalSignal` is not broken — `matching/signals.py` deliberately gives a pre-shutdown pair partial credit (`BACKWARD_WINDOW_DAYS = 180`, `BACKWARD_SCALE = 0.5`) because pre-positioning a successor ahead of a known-coming shutdown is a real tactic — it is simply outvoted: `temporal` carries 0.05 of the 0.94 configured total (a ceiling of ~0.053 on any score), while the three name signals above carry 0.45 combined, nine times as much. **This is independent corroboration of the name-triple-weighting item below, not a new defect.** A caveat that travels with it: 49 CFR 386.73 covers operating as an _affiliated entity_, not only under a new identity, so a high-scoring pair naming a pre-existing company is not automatically a false positive — it may be a genuine affiliate. What it is not is _reincarnation_, which is what this project says it hunts. (Item 3, closed below, has since gated the live sweep on exactly this window — the 34.5% measured here against the 2026-08-06 run is 100% in the current `chameleon-candidates-000001` output.)

   Secondary — does the flagged population show GAO-12-364's measured crash lift (18% vs 6%, a proxy for safety risk, not a redefinition of "chameleon"): `.venv/bin/python scripts/measure_crash_lift.py`. Crash window (read from `crashes-000001`, reloaded to 333,120 docs matching `crashes.csv` exactly — a prior index held 333,122 from an older rolling-window extract, so any earlier crash-figure quote will show small, legitimate movement against this one): 2024-08-12 to 2026-07-29. 249,778 distinct successors; the restricted cohort (`add_date` before the crash window, comparable to GAO's figures) is **196,707** (21.2% excluded as registered inside the window). Per-band crash rate inside that cohort does not rise with score:

   | score band | carriers | crashed |   rate |
   | ---------- | -------: | ------: | -----: |
   | 0.35-0.50  |  146,045 |   9,683 |  6.63% |
   | 0.50-0.60  |   46,797 |   2,296 |  4.91% |
   | 0.60-0.70  |    1,275 |      69 |  5.41% |
   | 0.70-0.80  |      283 |      39 | 13.78% |
   | 0.80-0.90  |      156 |      19 | 12.18% |
   | 0.90-1.00  |      145 |       6 |  4.14% |

   Flagged successors crashed at **6.64%** overall; the whole unflagged population (standardized by registration-year/fleet-size/state to the flagged cohort's mix, 252 strata excluded for holding no controls) crashed at **6.02%** — a lift of **1.10x** against GAO's published **3.0x**. The permuted-score placebo landed within a point of the same rate on the two bands holding 98.0% of the restricted cohort ((146,045 + 46,797) / 196,707); the small high-score tail bands (n=145-283) moved more, consistent with sampling noise at that count rather than a real trend. **Report this as "the flagged population is not measurably riskier than comparable carriers," never as a verdict on matching accuracy** — crash involvement is a proxy GAO used because chameleon carriers matter for safety, not this project's definition of one. The loaded census also carries no officer name, EIN, or DUNS — three of the identifiers FMCSA's own ARCHI tool matches on — so a weak lift is at least as consistent with missing inputs as with a bad scorer. **Both validations measure precision-shaped properties only** — whether a flagged pair is temporally coherent, and whether the flagged population is riskier than comparable carriers. Neither can measure recall: there is no list of known chameleon carriers to check the sweep against, so a real chameleon it never surfaced is invisible to both methods.

   **Why GAO measured 3.0x and this sweep measures 1.10x: dilution, not a difference in how the outcome was measured.** GAO flagged **1,136 carriers**; this sweep flags **249,778 distinct successors — 12.0% of every carrier in the census**. A flagged set that is mostly false positives has a crash rate that converges on the population base rate by arithmetic, and this one did: 6.64% flagged, 6.02% standardized control, 5.86% base. Three independent lines agree on that reading:

   - **A structural ceiling.** A predecessor has at most _one_ true successor. Of the **48,540** predecessors the selector examined, only **46,792** appear in at least one emitted pair — the rest are inert for this ratio, since a predecessor with no pair cannot contribute a true positive either way. Against that pair-producing denominator the sweep emits **9.0 pairs per predecessor** (421,846 over 46,792). So at most 46,792 pairs — **11.1%** — can be correct, and that assumes every shut-down carrier reincarnated, which is nowhere near true. (Dividing by all 48,540 examined instead gives 8.7 pairs and a 11.5% ceiling — close enough to read the same way, but the two denominators answer different questions, so pick one and say which.) The real ceiling is a fraction of 11.1%.
   - **The dilution arithmetic.** If true positives carry GAO's 3.0x and false positives sit at base rate, an observed 1.10x implies a true-positive share of `(1.10 - 1) / (3.0 - 1)` ≈ **5%** — comfortably under the structural ceiling, which is what an independent estimate should look like.
   - **The primary measurement, by a different route.** Only 34.5% of the ≥ 0.70 tier is temporally coherent. Different method, same conclusion. (Since gated to 100% by item 3, closed below; this bullet describes the pre-gate measurement that motivated the gate, not the current sweep.)

   Two population differences compound it. GAO examined **new applicants** — a narrow funnel by construction — while this sweeps 2,085,534 carriers. And GAO required attribute match **and** demonstrated motive to evade; the motive filter here (an out-of-service order) was measured against the July 2026 extract cited throughout this validation, which predates the `nested`-mapping fix below and its unchanged-population result — this validation's population figures were not re-run after that fix, since it did not change the predecessor set. FMCSA's own vetting also matches officer names, EIN and DUNS, none of which the census carries.

   Ruled out as explanations: the **24-month crash window** (the control used the identical window, so the comparison is internally consistent) and the **outcome definition** (the measured base rate — 5.86%, against the current `crashes-000001` index this validation reads throughout — lands within 0.14 points of GAO's 6%, which is strong evidence it is the same outcome variable). The 5% figure is a one-parameter model assuming GAO's lift transfers and false positives sit exactly at base rate; GAO's population was 2005-2010 new applicants rather than 2026 carriers, so treat it as an order-of-magnitude read.

   **This is a precision problem, and precision is fixable.** Ranked by expected effect on the dilution above:

   | #   | Change                                                                                         | Why it should tighten precision                                                                                                                                                                                                                                                     | Status                      |
   | --- | ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
   | 1   | Map `out_of_service_orders` as `nested` and use a `nested` query in `matching/predecessors.py` | Was expected to shrink the predecessor set at the source; measured instead to leave it unchanged on this extract, because no carrier here currently has the split-order shape the bug required. Still repairs `shutdown_date`/`gap_days` for any carrier that does have that shape. | **closed** — measured below |
   | 2   | Rebalance signal weights away from name                                                        | 0.45 of 0.94 sits on one field while `temporal` gets 0.05, which is why 42.1% of the top tier sits outside the modelled window and still clears 0.70 on name and address alone. A complete name change is the defining chameleon move.                                              | open item below             |
   | 3   | Require temporal coherence rather than scoring it                                              | A successor registered more than 180 days before the shutdown fails the project's own definition. Gating the score on it, rather than leaving it a 5%-weighted signal, removes pairs outside the window outright regardless of how well name and address otherwise match.           | **closed** — measured below |
   | 4   | Parse street into house number / name / unit                                                   | `100 MAIN ST` vs `200 MAIN ST` scores 0.525 on a signal weighted 0.20 — a false-positive generator at scale.                                                                                                                                                                        | open item below             |
   | 5   | Source officer name (EIN and DUNS are not obtainable — see below)                              | The identifiers ARCHI matches on and this census lacks. Still the largest single lever, but only one third of it is actually available; the other two are dead ends, which is worth knowing before anyone plans work around them.                                                   | not yet an item             |

   **Item 1 has now been measured, and the result is not the one the table above predicted.** Two sweeps over the same July 2026 extract, differing only in the `out_of_service_orders` mapping (`object` vs `nested`) and the corresponding query in `matching/predecessors.py` — nothing else under `matching/` or in `entity-match.json` changed:

   |                         |  before |   after |
   | ----------------------- | ------: | ------: |
   | predecessors examined   |  48,540 |  48,540 |
   | pairs emitted           | 421,846 | 421,844 |
   | `coherent_share_ge_070` |  34.47% |  34.47% |
   | `vin_only`              |     519 |     519 |
   | `vin_only_identity`     |     675 |     675 |
   | `triage_bounded`        |     186 |     186 |
   | `identical_name_triage` |     436 |     436 |

   The predecessor population is identical, carrier for carrier — not just equal in count. Diffed the actual `dot_number` sets matched by each query rather than trusting the totals: zero carriers differ either direction. No carrier in this extract currently has an out-of-service order whose `status` and `oos_date` only jointly satisfy the `out-of-service` selector's configured filters (`ACTIVE`, `oos_date_from: 2020-01-01`) when pulled from two different array elements — the exact shape the `object` mapping's flattening bug required. The fix is real and was proven correct independently on a synthetic document holding that shape (matched under the old `object` mapping, correctly excluded under `nested`); this extract simply doesn't contain a carrier the bug was affecting, under this selector's current filters. The 2-pair difference (421,846 → 421,844) is within `max_candidates` truncation noise — 43,111 of 48,540 predecessors hit the 500-candidate ceiling on this run, so a tie at that boundary can move by a document or two between runs with no change in selection logic. No guarded metric regressed (`scripts/compare_sweeps.py` exit code 0), so the change was kept: `carriers-000001` now points at the `nested`-mapped index, and `matching/predecessors.py` queries it as `nested`. See the closed item below for the fix itself.

   **Item 3 has now been measured, and it is implemented differently from how the table above describes it — recorded here rather than silently done differently.** The gap is a property of the _pair_ (successor `add_date` minus predecessor `oos_date`), not of the predecessor alone, so it cannot be expressed as a predecessor-only selector query. It is instead a `PairScorer` guard (`matching/scorer.py`): `scoring.min_gap_days` / `scoring.max_gap_days` in `entity-match.json`, both `None` by default so an unconfigured deployment is unaffected, drop a pair outright — `score_pair` returns `None` — when its gap falls outside the window, before any score threshold is applied. Configured to `-180` / `365`: the same `BACKWARD_WINDOW_DAYS` the `temporal` signal itself already treats as a plausible pre-positioning window, and the same `max_gap_days` the signal already uses, so the gate admits exactly what the scorer claims to model and nothing wider.

   Two sweeps over the same July 2026 extract, differing only in this gate:

   |                          |  before |   after |
   | ------------------------ | ------: | ------: |
   | pairs emitted            | 421,844 | 258,326 |
   | `pairs_ge_070`           |   1,729 |     596 |
   | `coherent_ge_070`        |     596 |     596 |
   | `coherent_share_ge_070`  |  34.47% |    100% |
   | `vin_only`               |     519 |       1 |
   | `vin_only_identity`      |     675 |     190 |
   | `triage_unbounded`       |     906 |     291 |
   | `triage_bounded`         |     186 |     186 |
   | `identical_name_triage`  |     436 |     153 |
   | `canary`                 |      11 |      11 |
   | predecessors with a pair |  46,792 |  41,969 |

   `coherent_share_ge_070` — the whole point of the gate — moved from 34.47% to **100%**: every pair the current sweep scores ≥ 0.70 now falls inside the window the scorer itself models, because a pair outside it can no longer reach that score at all. `coherent_ge_070`, `triage_bounded` and `canary` hold exactly flat, so the pairs the gate is supposed to preserve survived completely intact — nothing coherent was lost reaching this result.

   `identical_name_triage` fell 436 → 153, which `scripts/compare_sweeps.py` flags against its `within_10pct` expectation (exit code 1). Investigated rather than waved through: of the baseline's 436, exactly 283 have `gap_days < -180` — a successor registered more than 180 days _before_ the predecessor's shutdown, sharing an identical legal name coincidentally with respect to timing. That is precisely the shape this item exists to remove, and `triage_bounded` does not count it (it already excludes `gap_days < 0` and held flat at 186 both runs) because `identical_name_triage`'s own definition, unlike `triage_bounded`'s, carries no lower bound on `gap_days`. The metric predates this gate and does not distinguish direction; the fall is the gate working as intended against a metric that happens not to share its floor, not a lost match. Kept on that basis: `chameleon-candidates-000001` now points at the gated sweep, measured 2026-08-11.

   The 34.5% coherent-share figure the primary validation above measures against the 2026-08-06 run no longer describes the live sweep: gated to **100%** by this item, an incoherent pair cannot reach the ≥ 0.70 tier at all any more. That earlier paragraph is left as-is because it documents the measurement that motivated this gate, not because the number is still current.

   **Operational note for whoever runs this sweep next.** The full-scale run died silently three times before this result — no traceback, no error text, the log stopping mid-setup each time, always within a narrow band of roughly the same 48% of the predecessor population processed regardless of launch mechanism or host memory state at the time. Root cause undetermined: `log show` was non-functional in the sandbox this was diagnosed in, so no OS-level kill signature was ever captured, and a data-triggered cause (one carrier's outsized sub-document array, since the selector processes predecessors sorted by `dot_number` and the deaths clustered at a consistent relative position) could not be ruled out before two larger bisection runs and the full run itself all completed cleanly, which points toward something transient — contention, timing, host memory pressure — rather than a deterministic trigger in this task's own code. Separately, and worth fixing on its own regardless of this: `phase_entity_match.py` logs nothing between the setup phase and the final `entity-match complete` line, so a hung run and a genuinely-working long run are indistinguishable from the log, or from a document count that looks "stable" for the same reason a finished run's does. That gap is what made three separate death reports need independent verification before anyone could trust them, and will cost the next person the same time.

   Item 5 has no home elsewhere in this list, so it is recorded here. Items 1 and 3 have now been measured, above; items 2, 4 and 5 have not, and the ordering among the remainder is still an expectation, not a measurement — re-run both validation scripts after each to find out.

   **On item 5 specifically, researched 2026-08-08 and recorded so it is not re-researched.** The three ARCHI identifiers are not equally available, and two of them are not available at all:

   - **DUNS is obsolete.** The federal government retired it on 2022-04-04 in favour of the [UEI in SAM.gov](https://www.fema.gov/node/what-unique-entity-identifier-uei-and-how-it-related-system-award-management-sam). GAO-12-364 cites DUNS because it was written in 2012. DUNS still exists only as Dun & Bradstreet's commercial product — licensed, not bulk-downloadable. UEI is free but covers only entities registered for federal awards, which most small carriers are not.
   - **EIN is not public.** FMCSA collects it at registration and does not disseminate it; the IRS publishes EINs only for tax-exempt organizations. No public dataset of carrier EINs exists, and a tax identifier is an unlikely FOIA result.
   - **Officer name is obtainable, but not from FMCSA.** Verified: the carriers index has 47 fields and none is officer/owner/contact/principal, and `carriers.csv` has no such column. It would have to come from state Secretary of State registries (authoritative, but 50 different formats), an aggregator such as OpenCorporates (one API, paid for bulk), FMCSA's [L&I system](https://li-public.fmcsa.dot.gov/lihtml/liintro.html) (whose [data.gov entry documents HTML access only](https://catalog.data.gov/dataset/licensing-and-insurance), no bulk file), or FOIA.

   **A person-name field is already downloaded and unused.** `boc3_agents.attn_to_or_title` is indexed, populated on **75.4% of sampled rows**, and referenced by no signal in `entity-match.json`. It is the process agent's contact rather than the carrier's officer, so it is weaker than what ARCHI matches on and will be shared across a filing agent's entire book of business — the exact false-positive shape `ignore_values` exists to suppress. Recorded because it costs nothing to evaluate if item 5 is ever picked up: the data is already on disk and in the cluster. Not judged worth measuring yet.

1. **Name similarity is effectively triple-weighted, which currently ranks the wrong pairs highest.** `entity-match.json` lists three name signals over the same two fields (`name-phonetic` twice plus `name-token`, together 0.45 of the 0.94 total). Because `carrier_suffix_stop` strips `TRUCKING`/`LOGISTICS`/`LLC`/`INC`, most carrier names reduce to a single token, so the blended overlap becomes effectively binary. Measured: a pair with a byte-identical street, same state, and registration 45 days after the shutdown scored **0.3483 and was dropped** by the 0.35 floor, while `ABC TRUCKING LLC` vs `ABC LOGISTICS INC` in different states — sharing nothing but the token `ABC` — scored **0.5113 and was emitted**. A complete name change is the defining chameleon move, so this is backwards.

   The `min_signals` half of this is **fixed**: `PairScorer` now counts distinct evidence sources rather than signal instances, so the three name arms collapse into one and a name-only pair no longer clears a floor written to demand corroboration from a second, independent source. Against the shipped config, 8 signals resolve to 6 sources. What remains is the weighting itself — 0.45 of 0.94 still sits on one field — and that is a calibration decision rather than a structural one, so it should be made against real sweep output rather than guessed at a second time.

1. **Superseded indexes are never pruned.** Since the alias swap landed, a reload leaves the previous date-stamped index on disk, still holding a full copy of the dataset but no longer answering to the alias — so it is both invisible to every consumer and consuming disk. That trade was deliberate (a correctness fix should not delete data), but it means retention is now an unowned operational decision: nothing here says how many reloads back to keep or removes anything. Any existing cluster also still carries the indexes accumulated before the fix; each alias is repaired by the next run of its step, but the detached indexes remain.

1. **`AddressSignal` scores a street as an unordered bag of tokens, so the house number carries no more weight than the word `STREET`.** Two different buildings on one road overlap on everything except one token: `100 MAIN ST` against `200 MAIN ST` scores 0.75 containment, **0.525** after `fuzzy_scale`, on a signal weighted 0.20. Canonical-token contraction reduced but did not remove this, since it shrinks the denominator as well. The fix is to parse the street into house number, street name and secondary unit and score the parts separately, letting a house-number mismatch cap the result rather than cost one token. Deferred from the address synonym normalization work.

### Closed work items

1. **`entity-match` could over-select predecessors** because `out_of_service_orders` was mapped as a plain `object` rather than `nested`. A carrier with an ACTIVE 2015 order and an INACTIVE 2022 order would satisfy `status: ACTIVE` and `oos_date >= 2020` from two _different_ array elements, so it would be swept even though no single order matched both filters, and `TemporalSignal` could report a `shutdown_date` from the order the selector never intended to match. Fixed by mapping `out_of_service_orders` as `nested` (`configuration/carriers/index-mappings.json`) and querying it with a `nested` clause in `matching/predecessors.py`, gated on 9 new tests in `tests/test_predecessors.py`. The array path is read from config (`oos_path`, defaulting to the existing field name) rather than hardcoded, so the fix doesn't add a second FMCSA-specific literal on top of the ones the framework-generality item above already tracks. Verified correct against a synthetic document holding exactly the split-order shape (matched under `object`, correctly excluded under `nested`) and against the real July 2026 extract, where the predecessor population turned out to be unchanged (48,540 both ways, identical carrier for carrier) — the measured before/after table lives in the uncalibrated-thresholds item above, since that is where every other sweep measurement in this file is recorded.

## Fetching Data

Run `python3 fetch_commercial_carriers.py` from this directory to pull the latest carrier census, crash, inspection, inspection-unit (VIN), authority-history, out-of-service-order, and BOC-3 process-agent data from the data.transportation.gov Socrata API. Optionally pass `--dataset=<carriers|crashes|inspections|inspections_per_unit|auth_history|out_of_service_orders|boc3_agents>` to fetch just one. See `configuration/fetch-config.json` for dataset IDs; `carriers`/`auth_history`/`out_of_service_orders`/`boc3_agents` are unwindowed full pulls (no `date_field`), while `crashes`/`inspections`/`inspections_per_unit` use the lookback window described there.

## Datasets

| Step                    | Socrata ID  | Rows      | Purpose                                                                                                                                                                                                                                                   |
| ----------------------- | ----------- | --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `carriers`              | `kjg3-diqy` | 2,085,534 | Carrier census — the core entity each other dataset enriches.                                                                                                                                                                                             |
| `crashes`               | `aayw-vxb3` | 333,300   | Crash history per carrier.                                                                                                                                                                                                                                |
| `inspections`           | `fx4q-ay7w` | 5,647,567 | Vehicle inspection history per carrier.                                                                                                                                                                                                                   |
| `inspections-per-unit`  | `wt8s-2hbx` | 9,620,293 | Per-unit VIN/vehicle detail, enriched onto `inspections`.                                                                                                                                                                                                 |
| `auth-history`          | `9mw4-x3tu` | 4,941,925 | Every authority grant/revocation event per carrier — the reincarnation-timing signal for shadow/chameleon carriers (revoked → new DOT# granted soon after).                                                                                               |
| `out-of-service-orders` | `p2mt-9ige` | 394,963   | Carriers ordered out of service for safety, with reason/date/rescind date — flags who was shut down, a prime candidate for "who reappeared nearby afterward."                                                                                             |
| `boc3-agents`           | `2emp-mxtb` | 1,860,604 | Each carrier's legal process agent (name + address). **Weak signal:** only 89 distinct agents cover all 1.43M filings, so two unrelated carriers share an agent roughly 7% of the time by chance. Used only as IDF-weighted corroboration at weight 0.04. |

`auth-history`, `out-of-service-orders`, and `boc3-agents` were added specifically to support detecting shadow/chameleon commercial carriers — entities that get shut down and reappear under a new DOT number while reusing infrastructure. See `docs/superpowers/specs/2026-07-28-dot-commercial-shadow-carrier-datasets-design.md` for the full tiered survey (these three are "Tier 1"; insurance-churn and richer safety-history datasets were surveyed and deliberately deferred as Tier 2/3).

The earlier claim that a shared BOC-3 agent is "a harder signal to fake than a business address" did not survive measurement — the dataset carries no per-carrier information, only which of ~89 commercial filing companies a carrier paid. See the chameleon carrier matching design spec.

### Document IDs

Every dataset now has an `id_field` in its `index-config.json`, so re-running a dataset's `index-populate` phase against the same day's index overwrites existing documents instead of duplicating them. `id_field` can be a single column name (`carriers`: `dot_number`, `inspections`: `inspection_id`, `inspections-per-unit`: `insp_unit_id`, `crashes`: `crash_id`, `boc3-agents`: `docket_number`) or, for the two datasets with no single unique column, a JSON list of columns that `phase_index_populate.py` joins into a composite key (`out-of-service-orders`: `dot_number`+`oos_date`+`oos_reason`+`status`+`rescind_date`; `auth-history`: all 9 columns). See the "`id_field` fix" section of the shadow-carrier design spec above for the uniqueness analysis behind each choice.

### Enriched field mappings

`carriers/index-mappings.json` explicitly maps every field the `carriers-ingestion-setup` enrich policies slipstream onto a carrier document (`out_of_service_orders`, `auth_history`, `crashes`, `inspections`, and — since an earlier fix — `boc3_agents`). Without an explicit mapping, the first document indexed with a given enriched field determines its dynamic type, with two failure modes this repo has already hit once each (see the enrich-match and inspections-per-unit design specs):

- **Dynamic string fields get `terms`/`term`-hostile analysis.** A bare string dynamically maps as `text` with a `.keyword` multi-field, not `keyword` outright, and `.keyword` additionally carries `ignore_above: 256` — a long value silently stops being indexed there. `matching/predecessors.py`'s selector queries (`{"terms": {"out_of_service_orders.status": [...]}}`, `{"term": {"auth_history.disp_action_desc": "REVOKED"}}`) run against the exact-value field, not the analyzed one, and standard analysis lowercases the indexed token — an uppercase query term like `"ACTIVE"` then matches nothing. Confirmed directly against a live index: `terms` on the dynamically-mapped `out_of_service_orders.status` returned 0 hits for `["ACTIVE"]`; the same query against `.status.keyword` returned the expected hit. Pinning every enriched field to `keyword` closes this off for all four `PredecessorSelector` selectors at once, the same way Task 9 already had to pin `boc3_agents.co_name.keyword` for its own aggregation query.
- **Dynamic date detection can reject the whole document.** `out_of_service_orders.oos_date` would otherwise auto-detect as `date`, reopening the trap the inspections-per-unit design spec spent two fix rounds closing: a single malformed date value throws `document_parsing_exception` and Elasticsearch drops the entire carrier document, not just that field. `oos_date` is mapped `keyword` here to match how the standalone `out-of-service-orders` index already maps it, and because the chameleon-matching temporal signal (`matching/signals.py::parse_flexible_date`) parses dates client-side rather than relying on Elasticsearch date math — `PredecessorSelector`'s `oos_date_from` range query still works correctly on ISO-formatted keywords because they sort lexicographically.

`inspections.units.insp_unit_vehicle_id_number` is mapped `keyword` for the same reason — it carries the per-unit VIN through the two-level enrichment chain (`inspections-per-unit` → `inspections` → `carriers`) so the `vin-overlap` signal can see the 5.6M-row inspection VINs rather than only the 333K crash records.

### Name and address analyzers

`carriers/index-settings.json` defines the analyzers the chameleon matching relies on. Three choices in it are deliberate and easy to undo by accident.

**Two phonetic encoders, not one.** `name_phonetic` uses `double_metaphone` and `name_phonetic_bm` uses `beider_morse`, and `entity-match.json` weights them independently (0.22 and 0.13). They are complementary rather than redundant — measured against a live cluster:

| Input     | `double_metaphone` | `beider_morse` |
| --------- | ------------------ | -------------- |
| `SMITH`   | `SM0 XMT`          | `zmit`         |
| `SMYTH`   | `SM0 XMT`          | —              |
| `SCHMIDT` | `XMT SMT`          | `zmit`         |

Double-metaphone collides spelling variants exactly (`SMITH`/`SMYTH`); Beider-Morse collides cross-language ones (`SMITH`/`SCHMIDT`) that double-metaphone only partially matches. Dropping either arm loses a class of name evasion.

`double_metaphone` replaced the original `metaphone` outright: it emits a primary _and_ an alternate encoding, and `max_code_len` is raised from its default of 4 to 6 because four characters over-collide on company-name tokens. `beider_morse` is pinned to `["english","spanish"]` rather than left to guess — language guessing on short corporate tokens is unstable and makes output non-reproducible between runs. Note it emits multiple tokens only for names ambiguous across those languages (`GONZALEZ` → four; `SMITH` → one).

**A corporate-suffix stop filter runs before phonetic encoding, and only in the phonetic analyzers.** Nearly every carrier name ends in `LLC`, `INC`, `TRUCKING`, or `TRANSPORT`. Because scoring happens in Python there is no BM25 IDF to discount them, so left in place they would dominate every comparison. `.clean` keeps the full name. One consequence worth knowing: a carrier named literally `TRUCKING LLC` reduces to zero tokens, which the scorer treats as "no signal" rather than "no match".

**Streets have two subfields because one tokenizer cannot serve both purposes.** `street_clean` uses a `keyword` tokenizer for exact-after-normalization comparison; `street_tokens` uses a standard tokenizer, drops secondary-unit designators, and contracts street suffixes to a canonical token for fuzzy matching. `street_clean` also carries a `collapse_whitespace` filter: `punct_white` turns each punctuation mark into a space without collapsing the run, so without it `55 CEDAR ST, STE 4` and `55 CEDAR ST STE 4` were different single tokens and identical addresses silently produced zero candidates.

**Suffixes contract to one canonical token rather than expanding to both forms.** `street_suffix_canon` maps `st, str, strt => street`, not `st, street`. Under expansion a matching suffix contributed **2** to both the intersection and the set sizes in `containment`, so the least identifying part of an address counted twice: two unrelated same-state addresses sharing only `AVE` scored **0.420** on a signal weighted 0.20, and score **0.233** after the change. Measured over 2,000 sampled pairs, 40% of address scores move and 790 of them move down.

**Secondary-unit designators are removed, not canonicalized.** `unit_designator_stop` drops `STE`/`APT`/`UNIT`/`RM`/`BLDG` and keeps the unit number, so `STE 200` and `UNIT 200` still match on `200`. Mapping them all to one shared `unit` token was measured and rejected: it handed a free common token to any two addresses that merely both had a suite, lifting an unrelated pair in different states from 0.000 to 0.140. `KEY` is deliberately absent from the stop list because `KEY LARGO` and `KEY WEST` are street names.

**A `po_box_canon` char filter runs before the tokenizer.** The standard tokenizer keeps `P.O` as a single token, so `punct_white` — a _token_ filter — rewrote the period in place and emitted the literal token `p o`, which could never equal `po`. 43,799 `mailing_street` records use a punctuated form against 193,722 plain, and none of them could match across the spelling. A token filter cannot fix this; the repair has to happen before tokenization.

## Processing Steps

This data set is loaded and configured in 11 steps.

1. `crashes-ingestion-setup` - create a pipeline that coerces `dot_number` to a real integer in `_source` (fixes the enrich-match bug described in the design spec)
1. `crashes` - create an index and load the crash data
1. `inspections-per-unit` - create an index and load the per-unit VIN/vehicle data (FMCSA `wt8s-2hbx`)
1. `inspections-ingestion-setup` - create the enrichment index on `inspections-per-unit` and an ingestion pipeline that uses it
1. `inspections` - create an index and load the vehicle inspections data, enriched with per-unit VIN data via the pipeline from `inspections-ingestion-setup`
1. `auth-history` - create an index and load authority grant/revocation history (FMCSA `9mw4-x3tu`)
1. `out-of-service-orders` - create an index and load out-of-service order history (FMCSA `p2mt-9ige`)
1. `boc3-agents` - create an index and load BOC-3 legal process agent history (FMCSA `2emp-mxtb`)
1. `carriers-ingestion-setup` - create the enrichment indexes on `crashes`, `inspections`, `auth-history`, `out-of-service-orders`, and `boc3-agents`, and an ingestion pipeline that uses them
1. `carriers` - create an index and load the carriers data using the pipeline to enrich `carriers` with data from `crashes`, `inspections`, `auth-history`, `out-of-service-orders`, and `boc3-agents`
1. `chameleon-detection` - sweep shut-down carriers for likely successors and write ranked suspect pairs to `chameleon-candidates`

We could have combined some of the setup and indexing steps and used the phase boundaries but this seemed to be an easier partitioning scheme to use just needing the `--step` parameter for partial work

The first ten steps **load data**; `chameleon-detection` **looks for fraud**. They are independent: the sweep reads only `carriers-000001` and touches no CSV, so retuning thresholds, weights, or seeding means rerunning that one step — no reload. Conversely, a defect in the load is invisible to the sweep, which will happily score whatever is in the index and report a confident result.

**Refresh before every `*-ingestion-setup` step.** Enrich policy execution only sees _searchable_ documents, and Elasticsearch's 1-second default refresh interval means documents indexed moments earlier are invisible. Running the whole project in one `--project=DOT-Commercial` call reproduces this as a timing-dependent silent failure: every phase logs success and the carriers come out with no enrichment at all.

### Chameleon detection tuning

`configuration/chameleon-detection/entity-match.json` holds the knobs. Measured against the full July 2026 extract (2,085,534 carriers; 46,529 predecessors matching the configured selector):

| Setting                           | Value                  | Why                                                                                                                                                                                                                                                       |
| --------------------------------- | ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `predecessors.selector`           | `out-of-service`       | `revoked-authority` covers roughly half of every carrier ever registered — involuntary revocation for lapsed insurance is routine and is not evidence of a chameleon.                                                                                     |
| `candidates.max_candidates`       | `500`                  | Raised from 100. Cost 4× the runtime and produced **no new top-tier findings** — relevance ranking already had those near the top — but recovered mid-tier recall (+13% shared-VIN pairs). 89% of predecessors still truncate; going higher has poor ROI. |
| `candidates.seed_signals`         | includes `vin-overlap` | Without it a carrier that changes name, address and phone but keeps its trucks is unreachable at any `max_candidates`. Measured: 257 carriers per 400 predecessors reachable only this way.                                                               |
| `vin-overlap.conclusive`          | `true`                 | A shared VIN at weight 0.08 totals ~0.11 for a pair sharing nothing else, under the 0.35 floor, so every such pair was discarded. Clearing the floor by weight alone would need ~0.46 and would swamp every other pair.                                   |
| `vin-overlap.max_shared_carriers` | `5`                    | Any VIN on more than 5 carriers is not identifying. Without this, 94% of the apparent VIN recall gain was placeholder noise.                                                                                                                              |
| `scoring.min_signals`             | `2`                    | Counts distinct **evidence sources**, not signal instances — the three name signals read the same two fields and collapse to one.                                                                                                                         |

**`ignore_values` records the placeholder VINs this dataset actually contains.** FMCSA crash reports carry `GGGG` on 158 carriers, `UNKNOWN` on 79, `99999999999999999` on 51, plus runs of zeros, `-`, `.` and `*****************`. A binary shared-identifier signal scores 1.0 on those, so two carriers that both filed "UNKNOWN" read as a perfect identity match. The declared list covers what is known; the `max_shared_carriers` frequency scan catches the rest (203 values on the current extract). Both feed the same suppression set.

## Processing Phases

Each step can contain one or more phases as described by json configuration files. Phases represent the type of work that can be done in one or more steps. Each step can contain zero or more phases.
See [README.md](../README.md)

## Index Data

The data is organized and related as follows.

```mermaid
flowchart LR
    subgraph crashes-graph[crashes]
        crashes-alias[alias] -..-|points at| crashes[crashes index]
        crashes --> | optimized index| crashes-enrichment[crashes enrichment index]
    end
    subgraph inspections-graph[inspections]
        inspections-alias[alias] -..-|points at| inspections[inspections index]
        inspections --> |optimized index| inspections-enrichment[inspections enrichment index]
    end
    subgraph auth-history-graph[auth-history]
        auth-history-alias[alias] -..-|points at| auth-history[auth-history index]
        auth-history --> | optimized index| auth-history-enrichment[auth-history enrichment index]
    end
    subgraph oos-graph[out-of-service-orders]
        oos-alias[alias] -..-|points at| oos[out-of-service-orders index]
        oos --> | optimized index| oos-enrichment[out-of-service-orders enrichment index]
    end
    subgraph boc3-graph[boc3-agents]
        boc3-alias[alias] -..-|points at| boc3[boc3-agents index]
        boc3 --> | optimized index| boc3-enrichment[boc3-agents enrichment index]
    end
    subgraph carriers-graph[carriers]
        crashes-enrichment -.->|enriches| carriers-core
        inspections-enrichment -.->|enriches| carriers-core
        auth-history-enrichment -.->|enriches| carriers-core
        oos-enrichment -.->|enriches| carriers-core
        boc3-enrichment -.->|enriches| carriers-core
        carriers-alias[alias] -..- | points at| carriers-core[carriers index]
    end
```

## Flow

An integrated view of the steps and phases.

```mermaid
flowchart LR
    subgraph steps
        direction LR
        crashes-ingestion-setup-step[crashes ingestion setup]
        crashes-step[crashes]
        per-unit-step[inspections-per-unit]
        inspections-ingestion-setup-step[inspections ingestion setup]
        inspections-step[inspections]
        auth-history-step[auth-history]
        oos-step[out-of-service-orders]
        boc3-step[boc3-agents]
        carriers-step[carriers]
        carriers-ingestion-setup-step[carriers ingestion setup]
        chameleon-step[chameleon-detection]
        chameleon-validation-step[chameleon-validation]
    end

    subgraph indexes
        direction LR
        crashes-index["crashes-{day}-000001"] -..- crashes-alias[alias]
        per-unit-index["inspections-per-unit-{day}-000001"] -..- per-unit-alias[alias]
        inspections-index["inspections-{day}-000001"] -..- inspections-alias[alias]
        auth-history-index["auth-history-{day}-000001"] -..- auth-history-alias[alias]
        oos-index["out-of-service-orders-{day}-000001"] -..- oos-alias[alias]
        boc3-index["boc3-agents-{day}-000001"] -..- boc3-alias[alias]
        carriers-index["carriers-{day}-000001"] -..-> carriers-alias[alias]

        chameleon-index["chameleon-candidates-{day}-000001"] -..- chameleon-alias[alias]
        chameleon-validation-index["chameleon-validation-{day}-000001"] -..- chameleon-validation-alias[alias]

        per-unit-enrichment-index[inspections-per-unit enrichment]
        crashes-enrichment-index[crashes enrichment]
        inspections-enrichment-index[inspections enrichment]
        auth-history-enrichment-index[auth-history enrichment]
        oos-enrichment-index[out-of-service-orders enrichment]
        boc3-enrichment-index[boc3-agents enrichment]
    end

    subgraph datasets
        direction LR
        crashes-csv[crashes csv]
        per-unit-csv[inspections-per-unit csv]
        inspections-csv[inspections csv]
        auth-history-csv[auth-history csv]
        oos-csv[out-of-service-orders csv]
        boc3-csv[boc3-agents csv]
        carriers-csv[carriers csv]
    end

    subgraph crashes-pipelines[ crashes pipelines]
        direction LR
        crashes-pipeline
    end

    subgraph inspections-pipelines[ inspections pipelines]
        direction LR
        inspections-pipeline
    end

    subgraph carriers-pipelines[ carriers pipelines]
        direction LR
        enriching-pipeline
    end

    crashes-step -->|index-populate| crashes-pipeline
    crashes-step -->|index-map| crashes-index
    per-unit-step -->|index-map| per-unit-index
    per-unit-step -->|index-populate| per-unit-index
    inspections-step -->|index-map| inspections-index
    inspections-step -->|index-populate| inspections-pipeline
    auth-history-step -->|index-map| auth-history-index
    auth-history-step -->|index-populate| auth-history-index
    oos-step -->|index-map| oos-index
    oos-step -->|index-populate| oos-index
    boc3-step -->|index-map| boc3-index
    boc3-step -->|index-populate| boc3-index
    carriers-step --> | index-map | carriers-index
    carriers-step --> | index-populate| enriching-pipeline

    crashes-csv-->|import| crashes-step
    per-unit-csv -->|import| per-unit-step
    inspections-csv -->|import| inspections-step
    auth-history-csv -->|import| auth-history-step
    oos-csv -->|import| oos-step
    boc3-csv -->|import| boc3-step
    carriers-csv -->|import| carriers-step

    crashes-pipeline -->|populate| crashes-index
    inspections-pipeline -->|populate| inspections-index

    per-unit-enrichment-index -.->|enrich-policies| inspections-pipeline
    inspections-ingestion-setup-step -.->|enrichment-policies| per-unit-enrichment-index
    inspections-ingestion-setup-step -.->|"pipelines (create)"| inspections-pipeline

    crashes-enrichment-index -.->|enrich-policies| enriching-pipeline
    inspections-enrichment-index -.->|enrich-policies| enriching-pipeline
    auth-history-enrichment-index -.->|enrich-policies| enriching-pipeline
    oos-enrichment-index -.->|enrich-policies| enriching-pipeline
    boc3-enrichment-index -.->|enrich-policies| enriching-pipeline
    enriching-pipeline -->|populate| carriers-index

    crashes-ingestion-setup-step -.->|"pipelines (create)"| crashes-pipeline

    carriers-ingestion-setup-step -.->|enrichment-policies| crashes-enrichment-index
    carriers-ingestion-setup-step -.->|enrichment-policies| inspections-enrichment-index
    carriers-ingestion-setup-step -.->|enrichment-policies| auth-history-enrichment-index
    carriers-ingestion-setup-step -.->|enrichment-policies| oos-enrichment-index
    carriers-ingestion-setup-step -.->|enrichment-policies| boc3-enrichment-index
    carriers-ingestion-setup-step -.->|"pipelines (create)"| enriching-pipeline

    carriers-index -->|"source_index — read only"| chameleon-step
    chameleon-step -->|"index-create, index-map, entity-match"| chameleon-index
    entity-match-config["entity-match.json<br/>selector · seed_signals · weights<br/>ignore_values · max_shared_records"] -.-> chameleon-step
    carriers-index -.->|"corpus frequency scan<br/>finds non-identifying values"| chameleon-step

    chameleon-validation-step -->|"index-create, index-map"| chameleon-validation-index
    validation-scripts["scripts/measure_chameleon_shape.py<br/>scripts/measure_crash_lift.py"] -->|"writes result rows"| chameleon-validation-index
    chameleon-index -.->|"scored pairs — read only"| validation-scripts
    carriers-index -.->|"registration date, fleet size, state"| validation-scripts
    crashes-index -.->|"outcome the matcher never sees"| validation-scripts


```
