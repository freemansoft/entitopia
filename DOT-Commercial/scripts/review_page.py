"""Turn a review sample into a page a human can label, and summarize the labels.

sample_pairs_for_review.py draws the pairs; this is what makes judging them
cheap enough to actually happen. The sample sat unread for months as raw
JSONL. A reviewer weighing a pair needs both sides next to each other with the
evidence that scored them, and a way to record a verdict that survives closing
the tab.

Both outputs stay under the gitignored */data/: the page embeds the legal names
and addresses of carriers the matcher flagged, so it must never be committed
or published. It is a local file, opened from disk.

    .venv/bin/python DOT-Commercial/scripts/review_page.py render
    .venv/bin/python DOT-Commercial/scripts/review_page.py summarize --sample <labelled.jsonl>

Labelling measures precision per score band: how many surfaced pairs are real.
It cannot measure recall, because a chameleon the sweep never paired is not in
the sample. See the recall item in DOT-Commercial/README.md.
"""

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_SAMPLER = Path(__file__).resolve().parent / "sample_pairs_for_review.py"
_spec = importlib.util.spec_from_file_location("sample_pairs_for_review", _SAMPLER)
_sampler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sampler)

DEFAULT_SAMPLE = "DOT-Commercial/data/precision/review-sample.jsonl"
DEFAULT_PAGE = "DOT-Commercial/data/precision/review.html"

# The only verdicts the summary understands. "unsure" is a real answer, not a
# skipped row: it is reported, but kept out of precision, because counting it
# as a miss would understate precision in exactly the bands reviewers find hard.
VERDICTS = ("chameleon", "not-chameleon", "unsure")


def load_sample(path):
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def render_page(rows, source_name):
    """The review page as one self-contained HTML string.

    The sample is embedded as JSON and rendered with textContent, never parsed
    as markup. A carrier name is free text from a public filing; built into
    HTML directly, a name containing "</" or "<b>" would show the reviewer a
    different record from the one being judged. "</" is escaped so no name can
    close the script tag that carries the data.
    """
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    # Keyed on the sample's content, so verdicts saved in the browser for one
    # draw never appear on a different draw that happens to share a filename.
    storage_key = "entitopia-review:" + hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]
    return (
        _PAGE.replace("__SOURCE__", json.dumps(source_name).replace("</", "<\\/"))
        .replace("__KEY__", json.dumps(storage_key))
        .replace("__VERDICTS__", json.dumps(list(VERDICTS)))
        .replace("__DATA__", data)
    )


def summarize(rows):
    """Verdict counts and precision for each review band.

    Precision is chameleon / (chameleon + not-chameleon): the share of decided
    pairs that are real. None when a band has no decided pair, because a band
    nobody judged has no precision, and 0.0 would read as "all wrong".
    """
    summary = {}
    for _, _, label in _sampler.SAMPLE_BANDS:
        summary[label] = dict.fromkeys(VERDICTS, 0)
        summary[label]["unreviewed"] = 0
    for row in rows:
        band = _sampler._band(row.get("total_score") or 0.0)
        if band is None:
            continue
        verdict = row.get("verdict")
        summary[band][verdict if verdict in VERDICTS else "unreviewed"] += 1
    for counts in summary.values():
        decided = counts["chameleon"] + counts["not-chameleon"]
        counts["precision"] = counts["chameleon"] / decided if decided else None
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render", help="write the review page for a sample")
    render.add_argument("--sample", default=DEFAULT_SAMPLE)
    render.add_argument("--out", default=DEFAULT_PAGE)
    summary = sub.add_parser("summarize", help="precision per band from a labelled sample")
    summary.add_argument("--sample", default=DEFAULT_SAMPLE)
    args = parser.parse_args()

    rows = load_sample(args.sample)
    if args.command == "render":
        Path(args.out).write_text(render_page(rows, Path(args.sample).name), encoding="utf-8")
        print("wrote {} pairs to {}; open it in a browser".format(len(rows), args.out))
        return 0

    print("{:<10} {:>9} {:>13} {:>6} {:>10} {:>9}".format(
        "band", "chameleon", "not-chameleon", "unsure", "unreviewed", "precision"))
    for band, counts in summarize(rows).items():
        precision = "-" if counts["precision"] is None else "{:.0%}".format(counts["precision"])
        print("{:<10} {:>9} {:>13} {:>6} {:>10} {:>9}".format(
            band, counts["chameleon"], counts["not-chameleon"], counts["unsure"], counts["unreviewed"], precision))
    return 0


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chameleon Pair Review</title>
<style>
:root { --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --card:#fff; --line:#e7e5e4;
        --accent:#2563eb; --yes:#15803d; --no:#b91c1c; --maybe:#a16207; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --card:#292524; --line:#44403c;
          --accent:#60a5fa; --yes:#4ade80; --no:#f87171; --maybe:#facc15; }
}
* { box-sizing: border-box; }
body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
       font:15px/1.45 system-ui, -apple-system, sans-serif; }
header { position:sticky; top:0; background:var(--bg); padding:8px 0 12px;
         border-bottom:1px solid var(--line); margin-bottom:16px; z-index:1; }
h1 { font-size:18px; margin:0 0 4px; }
.muted { color:var(--muted); font-size:13px; }
.bar { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-top:8px; }
button { font:inherit; padding:6px 12px; border:1px solid var(--line); border-radius:6px;
         background:var(--card); color:var(--fg); cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px;
        padding:12px 14px; margin:0 auto 14px; max-width:960px; }
.card.decided { border-left:4px solid var(--accent); }
.head { display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; font-weight:600; }
table { width:100%; border-collapse:collapse; margin:8px 0; font-size:14px; }
th, td { text-align:left; padding:4px 6px; border-bottom:1px solid var(--line); vertical-align:top;
         overflow-wrap:anywhere; }
th { color:var(--muted); font-weight:500; width:22%; }
.sig { font-size:13px; color:var(--muted); }
.verdicts { display:flex; gap:14px; flex-wrap:wrap; margin:8px 0 6px; }
.verdicts label { display:flex; gap:4px; align-items:center; cursor:pointer; }
.v-chameleon { color:var(--yes); } .v-not-chameleon { color:var(--no); } .v-unsure { color:var(--maybe); }
textarea { width:100%; min-height:38px; font:inherit; font-size:14px; padding:6px;
           background:var(--bg); color:var(--fg); border:1px solid var(--line); border-radius:6px; }
</style>
</head>
<body>
<header>
  <h1>Chameleon pair review</h1>
  <div class="muted">Local file &mdash; it holds real carrier names. Do not commit, share or publish it.
    Verdicts save in this browser as you go. Download the labelled sample when you finish.</div>
  <div class="bar">
    <span id="progress"></span>
    <button class="primary" id="download">Download labelled sample</button>
    <span class="muted" id="source"></span>
  </div>
</header>
<main id="pairs"></main>
<script type="application/json" id="sample">__DATA__</script>
<script>
const SOURCE = __SOURCE__, KEY = __KEY__, VERDICTS = __VERDICTS__;
const rows = JSON.parse(document.getElementById("sample").textContent);
let saved = {};
try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { saved = {}; }
rows.forEach((r, i) => {
  const s = saved[i];
  if (s) { r.verdict = s.verdict; r.note = s.note; }
});
function persist() {
  const out = {};
  rows.forEach((r, i) => { if (r.verdict || r.note) out[i] = { verdict: r.verdict || null, note: r.note || "" }; });
  try { localStorage.setItem(KEY, JSON.stringify(out)); } catch (e) {}
}
function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined && text !== null) n.textContent = String(text);
  if (cls) n.className = cls;
  return n;
}
function side(p, key) { return p && p[key] !== undefined && p[key] !== null ? p[key] : ""; }
const FIELDS = [["DOT number","dot_number"],["Legal name","legal_name"],["DBA name","dba_name"],
  ["Street","phy_street"],["City","phy_city"],["State","phy_state"]];
function progress() {
  const done = rows.filter(r => r.verdict).length;
  document.getElementById("progress").textContent = done + " of " + rows.length + " judged";
}
const main = document.getElementById("pairs");
rows.forEach((r, i) => {
  const card = el("section", null, "card" + (r.verdict ? " decided" : ""));
  const head = el("div", null, "head");
  head.append(el("span", "#" + (i + 1) + "  score " + Number(r.total_score).toFixed(3)),
              el("span", "gap " + (r.gap_days === null || r.gap_days === undefined ? "n/a" : r.gap_days + " days")));
  card.append(head);
  const t = el("table"), hr = el("tr");
  hr.append(el("th", ""), el("th", "Predecessor (shut down)"), el("th", "Successor (registered)"));
  t.append(hr);
  FIELDS.forEach(([label, key]) => {
    const tr = el("tr");
    tr.append(el("th", label), el("td", side(r.predecessor, key)), el("td", side(r.successor, key)));
    t.append(tr);
  });
  const dates = el("tr");
  dates.append(el("th", "Date"),
    el("td", side(r.predecessor, "shutdown_date") + (side(r.predecessor, "shutdown_reason") ? " (" + r.predecessor.shutdown_reason + ")" : "")),
    el("td", side(r.successor, "add_date")));
  t.append(dates);
  card.append(t);
  const sig = (r.signals || []).filter(s => s.score).map(s =>
    s.signal_type + " " + Number(s.score).toFixed(2) + " (+" + Number(s.contribution || 0).toFixed(3) + ")");
  card.append(el("div", "Signals: " + (sig.length ? sig.join(", ") : (r.matched_on || []).join(", ")), "sig"));
  const vs = el("div", null, "verdicts");
  VERDICTS.forEach(v => {
    const lab = el("label", null, "v-" + v), input = el("input");
    input.type = "radio"; input.name = "v" + i; input.value = v; input.checked = r.verdict === v;
    input.addEventListener("change", () => { r.verdict = v; card.classList.add("decided"); persist(); progress(); });
    lab.append(input, document.createTextNode(v));
    vs.append(lab);
  });
  card.append(vs);
  const note = el("textarea");
  note.placeholder = "Why (optional)"; note.value = r.note || "";
  note.addEventListener("input", () => { r.note = note.value; persist(); });
  card.append(note);
  main.append(card);
});
document.getElementById("source").textContent = "from " + SOURCE;
document.getElementById("download").addEventListener("click", () => {
  const body = rows.map(r => JSON.stringify(r)).join("\\n") + "\\n";
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([body], { type: "application/x-ndjson" }));
  a.download = SOURCE.replace(/\\.jsonl$/, "") + "-labelled.jsonl";
  document.body.append(a); a.click(); a.remove();
});
progress();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
