"""
cleaner.py  —  Structure-Aware Post-Processor for PDF Extraction
================================================================
Reads raw line-level JSON produced by a PyMuPDF extractor and outputs
a hierarchical document JSON ready for structure-aware chunking.

Pipeline:
  raw JSON  →  normalize  →  noise filter  →  title/metadata extraction
            →  split-heading merge  →  heading detection  →  paragraph merge
            →  hierarchy tree build  →  final JSON

Usage:
  python cleaner.py --input pdf_structure.json --output document_structure.json
"""

import json
import re
import argparse
from pathlib import Path
from statistics import median


# ─────────────────────────────────────────────────────────────────────────────
# 0.  Config / tuneable constants
# ─────────────────────────────────────────────────────────────────────────────

# Lines whose font size is >= (body_median * HEADING_FONT_RATIO) are treated
# as heading candidates when other heuristics are inconclusive.
HEADING_FONT_RATIO = 1.10

# Max words a line may have and still be considered a heading.
MAX_HEADING_WORDS = 14

# Minimum word count to even consider a line as a paragraph (avoids stray
# isolated tokens like "and", "or" that escaped noise filters).
MIN_PARAGRAPH_WORDS = 2


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Text normalization
# ─────────────────────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Collapse unicode whitespace, non-breaking spaces, and control chars."""
    text = text.replace("\u00a0", " ").replace("\t", " ")
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)   # strip control chars
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Noise detection
# ─────────────────────────────────────────────────────────────────────────────

# Compiled once at module load for speed.
_NOISE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^arxiv:\s*\d{4}\.\d{4,5}",          # arXiv IDs
        r"^published\s+as\s+a\s+conference",   # venue headers
        r"^(under\s+review|preprint)",         # preprint labels
        r"^copyright\s+\d{4}",                # copyright lines
        r"^\d{4}\s+(ieee|acm|springer|elsevier)",  # publisher banners
        r"^proceedings\s+of\s+the",            # proceedings lines
        r"^(accepted|submitted|appeared)\s+at",
        r"^(iclr|neurips|icml|acl|emnlp|naacl|cvpr|iccv|eccv)\s+20\d{2}",
        r"^\d+\s*$",                           # bare page numbers
        r"^page\s+\d+\s*(of\s+\d+)?$",        # "page 3 of 10"
        r"^-\s*\d+\s*-$",                     # "- 3 -"  style page nums
        r"^(https?://|www\.)\S+$",             # bare URLs
        r"^\d{1,2}/\d{1,2}/\d{2,4}$",        # dates like 01/23/2024
    ]
]

# These look like metric names / acronyms that are NOT headings.
_METRIC_ACRONYMS = {
    "bm25", "dpr", "bleu", "rouge", "meteor", "f1", "pk", "wd",
    "ndcg", "map", "mrr", "recall", "precision", "accuracy",
    "url", "uri", "api", "gpu", "cpu", "ram", "llm", "rag",
}


def is_noise(text: str) -> bool:
    """Return True if this line should be discarded entirely."""
    t = text.strip()
    if not t:
        return True

    # Single character or pure punctuation
    if len(t) <= 2 and not t.isalpha():
        return True

    # Pure numeric / percentage tokens  →  35.3  57.9%  .28  6.7
    if re.fullmatch(r"[\d\s.,%±~<>≤≥]+", t):
        return True

    # Lone superscript / footnote markers  →  ∗  †  1,2  ∗∗
    if re.fullmatch(r"[∗†‡§¶*†,\d\s]{1,6}", t):
        return True

    for pat in _NOISE_PATTERNS:
        if pat.search(t):
            return True

    return False


def is_email(text: str) -> bool:
    return bool(re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", text))


def is_author_line(text: str) -> bool:
    """Heuristic: comma-separated names, often with superscript digits."""
    # Must have at least two commas and look like names (title-case words)
    if text.count(",") < 2:
        return False
    words = text.replace(",", " ").split()
    cap_ratio = sum(1 for w in words if w and w[0].isupper()) / max(len(words), 1)
    return cap_ratio > 0.5 and len(words) >= 4


def is_affiliation_line(text: str) -> bool:
    keywords = [
        "university", "institute", "college", "laboratory", "lab ",
        "school of", "department of", "dept.", "faculty of",
        "research center", "research centre",
    ]
    t = text.lower()
    return any(k in t for k in keywords)


def is_caption_like(text: str) -> bool:
    """Figure / Table / Algorithm captions must NOT become headings."""
    patterns = [
        r"^(Figure|Fig\.?)\s+\d+",
        r"^Table\s+\d+",
        r"^Algorithm\s+\d+",
        r"^Listing\s+\d+",
        r"^Appendix\s+[A-Z]\s*:",      # "Appendix A: ..." inline ref
    ]
    return any(re.match(p, text, re.IGNORECASE) for p in patterns)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Heading detection
# ─────────────────────────────────────────────────────────────────────────────

def _numbered_heading_level(text: str):
    """
    Match patterns like:
      1 Introduction          → level 1
      2.1 Querying            → level 2
      3.2.1 Results           → level 3
    Returns (level: int) or None.
    """
    m = re.match(r"^(\d+(?:\.\d+)*)\s{1,4}[A-Z\u00C0-\u024F]", text)
    if not m:
        return None
    dots = m.group(1).count(".")
    return dots + 1


def _appendix_heading_level(text: str):
    """
    Match patterns like:
      A ABLATION STUDY          → level 1
      B.1 METHODOLOGY           → level 2
      E.2 FINDINGS              → level 2
      I.1 HOW DO DIFFERENT ...  → level 2

    Guard: the letter must be followed by a space or dot — not be a word.
    """
    # Capital letter + optional .digit  then whitespace then at least one word
    m = re.match(r"^([A-Z](?:\.\d+)*)\s{1,4}(\S.{0,80})$", text)
    if not m:
        return None

    prefix = m.group(1)        # e.g.  "B"  or  "B.1"
    rest   = m.group(2)        # everything after the prefix

    # The rest must look like a heading: starts with uppercase word, short
    if not rest[0].isupper():
        return None

    # Exclude lone metric acronyms used as headings
    rest_lower = rest.lower().strip(".")
    if rest_lower in _METRIC_ACRONYMS:
        return None

    dots = prefix.count(".")
    return dots + 1


def _all_caps_heading(text: str) -> bool:
    """
    ABSTRACT / INTRODUCTION / RELATED WORK — short ALL-CAPS lines.
    Guards against metric acronyms and numeric tokens.
    """
    if text != text.upper():
        return False

    words = text.split()
    if not (1 <= len(words) <= MAX_HEADING_WORDS):
        return False

    if text.endswith("."):        # sentences end with period; headings don't
        return False

    if re.search(r"\d", text):    # contains digits → risky; skip
        return False

    # Must have at least one alphabetic character
    if not any(c.isalpha() for c in text):
        return False

    # Single-word ALL-CAPS that's a known acronym/metric → not a heading
    if len(words) == 1 and text.lower() in _METRIC_ACRONYMS:
        return False

    return True


def detect_heading(item: dict, body_font_median: float):
    """
    Classify a single extracted line as (is_heading: bool, level: int | None).

    Decision tree (in priority order):
      1. Caption-like  → not a heading
      2. Numbered section (1 / 2.1 / 3.2.1)
      3. Appendix letter pattern (A / B.1)
      4. Known ALL-CAPS section keywords
      5. General ALL-CAPS short line
      6. Font-size significantly above body median (conservative threshold)
    """
    text = normalize_text(item.get("text", ""))
    if not text or is_noise(text):
        return False, None

    if is_caption_like(text):
        return False, None

    # --- Rule 0: bare section prefix  →  heading so merge_split_headings can join it ---
    if _is_bare_section_prefix(text):
        dots = text.count(".")
        return True, dots + 1

    # --- Rule 1: numbered section heading ---
    lvl = _numbered_heading_level(text)
    if lvl is not None:
        return True, lvl

    # --- Rule 2: appendix letter heading ---
    lvl = _appendix_heading_level(text)
    if lvl is not None:
        return True, lvl

    # --- Rule 3: canonical section keywords ---
    KNOWN_SECTIONS = {
        "abstract", "introduction", "related work", "background",
        "preliminaries", "methods", "methodology", "approach",
        "model", "experiments", "evaluation", "results", "discussion",
        "conclusion", "conclusions", "future work", "acknowledgements",
        "acknowledgments", "references", "appendix", "supplementary material",
        "supplementary", "limitations", "ethical considerations",
        "broader impact", "reproducibility", "checklist",
    }
    if text.lower().strip(".") in KNOWN_SECTIONS:
        return True, 1

    # --- Rule 4: general ALL-CAPS short line ---
    if _all_caps_heading(text):
        word_count = len(text.split())
        # Single-word all-caps is level 1; longer all-caps title-like → level 1
        return True, 1

    # --- Rule 5: font-size heuristic (conservative) ---
    font_size = item.get("font_size", 0)
    word_count = len(text.split())
    if (
        body_font_median > 0
        and font_size >= body_font_median * HEADING_FONT_RATIO
        and word_count <= MAX_HEADING_WORDS
        and not text.endswith(".")    # real sentences end with a period
        and not is_email(text)
        and not is_author_line(text)
        and not is_affiliation_line(text)
    ):
        # Rough level from font size: bigger = higher (lower number)
        if font_size >= body_font_median * 1.4:
            return True, 1
        if font_size >= body_font_median * 1.2:
            return True, 1
        return True, 2   # slightly larger — treat as sub-heading

    return False, None


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Body font median  (needed by detect_heading)
# ─────────────────────────────────────────────────────────────────────────────

def compute_body_font_median(raw: list) -> float:
    """
    Estimate the dominant body font size from the extracted items.
    We collect all font sizes, weight by approximate token count, and
    return the median — this is far more robust than a fixed threshold.
    """
    sizes = []
    for item in raw:
        text = normalize_text(item.get("text", ""))
        if not text or is_noise(text):
            continue
        fs = item.get("font_size", 0)
        if fs > 0:
            # Weight by word count so long body paragraphs dominate
            sizes.extend([fs] * max(len(text.split()), 1))

    return median(sizes) if sizes else 11.0


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Title merging
# ─────────────────────────────────────────────────────────────────────────────

def merge_title_lines(raw: list, body_font_median: float) -> str:
    """
    Detect and merge multi-line titles on the first page.

    Strategy:
      • Consider only page 1 items.
      • Find the maximum font size on page 1.
      • Collect consecutive lines whose font size is close to that max
        (within 1pt) and that look like title text (not author / email).
      • Join them into a single string.
    """
    page1 = [
        item for item in raw
        if item.get("page") == 1 and normalize_text(item.get("text", ""))
    ]

    if not page1:
        return ""

    max_fs = max(item.get("font_size", 0) for item in page1)
    if max_fs <= 0:
        # Fallback: first non-noise line on page 1
        for item in page1:
            t = normalize_text(item["text"])
            if not is_noise(t) and not is_author_line(t) and "@" not in t:
                return t
        return ""

    # Collect title candidate lines (font close to max, not author/email)
    TITLE_FS_TOLERANCE = 1.5   # points
    title_lines = []
    for item in page1:
        t = normalize_text(item.get("text", ""))
        fs = item.get("font_size", 0)
        if (
            abs(fs - max_fs) <= TITLE_FS_TOLERANCE
            and not is_noise(t)
            and not is_author_line(t)
            and not is_affiliation_line(t)
            and "@" not in t
            and len(t) > 3
        ):
            title_lines.append(t)

    return " ".join(title_lines).strip()


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Metadata extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_metadata(raw: list, title: str) -> dict:
    """
    Extract authors, affiliation, emails, and source from page-1 items.
    Skips lines already consumed as the title.
    """
    metadata = {
        "authors": [],
        "affiliation": "",
        "emails": [],
        "source": "",
    }

    # Deduplicate title words for rough matching
    title_lower = title.lower()

    page1 = [
        item for item in raw
        if item.get("page") == 1
    ]

    for item in page1:
        t = normalize_text(item.get("text", ""))
        if not t or is_noise(t):
            continue

        # Skip lines that are part of the title
        if t.lower() in title_lower or title_lower in t.lower():
            continue

        if is_email(t):
            # Email lines — could contain multiple emails
            found = re.findall(r"[\w.+-]+@[\w.-]+\.\w{2,}", t)
            metadata["emails"].extend(found)

        elif is_author_line(t) and not metadata["authors"]:
            # Split on comma; strip trailing superscript digits/symbols
            parts = [re.sub(r"[\d∗†‡§¶*,]+$", "", p).strip() for p in t.split(",")]
            metadata["authors"] = [p for p in parts if p]

        elif is_affiliation_line(t) and not metadata["affiliation"]:
            metadata["affiliation"] = t

        elif re.search(r"arxiv:", t, re.IGNORECASE) and not metadata["source"]:
            metadata["source"] = t

    return metadata


# ─────────────────────────────────────────────────────────────────────────────
# 7.  Split heading merging
# ─────────────────────────────────────────────────────────────────────────────

def _is_bare_section_prefix(text: str) -> bool:
    """
    Return True for lines that are ONLY a section/appendix numbering prefix:
      "B.1"  "3.2"  "A"  "II"
    These are likely the first half of a split heading.
    """
    return bool(re.fullmatch(r"[A-Z](?:\.\d+)*|\d+(?:\.\d+)*", text.strip()))
    """
    Fix two-line heading splits, for example:
      Line A:  "B.1"         (looks like an appendix prefix alone)
      Line B:  "METHODOLOGY" (looks like a standalone word heading)
    → merge into one heading item: "B.1 METHODOLOGY"

    Also handles titles broken mid-phrase across two large-font lines.
    """
    if not items:
        return items

    merged = []
    i = 0
    while i < len(items):
        item = items[i]

        if item.get("type") == "heading" and i + 1 < len(items):
            next_item = items[i + 1]

            # Pattern: current heading is ONLY a numbering prefix ("B.1", "3.2")
            current_text = normalize_text(item.get("text", ""))
            is_bare_prefix = _is_bare_section_prefix(current_text)

            if is_bare_prefix and next_item.get("type") == "heading":
                next_text = normalize_text(next_item.get("text", ""))
                merged.append({
                    **item,
                    "text": f"{current_text} {next_text}",
                })
                i += 2
                continue

        merged.append(item)
        i += 1

    return merged


# ─────────────────────────────────────────────────────────────────────────────
# 8.  Paragraph merging
# ─────────────────────────────────────────────────────────────────────────────

def merge_split_headings(items: list) -> list:
    """
    Fix two-line heading splits, for example:
      Line A:  "B.1"         (lone prefix heading)
      Line B:  "METHODOLOGY" (standalone word heading)
    → merge into: "B.1 METHODOLOGY"
    """
    if not items:
        return items

    merged = []
    i = 0
    while i < len(items):
        item = items[i]

        if item.get("type") == "heading" and i + 1 < len(items):
            current_text = normalize_text(item.get("text", ""))
            next_item = items[i + 1]

            if _is_bare_section_prefix(current_text) and next_item.get("type") == "heading":
                next_text = normalize_text(next_item.get("text", ""))
                merged.append({
                    **item,
                    "text": f"{current_text} {next_text}",
                    # Re-derive level from the merged text
                    "level": _appendix_heading_level(f"{current_text} {next_text}")
                             or _numbered_heading_level(f"{current_text} {next_text}")
                             or item.get("level", 1),
                })
                i += 2
                continue

        merged.append(item)
        i += 1

    return merged


def merge_paragraphs(items: list) -> list:
    """
    Merge consecutive paragraph lines (between headings) into single blocks.
    Preserves the page number of the first line in each block.
    Drops paragraph fragments shorter than MIN_PARAGRAPH_WORDS.
    """
    merged = []
    buffer_texts = []
    buffer_page = None

    def flush_buffer():
        nonlocal buffer_texts, buffer_page
        if buffer_texts:
            full_text = normalize_text(" ".join(buffer_texts))
            if len(full_text.split()) >= MIN_PARAGRAPH_WORDS:
                merged.append({
                    "type": "paragraph",
                    "text": full_text,
                    "page": buffer_page,
                })
        buffer_texts = []
        buffer_page = None

    for item in items:
        if item["type"] == "heading":
            flush_buffer()
            merged.append(item)
        else:
            if buffer_page is None:
                buffer_page = item.get("page")
            buffer_texts.append(item["text"])

    flush_buffer()
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Hierarchy tree construction
# ─────────────────────────────────────────────────────────────────────────────

def build_hierarchy(items: list) -> list:
    """
    Convert a flat list of heading + paragraph items into a nested tree.

    Returns the top-level section list (children of an implicit root node).

    Algorithm: stack-based.  For each heading, pop the stack until the top
    has a strictly lower level number, then append the new node as a child.
    Paragraphs go into the content list of the current stack-top.
    """
    root = {
        "heading": "__ROOT__",
        "level": 0,
        "page": None,
        "content": [],
        "children": [],
    }
    stack = [root]

    for item in items:
        if item["type"] == "heading":
            node = {
                "heading": item["text"],
                "level": item.get("level", 1),
                "page": item.get("page"),
                "content": [],
                "children": [],
            }
            # Pop until we find a node whose level is strictly less than ours
            while len(stack) > 1 and stack[-1]["level"] >= node["level"]:
                stack.pop()

            stack[-1]["children"].append(node)
            stack.append(node)

        else:
            # Paragraph → add to current section's content
            stack[-1]["content"].append(item["text"])

    return root["children"]


# ─────────────────────────────────────────────────────────────────────────────
# 10.  Main pipeline
# ────────────────────────────────────────

─────────────────────────────────────────────────────────────────────────────

def run_cleaner(raw: list) -> dict:
    """
    Full pipeline: raw extracted lines → structured document dict.
    """

    # ── Step A: Compute body font median for relative heading detection ──
    body_font_median = compute_body_font_median(raw)

    # ── Step B: Title extraction ──
    title = merge_title_lines(raw, body_font_median)

    # ── Step C: Metadata extraction ──
    metadata = extract_metadata(raw, title)

    # ── Step D: Classify each line (noise / heading / paragraph) ──
    # Build set of exact title constituent texts to skip in the section loop.
    # Only skip page-1 lines whose font size equals the max title font size
    # (so ABSTRACT at a smaller size is NOT accidentally skipped).
    title_line_texts: set[str] = set()
    if title:
        page1_items = [x for x in raw if x.get("page") == 1]
        max_fs = max((x.get("font_size", 0) for x in page1_items), default=0)
        TITLE_FS_TOL = 1.5
        for item in raw:
            t = normalize_text(item.get("text", ""))
            fs = item.get("font_size", 0)
            if (
                t and item.get("page") == 1
                and abs(fs - max_fs) <= TITLE_FS_TOL
                and t in title
                and len(t) > 5
            ):
                title_line_texts.add(t)

    classified = []
    for item in raw:
        text = normalize_text(item.get("text", ""))

        if not text or is_noise(text):
            continue

        # Skip lines that were merged into the document title
        if text in title_line_texts:
            continue

        is_head, level = detect_heading(item, body_font_median)

        if is_head:
            classified.append({
                "type": "heading",
                "text": text,
                "level": level if level else 1,
                "page": item.get("page"),
            })
        else:
            classified.append({
                "type": "paragraph",
                "text": text,
                "page": item.get("page"),
            })

    # ── Step E: Merge split headings (e.g. "B.1" + "METHODOLOGY") ──
    classified = merge_split_headings(classified)

    # ── Step F: Merge consecutive paragraph lines into blocks ──
    merged = merge_paragraphs(classified)

    # ── Step G: Build nested section tree ──
    sections = build_hierarchy(merged)

    return {
        "title": title,
        "metadata": metadata,
        "sections": sections,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 11.  CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Structure-aware cleaner: raw PDF JSON → hierarchical document JSON"
    )
    parser.add_argument(
        "--input", "-i",
        default="pdf_structure.json",
        help="Path to raw extracted JSON (default: pdf_structure.json)",
    )
    parser.add_argument(
        "--output", "-o",
        default="document_structure.json",
        help="Path for cleaned output JSON (default: document_structure.json)",
    )
    args = parser.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = run_cleaner(raw)

    Path(args.output).write_text(
        json.dumps(result, indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"✓  Saved structured document → {args.output}")
    print(f"   Title    : {result['title'][:80]}")
    print(f"   Authors  : {len(result['metadata']['authors'])} found")
    print(f"   Sections : {len(result['sections'])} top-level")


if __name__ == "__main__":
    main()