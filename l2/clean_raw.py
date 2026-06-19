"""
Pre-filter raw blog and reddit data before downstream insight extraction.

Drops:
  - Blog rows that are 404 pages, nav-cruft, too short, or mostly markdown links.
  - Reddit rows that don't mention the POI, are too short, and chunks long posts.
"""

import json
import re
from collections import Counter
from pathlib import Path


L2_DATA = Path(__file__).resolve().parent / "data"

BLOG_IN = L2_DATA / "raw_blog_culinary.json"
BLOG_OUT = L2_DATA / "raw_blog_culinary_clean.json"

REDDIT_IN = L2_DATA / "raw_reddit_targeted.json"
REDDIT_OUT = L2_DATA / "raw_reddit_targeted_clean.json"


# --------------------------------------------------------------------------- #
# Blog filtering
# --------------------------------------------------------------------------- #

ERROR_404_PATTERNS = [
    re.compile(r"error\s*404", re.IGNORECASE),
    re.compile(r"404\s*not\s*found", re.IGNORECASE),
    re.compile(r"page\s*not\s*found", re.IGNORECASE),
]

MD_LINK_PATTERN = re.compile(r"\[[^\]]*\]\([^)]*\)")


def looks_like_404(text: str) -> bool:
    return any(p.search(text) for p in ERROR_404_PATTERNS)


def looks_like_nav_short(text: str) -> bool:
    stripped = text.lstrip()
    starts_with_skip = (
        stripped.lower().startswith("skip to main content")
        or stripped.lower().startswith("skip to footer")
        or stripped.lower().startswith("- skip to main content")
        or stripped.lower().startswith("- skip to footer")
    )
    return starts_with_skip and len(text) < 300


def mostly_markdown_links(text: str) -> bool:
    if not text:
        return False
    link_chars = sum(len(m.group(0)) for m in MD_LINK_PATTERN.finditer(text))
    return (link_chars / len(text)) > 0.5


def classify_blog_drop(text: str) -> str | None:
    """Return reason string if row should be dropped, else None."""
    if not text or not text.strip():
        return "empty_text"
    if looks_like_404(text):
        return "error_404"
    if looks_like_nav_short(text):
        return "nav_cruft_short"
    if len(text) < 250:
        return "too_short_<250"
    if mostly_markdown_links(text):
        return "mostly_md_links"
    return None


def clean_blog():
    rows = json.loads(BLOG_IN.read_text())
    n_in = len(rows)

    per_poi_in = Counter(r.get("poi_name") for r in rows)
    drop_reasons = Counter()
    per_poi_out = Counter()
    survivors = []

    for row in rows:
        reason = classify_blog_drop(row.get("text", ""))
        if reason:
            drop_reasons[reason] += 1
        else:
            survivors.append(row)
            per_poi_out[row.get("poi_name")] += 1

    BLOG_OUT.write_text(json.dumps(survivors, indent=2, ensure_ascii=False))
    n_out = len(survivors)
    pct = (n_out / n_in * 100) if n_in else 0.0

    print(f"\n=== BLOG CULINARY ===")
    print(f"In:  {n_in} rows")
    print(f"Out: {n_out} rows ({pct:.1f}% kept)")
    print(f"\nPer-POI breakdown (in -> out):")
    for poi in sorted(per_poi_in):
        print(f"  {poi}: {per_poi_in[poi]} -> {per_poi_out.get(poi, 0)}")
    print(f"\nTop drop reasons:")
    for reason, count in drop_reasons.most_common():
        print(f"  {reason}: {count}")


# --------------------------------------------------------------------------- #
# Reddit filtering + chunking
# --------------------------------------------------------------------------- #

COLOSSEUM_TERMS = re.compile(r"\b(colosseum|coliseum)\b", re.IGNORECASE)
LOUVRE_TERMS = re.compile(r"\b(louvre|mona\s*lisa)\b", re.IGNORECASE)

# rough sentence splitter — split on . ! ? followed by whitespace or newline
SENTENCE_SPLIT = re.compile(r"(?<=[\.\!\?])\s+")

CHUNK_TARGET = 1500
LONG_THRESHOLD = 8000


def poi_term_matches(poi_name: str, text: str) -> bool:
    name = (poi_name or "").lower()
    if "colosseum" in name or "coliseum" in name:
        return bool(COLOSSEUM_TERMS.search(text))
    if "louvre" in name:
        return bool(LOUVRE_TERMS.search(text))
    # Fallback for any other POI — require the POI name itself
    if poi_name:
        return poi_name.lower() in text.lower()
    return False


def chunk_text(text: str, target: int = CHUNK_TARGET) -> list[str]:
    """Greedy split on sentence boundaries, aiming for `target` chars per chunk."""
    sentences = SENTENCE_SPLIT.split(text)
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        sent_len = len(sent) + 1  # space separator

        # If a single sentence is itself larger than target, hard-wrap it
        if sent_len > target * 1.5:
            # flush current buf first
            if buf:
                chunks.append(" ".join(buf).strip())
                buf, buf_len = [], 0
            # hard wrap the giant sentence
            for i in range(0, len(sent), target):
                chunks.append(sent[i : i + target].strip())
            continue

        if buf_len + sent_len > target and buf:
            chunks.append(" ".join(buf).strip())
            buf, buf_len = [sent], sent_len
        else:
            buf.append(sent)
            buf_len += sent_len

    if buf:
        chunks.append(" ".join(buf).strip())

    return [c for c in chunks if c]


def classify_reddit_drop(row: dict) -> str | None:
    text = row.get("text", "") or ""
    if not text.strip():
        return "empty_text"
    if len(text) < 100:
        return "too_short_<100"
    if not poi_term_matches(row.get("poi_name", ""), text):
        return "no_poi_term"
    return None


def clean_reddit():
    rows = json.loads(REDDIT_IN.read_text())
    n_in = len(rows)

    per_poi_in = Counter(r.get("poi_name") for r in rows)
    drop_reasons = Counter()
    per_poi_out = Counter()
    survivors: list[dict] = []
    chunks_created = 0
    posts_chunked = 0

    for row in rows:
        reason = classify_reddit_drop(row)
        if reason:
            drop_reasons[reason] += 1
            continue

        text = row["text"]
        if len(text) > LONG_THRESHOLD:
            chunks = chunk_text(text, CHUNK_TARGET)
            posts_chunked += 1
            for i, chunk in enumerate(chunks):
                new_row = dict(row)
                new_row["source_id"] = f"{row['source_id']}_chunk_{i}"
                new_row["text"] = chunk
                # tag in meta so downstream knows
                meta = dict(row.get("meta", {}))
                meta["chunk_index"] = i
                meta["chunk_total"] = len(chunks)
                meta["parent_source_id"] = row["source_id"]
                new_row["meta"] = meta
                survivors.append(new_row)
                per_poi_out[row.get("poi_name")] += 1
                chunks_created += 1
        else:
            survivors.append(row)
            per_poi_out[row.get("poi_name")] += 1

    REDDIT_OUT.write_text(json.dumps(survivors, indent=2, ensure_ascii=False))
    n_out = len(survivors)
    pct = (n_out / n_in * 100) if n_in else 0.0

    print(f"\n=== REDDIT TARGETED ===")
    print(f"In:  {n_in} rows")
    print(f"Out: {n_out} rows ({pct:.1f}% kept)")
    print(f"Long posts chunked: {posts_chunked}  ->  {chunks_created} chunks created")
    print(f"\nPer-POI breakdown (in -> out):")
    for poi in sorted(per_poi_in):
        print(f"  {poi}: {per_poi_in[poi]} -> {per_poi_out.get(poi, 0)}")
    print(f"\nTop drop reasons:")
    for reason, count in drop_reasons.most_common():
        print(f"  {reason}: {count}")


def main():
    print(f"Reading blog from: {BLOG_IN}")
    print(f"Reading reddit from: {REDDIT_IN}")
    clean_blog()
    clean_reddit()
    print(f"\nWrote: {BLOG_OUT}")
    print(f"Wrote: {REDDIT_OUT}")


if __name__ == "__main__":
    main()
