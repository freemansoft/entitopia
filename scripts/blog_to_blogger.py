"""Convert a docs/blog article to HTML for pasting into Blogger's HTML view.

Blogger is where the series is published, and it breaks a plain Markdown
conversion quietly in four ways, each pinned by tests/test_blog_to_blogger.py:

1. It renders every newline in post HTML as `<br>`. The drafts hard-wrap at
   80 columns, so everything is unwrapped and a newline is put back only
   between two top-level blocks. Newlines inside `<pre>` are content and stay.
2. Its theme owns presentation, so no `style`, `class` or `id` attribute is
   emitted. Anything inline would fight a stylesheet nobody here can see.
3. It does not run mermaid. Each fence becomes a visible `[DIAGRAM n GOES
   HERE]` paragraph, and its source is written out for rendering to a PNG.
   The draft keeps the fence as the editable source.
4. The post title is a separate field, so the H1 is dropped and printed.

GitHub links to `blob/main` are pinned to a commit, so the figures a reader
follows stay the ones the article quotes.

The newline rule comes from Flutter-AdaptiveCards' tool/blog/to_blogger.dart,
which met it first. This handles only the Markdown subset the series uses
(## headings, paragraphs, - and 1. lists with indented continuations, pipe
tables, fenced code, inline code, **bold**, [links](url), and raw HTML lines
such as <details>) rather than adding a Markdown library to requirements.txt.

Prints to stdout, so a generated page never lands in the tree:

    .venv/bin/python scripts/blog_to_blogger.py docs/blog/<article>.md | pbcopy
    .venv/bin/python scripts/blog_to_blogger.py docs/blog/<article>.md --diagrams-dir /tmp/d

Paste into Blogger's HTML view and publish from there: the Compose view
re-serializes the post, which is the usual way a pasted table gets flattened.
"""

import argparse
import html
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_BLOB = "https://github.com/freemansoft/entitopia/blob/"

# Tags that may start a line in the output. Anything else on its own line
# would sit inside a cell or item, where Blogger shows the newline as <br>.
BLOCK_TAGS = r"h[1-6]|p|ul|ol|table|figure|blockquote|pre|hr|details"

_LIST_ITEM = re.compile(r"(- |\d+\. )")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


@dataclass
class Converted:
    """The pieces a Blogger post needs: title field, HTML view, diagram sources."""

    title: str = ""
    html: str = ""
    diagrams: list[str] = field(default_factory=list)


def inline(text: str) -> str:
    """Render code spans, links and bold, escaping everything else.

    Code spans are lifted out first so a `**` or `[` inside one stays literal,
    and link targets are lifted out so escaping cannot touch a URL.
    """
    codes: list[str] = []
    links: list[tuple[str, str]] = []

    def keep_code(m):
        codes.append("<code>" + html.escape(m.group(1), quote=False) + "</code>")
        return f"\x00{len(codes) - 1}\x00"

    def keep_link(m):
        links.append((m.group(1), m.group(2)))
        return f"\x01{len(links) - 1}\x01"

    def put_link(m):
        label, url = links[int(m.group(1))]
        label = _BOLD.sub(r"<strong>\1</strong>", html.escape(label, quote=False))
        return f'<a href="{html.escape(url)}">{label}</a>'

    text = re.sub(r"`([^`]+)`", keep_code, text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", keep_link, text)
    text = _BOLD.sub(r"<strong>\1</strong>", html.escape(text, quote=False))
    text = re.sub(r"\x01(\d+)\x01", put_link, text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def _table(rows: list[str]) -> str:
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    head = "".join(f"<th>{inline(c)}</th>" for c in cells[0])
    body = "".join(
        "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>"
        for r in cells[1:]
        if not all(re.fullmatch(r":?-+:?", c) for c in r)
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _list(lines: list[str], i: int) -> tuple[str, int]:
    tag = "ul" if lines[i].startswith("- ") else "ol"
    items: list[str] = []
    while i < len(lines) and (_LIST_ITEM.match(lines[i]) or (items and lines[i].startswith("  "))):
        if _LIST_ITEM.match(lines[i]):
            items.append(_LIST_ITEM.sub("", lines[i], count=1))
        else:
            items[-1] += " " + lines[i].strip()
        i += 1
    return f"<{tag}>" + "".join(f"<li>{inline(t)}</li>" for t in items) + f"</{tag}>", i


def _fence(lines: list[str], i: int, result: Converted) -> tuple[str, int]:
    lang = lines[i][3:].strip()
    end = i + 1
    while not lines[end].startswith("```"):
        end += 1
    body = "\n".join(lines[i + 1 : end])
    if lang != "mermaid":
        return f"<pre><code>{html.escape(body, quote=False)}</code></pre>", end + 1
    result.diagrams.append(body)
    n = len(result.diagrams)
    note = f"<!-- DIAGRAM {n}: replace the paragraph below with the rendered PNG of diagram-{n}.mmd -->"
    return f"{note}\n<p>[DIAGRAM {n} GOES HERE]</p>", end + 1


def _paragraph(lines: list[str], i: int) -> tuple[str, int]:
    words = []
    while i < len(lines) and lines[i].strip() and not re.match(r"(#|```|\||- |\d+\. |<)", lines[i]):
        words.append(lines[i].strip())
        i += 1
    return f"<p>{inline(' '.join(words))}</p>", i


def _block(  # noqa: PLR0911 -- one return per kind of block; a lookup table would hide the order they are tried in
    lines: list[str], i: int, result: Converted
) -> tuple[str | None, int]:
    """Render the block starting at line i; None for a blank line or the title."""
    line = lines[i]
    if not line.strip():
        return None, i + 1
    if line.startswith("# "):
        result.title = line[2:].strip()
        return None, i + 1
    if line.startswith("## "):
        return f"<h2>{inline(line[3:].strip())}</h2>", i + 1
    if line.startswith("```"):
        return _fence(lines, i, result)
    if line.startswith("|"):
        end = i
        while end < len(lines) and lines[end].startswith("|"):
            end += 1
        return _table(lines[i:end]), end
    if line.lstrip().startswith("<"):
        return line.strip(), i + 1
    if _LIST_ITEM.match(line):
        return _list(lines, i)
    return _paragraph(lines, i)


def _unwrap(body: str) -> str:
    pres: list[str] = []

    def keep_pre(m):
        pres.append(m.group(0))
        return f" \x00{len(pres) - 1}\x00 "

    body = re.sub(r"<pre>[\s\S]*?</pre>", keep_pre, body)
    body = re.sub(r"\s*\n\s*", " ", body).strip()
    body = re.sub(rf"(</(?:{BLOCK_TAGS})>|-->) *(<(?:{BLOCK_TAGS})[ >]|<!--|\x00)", r"\1\n\2", body)
    return re.sub(r"\x00(\d+)\x00", lambda m: pres[int(m.group(1))], body)


def convert(markdown: str, commit: str) -> Converted:
    """Convert one article; `commit` is the sha `blob/main` links are pinned to."""
    lines = markdown.split("\n")
    result = Converted()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        rendered, i = _block(lines, i, result)
        if rendered is not None:
            blocks.append(rendered)
    body = "\n".join(blocks).replace(REPO_BLOB + "main/", REPO_BLOB + commit + "/")
    result.html = _unwrap(body)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("article", type=Path)
    parser.add_argument("--commit", help="sha to pin blob/main links to (default: HEAD)")
    parser.add_argument("--diagrams-dir", type=Path, help="write each mermaid fence here as diagram-<n>.mmd")
    args = parser.parse_args()

    commit = args.commit or subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    result = convert(args.article.read_text(encoding="utf-8"), commit)
    sys.stdout.write(result.html + "\n")
    print(f"title: {result.title}\nlinks pinned to: {commit}", file=sys.stderr)
    if args.diagrams_dir:
        args.diagrams_dir.mkdir(parents=True, exist_ok=True)
        for n, source in enumerate(result.diagrams, 1):
            (args.diagrams_dir / f"diagram-{n}.mmd").write_text(source + "\n", encoding="utf-8")
        print(f"diagrams: {len(result.diagrams)} written to {args.diagrams_dir}", file=sys.stderr)
    elif result.diagrams:
        print(f"diagrams: {len(result.diagrams)} (pass --diagrams-dir to write their sources)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
