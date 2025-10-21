from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .utils import (
    SOL_CACHE,
    WORKSPACE_CACHE,
    ensure_dir,
    read_text_file,
    sanitize_filename,
    sha1_hex,
    write_json,
    write_text_file,
)

logger = logging.getLogger("cto_inv")

USER_AGENT = (
    "cto-inv/0.0.1 (+https://example.com) Python requests; prototype ingestion"
)


@dataclass
class CodeBlock:
    language: Optional[str]
    code: str


@dataclass
class Artifact:
    url: str
    kind: str  # html, markdown, pdf, github, google_doc, slite_skip, error, local
    title: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    raw_text: Optional[str] = None
    extracted_code: List[CodeBlock] = field(default_factory=list)
    downloaded_files: List[str] = field(default_factory=list)  # local paths for cached sources
    error: Optional[str] = None

    def to_json(self) -> Dict:
        d = asdict(self)
        # convert CodeBlock list
        d["extracted_code"] = [asdict(cb) for cb in self.extracted_code]
        return d


class Ingestor:
    def __init__(self, run_ingest_dir: Path):
        self.run_ingest_dir = run_ingest_dir
        ensure_dir(self.run_ingest_dir)
        ensure_dir(WORKSPACE_CACHE)
        ensure_dir(SOL_CACHE)
        self._session_cache = None  # lazy requests.Session

    def _session(self):
        try:
            import requests  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "requests is required for network ingestion paths; install project dependencies"
            ) from e
        if self._session_cache is None:
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT})
            self._session_cache = s
        return self._session_cache

    def ingest_urls(self, urls: List[str]) -> List[Artifact]:
        artifacts: List[Artifact] = []
        for url in urls:
            try:
                artifact = self.ingest_url(url)
            except Exception as e:  # noqa: BLE001
                logger.exception("ingest failed for %s", url)
                artifact = Artifact(url=url, kind="error", error=str(e))
            artifacts.append(artifact)
            self._persist_artifact(artifact)
        return artifacts

    def ingest_url(self, url: str) -> Artifact:
        # Support local files
        path = self._maybe_local_path(url)
        if path is not None and path.exists():
            return self._ingest_local(path)

        # special handlers by domain/pattern
        if self._is_github(url):
            return self._ingest_github(url)
        if self._is_google_doc(url):
            return self._ingest_google_doc(url)
        if self._is_slite(url):
            return self._ingest_slite(url)
        if url.lower().endswith(".pdf"):
            return self._ingest_pdf(url)

        # generic HTML / Markdown fetch
        return self._ingest_html_or_markdown(url)

    def _persist_artifact(self, artifact: Artifact) -> None:
        # save normalized JSON with metadata, raw text, extracted code
        idx = int(time.time() * 1000)
        base = sanitize_filename(artifact.title or artifact.url)
        json_path = self.run_ingest_dir / f"{base}.{idx}.json"
        txt_path = self.run_ingest_dir / f"{base}.{idx}.txt"
        write_json(json_path, artifact.to_json())
        if artifact.raw_text:
            write_text_file(txt_path, artifact.raw_text)

    # ----- detectors -----
    def _maybe_local_path(self, url: str) -> Optional[Path]:
        if url.startswith("file://"):
            return Path(url[len("file://") :])
        p = Path(url)
        if p.exists():
            return p
        return None

    def _is_github(self, url: str) -> bool:
        return re.match(r"https?://(www\.)?github.com/[^/]+/[^/]+", url) is not None

    def _is_google_doc(self, url: str) -> bool:
        return re.match(r"https?://docs\.google\.com/document/d/[A-Za-z0-9_-]+", url) is not None

    def _is_slite(self, url: str) -> bool:
        return "slite.com" in url

    # ----- local files -----
    def _ingest_local(self, path: Path) -> Artifact:
        suffix = path.suffix.lower()
        raw = None
        title = path.name
        if suffix in {".md", ".markdown"}:
            raw = read_text_file(path)
            extracted_code = self._extract_code_blocks_markdown(raw)
            return Artifact(
                url=str(path),
                kind="markdown",
                title=title,
                metadata={"source": "local"},
                raw_text=raw,
                extracted_code=extracted_code,
            )
        if suffix in {".html", ".htm"}:
            raw = read_text_file(path)
            text, title = self._html_to_text(raw)
            extracted_code = self._extract_code_blocks_html(raw)
            return Artifact(
                url=str(path),
                kind="html",
                title=title,
                metadata={"source": "local"},
                raw_text=text,
                extracted_code=extracted_code,
            )
        if suffix == ".pdf":
            from pdfminer.high_level import extract_text

            raw_bytes = path.read_bytes()
            try:
                text = extract_text(io.BytesIO(raw_bytes))
            except Exception as e:  # noqa: BLE001
                return Artifact(url=str(path), kind="pdf", title=title, error=str(e))
            return Artifact(
                url=str(path),
                kind="pdf",
                title=title,
                metadata={"source": "local"},
                raw_text=text,
                extracted_code=self._extract_code_blocks_text(text),
            )
        # fallback: treat as text
        raw = read_text_file(path)
        return Artifact(
            url=str(path),
            kind="local",
            title=title,
            metadata={"source": "local"},
            raw_text=raw,
            extracted_code=self._extract_code_blocks_text(raw),
        )

    # ----- html/markdown -----
    def _ingest_html_or_markdown(self, url: str) -> Artifact:
        try:
            resp = self._session().get(url, timeout=20)
        except Exception as e:  # noqa: BLE001
            return Artifact(url=url, kind="error", error=str(e))
        ct = resp.headers.get("content-type", "").lower()
        if resp.status_code != 200:
            return Artifact(url=url, kind="error", error=f"HTTP {resp.status_code}")
        content = resp.content
        text = content.decode("utf-8", errors="ignore")
        if "/markdown" in ct or url.lower().endswith((".md", ".markdown")):
            extracted_code = self._extract_code_blocks_markdown(text)
            return Artifact(url=url, kind="markdown", title=url, raw_text=text, extracted_code=extracted_code)
        # assume html
        text_content, title = self._html_to_text(text)
        extracted_code = self._extract_code_blocks_html(text)
        return Artifact(url=url, kind="html", title=title, raw_text=text_content, extracted_code=extracted_code)

    def _html_to_text(self, html: str) -> Tuple[str, Optional[str]]:
        # Import readability and bs4 lazily to avoid hard deps at import time
        try:
            from readability import Document  # type: ignore
        except Exception:
            Document = None  # type: ignore
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except Exception as e:  # noqa: BLE001
            # Without BeautifulSoup, fallback to plain text by stripping tags crudely
            text = re.sub(r"<[^>]+>", "\n", html)
            return (text, None)
        try:
            if Document is not None:
                doc = Document(html)
                title = doc.short_title()
                summary_html = doc.summary(html_partial=True)
                soup = BeautifulSoup(summary_html, "html.parser")
                text = soup.get_text("\n")
                return (text, title)
        except Exception:
            pass
        # fallback to full
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.string if soup.title else None
        return (soup.get_text("\n"), title)

    def _extract_code_blocks_html(self, html: str) -> List[CodeBlock]:
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except Exception:
            # Fallback to text-only extraction
            return self._extract_code_blocks_text(html)
        soup = BeautifulSoup(html, "html.parser")
        blocks: List[CodeBlock] = []
        for pre in soup.find_all(["pre", "code"]):
            code_text = pre.get_text("\n").strip()
            if not code_text:
                continue
            lang = None
            class_attr = pre.get("class") or []
            for cls in class_attr:
                m = re.match(r"language-([A-Za-z0-9_+-]+)", cls)
                if m:
                    lang = m.group(1)
                    break
            blocks.append(CodeBlock(language=lang, code=code_text))
        # also scan for triple backticks rendered as text
        blocks.extend(self._extract_code_blocks_text(soup.get_text("\n")))
        return self._dedupe_blocks(blocks)

    def _extract_code_blocks_markdown(self, md: str) -> List[CodeBlock]:
        return self._extract_code_blocks_text(md)

    def _extract_code_blocks_text(self, text: str) -> List[CodeBlock]:
        blocks: List[CodeBlock] = []
        # fenced code blocks ```lang\n...\n```
        fence_pat = re.compile(r"```([A-Za-z0-9_+-]*)\n(.*?)```", re.DOTALL)
        for m in fence_pat.finditer(text):
            lang = m.group(1) or None
            code = m.group(2).strip()
            if code:
                blocks.append(CodeBlock(language=lang, code=code))
        # simple heuristic for Solidity snippets
        for chunk in text.split("\n\n"):
            if "pragma solidity" in chunk:
                blocks.append(CodeBlock(language="solidity", code=chunk.strip()))
        return self._dedupe_blocks(blocks)

    def _dedupe_blocks(self, blocks: List[CodeBlock]) -> List[CodeBlock]:
        seen = set()
        out: List[CodeBlock] = []
        for b in blocks:
            h = sha1_hex(b.code.encode("utf-8"))
            if h in seen:
                continue
            seen.add(h)
            out.append(b)
        return out

    # ----- github -----
    def _ingest_github(self, url: str) -> Artifact:
        # supported forms:
        # https://github.com/{owner}/{repo}
        # https://github.com/{owner}/{repo}/tree/{branch}/{path?}
        m = re.match(r"https?://github.com/([^/]+)/([^/]+)(/tree/([^/]+)(/(.*))?)?", url)
        if not m:
            return Artifact(url=url, kind="error", error="Unrecognized GitHub URL")
        owner = m.group(1)
        repo = m.group(2).replace(".git", "")
        branch = m.group(4)
        subpath = m.group(6) or ""

        repo_api = f"https://api.github.com/repos/{owner}/{repo}"
        # find default branch if not specified
        if not branch:
            try:
                r = self._session().get(repo_api, timeout=20)
                if r.status_code == 200:
                    j = r.json()
                    branch = j.get("default_branch")
            except Exception:
                branch = None
        if not branch:
            branch = "main"

        # get tree
        try:
            tree_url = f"{repo_api}/git/trees/{branch}?recursive=1"
            r2 = self._session().get(tree_url, timeout=30)
            if r2.status_code != 200:
                return Artifact(url=url, kind="github", title=f"{owner}/{repo}", error=f"HTTP {r2.status_code}")
            tree = r2.json().get("tree", [])
            sol_paths = [t["path"] for t in tree if t.get("type") == "blob" and t.get("path", "").endswith(".sol")]
            if subpath:
                sol_paths = [p for p in sol_paths if p.startswith(subpath)]
        except Exception as e:  # noqa: BLE001
            return Artifact(url=url, kind="github", title=f"{owner}/{repo}", error=str(e))

        downloaded: List[str] = []
        for sp in sol_paths:
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{sp}"
            try:
                resp = self._session().get(raw_url, timeout=30)
                if resp.status_code != 200:
                    logger.warning("skip %s: HTTP %s", raw_url, resp.status_code)
                    continue
            except Exception as e:  # noqa: BLE001
                logger.warning("download failed %s: %s", raw_url, e)
                continue
            rel_dir = Path(owner) / repo / branch / Path(sp).parent
            cache_dir = SOL_CACHE / rel_dir
            ensure_dir(cache_dir)
            fname = Path(sp).name
            out_path = cache_dir / fname
            out_path.write_bytes(resp.content)
            downloaded.append(str(out_path.resolve()))
        meta = {"owner": owner, "repo": repo, "branch": branch, "downloaded_count": len(downloaded)}
        return Artifact(
            url=url,
            kind="github",
            title=f"{owner}/{repo}@{branch}",
            metadata=meta,
            raw_text=f"Downloaded {len(downloaded)} Solidity files.",
            extracted_code=[],
            downloaded_files=downloaded,
        )

    # ----- google docs -----
    def _ingest_google_doc(self, url: str) -> Artifact:
        m = re.match(r"https?://docs\.google\.com/document/d/([A-Za-z0-9_-]+)", url)
        if not m:
            return Artifact(url=url, kind="google_doc", error="Invalid Google Doc URL")
        doc_id = m.group(1)
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
        try:
            r = self._session().get(export_url, timeout=20)
            if r.status_code != 200:
                return Artifact(
                    url=url,
                    kind="google_doc",
                    title=f"Google Doc {doc_id}",
                    error=f"Export failed: HTTP {r.status_code} (likely private/restricted)",
                )
            text = r.content.decode("utf-8", errors="ignore")
        except Exception as e:  # noqa: BLE001
            return Artifact(url=url, kind="google_doc", title=f"Google Doc {doc_id}", error=str(e))
        return Artifact(
            url=url,
            kind="google_doc",
            title=f"Google Doc {doc_id}",
            raw_text=text,
            extracted_code=self._extract_code_blocks_text(text),
        )

    # ----- slite -----
    def _ingest_slite(self, url: str) -> Artifact:
        try:
            r = self._session().get(url, timeout=15)
            if r.status_code in (401, 403):
                return Artifact(url=url, kind="slite_skip", error=f"Restricted: HTTP {r.status_code}")
            text = r.text
            if any(k in text.lower() for k in ["login", "sign in", "sign-in"]) and "slite" in text.lower():
                return Artifact(url=url, kind="slite_skip", error="Restricted (login page)")
            # If accessible, attempt to parse as html
            return self._ingest_html_or_markdown(url)
        except Exception as e:  # noqa: BLE001
            return Artifact(url=url, kind="slite_skip", error=str(e))

    # ----- pdf -----
    def _ingest_pdf(self, url: str) -> Artifact:
        try:
            r = self._session().get(url, timeout=30)
            if r.status_code != 200:
                return Artifact(url=url, kind="pdf", error=f"HTTP {r.status_code}")
            from pdfminer.high_level import extract_text

            text = extract_text(io.BytesIO(r.content))
        except Exception as e:  # noqa: BLE001
            return Artifact(url=url, kind="pdf", error=str(e))
        return Artifact(url=url, kind="pdf", title=url, raw_text=text, extracted_code=self._extract_code_blocks_text(text))
