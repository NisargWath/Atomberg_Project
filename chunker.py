"""
chunker.py  —  Structure-Aware Hierarchical Chunker
====================================================
Reads the hierarchical document JSON produced by cleaner.py and emits
one chunk per section node (at every depth level), with full breadcrumb
context attached to each chunk.

What "one layer deeper" means
──────────────────────────────
Before (flat):
  Chunk 1 → heading: "2 Methods",  text: all text under Methods including subsections
  
After (hierarchical):
  Chunk 1 → heading: "2 Methods",         breadcrumb: [],           text: intro text only
  Chunk 2 → heading: "2.1 Clustering",    breadcrumb: ["2 Methods"], text: clustering text
  Chunk 3 → heading: "2.2 Tree Building", breadcrumb: ["2 Methods"], text: tree text
  Chunk 4 → heading: "B.1 Methodology",   breadcrumb: ["B Appendix"], text: ...

Each chunk knows:
  - its own heading
  - its level (1 = top, 2 = sub, 3 = sub-sub)
  - its breadcrumb (ancestor heading path, outermost first)
  - its direct content (paragraph text at this node only)
  - its page
  - a pre-built "context_prefix" string ready for embedding

Usage:
  python chunker.py --input document_structure.json --output chunks.json
  python chunker.py --input document_structure.json --output chunks.json --min-words 20
"""

import json
import argparse
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

# Chunks whose content is shorter than this (in words) will still be kept
# but flagged with "short": true — useful for the next stage to decide
# whether to merge them with a sibling.
SHORT_CHUNK_THRESHOLD = 30

# When building context_prefix, how many breadcrumb levels to include.
# None = all levels.
MAX_BREADCRUMB_DEPTH = None


# ─────────────────────────────────────────────────────────────────────────────
# Core: walk the section tree depth-first
# ─────────────────────────────────────────────────────────────────────────────

def walk_tree(
    nodes: list,
    breadcrumb: list[str] = None,
    title: str = "",
) -> list[dict]:
    """
    Recursively walk the section tree and emit one chunk per node.

    Parameters
    ----------
    nodes       : list of section dicts (each has heading/level/page/content/children)
    breadcrumb  : ancestor heading path (outermost first), built during recursion
    title       : document title, prepended to top-level breadcrumbs

    Returns
    -------
    List of chunk dicts, depth-first order (parent before children).
    """
    if breadcrumb is None:
        breadcrumb = []

    chunks = []

    for node in nodes:
        heading   = node.get("heading", "")
        level     = node.get("level", 1)
        page      = node.get("page")
        content   = node.get("content", [])   # list of paragraph strings
        children  = node.get("children", [])

        # ── Build the full text for this node's direct content only ──
        # Children's text is NOT included here — each child gets its own chunk.
        direct_text = " ".join(content).strip()
        word_count  = len(direct_text.split()) if direct_text else 0

        # ── Build context prefix for embedding ──
        # Format:  "Document Title > Parent Heading > This Heading\n\n<text>"
        # This lets the embedding model understand where in the document we are.
        crumb_parts = []
        if title:
            crumb_parts.append(title)
        crumb_parts.extend(breadcrumb)

        if MAX_BREADCRUMB_DEPTH is not None:
            crumb_parts = crumb_parts[-MAX_BREADCRUMB_DEPTH:]

        breadcrumb_str = " > ".join(crumb_parts) if crumb_parts else ""
        context_prefix = f"{breadcrumb_str} > {heading}" if breadcrumb_str else heading

        # Full embedding-ready text = context prefix + newline + content
        embed_text = f"{context_prefix}\n\n{direct_text}" if direct_text else context_prefix

        chunk = {
            "heading":        heading,
            "level":          level,
            "page":           page,
            "breadcrumb":     list(breadcrumb),   # copy — don't mutate
            "context_prefix": context_prefix,
            "content":        direct_text,
            "word_count":     word_count,
            "short":          word_count < SHORT_CHUNK_THRESHOLD,
            "has_children":   len(children) > 0,
            "embed_text":     embed_text,
        }

        chunks.append(chunk)

        # ── Recurse into children with updated breadcrumb ──
        if children:
            child_breadcrumb = breadcrumb + [heading]
            child_chunks = walk_tree(children, child_breadcrumb, title)
            chunks.extend(child_chunks)

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Optional: merge short sibling chunks into their parent
# ─────────────────────────────────────────────────────────────────────────────

def merge_short_chunks(chunks: list, min_words: int = SHORT_CHUNK_THRESHOLD) -> list:
    """
    Post-processing pass: if a chunk is very short AND has no children,
    merge its content upward into the nearest preceding chunk at a lower level
    (its parent in the flat list).

    This is optional — the RAG stage can also handle short chunks by
    retrieving adjacent context. Only enable if your embedding model
    struggles with very short inputs.
    """
    if not chunks:
        return chunks

    result = []
    for chunk in chunks:
        if (
            chunk["word_count"] < min_words
            and not chunk["has_children"]
            and result
        ):
            # Find nearest ancestor in result (lower level number)
            parent_idx = None
            for i in range(len(result) - 1, -1, -1):
                if result[i]["level"] < chunk["level"]:
                    parent_idx = i
                    break

            if parent_idx is not None:
                parent = result[parent_idx]
                # Append this chunk's content to parent, labelled by heading
                addition = f"\n\n[{chunk['heading']}]\n{chunk['content']}" if chunk["content"] else ""
                parent["content"]    += addition
                parent["embed_text"] += addition
                parent["word_count"] += chunk["word_count"]
                parent["short"]       = parent["word_count"] < min_words
                # Don't append the short chunk itself
                continue

        result.append(chunk)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_chunker(doc: dict, min_words: int = 0, merge_short: bool = False) -> list:
    """
    doc       : parsed document_structure.json dict
    min_words : if >0 and merge_short=True, merge chunks shorter than this
    """
    title    = doc.get("title", "")
    sections = doc.get("sections", [])

    chunks = walk_tree(sections, breadcrumb=[], title=title)

    if merge_short and min_words > 0:
        chunks = merge_short_chunks(chunks, min_words=min_words)

    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Hierarchical chunker: document_structure.json → chunks.json"
    )
    parser.add_argument("--input",       "-i", default="document_structure.json")
    parser.add_argument("--output",      "-o", default="chunks.json")
    parser.add_argument(
        "--min-words", "-m", type=int, default=0,
        help="Merge chunks shorter than N words into their parent (0 = disabled)",
    )
    parser.add_argument(
        "--merge-short", action="store_true",
        help="Enable short-chunk merging (requires --min-words > 0)",
    )
    parser.add_argument(
        "--summary", "-s", action="store_true",
        help="Print a human-readable summary of all chunks instead of saving JSON",
    )
    args = parser.parse_args()

    doc    = json.loads(Path(args.input).read_text(encoding="utf-8"))
    chunks = run_chunker(
        doc,
        min_words   = args.min_words,
        merge_short = args.merge_short,
    )

    if args.summary:
        print(f"\n{'─'*72}")
        print(f"  Document : {doc.get('title', '(no title)')[:70]}")
        print(f"  Chunks   : {len(chunks)}")
        print(f"{'─'*72}\n")
        for i, c in enumerate(chunks):
            indent = "  " * (c["level"] - 1)
            short_flag = " [SHORT]" if c["short"] else ""
            child_flag = " [+children]" if c["has_children"] else ""
            crumb = " > ".join(c["breadcrumb"]) if c["breadcrumb"] else "(top)"
            print(f"  [{i+1:03d}] {indent}L{c['level']}  {c['heading']}")
            print(f"         {indent}breadcrumb : {crumb}")
            print(f"         {indent}page       : {c['page']}  |  words: {c['word_count']}{short_flag}{child_flag}")
            if c["content"]:
                preview = c["content"][:120].replace("\n", " ")
                print(f"         {indent}content    : {preview}...")
            print()
    else:
        Path(args.output).write_text(
            json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        short_count = sum(1 for c in chunks if c["short"])
        print(f"✓  Saved {len(chunks)} chunks → {args.output}")
        print(f"   Levels   : {sorted(set(c['level'] for c in chunks))}")
        print(f"   Short    : {short_count} chunks below {SHORT_CHUNK_THRESHOLD} words")
        print(f"   Pages    : {min(c['page'] for c in chunks if c['page'])} – "
              f"{max(c['page'] for c in chunks if c['page'])}")


if __name__ == "__main__":
    main()