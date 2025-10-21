from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .utils import SOL_CACHE, ensure_dir, is_executable, iter_files, write_json

logger = logging.getLogger("cto_inv")


@dataclass
class AnalysisResult:
    solidity_sources: List[str]
    asts: Dict[str, dict]
    call_graph: Dict[str, List[str]]
    state_layout: Dict[str, dict]
    external_calls: Dict[str, List[str]]
    modifier_usage: Dict[str, List[str]]
    erc_standards: Dict[str, List[str]]
    reentrancy_candidates: Dict[str, List[str]]

    def to_json(self) -> dict:
        return {
            "solidity_sources": self.solidity_sources,
            "asts": self.asts,
            "call_graph": self.call_graph,
            "state_layout": self.state_layout,
            "external_calls": self.external_calls,
            "modifier_usage": self.modifier_usage,
            "erc_standards": self.erc_standards,
            "reentrancy_candidates": self.reentrancy_candidates,
        }


def gather_solidity_sources(root: Optional[Path] = None) -> List[Path]:
    root_dir = (root or SOL_CACHE).resolve()
    if not root_dir.exists():
        return []
    return sorted(iter_files(root_dir, exts=[".sol"]))


def try_run_solc_asts(files: List[Path]) -> Dict[str, dict]:
    if not files:
        return {}
    if not is_executable("solc"):
        logger.info("solc not found in PATH; skipping AST generation")
        return {}
    asts: Dict[str, dict] = {}
    # Attempt per-file compact AST to avoid combined issues
    for f in files:
        try:
            cmd = [
                "solc",
                "--ast-compact-json",
                str(f),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                logger.debug("solc failed for %s: %s", f, res.stderr.strip())
                continue
            # solc prints filename then JSON; extract last JSON object
            out = res.stdout.strip()
            # Find first '{'
            i = out.find("{")
            if i >= 0:
                j = out.rfind("}")
                if j >= i:
                    js = out[i : j + 1]
                    try:
                        ast = json.loads(js)
                        asts[str(f)] = ast
                    except json.JSONDecodeError:
                        pass
        except Exception as e:  # noqa: BLE001
            logger.debug("solc exception for %s: %s", f, e)
            continue
    return asts


def try_run_slither_json(target_dir: Path, out_json: Path) -> Optional[dict]:
    if not is_executable("slither"):
        logger.info("slither not found in PATH; skipping Slither analysis")
        return None
    cmd = [
        "slither",
        str(target_dir),
        "--json",
        str(out_json),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.warning("slither failed: %s", res.stderr.strip())
        return None
    try:
        data = json.loads(out_json.read_text())
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning("failed reading slither json: %s", e)
        return None


SOLIDITY_EXTERNAL_CALL_PATTERNS = [
    r"\.call\s*\(",
    r"\.call\s*\{",
    r"\.delegatecall\s*\(",
    r"\.transfer\s*\(",
    r"\.send\s*\(",
]

ERC_PATTERNS = {
    "ERC20": [r"\bIERC20\b", r"\bERC20\b"],
    "ERC721": [r"\bIERC721\b", r"\bERC721\b"],
    "ERC1155": [r"\bIERC1155\b", r"\bERC1155\b"],
}


def naive_source_scan(files: List[Path]) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        external_calls: List[str] = []
        for pat in SOLIDITY_EXTERNAL_CALL_PATTERNS:
            for m in re.finditer(pat, text):
                snippet = text[max(0, m.start() - 40) : m.end() + 40]
                external_calls.append(snippet)
        modifiers = re.findall(r"modifier\s+([A-Za-z0-9_]+)", text)
        ercs: List[str] = []
        for name, pats in ERC_PATTERNS.items():
            if any(re.search(p, text) for p in pats):
                ercs.append(name)
        reentrancy = []
        if any(name in text for name in [".call{", ".send(", ".transfer("]):
            reentrancy.append("external_call_present")
        result[str(f)] = {
            "external_calls": external_calls,
            "modifier_usage": modifiers,
            "erc_standards": ercs,
            "reentrancy_candidates": reentrancy,
        }
    return result


def run_static_analysis(out_dir: Path, sources_root: Optional[Path] = None) -> AnalysisResult:
    ensure_dir(out_dir)
    files = gather_solidity_sources(sources_root)
    # Try to produce ASTs via solc if available
    asts = try_run_solc_asts(files)

    # Try slither
    slither_json_path = out_dir / "slither.json"
    slither_data = try_run_slither_json((sources_root or SOL_CACHE), slither_json_path)

    # Naive analysis fallback
    naive = naive_source_scan(files)

    # Merge results
    call_graph: Dict[str, List[str]] = {}
    state_layout: Dict[str, dict] = {}
    external_calls: Dict[str, List[str]] = {}
    modifier_usage: Dict[str, List[str]] = {}
    erc_standards: Dict[str, List[str]] = {}
    reentrancy_candidates: Dict[str, List[str]] = {}

    for f in files:
        key = str(f)
        external_calls[key] = naive.get(key, {}).get("external_calls", [])
        modifier_usage[key] = naive.get(key, {}).get("modifier_usage", [])
        erc_standards[key] = naive.get(key, {}).get("erc_standards", [])
        reentrancy_candidates[key] = naive.get(key, {}).get("reentrancy_candidates", [])

    if slither_data:
        try:
            # Slither JSON schema may change; attempt to extract some useful info
            cg = slither_data.get("results", {}).get("call-graph", {})
            if isinstance(cg, dict):
                call_graph.update(cg)
        except Exception:
            pass

    result = AnalysisResult(
        solidity_sources=[str(p) for p in files],
        asts=asts,
        call_graph=call_graph,
        state_layout=state_layout,
        external_calls=external_calls,
        modifier_usage=modifier_usage,
        erc_standards=erc_standards,
        reentrancy_candidates=reentrancy_candidates,
    )

    write_json(out_dir / "analysis_summary.json", result.to_json())
    return result
