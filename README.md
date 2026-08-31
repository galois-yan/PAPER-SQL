# Paper-SQL MCP Server

<p align="center">
  <img src="./logo.png" alt="Paper-SQL logo" width="160" />
</p>

> OpenAlex literature search and research library for MCP clients.
>
> 中文文档：[README_zh.md](./README_zh.md)

An [OpenAlex](https://openalex.org)-based MCP server for discovering, organizing, and analyzing academic literature. It supports keyword and semantic search, persistent local storage, vector similarity queries, BibTeX export, open-access PDF downloads, and citation-network analysis. The server communicates over stdio and works with MCP clients such as Claude Code, Codex, Kimi Code, OpenCode, Cherry Studio, and DeepSeek Harness.

## What it provides

- Search more than 250 million OpenAlex works by keyword, meaning, DOI, or OpenAlex ID.
- Ingest search results into a local SQLite library with idempotent UPSERTs.
- Query the library with read-only SQL, including semantic vector search through sqlite-vec.
- Generate local embeddings with ZhipuAI Embedding-3 when semantic library search is needed.
- Backfill missing abstracts through Crossref, Elsevier, and Scopus when configured.
- Export selected papers as BibTeX.
- Download open-access PDFs on explicit request.
- Analyze and visualize citation networks with PageRank, centrality, and communities; large graphs use adaptive approximate centrality and heuristic community detection.

## Demo video

[Paper-SQL] Using MCP to let agents search real literature: full workflow demo — search → local ingestion → citation analysis → PDF download for close reading → survey generation

https://www.bilibili.com/video/BV1Vntt66Ejc/?share_source=copy_web&vd_source=601c92cd729f5d300329945382cb791f

## Design overview

1. **Search is ingestion.** Every remote result is written to the local SQLite library. Repeating a search refreshes records instead of creating duplicates.
2. **SQL is the library interface.** The read-only `library_query` tool exposes the schema so an MCP client can inspect, filter, aggregate, and select records without a collection of one-off query APIs.
3. **Vectors stay in SQLite.** Embeddings are stored as float32 BLOBs in `works.vec` and compared with sqlite-vec's `vec_distance_cosine()`; no separate vector database is required.
4. **Reads remain responsive.** SQLite WAL mode allows readers and writers to progress concurrently. Only write operations are serialized with an `asyncio.Lock`.

For the shared execution chains, algorithms, database safeguards, and internal
behavior of all 16 tools, see [Tool internals](./docs/TOOL_INTERNALS_zh.md)
(Chinese only).

## Project layout

```
openalex_mcp/
├── registry.py   # FastMCP instance, tool registration, and stdio entry point
├── common.py     # Shared zero-dependency helpers
├── remote/       # OpenAlex, ZhipuAI, Crossref, Elsevier, and Scopus clients
├── local/        # SQLite library, sqlite-vec, UPSERT, and BibTeX export
└── graph/        # Citation-network analysis and vis.js visualization
```

## Installation

Requires Python 3.10 or later.

### 1. Build the project-local environment with uv

The MCP server uses a uv-managed project-local `.venv`, which skips Conda
activation and environment-management overhead while `uv.lock` pins
dependencies; network and algorithm costs still depend on the invoked tool.

Install uv first if needed, following the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).
Then run this from the project root:

```powershell
uv python install --managed-python 3.12
uv python pin 3.12
uv sync --managed-python --python 3.12
```

This creates `.python-version`, the project-local `.venv`, and `uv.lock`, and
installs this project and its dependencies in editable mode. Use `uv run` for
ordinary commands:

```powershell
uv run python -m unittest discover -s tests -v
```

Activation is unnecessary. Every later `<VENV_PY>` refers to the Python
executable inside `.venv`.

### 2. Configure API keys

Copy `.env.example` to `.env` and fill in the keys you need:

```dotenv
OPENALEX_API_KEY=your-api-key-here          # required for OpenAlex requests
OPENALEX_EMAIL=you@example.org              # recommended for provider contactability
ZHIPUAI_API_KEY=your-zhipuai-key-here       # optional: local semantic embeddings
ELSEVIER_API_KEY=your-elsevier-api-key-here # optional: abstract retrieval/backfill
ELSEVIER_INST_TOKEN=your-elsevier-inst-token-here
BACKFILL_ABSTRACTS=true
BACKFILL_MAX_TARGETS=25
BACKFILL_CONCURRENCY=4
```

Get a free OpenAlex key from [OpenAlex API settings](https://openalex.org/settings/api). ZhipuAI is available at [bigmodel.cn](https://bigmodel.cn).

Capabilities by configuration:

| Configuration | Available capabilities |
|---|---|
| No remote API key | SQL, statistics, BibTeX export, and citation analysis over an existing local library |
| `OPENALEX_API_KEY` | OpenAlex keyword, semantic, and ID search, ingestion, autocomplete, and Content API downloads. Crossref abstract backfill is included and needs no additional key. |
| + `ZHIPUAI_API_KEY` | Local semantic embeddings and vector search |
| + `ELSEVIER_API_KEY` | Adds the Elsevier and Scopus stages to abstract backfill, plus the `fetch_elsevier_abstracts` tool. One key covers both Elsevier and Scopus; there is no separate Scopus key. |

Automatic backfill tries **Crossref first**, then Elsevier, then Scopus, and
stops at the first source that returns an abstract. Only the Crossref stage
runs without an Elsevier key, so leaving `ELSEVIER_API_KEY` unset reduces
backfill coverage rather than disabling it. A work is never dropped because
every source failed.

The three `BACKFILL_*` variables behave as follows:

| Variable | Default | Behavior |
|---|---|---|
| `BACKFILL_ABSTRACTS` | enabled when unset | Only `1`, `true`, `yes`, or `on` count as true; anything else disables backfill. |
| `BACKFILL_MAX_TARGETS` | `25` | Batch threshold described below. `0` removes the cap entirely. |
| `BACKFILL_CONCURRENCY` | `4` | Silently clamped to the range 1–8, so a configured `32` runs as `8`. |

A non-integer value in either numeric variable falls back to its default
without an error.

> **Elsevier API requires the campus network.** Elsevier abstract retrieval and automatic abstract backfill only work from the campus network (or the campus VPN). Outside that environment the `ELSEVIER_API_KEY` / `ELSEVIER_INST_TOKEN` pair is rejected with 401/403, so the Elsevier-based backfill does not fill abstracts. Crossref backfill is unaffected.
> When a batch has more missing abstracts than `BACKFILL_MAX_TARGETS`, automatic backfill now ranks Elsevier journal works in that batch by citation count and backfills the top K instead of skipping the whole batch.

## Connect an MCP client

For complete installation, environment-variable, client configuration,
verification, and troubleshooting instructions, see the
**[MCP client setup guide](./docs/MCP_CLIENT_SETUP.md)**.

Every client launches the server over stdio with the same command:

```bash
<VENV_PY> -m openalex_mcp.registry
```

`<VENV_PY>` must be the absolute path to the Python interpreter where this package was installed. For the project-local `.venv` used above, that is:

- Windows: `<project>\.venv\Scripts\python.exe`
- Linux / macOS: `<project>/.venv/bin/python`

Replace `<VENV_PY>` and `<project>` with absolute paths. `<VENV_PY>` must point
to the project-local uv `.venv` created above, not a Conda environment. In JSON
on Windows, either use forward slashes (`C:/path/to/python.exe`) or escape each
backslash (`C:\\path\\to\\python.exe`).

`uv sync` also installs an `openalex-mcp` console script next to the
interpreter (`.venv\Scripts\openalex-mcp.exe` on Windows,
`.venv/bin/openalex-mcp` elsewhere). It starts the same server with no
arguments, so a client can use it in place of the interpreter plus
`-m openalex_mcp.registry`. The `-m` form is used throughout this README and
the setup guide because it fails loudly if the wrong interpreter is configured.

Keep API keys in the project's `.env` file and pass only its absolute path through `OPENALEX_MCP_ENV_FILE`. The extra `PYTHON*` settings avoid encoding problems in Windows logs; `FASTMCP_SHOW_SERVER_BANNER=false` only suppresses the FastMCP startup banner.

Before configuring a client, verify that the server starts and waits for stdio input. Use the command for your shell:

```powershell
& "<VENV_PY>" -m openalex_mcp.registry
```

```bash
"<VENV_PY>" -m openalex_mcp.registry
```

Press Ctrl+C to stop the standalone process.

### Client configuration summary

| Client | Configuration | Server key | Environment field |
|---|---|---|---|
| Claude Code | User `~/.claude.json`; project `.mcp.json` | `mcpServers` | `env` |
| Kimi Code | User `~/.kimi-code/mcp.json`; project `.kimi-code/mcp.json` | `mcpServers` | `env` |
| Codex CLI | User `~/.codex/config.toml`; trusted project `.codex/config.toml` | `[mcp_servers.*]` | `[mcp_servers.*.env]` |
| OpenCode | Global `~/.config/opencode/opencode.json`; project `opencode.json` | `mcp` | `environment` |
| Cherry Studio | Settings → MCP → MCP Servers → Add | `mcpServers` | `env` |
| DeepSeek Harness | `~/.dsh/profiles/<profile>/cordis.patch.yml` | cordis patch `insert` (plugin `@deepseek-ai/dsh-mcp-client`) | `config.env` |

Client-specific commands, complete configuration blocks, verification steps,
and caveats for all six clients are maintained in the
[MCP client setup guide](./docs/MCP_CLIENT_SETUP.md).

## Tools

### Remote search and ingestion

| Tool | Input | Returns | Use it when |
|---|---|---|---|
| `search_keyword` | Optional `query` (Boolean, phrases, wildcards) plus filters and shared options below | Preview and ingestion statistics | Searching by exact terms or topic |
| `search_semantic` | Required natural-language `query` plus the same filters and shared options | OpenAlex server-side semantic matches and ingestion statistics | Searching by meaning when keywords are uncertain |
| `search_ids` | Comma-separated OpenAlex IDs/URLs or DOI values/URLs | Summary and ingestion statistics | Fetching known works |
| `autocomplete` | Entity type and name fragment | Up to 10 candidates with IDs | Resolving authors, journals, institutions, publishers, or funders |

`search_keyword` runs without a `query`: leave it empty to list every work
matching the filters alone. `search_semantic` requires one. `search_ids` takes
neither filters nor paging.

#### Filters

`search_keyword` and `search_semantic` accept the same filters. Resolve names to
IDs with `autocomplete` first.

| Filter | Example | Selects |
|---|---|---|
| `publication_year` | `>2021`, `2019` | Publication year or range; passed to OpenAlex verbatim |
| `cited_by_count` | `>50` | Citation-count threshold |
| `cites` | `W2741809807` | Works that cite the given IDs — forward citation chasing |
| `cited_by` | `W2741809807` | Works cited by the given IDs, i.e. their reference lists — backward citation chasing |
| `related_to` | `W2741809807` | Works OpenAlex marks as related |
| `source_id` | `S4210208519` | Journal or other source |
| `institution_id` | `I129432676` | Affiliated institution |
| `author_id` | `A5023888391` | Author |
| `publisher_id` | `P4310319901` | Publisher |
| `funder_id` | `F4320306076` | Funder |

#### Shared search options

| Option | Default | Behavior |
|---|---|---|
| `publication_year` | `>2021` | **Applied even when you omit it**, so both searches return only works published after 2021 by default. Pass an empty string to remove the year filter. Not accepted by `search_ids`. |
| `page` | `1` | `search_keyword` returns 100 works per page. `search_semantic` returns at most 50 and is rate-limited to one request per second. `search_ids` ignores paging. |
| `fetch_fulltext` | `false` | Extract body text into `works.fulltext`. |
| `fulltext_limit` | `2` | Maximum PDFs converted to text per call, so a 100-result page still yields full text for only two works unless raised. |
| `use_openalex_content_api` | `false` | Allows the paid OpenAlex Content API as a PDF fallback; see the cost note under `download_pdf`. |

### Local library

| Tool | Input | Returns | Use it when |
|---|---|---|---|
| `library_query` | Read-only SQL and optional `semantic_query` | JSON array of row objects, or a JSON object carrying `error` or `message` | Inspecting the schema, filtering papers, reading full text, or running vector search |
| `library_stats` | None | Counts, year span, coverage, OA rate, and top sources/concepts | Checking library status |
| `library_generate_embeddings` | None | Total works and newly embedded works | Backfilling local embeddings after bulk ingestion |
| `library_export` | IDs or `*` (default `*`), one `.bib` filename (default `export.bib`), sort, and cite-key style | BibTeX path and entry count | Exporting a reference set |
| `literature_review_prompt` | None | System prompt for a BibTeX-grounded literature review | Writing a review after the BibTeX is supplied in the conversation or read locally by the outer model |
| `library_delete` | IDs or `*` | Deleted and remaining counts | Deleting records after an explicit request |
| `library_close` | None | Confirmation | Releasing the SQLite file for another process |

### Abstracts and full text

| Tool | Input | Returns | Use it when |
|---|---|---|---|
| `fetch_elsevier_abstracts` | Up to 25 comma-separated values per call, plus `input_type`, `update_library`, and `overwrite` | Abstract, source, and write-back status | Manually filling missing abstracts |
| `download_pdf` | Comma-separated work IDs; `save_to_project=true` only after an explicit user request | Status table and PDF paths | Keeping open-access PDF files |

`fetch_elsevier_abstracts` defaults to `input_type="doi"`; pass `"work_id"` to
look values up by OpenAlex ID instead. It **writes results back into
`works.abstract` by default** (`update_library=true`); set it to `false` for a
read-only fetch. Existing local abstracts are preserved unless
`overwrite=true`. More than 25 values in one call is rejected. Unlike
automatic backfill, this tool only queries the Elsevier Abstract Retrieval
API — it does not fall back to Crossref or Scopus, so a DOI that automatic
backfill would resolve via Scopus can still come back "not found" here.

`download_pdf` uses the OpenAlex Content API, which bills **$0.01 per PDF**
(the free tier covers roughly 100 per day). Already-downloaded files are
skipped rather than re-fetched, so repeating a call does not re-bill. The same
paid endpoint backs the `use_openalex_content_api` fallback on the search
tools, which is why both default to off.

Full-text extraction is enabled with `fetch_fulltext=true` on `search_keyword`, `search_semantic`, or `search_ids`. Available OA PDFs are downloaded to a temporary directory, converted into `works.fulltext`, and removed afterward. At most `fulltext_limit` works per call are converted (default 2). It is disabled by default and should be enabled only when methods, parameters, procedures, or body-text evidence are required.

### Citation network

| Tool | Input | Returns | Use it when |
|---|---|---|---|
| `graph_analyze` | Comma-separated work IDs | PageRank, degree, betweenness, and communities; adaptive approximations for large graphs | Analyzing citation structure within a selected set |
| `graph_neighbors` | Comma-separated work IDs and `direction` (`in`/`out`/`both`, default `both`) | Neighbors within the selected set | Finding citing or cited works |
| `graph_visualize` | Work IDs and optional `output_dir` | Path to an interactive HTML graph | Exploring a clickable citation graph; selections over 120 stored works are reduced to 120 important nodes |

All three take an induced subgraph: only the IDs you pass become nodes, and
only citations where both endpoints are in that set become edges. IDs absent
from the local library are dropped before the 120-node cap applies.

When `graph_visualize` trims a selection, "important" is a weighted rank of
citation count, PageRank, in-degree within the selected set, and age, so
highly-cited, structurally central, and foundational works survive the cut.
`graph_analyze` stays uncapped, so use it when you need the full set.

`library_export.sort` accepts one of `id`, `title`, `publication_year`,
`publication_date`, `cited_by_count`, `source_name`, or `is_oa`, followed by
optional `:asc` or `:desc`. Omitting `sort` is not "unsorted" — the export
falls back to `publication_year DESC`. `cite_key_style` accepts `author_year`
(default, e.g. `liu2024`) or `openalex_id`.

For vector search, use a normal SQL query with the `{query_vec}` placeholder:

```sql
SELECT *, vec_distance_cosine(vec, {query_vec}) AS score
FROM works
WHERE vec IS NOT NULL
ORDER BY score
LIMIT 30;
```

`search_semantic` uses OpenAlex's remote semantic index. The optional
`semantic_query` argument to `library_query` instead embeds the query with
ZhipuAI and compares it with vectors stored in the local SQLite library.
Queries and graph analysis have no hard result-size limit; `graph_analyze`
uses approximate betweenness and Louvain communities for large graphs. Still
use SQL `LIMIT`, bounded `substr()`, and a focused work-ID set to avoid
oversized responses or expensive graph calculations.

## Typical workflow

1. Search and ingest with `search_keyword` or `search_semantic`. Both apply
   `publication_year=">2021"` unless you override it, so pass an empty string
   when older literature matters.
2. Filter candidates with `library_query`, for example:

   ```sql
   SELECT id, title, publication_year, cited_by_count
   FROM works
   WHERE cited_by_count > 50 AND publication_year > 2020
   ORDER BY cited_by_count DESC
   LIMIT 50;
   ```

3. Optionally add semantic filtering with `semantic_query` and `{query_vec}`.
4. Let the configured abstract backfill run, or call `fetch_elsevier_abstracts` for selected papers.
5. Enable `fetch_fulltext=true` only when full-text evidence is needed, then query a bounded excerpt:

   ```sql
   SELECT substr(fulltext, 1, 12000)
   FROM works
   WHERE id = 'W123';
   ```

6. Analyze selected IDs with `graph_analyze` or `graph_visualize`.
7. Export the final references with `library_export`.
8. Download original PDFs with `download_pdf` only when the files need to be retained.

## Runtime data and output paths

`~` means the account home directory (`%USERPROFILE%` on Windows). Default
directories are created on first use.

| Data or tool | Default path | Custom path rules | Lifetime and write behavior |
|---|---|---|---|
| Local library | `~/.AI-CACHE/openalex/library.db` | No tool parameter or dedicated environment variable changes it. All four default paths resolve `~`, so setting `USERPROFILE` (Windows) or `HOME` (POSIX) in the client's `env` block relocates the whole tree. | Persistent SQLite database. WAL mode can create `library.db-wal` and `library.db-shm` while the server is running. |
| `library_export` | `~/.AI-CACHE/openalex/collections/<target>` (default `export.bib`) | `target` must already be one `.bib` filename (case-insensitive, e.g. `EXPORT.BIB` is accepted); no extension is appended. Absolute paths, subdirectories, separators, and `..` are rejected. | Persistent; exporting to an existing accepted filename replaces that BibTeX file. |
| `download_pdf` | `~/.AI-CACHE/openalex/pdfs/<WORK_ID>.pdf` | **Strict default:** no path change unless the user explicitly requests the project directory; only then `save_to_project=true` writes to `<cwd>/pdfs/<WORK_ID>.pdf`, where `<cwd>` is the MCP server process working directory captured at startup. No arbitrary path is accepted. Missing directories are created. | Persistent; an existing `<WORK_ID>.pdf` is skipped rather than downloaded or overwritten. Work IDs are upper-cased in the filename. |
| `graph_visualize` | `~/.AI-CACHE/openalex/graphs/graph_<digest>_<N>n.html` | `output_dir` may be an absolute directory or a path relative to the MCP server process working directory. Missing directories are created. | Persistent; the 12-character digest is derived from rendered work IDs and `N` is the rendered node count. Regenerating the same selected graph replaces the same HTML file. Each file inlines the vis.js bundle, so expect roughly 0.7 MB per graph. |
| Search with `fetch_fulltext=true` | System temporary directory: `openalex-fulltext-*/paper.pdf` | The directory prefix and `paper.pdf` name are fixed, but the parent temporary directory follows the usual `TMPDIR` / `TEMP` / `TMP` variables. | Temporary PDF is deleted immediately after extraction. Extracted text is stored in `library.db` as `works.fulltext`; no permanent PDF is kept. |

Search results, abstracts, embeddings, and extracted full text are stored inside
`library.db`, not as separate files. `autocomplete`, `library_query`,
`library_stats`, `library_delete`, `library_close`, `graph_analyze`, and
`graph_neighbors` do not create standalone output files. `download_pdf` is the
only workflow that permanently retains original PDF files.

The default directory tree is:

```
~/.AI-CACHE/openalex/
├── library.db    # SQLite literature library
├── library.db-wal / library.db-shm  # transient SQLite WAL sidecars
├── collections/  # BibTeX exports
├── pdfs/         # Downloaded open-access PDFs
└── graphs/       # Citation-network HTML files
```

## Contact

- Dr. Yinzhong Yan · School of Civil Aviation, Northwestern Polytechnical University
- Email: <yinzhong.yan@nwpu.edu.cn>

## Acknowledgments

Special thanks to my two students **Linze Li** and **Yaoxuan Shi**, who have collaborated with me since last year on researching and learning agent development technologies.