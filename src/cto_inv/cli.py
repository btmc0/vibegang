from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .analysis import run_static_analysis
from .ingest import Ingestor
from .utils import (
    CLIError,
    default_artifacts_dir,
    ensure_dir,
    load_urls_file,
    make_run_id,
    now_iso,
    split_urls_arg,
    write_json,
)

app = typer.Typer(help="CTO Investigator CLI")
console = Console()
logger = logging.getLogger("cto_inv")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


@app.command()
def version() -> None:
    """Show version"""
    console.print(f"cto-inv {__version__}")


@app.command()
def analyze(
    urls: List[str] = typer.Option(
        None,
        "--urls",
        help="One or more URLs (space/comma separated). Can be repeated.",
    ),
    urls_file: Optional[Path] = typer.Option(
        None, "--urls-file", exists=False, help="Path to a file with one URL per line"
    ),
    run_id: Optional[str] = typer.Option(None, help="Override run id (default: timestamp)"),
    artifacts_dir: Optional[Path] = typer.Option(
        None, help="Base artifacts dir (default: artifacts/<run_id>)"
    ),
) -> None:
    """Ingest URLs and run static analysis on discovered Solidity sources."""
    all_urls: List[str] = []
    all_urls.extend(split_urls_arg(urls))
    if urls_file:
        if not urls_file.exists():
            raise CLIError(f"urls file not found: {urls_file}")
        all_urls.extend(load_urls_file(urls_file))
    if not all_urls:
        raise CLIError("No URLs provided. Use --urls or --urls-file.")

    rid = run_id or make_run_id()
    base_artifacts = artifacts_dir or default_artifacts_dir(rid)
    ingest_dir = ensure_dir(base_artifacts / "ingest")
    analysis_dir = ensure_dir(base_artifacts / "analysis")

    console.print(f"Run ID: {rid}")
    console.print(f"Artifacts directory: {base_artifacts}")

    manifest = {
        "run_id": rid,
        "started_at": now_iso(),
        "urls": all_urls,
        "ingestion": {},
        "analysis": {},
    }

    # Ingestion
    console.print("[bold]Ingesting URLs...\n[/bold]")
    ingestor = Ingestor(ingest_dir)
    artifacts = ingestor.ingest_urls(all_urls)
    manifest["ingestion"]["count"] = len(artifacts)
    manifest["ingestion"]["artifacts_dir"] = str(ingest_dir)

    # Static analysis on cached solidity sources
    console.print("\n[bold]Running static analysis...\n[/bold]")
    result = run_static_analysis(analysis_dir)
    manifest["analysis"]["summary"] = str(analysis_dir / "analysis_summary.json")
    manifest["analysis"]["solidity_sources_count"] = len(result.solidity_sources)

    manifest["finished_at"] = now_iso()
    write_json(base_artifacts / "run_manifest.json", manifest)

    # Summarize
    table = Table(title="Run Summary")
    table.add_column("Key")
    table.add_column("Value")
    table.add_row("Run ID", rid)
    table.add_row("URLs", str(len(all_urls)))
    table.add_row("Ingested Artifacts", str(len(artifacts)))
    table.add_row("Solidity Sources", str(len(result.solidity_sources)))
    table.add_row("Manifest", str(base_artifacts / "run_manifest.json"))
    console.print(table)


if __name__ == "__main__":
    app()
