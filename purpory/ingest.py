# fetch URLs (tweet/arxiv/pdf/web) and save as annotated markdown
from __future__ import annotations
import json
import re
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from purpory.security import safe_fetch, safe_fetch_text, validate_url


def _yaml_str(s: str) -> str:
    """Escape a string for embedding in a YAML double-quoted scalar.

    Handles every YAML 1.1/1.2 line-break and control character that could
    let a hostile value (e.g. a fetched page title) break out of the quoted
    scalar and inject sibling YAML keys (F-009 / F-019). The previous
    implementation missed `\\t`, `\\0`, the unicode line-separator U+2028 and
    paragraph-separator U+2029 — all of which YAML treats as line breaks.

    We intentionally do not depend on PyYAML (not in pyproject deps) and
    instead emit safely-escaped double-quoted scalars by hand: the YAML
    double-quoted form recognises `\\\\`, `\\"`, `\\n`, `\\r`, `\\t`, `\\0`,
    `\\L` (U+2028), `\\P` (U+2029), and `\\xNN`/`\\uNNNN` numeric escapes.
    """
    if s is None:
        return ""
    out: list[str] = []
    for ch in str(s):
        cp = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\0":
            out.append("\\0")
        elif cp == 0x2028:
            out.append("\\L")
        elif cp == 0x2029:
            out.append("\\P")
        elif cp < 0x20 or cp == 0x7F:
            out.append(f"\\x{cp:02x}")
        else:
            out.append(ch)
    return "".join(out)


def _safe_filename(url: str, suffix: str) -> str:
    """Turn a URL into a safe filename."""
    parsed = urllib.parse.urlparse(url)
    name = parsed.netloc + parsed.path
    name = re.sub(r"[^\w\-]", "_", name).strip("_")
    name = re.sub(r"_+", "_", name)[:80]
    return name + suffix


def _detect_url_type(url: str) -> str:
    """Classify the URL for targeted extraction."""
    lower = url.lower()
    if "twitter.com" in lower or "x.com" in lower:
        return "tweet"
    if "arxiv.org" in lower:
        return "arxiv"
    if "github.com" in lower:
        return "github"
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube"
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if any(path.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "image"
    return "webpage"


def _fetch_html(url: str) -> str:
    return safe_fetch_text(url)


def _html_to_markdown(html: str, url: str) -> str:
    """Convert HTML to clean markdown. Uses markdownify if available, else basic strip."""
    # Always pre-strip script/style so their text content never leaks into output
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    try:
        from markdownify import markdownify
        return markdownify(html, heading_style="ATX", bullets="-", strip=["img"])
    except ImportError:
        # Fallback: basic tag strip
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000]


def _fetch_tweet(url: str, author: str | None, contributor: str | None) -> tuple[str, str]:
    """Fetch a tweet URL. Returns (content, filename)."""
    # Normalize to twitter.com for oEmbed
    oembed_url = url.replace("x.com", "twitter.com")
    oembed_api = f"https://publish.twitter.com/oembed?url={urllib.parse.quote(oembed_url)}&omit_script=true"
    data = json.loads(safe_fetch_text(oembed_api))
    if not isinstance(data, dict):
        raise ValueError("Twitter oEmbed response must be a JSON object")
    tweet_text = re.sub(r"<[^>]+>", "", str(data.get("html", ""))).strip()
    tweet_author = str(data.get("author_name", "")).strip()
    if not tweet_text or not tweet_author:
        raise ValueError("Twitter oEmbed response is missing content or author")

    now = datetime.now(timezone.utc).isoformat()
    content = f"""---
source_url: "{_yaml_str(url)}"
type: tweet
author: "{_yaml_str(tweet_author)}"
captured_at: {now}
contributor: "{_yaml_str(contributor or author or 'unknown')}"
---

# Tweet by @{tweet_author}

{tweet_text}

Source: {url}
"""
    filename = _safe_filename(url, ".md")
    return content, filename


def _fetch_webpage(url: str, author: str | None, contributor: str | None) -> tuple[str, str]:
    """Fetch a generic webpage and convert to markdown."""
    html = _fetch_html(url)
    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else url

    markdown = _html_to_markdown(html, url)
    now = datetime.now(timezone.utc).isoformat()
    content = f"""---
source_url: "{_yaml_str(url)}"
type: webpage
title: "{_yaml_str(title)}"
captured_at: {now}
contributor: "{_yaml_str(contributor or author or 'unknown')}"
---

# {title}

Source: {url}

---

{markdown[:12000]}
"""
    filename = _safe_filename(url, ".md")
    return content, filename


def _fetch_arxiv(url: str, author: str | None, contributor: str | None) -> tuple[str, str]:
    """Fetch arXiv abstract page."""
    # Convert /abs/ or /pdf/ to abs for the API
    arxiv_id = re.search(r"(\d{4}\.\d{4,5})", url)
    if arxiv_id:
        api_url = f"https://export.arxiv.org/abs/{arxiv_id.group(1)}"
        html = _fetch_html(api_url)
        abstract_match = re.search(
            r'class="abstract[^"]*"[^>]*>(.*?)</blockquote>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        title_match = re.search(
            r'class="title[^"]*"[^>]*>(.*?)</h1>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        authors_match = re.search(
            r'class="authors"[^>]*>(.*?)</div>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if abstract_match is None or title_match is None or authors_match is None:
            raise ValueError(f"arXiv response for {arxiv_id.group(1)} is missing metadata")
        abstract = re.sub(r"<[^>]+>", "", abstract_match.group(1)).strip()
        title = re.sub(r"<[^>]+>", " ", title_match.group(1)).strip()
        paper_authors = re.sub(r"<[^>]+>", "", authors_match.group(1)).strip()
    else:
        return _fetch_webpage(url, author, contributor)

    now = datetime.now(timezone.utc).isoformat()
    content = f"""---
source_url: "{_yaml_str(url)}"
arxiv_id: "{_yaml_str(arxiv_id.group(1) if arxiv_id else '')}"
type: paper
title: "{_yaml_str(title)}"
paper_authors: "{_yaml_str(paper_authors)}"
captured_at: {now}
contributor: "{_yaml_str(contributor or author or 'unknown')}"
---

# {title}

**Authors:** {paper_authors}
**arXiv:** {arxiv_id.group(1) if arxiv_id else url}

## Abstract

{abstract}

Source: {url}
"""
    filename = f"arxiv_{arxiv_id.group(1).replace('.', '_')}.md" if arxiv_id else _safe_filename(url, ".md")
    return content, filename


def _download_binary(url: str, suffix: str, target_dir: Path) -> Path:
    """Download a binary file (PDF, image) directly."""
    filename = _safe_filename(url, suffix)
    out_path = target_dir / filename
    out_path.write_bytes(safe_fetch(url))
    return out_path


def ingest(url: str, target_dir: Path, author: str | None = None, contributor: str | None = None) -> Path:
    """
    Fetch a URL and save it into target_dir as a purpory-ready file.

    Returns the path of the saved file.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    url_type = _detect_url_type(url)

    try:
        validate_url(url)
    except ValueError as exc:
        raise ValueError(f"ingest: {exc}") from exc

    try:
        if url_type == "pdf":
            out = _download_binary(url, ".pdf", target_dir)
            print(f"Downloaded PDF: {out.name}")
            return out

        if url_type == "image":
            suffix = Path(urllib.parse.urlparse(url).path).suffix or ".jpg"
            out = _download_binary(url, suffix, target_dir)
            print(f"Downloaded image: {out.name}")
            return out

        if url_type == "youtube":
            from purpory.transcribe import download_audio
            out = download_audio(url, target_dir)
            print(f"Downloaded audio: {out.name}")
            return out

        if url_type == "tweet":
            content, filename = _fetch_tweet(url, author, contributor)
        elif url_type == "arxiv":
            content, filename = _fetch_arxiv(url, author, contributor)
        else:
            content, filename = _fetch_webpage(url, author, contributor)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"ingest: failed to fetch {url!r}: {exc}") from exc

    out_path = target_dir / filename
    # Avoid overwriting - append counter if needed
    counter = 1
    while out_path.exists() and counter < 1000:
        stem = Path(filename).stem
        out_path = target_dir / f"{stem}_{counter}.md"
        counter += 1

    out_path.write_text(content, encoding="utf-8")
    print(f"Saved {url_type}: {out_path.name}")
    return out_path

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch a URL into a purpory /raw folder")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("target_dir", nargs="?", default="./raw", help="Target directory (default: ./raw)")
    parser.add_argument("--author", help="Your name (stored as node metadata)")
    parser.add_argument("--contributor", help="Contributor name for team graphs")
    args = parser.parse_args()
    out = ingest(args.url, Path(args.target_dir), author=args.author, contributor=args.contributor)
    print(f"Ready for purpory: {out}")
