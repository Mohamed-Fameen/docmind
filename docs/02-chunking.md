# Phase 2 — Loading & Chunking

**Status:** done
**Date:** <!-- fill in -->

## Goal

Turn raw Kubernetes markdown into retrieval-ready chunks that respect document structure,
rather than blindly splitting on a fixed character count — and handle the fact that this
corpus contains two structurally different kinds of document (see Phase 1 finding: `reference/`
is 70% of the corpus and mostly auto-generated CLI reference docs, not prose).

## The two document shapes

| | Prose docs (concepts/, tasks/) | Auto-gen reference (reference/command-line-tools-reference/, reference/generated/) |
|---|---|---|
| Headings | Plain markdown (`## Using Pods`) | Hugo shortcodes (`## {{% heading "options" %}}`) |
| Flags/fields | N/A | Raw HTML `<table>` elements |
| Typical section length | Several paragraphs | A single flag can be one line; a full binary's flag list (e.g. kube-apiserver) can be 40K+ chars |
| Callouts | `{{< caution >}}...{{< /caution >}}` | Rare |

## Approach

`ingestion/chunk_docs.py` runs every file through a shared pipeline:

1. Strip HTML comments (auto-gen boilerplate notices, editorial comments).
2. Convert HTML option/field tables → readable bullets via BeautifulSoup:
   `` - `--cache-dir string` (default: "..."): Default cache directory ``
3. Convert Hugo heading shortcodes → markdown headings: `{{% heading "options" %}}` → `Options`
   (mapped via a small dict; unrecognized names fall back to title-casing).
4. Unwrap callout shortcodes: `{{< caution >}}...{{< /caution >}}` → `**Caution:** ...`
5. Drop unresolvable inline shortcodes (`{{< skew currentVersion >}}`), cleaning up any
   orphaned punctuation this leaves behind.
6. Split by heading, then by a code-fence-aware paragraph accumulator, bounded by two
   safety valves (see below) that only became necessary once tested against real data.

## Design decision: excluding "Inherited Options"

The `parentoptions` table (global kubectl flags: `--kubeconfig`, `--namespace`, `--token`,
etc.) is identical boilerplate on essentially every kubectl/kubeadm/kubelet reference page —
100+ pages. `SKIP_INHERITED_OPTIONS = True` drops these sections entirely, avoiding wasted
embedding compute and near-duplicate vectors crowding the vector store. Flip to `False` later
if eval shows global-flag lookups are a real query pattern worth supporting per-page.

## Debugging journey — three real bugs found against production data

Testing against a hand-picked sample file caught the first two issues immediately; the corpus
run against the full 1672 real files caught a third, much subtler one. Documenting the full
arc here rather than just the final fix, since the debugging process itself demonstrates the
kind of validation a "looks right in a toy example" chunker usually skips.

**Bug 1 — heading duplication.** The Hugo heading replacement (`{{% heading "options" %}}` →
`Options`) was implemented as `## {title}`, but the source line already had `## ` before the
shortcode (`## {{% heading "options" %}}`) — so headings doubled up (`## ## Synopsis`). This
also silently broke the `SKIP_INHERITED_OPTIONS` check, since `"## Inherited Options" !=
"inherited options"`. **Fix:** the replacement only produces the title text, not the `##`
marker, since that marker already exists in the source.

**Bug 2 — reference content dropped by the prose minimum-length filter.** Applying
`MIN_CHUNK_CHARS = 100` (tuned for prose) to auto-generated reference docs silently dropped
*the entire test file* — every section (Synopsis, Examples, a single Options flag) was
individually under 100 chars. **Fix:** `MIN_CHUNK_CHARS_AUTO_GEN = 20` for `auto_generated:
true` docs, since a single flag description is short but complete and often exactly what a
targeted query should retrieve.

**Bug 3 — runaway chunk size, three fixes to actually resolve.** A full corpus run reported
a chunk of **366,599 characters** against a 1200-char target — found via
`sorted(chunks, key=lambda c: -c['char_count'])[:5]`, which pointed at
`reference/command-line-tools-reference/kube-apiserver.md`'s Options section. Root-caused in
three layers, each only visible once the fix for the previous layer exposed the next:

1. **Fence-toggle could get stuck.** `split_respecting_code_blocks` toggled an
   `in_code_block` flag based on counting ` ``` ` fences per paragraph, and only allowed a
   chunk break when *not* "inside" a code block. Also, the fence regex (`^\`\`\``) wasn't
   using `re.MULTILINE`, so it only matched a fence at the very start of the whole string,
   not the start of each line — meaning fence counts were unreliable in general.
   **Fix:** added `re.MULTILINE` and a `HARD_MAX_MULTIPLIER` safety valve that force-flushes
   after 4× the target size regardless of code-block state.
2. **No paragraph boundary to split on at all.** `html_table_to_bullets` joins converted
   table rows with single `\n`, not `\n\n`. A section that's entirely one huge converted
   table (e.g. kube-apiserver's ~200 flags) has **zero** `\n\n` breaks, so
   `text.split("\n\n")` returns the *entire section* as a single unsplittable "paragraph" —
   the hard-cap safety valve never gets a chance to fire because the loop only runs once.
   **Fix:** `_expand_oversized_blocks` pre-splits any oversized paragraph by single newlines
   first. This alone brought the worst case down from 366,599 → 10,572 chars.
3. **A single line can itself be oversized.** Even after splitting by single newlines, one
   specific bullet — kube-apiserver/kube-controller-manager's `--feature-gates` flag, whose
   description enumerates every feature gate in the codebase — is itself ~9,500+ characters
   on one unbroken line (BeautifulSoup's `get_text(separator=' ')` flattens the original
   HTML `<br/>`-separated list into one line with no newlines at all). The first version of
   `_expand_oversized_blocks` split by newline *once* and accepted whatever came out without
   re-checking length, so this one still-oversized line passed through untouched.
   **Fix:** made the expansion recursive — any fragment still too large after a split pass
   gets split again, falling back to raw character slicing only when there's truly no
   newline left.

Verified with a standalone debug script (`ingestion/debug_chunk.py`) that traced the exact
transformation of `kube-controller-manager.md`'s Options section step by step, rather than
guessing from aggregate stats alone — this caught a fourth, non-logic issue: an earlier
"fixed" zip had silently failed to actually deploy the recursive version (confirmed by
`grep`-counting occurrences of the function name before and after each fix, since a partial
match count was the tell that the update hadn't landed).

**Bug 4 — chunk_id collisions (only surfaced once Phase 3 indexed into Qdrant).** The
original `chunk_id` format, `{file}::{heading}::{piece_index}`, isn't unique when a file has
two different sections sharing an identical heading name — generic subsection titles like
"See Also" or "Examples" repeat often within one long page, each attached to a different
parent topic. Two genuinely different chunks produced the same `chunk_id`, and since Phase 3
derives a deterministic Qdrant point ID from `chunk_id`, the second chunk silently overwrote
the first at index time — 852 of 11,116 chunks vanished with no error at all. This bug was
invisible within Phase 2 itself (chunking ran fine, no exceptions, chunk count looked
correct) — it only became visible as a mismatched point count in Qdrant. **Fixed** by
including each section's position within the file (`s{section_idx}`) in the ID, which is
unique regardless of heading text repetition. See docs/03-embeddings.md for the full story,
including a second-order consequence (changing the ID scheme meant old and new IDs no longer
matched, so a naive re-embed left duplicate stale points behind).

## Chunk size decision

- **Prose docs:** `MIN_CHUNK_CHARS = 100`, target `MAX_CHUNK_CHARS = 1200` (~300 tokens).
- **Auto-generated reference docs:** `MIN_CHUNK_CHARS_AUTO_GEN = 20`.
- **Hard cap:** `HARD_MAX_MULTIPLIER = 4` (4800 chars), with a small expected overshoot since
  the check fires after appending a fragment, not before.

## Final corpus stats (full run, 1672 files)

```
Total chunks generated:    11,116
Avg chars/chunk:           610
Min/max chars/chunk:       20 / 5,176
Chunks from auto-generated (reference) docs: 3,022 (27%)
```

The 27% auto-generated share (vs. reference/ being 70% of raw *files*) reflects
`SKIP_INHERITED_OPTIONS` dropping a large fraction of reference-doc content, plus
reference pages generally producing fewer, denser chunks per file than prose pages.

## Known limitations / things to revisit

- Markdown tables (not HTML) elsewhere in the corpus aren't given special handling.
- No token-based length check — using char count as a ~4-chars/token proxy.
- `SKIP_INHERITED_OPTIONS` is all-or-nothing — revisit if eval shows global flag lookups are
  a real query pattern.
- The `--feature-gates`-style mega-descriptions, once character-sliced, lose their natural
  boundaries (a slice might cut mid-feature-gate-name). Acceptable for now since these are a
  small fraction of chunks, but worth flagging if eval shows poor retrieval quality
  specifically on feature-gate-related queries.

## Deliverable

- [x] `ingestion/chunk_docs.py` written, debugged against real data (not just samples)
- [x] Three distinct bugs found and fixed via full-corpus testing, each verified with
      targeted reproduction before and after the fix
- [x] Full corpus run completed: 11,116 chunks, bounded chunk sizes (max 5,176 chars)
- [ ] Spot-check a final handful of chunks for qualitative review before Phase 3
