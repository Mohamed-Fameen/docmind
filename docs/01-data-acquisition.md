# Phase 1 — Data Acquisition

**Status:** in progress
**Date:** <!-- fill in -->

## Goal

Get the Kubernetes documentation onto disk in a clean, source-controlled-friendly form —
markdown files straight from the `kubernetes/website` repo, not scraped HTML — so downstream
chunking works against clean source rather than fighting rendered page cruft (nav bars,
footers, JS-rendered widgets).

## Source

- **Repo:** [`kubernetes/website`](https://github.com/kubernetes/website) — this is the actual
  source repo that generates kubernetes.io. Docs live under `content/en/docs/`.
- **License:** CC BY 4.0 (documentation content) — fine to use for a portfolio project with
  attribution; not redistributing as a competing product.
- **Why not scrape the live site instead:** scraping rendered HTML means dealing with
  navigation chrome, footers, and JS-rendered content for no benefit — the markdown source is
  cleaner, has YAML front-matter with useful metadata (title, version), and updates the same
  way the live site does.

## Approach

`ingestion/fetch_docs.py`:
1. Shallow-clones (`--depth 1`) the repo to `data/raw/website` — we don't need git history,
   just the current docs, and shallow clone is significantly faster/smaller.
2. Reports corpus stats: file count, total size, approximate token count, and a breakdown by
   top-level section (`concepts/`, `tasks/`, `tutorials/`, `reference/`, etc.)

Note: the repo also contains documentation translated into ~10 other languages under
`content/<lang>/docs/`. We only use `content/en/docs/` — no need to filter these out at clone
time since we just don't touch those directories downstream, but if disk space or clone time
becomes an issue, a `git sparse-checkout` limited to `content/en/docs` would trim this
significantly (worth revisiting if the full clone is inconveniently large).

## Corpus stats

```
Markdown files:      1672
Total size:          14.8 MB
Approx total tokens: 3,876,850

Breakdown by section:
  reference/          1163 files  (70% of corpus)
  tasks/                220 files
  concepts/             176 files
  contribute/            43 files
  tutorials/             43 files
  setup/                 22 files
  (root)/                 2 files
  home/                   2 files
  doc-contributor-tools/  1 file
```

## Key finding: reference/ is 70% of the corpus and structurally different

`reference/` dominates the file count, and it's not prose like `concepts/`/`tasks/` — it's
largely **auto-generated CLI reference documentation** (`content_type: tool-reference`,
`auto_generated: true` in front matter), generated from Kubernetes' Go source via the
[reference-docs generator](https://github.com/kubernetes-sigs/reference-docs/). These pages
have two structural quirks a plain markdown chunker would mishandle:

1. Headings are Hugo shortcodes, not markdown: `## {{% heading "options" %}}` instead of
   `## Options`.
2. Command flags are encoded as raw HTML `<table>` elements embedded in the markdown, not as
   markdown tables or lists.

Non-reference prose pages also use Hugo shortcodes for callouts (`{{< caution >}}...{{<
/caution >}}`) and dynamic values (`{{< skew currentVersion >}}`), which needed cleaning
regardless of section.

See [docs/02-chunking.md](./02-chunking.md) for how this is handled — this finding directly
shaped the chunker design, so it's documented there rather than duplicated here.

## Data quality notes

<!--
Things worth flagging once you've looked at the actual files:
- Are there broken internal links?
- How much duplicated/versioned content exists (e.g. docs for multiple k8s versions)?
- Any pages that are mostly auto-generated reference tables vs prose?
- Any non-English content that leaked in?
-->

## Deliverable

- [ ] `kubernetes/website` cloned to `data/raw/website`
- [ ] Corpus stats captured above
- [ ] Spot-checked a handful of files for structure/quality
- [ ] Confirmed `content/en/docs/` is the correct target directory (matches what Phase 2's
      chunking script expects at `RAW_DOCS_DIR`)
