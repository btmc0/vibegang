# cto-inv

Prototype v0 for ingestion + static analysis pipeline targeting Solidity sources.

Features
- Typer-based CLI entrypoint: `cto-inv`
- URL ingestion subsystem for:
  - HTML and Markdown (requests + BeautifulSoup + readability)
  - GitHub repo .sol discovery and raw download into a workspace cache
  - PDF via pdfminer.six
  - Google Docs export (gracefully skips/notes private/auth-failing docs)
  - Slite pages are detected and skipped if restricted
- Normalization of ingested artifacts into JSON with metadata, raw text, and extracted code blocks
- Static analysis that enumerates cached Solidity sources and attempts to run solc/Slither; falls back to a lightweight regex-based scan when tools are unavailable
- Artifacts are stored under `artifacts/<run_id>` and downloaded Solidity sources cached under `.workspace_cache/solidity`

Prerequisites
- Python 3.9+
- Optional for deeper analysis:
  - solc (Solidity compiler) in PATH
  - Slither (https://github.com/crytic/slither) in PATH
  - Foundry (forge), Echidna are not required to run this prototype but are commonly used in smart contract workflows

Install
- From source checkout:
  - Create and activate a virtualenv
  - Install the project

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
# Optional analysis extras (slither)
# pip install -e .[analysis]
```

Usage

- Provide URLs directly or via a file (one URL per line). Local files are also supported for convenience in tests/offline usage.

```bash
# Analyze a couple of URLs
cto-inv analyze --urls "https://example.com/some.html,https://example.com/x.md"

# From a file
cto-inv analyze --urls-file urls.txt

# Specify artifacts directory (otherwise defaults to artifacts/<run_id>)
cto-inv analyze --urls-file urls.txt --artifacts-dir ./artifacts/$(date +%s)
```

Outputs
- `artifacts/<run_id>/ingest`: normalized ingestion JSON files and raw text snapshots
- `artifacts/<run_id>/analysis/analysis_summary.json`: summary of static analysis
- `.workspace_cache/solidity`: downloaded Solidity sources discovered in GitHub repos

Notes
- The pipeline attempts to use `solc` for ASTs and `slither` for analysis when available. If not found, it falls back to a naive static scan to keep the prototype functional.
- Google Docs that require authentication will be logged as skipped. Slite pages detected as restricted are also logged and skipped.
