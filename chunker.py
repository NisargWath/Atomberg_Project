"""
chunker.py  —  Structure-Aware Hierarchical Chunker
====================================================
Reads document_structure.json produced by cleaner.py and emits
embedding-ready chunks with full structure metadata.

Changes vs previous version
────────────────────────────
  [4] Large chunks are split into 100-400 word sub-chunks while preserving
      heading, level, breadcrumb, context_prefix, and page metadata.
  [5] All output chunks carry the full structure-aware metadata needed for
      embedding (context_prefix, breadcrumb, chunk_index, total_chunks).
  [6] References section processed separately — stored in "reference_chunks"
      key, not mixed into the main retrieval chunks.

Output schema per chunk
────────────────────────
  heading        : section heading this chunk belongs to
  level          : depth in hierarchy (1/2/3)
  page           : page number of first line in this chunk
  breadcrumb     : list of ancestor headings, outermost first
  context_prefix : "Title > Parent > Heading" string for embedding prefix
  content        : the actual paragraph text (100-400 words)
  word_count     : word count of content only
  chunk_index    : position within sibling chunks of same section (0-based)
  total_chunks   : total sub-chunks this section was split into
  is_split       : True when section was too large and was split
  embed_text     : context_prefix + "\\n\\n" + content  (pass to embedder)

Usage:
  python chunker.py --input document_structure.json --output chunks.json
  python chunker.py --input document_structure.json --output chunks.json --summary
  python chunker.py --input document_structure.json --output chunks.json --min-words 30 --merge-short
"""

import json
import re
import argparse
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# 0.  Config
# ─────────────────────────────────────────────────────────────────────────────

# [4] Target chunk size window (words)
CHUNK_MIN_WORDS = 100
CHUNK_MAX_WORDS = 400

# Chunks below this are flagged "short" — the merge pass can absorb them.
SHORT_CHUNK_THRESHOLD = 30

# How many ancestor levels to include in the breadcrumb prefix.
# None = all levels.
MAX_BREADCRUMB_DEPTH = None


# ─────────────────────────────────────────────────────────────────────────────
# 1.  [4] Sentence-aware text splitter
# ─────────────────────────────────────────────────────────────────────────────

def _split_sentences(text: str) -> list[str]:
    """
    Split text into sentences at '.', '!', '?' boundaries.
    Avoids splitting on abbreviations like 'e.g.', 'i.e.', 'Fig.', 'et al.'
    """
    # Protect common abbreviations
    protected = re.sub(
        r"\b(e\.g\.|i\.e\.|et al\.|Fig\.|fig\.|Eq\.|eq\.|cf\.|vs\.|approx\.|"
        r"Dr\.|Mr\.|Ms\.|Prof\.|Sr\.|Jr\.|[A-Z]\.[A-Z]\.)",
        lambda m: m.group(0).replace(".", "<!DOT!>"),
        text,
    )
    # Split on sentence-ending punctuation
    raw_sentences = re.split(r"(?<=[.!?])\s+", protected)
    # Restore dots
    return [s.replace("<!DOT!>", ".").strip() for s in raw_sentences if s.strip()]


def split_text_into_chunks(text: str, min_words: int = CHUNK_MIN_WORDS,
                            max_words: int = CHUNK_MAX_WORDS) -> list[str]:
    """
    [Requirement 4] Split a long text into chunks of min_words–max_words.

    Strategy:
    1. Split into sentences.
    2. Greedily pack sentences into a chunk until adding the next sentence
       would exceed max_words.
    3. When a chunk reaches at least min_words, close it and start a new one.
    4. If a single sentence exceeds max_words, split it on word boundaries.
    5. Never produce a chunk with zero words.
    """
    if not text:
        return []

    word_count = len(text.split())

    # Short enough → return as-is (no split needed)
    if word_count <= max_words:
        return [text]

    sentences = _split_sentences(text)
    chunks: list[str] = []
    current_sentences: list[str] = []
    current_words = 0

    def flush_current():
        nonlocal current_sentences, current_words
        if current_sentences:
            chunks.append(" ".join(current_sentences))
        current_sentences = []
        current_words = 0

    for sentence in sentences:
        sent_words = len(sentence.split())

        # If this single sentence is over max_words, hard-split it by words
        if sent_words > max_words:
            flush_current()
            words = sentence.split()
            for start in range(0, len(words), max_words):
                part = " ".join(words[start : start + max_words])
                if part:
                    chunks.append(part)
            continue

        # Adding this sentence would exceed max and we already have min → flush
        if current_words + sent_words > max_words and current_words >= min_words:
            flush_current()

        current_sentences.append(sentence)
        current_words += sent_words

    flush_current()

    # Merge any trailing chunk that's too short into the previous one
    if len(chunks) >= 2 and len(chunks[-1].split()) < min_words:
        last = chunks.pop()
        chunks[-1] = chunks[-1] + " " + last

    return [c for c in chunks if c.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# 2.  [5] Build one chunk dict (structure-aware, embedding-ready)
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
) -> dict:
    """
    [Requirement 5] Assemble a single embedding-ready chunk dict.

    context_prefix format:
      "Document Title > Parent Heading > This Heading"
    embed_text format:
      "<context_prefix>\n\n<content>"

    All structural metadata is preserved on every sub-chunk so that each
    chunk is independently retrievable without needing its siblings.
    """
    crumb_parts: list[str] = []
    if title:
        crumb_parts.append(title)
    if MAX_BREADCRUMB_DEPTH is not None:
        crumb_parts.extend(breadcrumb[-MAX_BREADCRUMB_DEPTH:])
    else:
        crumb_parts.extend(breadcrumb)

    breadcrumb_str = " > ".join(crumb_parts)
    context_prefix = f"{breadcrumb_str} > {heading}" if breadcrumb_str else heading

    # For split chunks, append position suffix so retrieval can reconstruct order
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
        "short":          word_count < SHORT_CHUNK_THRESHOLD,
        "has_children":   False,   # set by caller
        "embed_text":     embed_text,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Core: walk the section tree depth-first
# ─────────────────────────────────────────────────────────────────────────────

def walk_tree(
    nodes: list,
    breadcrumb: list[str] = None,
    title: str = "",
) -> list[dict]:
    """
    Recursively walk the section tree.

    For each node:
    1. Collect the node's DIRECT content only (not children's content).
    2. [4] If that content > CHUNK_MAX_WORDS, split into sub-chunks.
    3. [5] Each sub-chunk carries full heading/level/breadcrumb/context_prefix.
    4. Recurse into children with updated breadcrumb.
    """
    if breadcrumb is None:
        breadcrumb = []

    chunks: list[dict] = []

    for node in nodes:
        heading  = node.get("heading", "")
        level    = node.get("level", 1)
        page     = node.get("page")
        content  = node.get("content", [])
        children = node.get("children", [])

        # Direct text for this node (children excluded)
        direct_text = " ".join(content).strip()

        # [4] Split if too large
        text_chunks = split_text_into_chunks(direct_text)
        total = len(text_chunks) if text_chunks else 1

        if not text_chunks:
            # No content — emit a stub chunk (heading only, no text)
            chunk = _build_chunk(
                heading=heading, level=level, page=page,
                breadcrumb=breadcrumb, content="",
                title=title, chunk_index=0, total_chunks=1,
            )
            chunk["has_children"] = len(children) > 0
            chunks.append(chunk)
        else:
            for idx, text_part in enumerate(text_chunks):
                chunk = _build_chunk(
                    heading=heading, level=level, page=page,
                    breadcrumb=breadcrumb, content=text_part,
                    title=title, chunk_index=idx, total_chunks=total,
                )
                chunk["has_children"] = len(children) > 0
                chunks.append(chunk)

        # Recurse into children
        if children:
            child_breadcrumb = breadcrumb + [heading]
            chunks.extend(walk_tree(children, child_breadcrumb, title))

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Short-chunk merging (optional post-pass)
# ─────────────────────────────────────────────────────────────────────────────

def merge_short_chunks(chunks: list, min_words: int = SHORT_CHUNK_THRESHOLD) -> list:
    """
    Optional: absorb very short leaf chunks into their nearest ancestor.
    Only fires when --merge-short is passed.
    """
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
# 5.  [6] References chunking (kept separate)
# ─────────────────────────────────────────────────────────────────────────────

def chunk_references(ref_sections: list, title: str = "") -> list[dict]:
    """
    [Requirement 6] Chunk the References section separately.

    Reference chunks are NOT split by the 100-400 word rule — each reference
    entry is typically a single paragraph and should stay intact for BM25
    or citation lookups. We just walk the tree normally and mark every chunk
    with "is_reference": True so the downstream system can route them to a
    separate index.
    """
    ref_chunks = walk_tree(ref_sections, breadcrumb=[], title=title)
    for chunk in ref_chunks:
        chunk["is_reference"] = True
    return ref_chunks


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_chunker(
    doc: dict,
    min_words: int = 0,
    merge_short: bool = False,
) -> dict:
    """
    Returns {"chunks": [...], "reference_chunks": [...]}

    "chunks"           → main retrieval chunks (feed to FAISS / semantic search)
    "reference_chunks" → reference entries (feed to BM25 / citation lookup)
    """
    title       = doc.get("title", "")
    sections    = doc.get("sections", [])
    ref_sections = doc.get("references", [])

    # Main chunks
    chunks = walk_tree(sections, breadcrumb=[], title=title)
    if merge_short and min_words > 0:
        chunks = merge_short_chunks(chunks, min_words=min_words)

    # [6] Reference chunks (separate)
    reference_chunks = chunk_references(ref_sections, title=title)

    return {
        "chunks":           chunks,
        "reference_chunks": reference_chunks,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7.  CLI
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(result: dict, doc: dict) -> None:
    chunks = result["chunks"]
    ref_chunks = result["reference_chunks"]

    print(f"\n{'─'*72}")
    print(f"  Document   : {doc.get('title', '(no title)')[:68]}")
    print(f"  Chunks     : {len(chunks)} main  |  {len(ref_chunks)} reference")
    print(f"  Split      : {sum(1 for c in chunks if c['is_split'])} chunks were split")
    print(f"  Short      : {sum(1 for c in chunks if c['short'])} chunks below {SHORT_CHUNK_THRESHOLD}w")
    print(f"{'─'*72}\n")

    print("  MAIN CHUNKS")
    for i, c in enumerate(chunks):
        indent = "  " * (c["level"] - 1)
        flags = ""
        if c["is_split"]:  flags += f" [split {c['chunk_index']+1}/{c['total_chunks']}]"
        if c["short"]:     flags += " [short]"
        crumb = " > ".join(c["breadcrumb"]) if c["breadcrumb"] else "(top)"
        print(f"  [{i+1:03d}] {indent}L{c['level']}  {c['heading']}{flags}")
        print(f"         {indent}breadcrumb : {crumb}")
        print(f"         {indent}page: {c['page']}  words: {c['word_count']}")
        if c["content"]:
            print(f"         {indent}preview: {c['content'][:100].replace(chr(10),' ')}...")
        print()

    if ref_chunks:
        print(f"  REFERENCE CHUNKS ({len(ref_chunks)} entries)")
        for rc in ref_chunks[:3]:
            print(f"    - {rc['content'][:80]}...")
        if len(ref_chunks) > 3:
            print(f"    ... and {len(ref_chunks)-3} more")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Hierarchical chunker: document_structure.json → chunks.json"
    )
    parser.add_argument("--input",       "-i", default="document_structure.json")
    parser.add_argument("--output",      "-o", default="chunks.json")
    parser.add_argument("--min-words",   "-m", type=int, default=0,
                        help="Merge chunks shorter than N words (0 = disabled)")
    parser.add_argument("--merge-short", action="store_true",
                        help="Enable short-chunk merging (requires --min-words > 0)")
    parser.add_argument("--summary",     "-s", action="store_true",
                        help="Print human-readable chunk summary instead of saving JSON")
    args = parser.parse_args()

    doc    = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = run_chunker(doc, min_words=args.min_words, merge_short=args.merge_short)

    if args.summary:
        _print_summary(result, doc)
    else:
        # Always save into chunker_json/ folder (auto-created if missing)
        out_dir = Path("chunker_json")
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
