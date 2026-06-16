"""
chunker.py  —  Structure-Aware Hierarchical Chunker
====================================================
Reads document_structure.json (from cleaner.py) and emits retrieval-ready
chunks with full structural metadata.

Changes in this version
────────────────────────
  [2] Empty chunks skipped: walk_tree never emits a chunk whose content
      is empty or whose word_count == 0, unless it has children (in which
      case it may be kept as a structural stub — see [3]).

  [3] Structural parent-only nodes skipped from retrieval output:
      A node that has children but zero direct content is a pure organiser
      (e.g. "2 Methods" with only "2.1 / 2.2 / 2.3" children and no intro
      paragraph of its own).  Such nodes are not emitted as standalone
      chunks because they carry no retrievable text.
      The heading is still used as a breadcrumb for its children.

  [4] Short-chunk threshold gate:
      Any chunk whose word_count < RETRIEVAL_MIN_WORDS after all splitting
      is either merged with the next sibling chunk (if within the same
      section) or dropped entirely from the retrieval output.
      The threshold is separate from CHUNK_MIN_WORDS (the split target)
      so it can be tuned independently.

  [5] Figure/table/chart heading filter (belt-and-suspenders):
      Even if a figure-label heading slips past cleaner.py's filter, the
      chunker now has a secondary gate: any heading that matches caption
      patterns is skipped at chunk-emit time.

  [6] Only retrieval-meaningful chunks emitted:
      A chunk is retrieval-meaningful iff:
        (a) its content has >= RETRIEVAL_MIN_WORDS words, OR
        (b) it has children (it matters as a navigation breadcrumb node),
            AND it has at least some content (even if below threshold)
      Metadata-only, empty-content, and heading-only stubs are excluded.

Output schema per chunk
────────────────────────
  heading        : section heading
  level          : depth (1/2/3...)
  page           : page of first word in this chunk
  breadcrumb     : ancestor heading path (outermost first)
  context_prefix : "Title > Parent > Heading" string
  content        : text (paragraph-boundary split, 100-400 words target)
  word_count     : content words
  chunk_index    : 0-based index among sub-chunks of same section
  total_chunks   : total sub-chunks produced from this section
  is_split       : True when content was split across multiple chunks
  split_reason   : "none" | "paragraph" | "sentence"
  embed_text     : context_prefix + "\\n\\n" + content  (pass to embedder)
  retrieval_skip : True on chunks excluded from retrieval (debug mode only)

Usage:
  python chunker.py --input document_structure.json --output chunks.json
  python chunker.py --input document_structure.json --summary
  python chunker.py --input document_structure.json --min-words 30 --merge-short
  python chunker.py --input document_structure.json --debug-skipped
"""

import json
import re
import argparse
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# 0.  Config
# ─────────────────────────────────────────────────────────────────────────────

CHUNK_MIN_WORDS      = 100    # target lower bound when splitting large sections
CHUNK_MAX_WORDS      = 400    # target upper bound when splitting large sections
SHORT_CHUNK_THRESHOLD = 30    # flag as "short" for reporting
RETRIEVAL_MIN_WORDS  = 20     # [4][6] minimum words to be retrieval-worthy
MAX_BREADCRUMB_DEPTH = None   # None = all levels


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Caption / figure-heading filter  [5]
# ─────────────────────────────────────────────────────────────────────────────

_CAPTION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^(Figure|Fig\.?)\s+\d+",
        r"^Table\s+\d+",
        r"^Algorithm\s+\d+",
        r"^Listing\s+\d+",
        r"^Chart\s+\d+",
        r"^Graph\s+\d+",
        r"^Equation\s+\d+",
        r"^Appendix\s+[A-Z]\s*:",
        r"^\(\d+(?:\.\d+)?\)$",
    ]
]


def _is_caption_heading(text: str) -> bool:
    """[5] Belt-and-suspenders: catch figure/table/chart headings at emit time."""
    return any(pat.match(text) for pat in _CAPTION_PATTERNS)


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Retrieval-worthiness gate  [2][3][4][6]
# ─────────────────────────────────────────────────────────────────────────────

def _is_retrieval_worthy(
    content: str,
    heading: str,
    has_children: bool,
    word_count: int,
) -> tuple[bool, str]:
    """
    [Requirements 2, 3, 4, 6] Decide whether a chunk should be emitted.

    Returns (emit: bool, reason: str).

    Rules (evaluated in order — first match wins):
      SKIP-1: heading is a caption/figure/chart label         → skip [5]
      SKIP-2: content is empty AND node has no children       → skip [2]
      SKIP-3: content is empty AND node has children only     → skip [3]
               (pure structural organiser — kept as breadcrumb,
                not as a retrieval chunk)
      SKIP-4: word_count < RETRIEVAL_MIN_WORDS                → skip [4]
               (too short to be useful as a standalone chunk)
      EMIT:   everything else                                 → emit [6]
    """
    if _is_caption_heading(heading):
        return False, "caption-heading"

    if word_count == 0:
        if has_children:
            return False, "empty-parent"     # [3] pure structural organiser
        return False, "empty-leaf"           # [2] completely empty

    if word_count < RETRIEVAL_MIN_WORDS:
        return False, "too-short"            # [4] below retrieval threshold

    return True, "ok"


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Sentence splitter  (fallback when a single paragraph is too large)
# ─────────────────────────────────────────────────────────────────────────────

def _split_sentences(text: str) -> list[str]:
    protected = re.sub(
        r"\b(e\.g\.|i\.e\.|et al\.|Fig\.|fig\.|Eq\.|eq\.|cf\.|vs\.|approx\.|"
        r"Dr\.|Mr\.|Ms\.|Prof\.|Sr\.|Jr\.|[A-Z]\.[A-Z]\.)",
        lambda m: m.group(0).replace(".", "\x00"),
        text,
    )
    parts = re.split(r"(?<=[.!?])\s+", protected)
    return [p.replace("\x00", ".").strip() for p in parts if p.strip()]


def _pack_sentences(sentences: list[str], min_w: int, max_w: int) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    buf_words = 0

    def flush():
        nonlocal buf, buf_words
        if buf:
            chunks.append(" ".join(buf))
        buf.clear()
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

    if len(chunks) >= 2 and len(chunks[-1].split()) < min_w:
        tail = chunks.pop()
        chunks[-1] = chunks[-1] + " " + tail

    return [c for c in chunks if c.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Semantic paragraph-boundary splitter
# ─────────────────────────────────────────────────────────────────────────────

def split_paragraphs_into_chunks(
    paragraphs: list[str],
    min_words: int = CHUNK_MIN_WORDS,
    max_words: int = CHUNK_MAX_WORDS,
) -> tuple[list[str], str]:
    """
    Split content at paragraph boundaries first; fall back to sentence
    boundaries only when a single paragraph exceeds max_words.

    Returns (list_of_chunk_strings, split_reason).
    split_reason: "none" | "paragraph" | "sentence"
    """
    if not paragraphs:
        return [], "none"

    total_words = sum(len(p.split()) for p in paragraphs)

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

        if pw > max_words:
            if buf_paras and buf_words >= min_words:
                chunks.append("\n\n".join(buf_paras))
                buf_paras, buf_words = [], 0
            sents = _split_sentences(para)
            sent_chunks = _pack_sentences(sents, min_words, max_words)
            if buf_paras and sent_chunks:
                first = sent_chunks.pop(0)
                if buf_words + len(first.split()) <= max_words:
                    buf_paras.append(first)
                    buf_words += len(first.split())
                else:
                    chunks.append("\n\n".join(buf_paras))
                    buf_paras, buf_words = [first], len(first.split())
            chunks.extend(sent_chunks)
            split_reason = "sentence"
            continue

        if buf_words + pw > max_words and buf_words >= min_words:
            chunks.append("\n\n".join(buf_paras))
            buf_paras, buf_words = [], 0

        buf_paras.append(para)
        buf_words += pw

    if buf_paras:
        remainder = "\n\n".join(buf_paras)
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
# 5.  Build one embedding-ready chunk dict
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
        "has_children":   False,   # set by caller
        "embed_text":     embed_text,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Core: walk the section tree depth-first  [2][3][4][5][6]
# ─────────────────────────────────────────────────────────────────────────────

def walk_tree(
    nodes: list,
    breadcrumb: list[str] = None,
    title: str = "",
    debug_skipped: bool = False,
) -> tuple[list[dict], list[dict]]:
    """
    Walk the hierarchy tree and emit retrieval-worthy chunks.

    Returns (emitted_chunks, skipped_chunks).
    skipped_chunks is populated only when debug_skipped=True.

    For each node:
      1. Collect direct content paragraphs (children excluded).
      2. Apply the retrieval-worthiness gate.
      3. If worthy: split at paragraph boundaries, emit one chunk per split.
      4. If not worthy: record in skipped (debug mode); still recurse so
         children are processed with this node's heading in their breadcrumb.
      5. Recurse into children with updated breadcrumb.
    """
    if breadcrumb is None:
        breadcrumb = []

    emitted: list[dict] = []
    skipped: list[dict] = []

    for node in nodes:
        heading   = node.get("heading", "")
        level     = node.get("level", 1)
        page      = node.get("page")
        content   = node.get("content", [])
        children  = node.get("children", [])
        has_ch    = bool(children)

        # Normalise content to list of paragraph strings
        if isinstance(content, str):
            paragraphs = [content] if content.strip() else []
        else:
            paragraphs = [p for p in content if p and p.strip()]

        direct_text = "\n\n".join(paragraphs)
        total_words = len(direct_text.split()) if direct_text else 0

        # ── Retrieval gate ──
        worthy, reason = _is_retrieval_worthy(
            content=direct_text,
            heading=heading,
            has_children=has_ch,
            word_count=total_words,
        )

        if worthy:
            # [paragraph-boundary split]
            text_chunks, split_reason = split_paragraphs_into_chunks(paragraphs)
            total = len(text_chunks) if text_chunks else 1

            if not text_chunks:
                # Content existed but entirely whitespace after join — skip
                pass
            else:
                for idx, text_part in enumerate(text_chunks):
                    chunk = _build_chunk(
                        heading=heading, level=level, page=page,
                        breadcrumb=breadcrumb, content=text_part,
                        title=title, chunk_index=idx, total_chunks=total,
                        split_reason=split_reason if total > 1 else "none",
                    )
                    chunk["has_children"] = has_ch
                    emitted.append(chunk)

        else:
            # Not retrieval-worthy — record for debug, but still recurse
            if debug_skipped:
                skipped.append({
                    "heading":      heading,
                    "level":        level,
                    "page":         page,
                    "skip_reason":  reason,
                    "word_count":   total_words,
                    "has_children": has_ch,
                })

        # Recurse into children regardless of whether this node was emitted
        if children:
            child_breadcrumb = breadcrumb + [heading]
            child_emitted, child_skipped = walk_tree(
                children, child_breadcrumb, title, debug_skipped
            )
            emitted.extend(child_emitted)
            skipped.extend(child_skipped)

    return emitted, skipped


# ─────────────────────────────────────────────────────────────────────────────
# 7.  [4] Short-chunk merge — sibling-aware  [updated]
# ─────────────────────────────────────────────────────────────────────────────

def merge_short_chunks(
    chunks: list[dict],
    min_words: int = RETRIEVAL_MIN_WORDS,
) -> list[dict]:
    """
    [Requirement 4] Post-pass: merge chunks below min_words into an adjacent
    sibling or parent chunk rather than dropping them outright.

    Merge strategy (in priority order):
      1. If the NEXT chunk has the same heading and level (it's a sibling
         sub-chunk from the same section), prepend this chunk's content to it.
      2. If the PREVIOUS chunk has a lower level (it's the parent), append
         this chunk's content to the parent.
      3. Otherwise drop (the chunk has no useful standalone value).
    """
    if not chunks:
        return chunks

    result: list[dict] = []

    i = 0
    while i < len(chunks):
        chunk = chunks[i]

        if chunk["word_count"] < min_words and not chunk["has_children"]:
            # Try merge-forward into next sibling
            if (
                i + 1 < len(chunks)
                and chunks[i + 1]["heading"] == chunk["heading"]
                and chunks[i + 1]["level"] == chunk["level"]
            ):
                nxt = chunks[i + 1]
                sep = "\n\n" if chunk["content"] and nxt["content"] else ""
                nxt["content"]    = chunk["content"] + sep + nxt["content"]
                nxt["embed_text"] = nxt["context_prefix"] + "\n\n" + nxt["content"]
                nxt["word_count"] = len(nxt["content"].split())
                nxt["short"]      = nxt["word_count"] < SHORT_CHUNK_THRESHOLD
                i += 1   # skip this chunk, next iteration processes the merged one
                continue

            # Try merge-backward into nearest lower-level ancestor
            parent_idx = None
            for j in range(len(result) - 1, -1, -1):
                if result[j]["level"] < chunk["level"]:
                    parent_idx = j
                    break

            if parent_idx is not None:
                parent = result[parent_idx]
                addition = (
                    f"\n\n[{chunk['heading']}]\n{chunk['content']}"
                    if chunk["content"] else ""
                )
                parent["content"]    += addition
                parent["embed_text"] += addition
                parent["word_count"] += chunk["word_count"]
                parent["short"]       = parent["word_count"] < SHORT_CHUNK_THRESHOLD
                i += 1
                continue

            # No merge target — drop silently
            i += 1
            continue

        result.append(chunk)
        i += 1

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 8.  References chunking (separate index, no retrieval gate)
# ─────────────────────────────────────────────────────────────────────────────

def chunk_references(ref_sections: list, title: str = "") -> list[dict]:
    """
    Reference entries are walked without the retrieval gate — each entry
    is typically short by design and should stay intact for BM25/citation
    lookup.  They are marked is_reference=True for routing to a separate index.
    """
    ref_chunks: list[dict] = []
    for section in ref_sections:
        content  = section.get("content", [])
        heading  = section.get("heading", "")
        page     = section.get("page")
        if isinstance(content, list):
            paragraphs = [p for p in content if p and p.strip()]
        else:
            paragraphs = [content] if content else []

        for para in paragraphs:
            wc = len(para.split())
            if wc == 0:
                continue
            chunk = _build_chunk(
                heading=heading, level=1, page=page,
                breadcrumb=[], content=para,
                title=title, chunk_index=0, total_chunks=1,
            )
            chunk["has_children"] = False
            chunk["is_reference"] = True
            ref_chunks.append(chunk)

    return ref_chunks


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_chunker(
    doc: dict,
    min_words: int = 0,
    merge_short: bool = False,
    debug_skipped: bool = False,
) -> dict:
    """
    Returns:
      {
        "chunks":           [...],   # retrieval-worthy main chunks
        "reference_chunks": [...],   # reference entries for separate index
        "skipped_chunks":   [...],   # debug: chunks that failed the gate
      }
    """
    title        = doc.get("title", "")
    sections     = doc.get("sections", [])
    ref_sections = doc.get("references", [])

    emitted, skipped = walk_tree(
        sections, breadcrumb=[], title=title, debug_skipped=debug_skipped
    )

    if merge_short and min_words > 0:
        emitted = merge_short_chunks(emitted, min_words=min_words)

    reference_chunks = chunk_references(ref_sections, title=title)

    return {
        "chunks":           emitted,
        "reference_chunks": reference_chunks,
        "skipped_chunks":   skipped,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 10.  CLI
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(result: dict, doc: dict) -> None:
    chunks     = result["chunks"]
    ref_chunks = result["reference_chunks"]
    skipped    = result.get("skipped_chunks", [])

    para_splits = sum(1 for c in chunks if c.get("split_reason") == "paragraph")
    sent_splits = sum(1 for c in chunks if c.get("split_reason") == "sentence")

    print(f"\n{'─'*72}")
    print(f"  Document  : {doc.get('title','(no title)')[:68]}")
    print(f"  Emitted   : {len(chunks)} main  |  {len(ref_chunks)} reference")
    print(f"  Skipped   : {len(skipped)}")
    print(f"  Splits    : {para_splits} paragraph  |  {sent_splits} sentence")
    print(f"  Short     : {sum(1 for c in chunks if c['short'])} below {SHORT_CHUNK_THRESHOLD}w")
    print(f"{'─'*72}\n")

    print("  EMITTED CHUNKS")
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

    if skipped:
        print(f"  SKIPPED CHUNKS ({len(skipped)})")
        reasons: dict[str, int] = {}
        for s in skipped:
            reasons[s["skip_reason"]] = reasons.get(s["skip_reason"], 0) + 1
        for reason, count in sorted(reasons.items()):
            print(f"    {reason:20s} : {count}")
        print()

    if ref_chunks:
        print(f"  REFERENCE CHUNKS ({len(ref_chunks)})")
        for rc in ref_chunks[:3]:
            print(f"    • {rc['content'][:80]}...")
        if len(ref_chunks) > 3:
            print(f"    … and {len(ref_chunks)-3} more")
        print()



def _write_chunks_txt(result: dict, path) -> None:
    """
    Write a human-readable plain-text version of all chunks.

    Format per chunk:
      ┌─────────────────────────────────────────────────────┐
      │ [001] L2  2.1 Clustering  (page 3)                  │
      │ Breadcrumb : Title > 2 Methods                      │
      │ Words      : 143  |  split: paragraph 1/2           │
      └─────────────────────────────────────────────────────┘
      <content text>

      (blank line between chunks)

    Reference chunks follow in a separate block.
    Skipped chunks are listed as a summary table at the end.
    """
    lines = []

    def chunk_block(i: int, c: dict, label: str = "") -> None:
        crumb   = " > ".join(c.get("breadcrumb", [])) or "(top level)"
        split_s = (
            f"  |  {c['split_reason']}-split {c['chunk_index']+1}/{c['total_chunks']}"
            if c.get("is_split") else ""
        )
        ref_tag = "  [REF]" if c.get("is_reference") else ""
        header  = f"[{i:03d}] L{c['level']}  {c['heading']}{ref_tag}  (page {c['page']})"
        lines.append("┌" + "─" * 70 + "┐")
        lines.append(f"│ {header:<68} │")
        lines.append(f"│ Breadcrumb : {crumb:<56} │")
        lines.append(f"│ Words      : {c['word_count']}{split_s:<54} │")
        lines.append("└" + "─" * 70 + "┘")
        if c.get("content"):
            # Wrap content at 72 chars for readability
            import textwrap
            for para in c["content"].split("\n\n"):
                wrapped = textwrap.fill(para.strip(), width=72)
                lines.append(wrapped)
                lines.append("")
        else:
            lines.append("(no content)")
            lines.append("")

    chunks     = result.get("chunks", [])
    ref_chunks = result.get("reference_chunks", [])
    skipped    = result.get("skipped_chunks", [])

    # ── Main chunks ──
    lines.append("=" * 72)
    lines.append(f"  RETRIEVAL CHUNKS  ({len(chunks)} total)")
    lines.append("=" * 72)
    lines.append("")
    for i, c in enumerate(chunks, 1):
        chunk_block(i, c)

    # ── Reference chunks ──
    if ref_chunks:
        lines.append("=" * 72)
        lines.append(f"  REFERENCE CHUNKS  ({len(ref_chunks)} total)")
        lines.append("=" * 72)
        lines.append("")
        for i, c in enumerate(ref_chunks, 1):
            chunk_block(i, c)

    # ── Skipped summary ──
    if skipped:
        lines.append("=" * 72)
        lines.append(f"  SKIPPED CHUNKS  ({len(skipped)} total — not in retrieval index)")
        lines.append("=" * 72)
        lines.append("")
        from collections import Counter
        reasons = Counter(s["skip_reason"] for s in skipped)
        for reason, count in sorted(reasons.items()):
            lines.append(f"  {reason:25s} : {count} chunk(s)")
        lines.append("")
        lines.append("  Detail:")
        for s in skipped:
            lines.append(f"    [L{s['level']}] {s['heading']:<40}  {s['skip_reason']}  ({s['word_count']}w)")

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Hierarchical chunker: document_structure.json → chunks.json"
    )
    parser.add_argument("--input",          "-i", default="document_structure.json")
    parser.add_argument("--output",         "-o", default="chunks.json")
    parser.add_argument("--min-words",      "-m", type=int, default=0)
    parser.add_argument("--merge-short",    action="store_true")
    parser.add_argument("--summary",        "-s", action="store_true")
    parser.add_argument("--debug-skipped",  action="store_true",
                        help="Include skipped chunks in output for inspection")
    args = parser.parse_args()

    doc    = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = run_chunker(
        doc,
        min_words      = args.min_words,
        merge_short    = args.merge_short,
        debug_skipped  = args.debug_skipped,
    )

    if args.summary:
        _print_summary(result, doc)
    else:
        chunks     = result["chunks"]
        ref_chunks = result["reference_chunks"]
        skipped    = result.get("skipped_chunks", [])
        split_ct   = sum(1 for c in chunks if c["is_split"])
        short_ct   = sum(1 for c in chunks if c["short"])
        all_pages  = [c["page"] for c in chunks if c["page"]]

        out_dir  = Path("chunker_json")
        out_dir.mkdir(parents=True, exist_ok=True)

        # JSON output
        out_path = out_dir / Path(args.output).name
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # TXT output (same name, .txt extension)
        txt_path = out_path.with_suffix(".txt")
        _write_chunks_txt(result, txt_path)

        print(f"✓  Saved JSON → {out_path}")
        print(f"✓  Saved TXT  → {txt_path}")
        print(f"   Emitted chunks  : {len(chunks)}  ({split_ct} split, {short_ct} short)")
        print(f"   Reference chunks: {len(ref_chunks)}")
        print(f"   Skipped chunks  : {len(skipped)}")
        if all_pages:
            print(f"   Page range      : {min(all_pages)} – {max(all_pages)}")
        print(f"   Levels          : {sorted(set(c['level'] for c in chunks))}")


if __name__ == "__main__":
    main()
