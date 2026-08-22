"""
Phase 2 — Chunking

Turns raw Kubernetes markdown into retrieval-ready chunks with metadata,
respecting section boundaries (headings) and never splitting inside code blocks.

Handles two distinct document shapes found in kubernetes/website:

1. Prose docs (concepts/, tasks/, tutorials/) — regular markdown, but littered with Hugo
   shortcodes ({{< caution >}}...{{< /caution >}}, {{< skew currentVersion >}}) that need
   cleaning before chunking, or they'd pollute embeddings with template syntax.

2. Auto-generated CLI reference docs (reference/generated/kubectl/...) — headings are Hugo
   shortcodes ({{% heading "options" %}}), and command flags are encoded as raw HTML <table>
   elements, not markdown. These get parsed into readable "- `--flag`: description" bullet
   lists instead of being dropped or embedded as raw HTML.

Input:  data/raw/website/content/en/docs/**/*.md
Output: data/processed/chunks.jsonl

Usage:
    uv run python ingestion/chunk_docs.py
"""

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

# --- Config -------------------------------------------------------------

RAW_DOCS_DIR = Path("data/raw/website/content/en/docs")
OUTPUT_PATH = Path("data/processed/chunks.jsonl")

MAX_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 100

# Auto-generated CLI reference sections (Synopsis, a single Options flag, etc.) are often
# well under 100 chars but still complete, useful, information-dense units — e.g. "-h, --help:
# help for use-context" is only ~35 chars but is exactly what a query like "what does kubectl
# config use-context do" should retrieve. Applying the prose MIN_CHUNK_CHARS threshold here
# would silently drop nearly all reference content. Use a much lower floor for these instead.
MIN_CHUNK_CHARS_AUTO_GEN = 20

# The "Inherited Options" / "parentoptions" table is identical boilerplate on essentially
# every kubectl/kubeadm/kubelet reference page (global flags like --kubeconfig, --namespace).
# Chunking and embedding it hundreds of times wastes compute and pollutes the vector store
# with near-duplicate vectors. Skip these sections by default; flip to False if you decide
# you want them searchable per-page after all.
SKIP_INHERITED_OPTIONS = True

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
CODE_FENCE_RE = re.compile(r"^\s*```", re.MULTILINE)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
TABLE_RE = re.compile(r"<table.*?</table>", re.DOTALL)

# {{% heading "options" %}} -> ## Options
HUGO_HEADING_RE = re.compile(r'\{\{%\s*heading\s+"([^"]+)"\s*%\}\}')
HEADING_NAME_MAP = {
    "synopsis": "Synopsis",
    "examples": "Examples",
    "options": "Options",
    "parentoptions": "Inherited Options",
    "seealso": "See Also",
}

# Paired block shortcodes: {{< caution >}} ... {{< /caution >}}
HUGO_BLOCK_RE = re.compile(r"\{\{<\s*(\w+)[^>]*>\}\}(.*?)\{\{<\s*/\1\s*>\}\}", re.DOTALL)
CALLOUT_NAMES = {"caution", "warning", "note", "tip"}

# Leftover self-closing shortcodes: {{< skew currentVersion >}}. These reference a version
# number resolved dynamically at site-build time, which we don't have here — rather than
# guess or hardcode a version that will go stale, drop them and let the punctuation cleanup
# below smooth over the gap (e.g. "version {{< skew currentVersion >}}, only" -> "version, only").
HUGO_INLINE_RE = re.compile(r"\{\{<[^>]*>\}\}")
WHITESPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:])")
MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


@dataclass
class Chunk:
    chunk_id: str
    source_file: str
    section_heading: str
    heading_path: str
    text: str
    char_count: int
    k8s_version: str | None
    doc_url: str | None
    content_type: str | None
    auto_generated: bool


def extract_front_matter(md_text: str) -> tuple[dict, str]:
    if md_text.startswith("---"):
        parts = md_text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                meta = {}
            return meta, parts[2].strip()
    return {}, md_text.strip()


def html_table_to_bullets(table_html: str) -> str:
    """
    Convert a raw HTML options/field table (as used in kubectl reference docs) into a
    readable bullet list: "- `--flag string` (default: X): description".

    The tables alternate rows: one row holding the flag/field name (a single <td colspan=2>),
    followed by a row with an empty <td> and a <td> holding the description.
    """
    soup = BeautifulSoup(table_html, "html.parser")
    rows = soup.find_all("tr")

    lines = []
    pending_name = None
    for row in rows:
        cells = row.find_all("td")
        if len(cells) == 1 or (len(cells) == 2 and cells[0].get("colspan")):
            pending_name = cells[0].get_text(separator=" ", strip=True)
        elif len(cells) == 2 and pending_name:
            desc = cells[1].get_text(separator=" ", strip=True)
            name = pending_name.replace("\xa0", " ").strip()
            pending_name = None

            default_val = None
            if "Default:" in name:
                name, default_val = (p.strip() for p in name.split("Default:", 1))

            if default_val:
                lines.append(f"- `{name}` (default: {default_val}): {desc}")
            else:
                lines.append(f"- `{name}`: {desc}")

    return "\n".join(lines)


def convert_tables(text: str) -> str:
    return TABLE_RE.sub(lambda m: html_table_to_bullets(m.group(0)), text)


def clean_hugo_shortcodes(text: str) -> str:
    text = HTML_COMMENT_RE.sub("", text)
    text = convert_tables(text)

    def _heading_repl(m: re.Match) -> str:
        # NB: the surrounding "## " is already present in the source markdown line
        # (e.g. "## {{% heading "synopsis" %}}") — only replace the shortcode itself,
        # not the heading marker, or headings end up doubled ("## ## Synopsis").
        key = m.group(1).lower()
        return HEADING_NAME_MAP.get(key, key.replace("-", " ").title())

    text = HUGO_HEADING_RE.sub(_heading_repl, text)

    def _block_repl(m: re.Match) -> str:
        name, inner = m.group(1), m.group(2).strip()
        if name.lower() in CALLOUT_NAMES:
            return f"**{name.capitalize()}:** {inner}"
        return inner

    text = HUGO_BLOCK_RE.sub(_block_repl, text)
    text = HUGO_INLINE_RE.sub("", text)

    # Clean up any orphaned punctuation/spacing left behind by a dropped shortcode
    # (e.g. "version , only" -> "version, only").
    text = WHITESPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = MULTI_SPACE_RE.sub(" ", text)

    return text


def split_by_headings(body: str) -> list[tuple[str, str, str]]:
    matches = list(HEADING_RE.finditer(body))
    if not matches:
        return [("", "", body)]

    sections = []
    if matches[0].start() > 0:
        intro = body[: matches[0].start()].strip()
        if intro:
            sections.append(("", "Introduction", intro))

    for i, match in enumerate(matches):
        level, heading_text = match.group(1), match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        sections.append((level, heading_text, content))

    return sections


def _expand_oversized_blocks(paragraphs: list[str], max_chars: int) -> list[str]:
    """
    The main splitter only breaks at paragraph boundaries (blank-line-separated blocks).
    That assumption fails for content like a giant options/field table converted to
    bullets (html_table_to_bullets joins with single \\n, not \\n\\n) — the whole table
    can end up as ONE "paragraph" with no blank-line breaks at all, which the main loop
    then can't fragment further no matter how large it is. This is what produced a real
    366,599-char chunk against a 1200-char target on the actual corpus.

    Splitting by single newline isn't always enough either: some individual bullet lines
    are themselves oversized (e.g. kube-apiserver's --feature-gates flag has a single
    description line enumerating every feature gate, several thousand characters on its
    own) — this showed up as a real 10,572-char chunk even after the first fix. So this
    recurses: any fragment still too large after a split pass gets split again, falling
    back to raw character slicing only when there's no newline left to split on.
    """
    expanded = []
    for para in paragraphs:
        if len(para) <= max_chars:
            expanded.append(para)
            continue

        lines = para.split("\n")
        if len(lines) > 1:
            expanded.extend(_expand_oversized_blocks(lines, max_chars))
        else:
            expanded.extend(para[i : i + max_chars] for i in range(0, len(para), max_chars))
    return expanded


def split_respecting_code_blocks(text: str, max_chars: int) -> list[str]:
    """
    Split long section content into ~max_chars pieces, never splitting inside a fenced
    code block — except as a last resort (see HARD_MAX_MULTIPLIER below).

    Safety valve 1: some pages have an odd total count of ``` fence markers within a single
    section (a stray ``` inside a table cell, an unclosed fence, non-backtick fences, etc.).
    That makes the in_code_block toggle get stuck True indefinitely, which without a safety
    valve produces one runaway chunk covering the entire rest of the section.
    Safety valve 2: see _expand_oversized_blocks — handles the case where there's no
    paragraph boundary to split on at all.
    Together these bound worst-case chunk size, whereas before either fix a chunk could
    hit 366,599 chars against a 1200-char target on the real corpus.
    """
    if len(text) <= max_chars:
        return [text]

    HARD_MAX_MULTIPLIER = 4
    hard_max = max_chars * HARD_MAX_MULTIPLIER
    # Note: this is an approximate cap, not an exact one — since the check happens after
    # appending a paragraph, an unlucky final paragraph can push a chunk slightly past
    # hard_max. That's fine; the goal is bounding runaway growth, not hitting it precisely.

    paragraphs = _expand_oversized_blocks(text.split("\n\n"), max_chars)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    in_code_block = False

    for para in paragraphs:
        fence_count = len(CODE_FENCE_RE.findall(para))
        would_toggle = fence_count % 2 == 1

        current.append(para)
        current_len += len(para) + 2

        if would_toggle:
            in_code_block = not in_code_block

        if current_len >= max_chars and not in_code_block:
            chunks.append("\n\n".join(current).strip())
            current = []
            current_len = 0
        elif current_len >= hard_max:
            # Safety valve: force-flush even mid-code-block. A fence count mismatch means
            # our assumption about being "inside" a code block is unreliable anyway, so
            # there's no clean split point to wait for.
            chunks.append("\n\n".join(current).strip())
            current = []
            current_len = 0
            in_code_block = False  # reset — treat whatever follows as fresh prose

    if current:
        chunks.append("\n\n".join(current).strip())

    return [c for c in chunks if c]


def process_file(filepath: Path, docs_root: Path) -> list[Chunk]:
    raw_text = filepath.read_text(encoding="utf-8", errors="ignore")
    meta, body = extract_front_matter(raw_text)
    body = clean_hugo_shortcodes(body)

    rel_path = filepath.relative_to(docs_root)
    doc_url = f"https://kubernetes.io/docs/{rel_path.with_suffix('')}/"
    k8s_version = meta.get("min-kubernetes-server-version")
    page_title = meta.get("title", filepath.stem)
    content_type = meta.get("content_type")
    auto_generated = bool(meta.get("auto_generated", False))

    min_chars = MIN_CHUNK_CHARS_AUTO_GEN if auto_generated else MIN_CHUNK_CHARS

    chunks: list[Chunk] = []
    for level, heading, content in split_by_headings(body):
        if not content or len(content) < min_chars:
            continue

        if SKIP_INHERITED_OPTIONS and heading.strip().lower() == "inherited options":
            continue

        heading_path = f"{page_title} > {heading}" if heading else page_title
        pieces = split_respecting_code_blocks(content, MAX_CHUNK_CHARS)

        for idx, piece in enumerate(pieces):
            if len(piece) < min_chars:
                continue
            chunk_id = f"{rel_path.as_posix()}::{heading or 'intro'}::{idx}"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    source_file=rel_path.as_posix(),
                    section_heading=heading or "Introduction",
                    heading_path=heading_path,
                    text=piece,
                    char_count=len(piece),
                    k8s_version=k8s_version,
                    doc_url=doc_url,
                    content_type=content_type,
                    auto_generated=auto_generated,
                )
            )

    return chunks


def main():
    if not RAW_DOCS_DIR.exists():
        raise SystemExit(
            f"Raw docs directory not found: {RAW_DOCS_DIR}\n"
            "Run ingestion/fetch_docs.py first (Phase 1)."
        )

    md_files = sorted(RAW_DOCS_DIR.rglob("*.md"))
    print(f"Found {len(md_files)} markdown files under {RAW_DOCS_DIR}")

    all_chunks: list[Chunk] = []
    for filepath in md_files:
        all_chunks.extend(process_file(filepath, RAW_DOCS_DIR))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(asdict(chunk)) + "\n")

    char_counts = [c.char_count for c in all_chunks]
    auto_gen_count = sum(1 for c in all_chunks if c.auto_generated)
    print(f"Wrote {len(all_chunks)} chunks to {OUTPUT_PATH}")
    if char_counts:
        print(f"  avg chars/chunk:          {sum(char_counts) / len(char_counts):.0f}")
        print(f"  min/max chars:            {min(char_counts)} / {max(char_counts)}")
        print(f"  from auto-generated docs: {auto_gen_count} ({auto_gen_count / len(all_chunks):.0%})")


if __name__ == "__main__":
    main()
