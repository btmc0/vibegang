from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from cto_inv.cli import app


def test_analyze_with_local_markdown(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    md = data_dir / "doc.md"
    md.write_text(
        """
# Sample Doc

Some text.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;
contract X { function f() external { payable(msg.sender).transfer(1 ether); } }
```
""",
        encoding="utf-8",
    )
    urls = data_dir / "urls.txt"
    urls.write_text(str(md), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, [
        "analyze",
        "--urls-file",
        str(urls),
        "--artifacts-dir",
        str(tmp_path / "artifacts"),
    ])
    assert result.exit_code == 0, result.output

    # Verify manifest created
    artifacts_base = tmp_path / "artifacts"
    runs = list(artifacts_base.iterdir())
    assert runs, "Artifacts directory should have a run subdir"
    # We passed explicit artifacts dir pointing at run dir; manifest should exist there
    manifest = (tmp_path / "artifacts" / "run_manifest.json")
    # In our CLI, when artifacts-dir is provided, it points to base path for current run
    # so manifest should be exactly there
    assert manifest.exists(), f"manifest not found at {manifest}"

    # Analysis summary exists
    analysis_summary = tmp_path / "artifacts" / "analysis" / "analysis_summary.json"
    assert analysis_summary.exists(), "analysis summary should be generated"

