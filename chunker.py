"""
chunker.py  —  Structure-Aware Hierarchical Chunker
====================================================
Reads document_structure.json (from cleaner.py) and emits embedding-ready
chunks with full structural metadata.

Changes in this version
────────────────────────
  [1] Caption / running-header filtering done upstream (cleaner.py).
      Chunker no longer needs to re-filter — it trusts the input is clean.

  [2] Subsection boundaries from cleaner's improved heading detection are
      respected automatically — better hierarchy → better breadcrumbs.

  [3] SEMANTIC paragraph-boundary splitting:
        - cleaner.py now stores content as a LIST of paragraph strings,
          not one big blob.
        - chunker first tries to split at paragraph boundaries.
        - only falls back to sentence-boundary splitting when a single
          paragraph still exceeds CHUNK_MAX_WORDS.
        - never splits in the middle of a paragraph if it fits.
        This produces chunks that start and end at natural thought boundaries.

Output schema per chunk
────────────────────────
  heading        : section heading
  level          : hierarchy depth (1/2/3)
  page           : page of first word in this chunk
  breadcrumb     : ancestor heading path (outermost first)
  context_prefix : "Title > Parent > Heading" string
  content        : paragraph text (100-400 words, semantically bounded)
  word_count     : words in content
  chunk_index    : 0-based position among split sub-chunks of same section
  total_chunks   : total sub-chunks this section produced
  is_split       : True when section content was split across chunks
  split_reason   : "paragraph" | "sentence" | "none" — why we split
  embed_text     : context_prefix + "\\n\\n" + content

Usage:
  python chunker.py --input document_structure.json --output chunks.json
  python chunker.py --input document_structure.json --summary
  python chunker.py --input document_structure.json --min-words 30 --merge-short
"""

import json
import re
import argparse
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# 0.  Config
# ─────────────────────────────────────────────────────────────────────────────

CHUNK_MIN_WORDS      = 100
CHUNK_MAX_WORDS      = 400
SHORT_CHUNK_THRESHOLD = 30
MAX_BREADCRUMB_DEPTH  = None   # None = include all ancestor levels


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Sentence splitter  (fallback when a single paragraph is too large)
# ─────────────────────────────────────────────────────────────────────────────

def _split_sentences(text: str) -> list[str]:
    """Split on sentence boundaries, protecting common abbreviations."""
    protected = re.sub(
        r"\b(e\.g\.|i\.e\.|et al\.|Fig\.|fig\.|Eq\.|eq\.|cf\.|vs\.|approx\.|"
        r"Dr\.|Mr\.|Ms\.|Prof\.|Sr\.|Jr\.|[A-Z]\.[A-Z]\.)",
        lambda m: m.group(0).replace(".", "\x00"),
        text,
    )
    parts = re.split(r"(?<=[.!?])\s+", protected)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


def _pack_sentences(sentences: list[str], min_w: int, max_w: int) -> list[str]:
    """
    Greedily pack sentences into chunks of [min_w, max_w] words.
    Any single sentence > max_w is hard-split on word boundaries.
    """
    chunks: list[str] = []
    buf: list[str] = []
    buf_words = 0

    def flush():
        nonlocal buf, buf_words
        if buf:
            chunks.append(" ".join(buf))
        buf = []
        buf_words = 0

    for sent in sentences:
        sw = len(sent.split())
        if sw > max_w:
            flush()
            words = sent.split()
            for start in range(0, len(words), max_w):
                chunks.append(" ".join(words[start: start + max_w]))
            continue
        if buf_words + sw > max_w and buf_words >= min_w:
            flush()
        buf.append(sent)
        buf_words += sw

    flush()

    # Merge a tiny trailing chunk into the previous one
    if len(chunks) >= 2 and len(chunks[-1].split()) < min_w:
        tail = chunks.pop()
        chunks[-1] = chunks[-1] + " " + tail

    return [c for c in chunks if c.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# 2.  [3] Semantic paragraph-boundary splitter
# ─────────────────────────────────────────────────────────────────────────────

def split_paragraphs_into_chunks(
    paragraphs: list[str],
    min_words: int = CHUNK_MIN_WORDS,
    max_words: int = CHUNK_MAX_WORDS,
) -> tuple[list[str], str]:
    """
    [Requirement 3] Split content into chunks, preferring paragraph boundaries.

    Algorithm:
    1. Walk paragraphs in order, accumulating words.
    2. When adding the next paragraph would push us over max_words AND we
       already have at least min_words → close the current chunk (paragraph
       boundary split).
    3. If a SINGLE paragraph is itself > max_words, fall back to sentence-
       boundary splitting for just that paragraph (sentence boundary split).
    4. Never split in the middle of a paragraph that fits within max_words.

    Returns:
      (list_of_chunk_strings, split_reason)
      split_reason: "none" | "paragraph" | "sentence"
    """
    if not paragraphs:
        return [], "none"

    total_words = sum(len(p.split()) for p in paragraphs)

    # Everything fits in one chunk — no split needed
    if total_words <= max_words:
        combined = "\n\n".join(p for p in paragraphs if p.strip())
        return [combined] if combined.strip() else [], "none"

    chunks: list[str] = []
    buf_paras: list[str] = []
    buf_words = 0
    split_reason = "paragraph"

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        pw = len(para.split())

        # Single oversized paragraph → sentence-level fallback
        if pw > max_words:
            # First flush whatever we have
            if buf_paras and buf_words >= min_words:
                chunks.append("\n\n".join(buf_paras))
                buf_paras, buf_words = [], 0
            # Sentence split this paragraph
            sents = _split_sentences(para)
            sent_chunks = _pack_sentences(sents, min_words, max_words)
            # The first sent_chunk might be merged with leftover buf
            if buf_paras:
                first = sent_chunks.pop(0) if sent_chunks else ""
                if first:
                    merged_words = buf_words + len(first.split())
                    if merged_words <= max_words:
                        buf_paras.append(first)
                        buf_words = merged_words
                    else:
                        chunks.append("\n\n".join(buf_paras))
                        buf_paras, buf_words = [first], len(first.split())
            chunks.extend(sent_chunks)
            split_reason = "sentence"
            continue

        # Would this paragraph push us over max AND we already have min?
        if buf_words + pw > max_words and buf_words >= min_words:
            chunks.append("\n\n".join(buf_paras))
            buf_paras, buf_words = [], 0

        buf_paras.append(para)
        buf_words += pw

    # Flush remainder
    if buf_paras:
        remainder = "\n\n".join(buf_paras)
        # Merge tiny remainder into previous chunk if possible
        if buf_words < min_words and chunks:
            prev = chunks[-1]
            if len(prev.split()) + buf_words <= max_words * 1.1:
                chunks[-1] = prev + "\n\n" + remainder
            else:
                chunks.append(remainder)
        else:
            chunks.append(remainder)

    return [c for c in chunks if c.strip()], split_reason


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Build one embedding-ready chunk dict
# ─────────────────────────────────────────────────────────────────────────────

def _build_chunk(
    heading: str,
    level: int,
    page: int | None,
    breadcrumb: list[str],
    content: str,
    title: str,
    chunk_index: int = 0,
    total_chunks: int = 1,
    split_reason: str = "none",
) -> dict:
    crumb_parts: list[str] = []
    if title:
        crumb_parts.append(title)
    if MAX_BREADCRUMB_DEPTH is not None:
        crumb_parts.extend(breadcrumb[-MAX_BREADCRUMB_DEPTH:])
    else:
        crumb_parts.extend(breadcrumb)

    breadcrumb_str = " > ".join(crumb_parts)
    context_prefix = f"{breadcrumb_str} > {heading}" if breadcrumb_str else heading

    if total_chunks > 1:
        context_prefix = f"{context_prefix} (part {chunk_index + 1}/{total_chunks})"

    word_count = len(content.split()) if content else 0
    embed_text = f"{context_prefix}\n\n{content}" if content else context_prefix

    return {
        "heading":        heading,
        "level":          level,
        "page":           page,
        "breadcrumb":     list(breadcrumb),
        "context_prefix": context_prefix,
        "content":        content,
        "word_count":     word_count,
        "chunk_index":    chunk_index,
        "total_chunks":   total_chunks,
        "is_split":       total_chunks > 1,
        "split_reason":   split_reason,
        "short":          word_count < SHORT_CHUNK_THRESHOLD,
        "has_children":   False,
        "embed_text":     embed_text,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Core: walk the section tree depth-first
# ─────────────────────────────────────────────────────────────────────────────

def walk_tree(
    nodes: list,
    breadcrumb: list[str] = None,
    title: str = "",
) -> list[dict]:
    """
    Walk the hierarchy tree and emit chunks.

    Content per node is now a LIST of paragraph strings (from cleaner.py).
    We pass the whole list to split_paragraphs_into_chunks() which handles
    paragraph-boundary splitting first, sentence-boundary only as fallback.
    """
    if breadcrumb is None:
        breadcrumb = []

    chunks: list[dict] = []

    for node in nodes:
        heading   = node.get("heading", "")
        level     = node.get("level", 1)
        page      = node.get("page")
        content   = node.get("content", [])
        children  = node.get("children", [])

        # content may be a list of paragraph strings (new format) or a
        # single string (legacy format from older cleaner versions)
        if isinstance(content, str):
            paragraphs = [content] if content.strip() else []
        else:
            paragraphs = [p for p in content if p and p.strip()]

        # [3] Semantic split at paragraph boundaries first
        text_chunks, split_reason = split_paragraphs_into_chunks(paragraphs)
        total = len(text_chunks) if text_chunks else 1

        if not text_chunks:
            chunk = _build_chunk(
                heading=heading, level=level, page=page,
                breadcrumb=breadcrumb, content="",
                title=title, chunk_index=0, total_chunks=1,
                split_reason="none",
            )
            chunk["has_children"] = bool(children)
            chunks.append(chunk)
        else:
            for idx, text_part in enumerate(text_chunks):
                chunk = _build_chunk(
                    heading=heading, level=level, page=page,
                    breadcrumb=breadcrumb, content=text_part,
                    title=title, chunk_index=idx, total_chunks=total,
                    split_reason=split_reason if total > 1 else "none",
                )
                chunk["has_children"] = bool(children)
                chunks.append(chunk)

        if children:
            child_breadcrumb = breadcrumb + [heading]
            chunks.extend(walk_tree(children, child_breadcrumb, title))

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Short-chunk merge (optional)
# ─────────────────────────────────────────────────────────────────────────────

def merge_short_chunks(chunks: list, min_words: int = SHORT_CHUNK_THRESHOLD) -> list:
    if not chunks:
        return chunks
    result: list[dict] = []
    for chunk in chunks:
        if chunk["word_count"] < min_words and not chunk["has_children"] and result:
            parent_idx = None
            for i in range(len(result) - 1, -1, -1):
                if result[i]["level"] < chunk["level"]:
                    parent_idx = i
                    break
            if parent_idx is not None:
                parent = result[parent_idx]
                addition = f"\n\n[{chunk['heading']}]\n{chunk['content']}" if chunk["content"] else ""
                parent["content"]    += addition
                parent["embed_text"] += addition
                parent["word_count"] += chunk["word_count"]
                parent["short"]       = parent["word_count"] < min_words
                continue
        result.append(chunk)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 6.  References chunking (separate index)
# ─────────────────────────────────────────────────────────────────────────────

def chunk_references(ref_sections: list, title: str = "") -> list[dict]:
    """
    Reference entries are NOT split — each is typically a single citation
    that should stay intact for BM25 / citation lookup.
    """
    ref_chunks = walk_tree(ref_sections, breadcrumb=[], title=title)
    for chunk in ref_chunks:
        chunk["is_reference"] = True
    return ref_chunks


# ─────────────────────────────────────────────────────────────────────────────
# 7.  Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_chunker(doc: dict, min_words: int = 0, merge_short: bool = False) -> dict:
    title        = doc.get("title", "")
    sections     = doc.get("sections", [])
    ref_sections = doc.get("references", [])

    chunks = walk_tree(sections, breadcrumb=[], title=title)
    if merge_short and min_words > 0:
        chunks = merge_short_chunks(chunks, min_words=min_words)

    reference_chunks = chunk_references(ref_sections, title=title)

    return {"chunks": chunks, "reference_chunks": reference_chunks}


# ─────────────────────────────────────────────────────────────────────────────
# 8.  CLI
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(result: dict, doc: dict) -> None:
    chunks     = result["chunks"]
    ref_chunks = result["reference_chunks"]

    para_splits = sum(1 for c in chunks if c.get("split_reason") == "paragraph")
    sent_splits = sum(1 for c in chunks if c.get("split_reason") == "sentence")

    print(f"\n{'─'*72}")
    print(f"  Document : {doc.get('title','(no title)')[:68]}")
    print(f"  Chunks   : {len(chunks)} main  |  {len(ref_chunks)} reference")
    print(f"  Splits   : {para_splits} paragraph-boundary  |  {sent_splits} sentence-boundary")
    print(f"  Short    : {sum(1 for c in chunks if c['short'])} below {SHORT_CHUNK_THRESHOLD}w")
    print(f"{'─'*72}\n")

    for i, c in enumerate(chunks):
        indent = "  " * (c["level"] - 1)
        flags = ""
        if c["is_split"]:
            flags += f" [{c['split_reason']}-split {c['chunk_index']+1}/{c['total_chunks']}]"
        if c["short"]:
            flags += " [short]"
        crumb = " > ".join(c["breadcrumb"]) if c["breadcrumb"] else "(top)"
        print(f"  [{i+1:03d}] {indent}L{c['level']}  {c['heading']}{flags}")
        print(f"         {indent}crumb  : {crumb}")
        print(f"         {indent}page={c['page']}  words={c['word_count']}")
        if c["content"]:
            preview = c["content"][:110].replace("\n", " ↵ ")
            print(f"         {indent}content: {preview}...")
        print()

    if ref_chunks:
        print(f"  REFERENCE CHUNKS ({len(ref_chunks)})")
        for rc in ref_chunks[:3]:
            print(f"    • {rc['content'][:80]}...")
        if len(ref_chunks) > 3:
            print(f"    … and {len(ref_chunks)-3} more")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Hierarchical chunker: document_structure.json → chunks.json"
    )
    parser.add_argument("--input",       "-i", default="document_structure.json")
    parser.add_argument("--output",      "-o", default="chunks.json")
    parser.add_argument("--min-words",   "-m", type=int, default=0)
    parser.add_argument("--merge-short", action="store_true")
    parser.add_argument("--summary",     "-s", action="store_true")
    args = parser.parse_args()

    doc    = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = run_chunker(doc, min_words=args.min_words, merge_short=args.merge_short)

    if args.summary:
        _print_summary(result, doc)
    else:
        chunks     = result["chunks"]
        ref_chunks = result["reference_chunks"]
        split_ct   = sum(1 for c in chunks if c["is_split"])
        short_ct   = sum(1 for c in chunks if c["short"])
        all_pages  = [c["page"] for c in chunks if c["page"]]

        out_dir  = Path("chunker_json")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / Path(args.output).name
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"✓  Saved → {out_path}")
        print(f"   Main chunks     : {len(chunks)}  ({split_ct} split, {short_ct} short)")
        print(f"   Reference chunks: {len(ref_chunks)}")
        if all_pages:
            print(f"   Page range      : {min(all_pages)} – {max(all_pages)}")
        print(f"   Levels          : {sorted(set(c['level'] for c in chunks))}")


if __name__ == "__main__":
    main()
