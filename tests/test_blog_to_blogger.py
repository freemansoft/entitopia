"""Tests for scripts/blog_to_blogger.py.

The blog articles are published by pasting this script's output into
Blogger's HTML view, and Blogger fails quietly on each thing pinned here:

- It renders every newline in post HTML as `<br>`. A newline inside a table
  or list puts a stray line break in a cell or item, and the post looks fine
  in the HTML view, so nobody notices until it is live.
- Its theme owns presentation. A `style`, `class` or `id` attribute fights a
  stylesheet the author cannot see.
- It does not run mermaid. A fence that passed through would publish the
  diagram's source as text, so each one becomes a visible placeholder.
- Links to `blob/main` drift as the repo moves, so a reader following a
  figure's link would find a different figure. They are pinned to a commit.

Example names are invented; a real carrier name in a fixture next to a
matcher is the publication CLAUDE.md forbids.

Loaded by path rather than import because scripts/ is a directory of
standalone tools, not an importable package.
"""

import importlib.util
import re
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "blog_to_blogger.py"
_COMMIT = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(scope="module")
def btb():
    spec = importlib.util.spec_from_file_location("blog_to_blogger", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _convert(btb, md):
    return btb.convert(md, _COMMIT)


def test_title_is_dropped_and_returned(btb):
    result = _convert(btb, "# A title\n\nBody text.\n")
    assert result.title == "A title"
    assert "<h1" not in result.html
    assert result.html == "<p>Body text.</p>"


def test_wrapped_paragraph_is_joined_onto_one_line(btb):
    result = _convert(btb, "One line\nwrapped here\nand here.\n")
    assert result.html == "<p>One line wrapped here and here.</p>"


def test_newlines_sit_only_between_top_level_blocks(btb):
    md = (
        "## Heading\n\nA paragraph.\n\n"
        "- first item\n  continues\n- second\n\n"
        "1. one\n2. two\n   continues\n\n"
        "| A | B |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |\n\n"
        "<details>\n<summary>Show</summary>\n\n| T | D |\n| - | - |\n| x | y |\n\n</details>\n"
    )
    html = _convert(btb, md).html
    for block in re.findall(r"<(ul|ol|table|details)>.*?</\1>", html, flags=re.S):
        assert "\n" not in block
    for inner in re.findall(r"<(?:ul|ol|table|details)>(.*?)</(?:ul|ol|table|details)>", html, flags=re.S):
        assert "\n" not in inner
    assert "<li>first item continues</li>" in html
    assert "<li>two continues</li>" in html
    assert html.split("\n")[0] == "<h2>Heading</h2>"


def test_code_block_keeps_its_newlines(btb):
    md = "Before.\n\n```bash\ncurl -s localhost:9200\n# comment <tag>\n```\n\nAfter.\n"
    html = _convert(btb, md).html
    assert "<pre><code>curl -s localhost:9200\n# comment &lt;tag&gt;</code></pre>" in html


def test_no_styling_attributes(btb):
    md = "## H\n\nText with `code`, **bold** and [a link](https://example.com).\n\n| A |\n| - |\n| 1 |\n"
    html = _convert(btb, md).html
    assert not re.search(r"\s(style|class|id)=", html)


def test_inline_code_inside_link_and_bold(btb):
    md = "See [`compare_pair_ids.py`](https://example.com/x) and **bold `x`**.\n"
    html = _convert(btb, md).html
    assert '<a href="https://example.com/x"><code>compare_pair_ids.py</code></a>' in html
    assert "<strong>bold <code>x</code></strong>" in html


def test_html_special_characters_are_escaped_outside_raw_html(btb):
    html = _convert(btb, "A < B & `C > D`.\n").html
    assert html == "<p>A &lt; B &amp; <code>C &gt; D</code>.</p>"


def test_mermaid_fence_becomes_placeholder_and_source_is_returned(btb):
    md = "Text.\n\n```mermaid\nflowchart LR\n  A --> B\n```\n\nMore.\n"
    result = _convert(btb, md)
    assert "flowchart" not in result.html
    assert "<p>[DIAGRAM 1 GOES HERE]</p>" in result.html
    assert result.diagrams == ["flowchart LR\n  A --> B"]


def test_main_links_are_pinned_to_the_commit(btb):
    md = "[README](https://github.com/freemansoft/entitopia/blob/main/README.md#glossary)\n"
    html = _convert(btb, md).html
    assert f"blob/{_COMMIT}/README.md#glossary" in html
    assert "blob/main/" not in html


def test_repo_root_link_is_left_alone(btb):
    html = _convert(btb, "[entitopia](https://github.com/freemansoft/entitopia) is here.\n").html
    assert '<a href="https://github.com/freemansoft/entitopia">entitopia</a>' in html


def test_table_header_and_body_cells(btb):
    html = _convert(btb, "| Name | Score |\n| ---- | ----: |\n| `QUILBY` | 0.5 |\n").html
    assert html == (
        "<table><thead><tr><th>Name</th><th>Score</th></tr></thead>"
        "<tbody><tr><td><code>QUILBY</code></td><td>0.5</td></tr></tbody></table>"
    )


def test_article_one_converts_cleanly(btb):
    article = Path(__file__).resolve().parent.parent / "docs" / "blog" / "2026-08-30-01-finding-the-same-business.md"
    result = btb.convert(article.read_text(encoding="utf-8"), _COMMIT)
    without_code = re.sub(r"<pre>.*?</pre>", "", result.html, flags=re.S)
    assert not re.search(r"\*\*|\]\(|^#|^\|", without_code, flags=re.M)
    assert result.title
    assert len(result.diagrams) == 1
