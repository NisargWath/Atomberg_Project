"""
cleaner.py  —  Structure-Aware Post-Processor for PDF Extraction
================================================================
Reads raw line-level JSON produced by a PyMuPDF extractor and outputs
a hierarchical document JSON ready for structure-aware chunking.

Pipeline:
  raw JSON  →  normalize  →  noise filter  →  body-font computation
            →  title extraction (page-1 top zone, stops at ABSTRACT)
            →  metadata extraction (page-1 zone between title and ABSTRACT)
            →  line classification (heading / paragraph)
            →  split-heading merge  →  paragraph merge
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

# Font size must be >= (body_median * ratio) to be a heading candidate via size.
HEADING_FONT_RATIO = 1.10

# Hard cap on word count for any heading.
MAX_HEADING_WORDS = 14

# Minimum words needed to keep a paragraph block (filters isolated stray tokens).
MIN_PARAGRAPH_WORDS = 2

# Section-boundary keywords used as stop-signals during title/metadata scanning.
_SECTION_STOPS = {
    "abstract", "introduction", "related work", "background",
    "preliminaries", "methods", "methodology", "experiments",
    "conclusion", "references",
}

# Words / labels that look like headings but are really table/metric labels.
# Used in detect_heading() to reject false positives.
_FAKE_HEADING_WORDS = {
    "model", "retriever", "dataset", "configuration",
    "accuracy", "rouge", "rouge-l", "rouge-1", "rouge-2",
    "bleu", "bleu-1", "bleu-4", "meteor", "bm25", "dpr",
    "sbert", "qasper", "quality", "narrativeqa",
    "url", "layer", "question", "content", "role",
    "comparison", "systems", "to", "and", "or", "the",
    "score", "scores", "value", "values", "type", "types",
    "input", "output", "size", "name", "method", "methods",
}

# Single-word ALL-CAPS lines are only headings if they are one of these.
_KNOWN_SINGLE_HEADINGS = {
    "abstract", "introduction", "background", "preliminaries",
    "methods", "methodology", "approach", "model",
    "experiments", "evaluation", "results", "discussion",
    "conclusion", "conclusions", "limitations",
    "acknowledgements", "acknowledgments", "references",
    "appendix", "supplementary", "checklist",
}

# Metric / acronym tokens that must never become headings.
_METRIC_ACRONYMS = {
    "bm25", "dpr", "bleu", "rouge", "meteor", "f1", "pk", "wd",
    "ndcg", "map", "mrr", "recall", "precision", "accuracy",
    "url", "uri", "api", "gpu", "cpu", "ram", "llm", "rag",
    "sbert", "bert", "gpt", "t5", "bart",
}


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Text normalization
# ─────────────────────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """Collapse unicode whitespace, non-breaking spaces, and control chars."""
    text = text.replace("\u00a0", " ").replace("\t", " ")
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Noise detection
# ─────────────────────────────────────────────────────────────────────────────

_NOISE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^arxiv:\s*\d{4}\.\d{4,5}",
        r"^published\s+as\s+a\s+conference",
        r"^(under\s+review|preprint)",
        r"^copyright\s+\d{4}",
        r"^\d{4}\s+(ieee|acm|springer|elsevier)",
        r"^proceedings\s+of\s+the",
        r"^(accepted|submitted|appeared)\s+at",
        r"^(iclr|neurips|icml|acl|emnlp|naacl|cvpr|iccv|eccv)\s+20\d{2}",
        r"^\d+\s*$",
        r"^page\s+\d+\s*(of\s+\d+)?$",
        r"^-\s*\d+\s*-$",
        r"^(https?://|www\.)\S+$",
        r"^\d{1,2}/\d{1,2}/\d{2,4}$",
    ]
]


def is_noise(text: str) -> bool:
    """Return True if this line should be discarded entirely."""
    t = text.strip()
    if not t:
        return True
    if len(t) <= 2 and not t.isalpha():
        return True
    # Pure numeric / percentage tokens  →  35.3  57.9%  .28  6.7
    if re.fullmatch(r"[\d\s.,%±~<>≤≥]+", t):
        return True
    # Lone superscript / footnote markers  →  ∗  †  1,2
    if re.fullmatch(r"[∗†‡§¶*†,\d\s]{1,6}", t):
        return True
    for pat in _NOISE_PATTERNS:
        if pat.search(t):
            return True
    return False


def is_email(text: str) -> bool:
    return bool(re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", text))


def is_author_line(text: str) -> bool:
    """Heuristic: comma-separated names — at least 2 commas, mostly title-case."""
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
        r"^Appendix\s+[A-Z]\s*:",
    ]
    return any(re.match(p, text, re.IGNORECASE) for p in patterns)


def _is_section_stop(text: str) -> bool:
    """Return True if this text signals the start of the body (ABSTRACT etc.)."""
    return text.lower().strip(".") in _SECTION_STOPS


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Heading detection helpers
# ─────────────────────────────────────────────────────────────────────────────

def _numbered_heading_level(text: str):
    """
    1 Introduction → 1   |   2.1 Querying → 2   |   3.2.1 Results → 3
    """
    m = re.match(r"^(\d+(?:\.\d+)*)\s{1,4}[A-Z\u00C0-\u024F]", text)
    if not m:
        return None
    return m.group(1).count(".") + 1


def _appendix_heading_level(text: str):
    """
    A ABLATION STUDY → 1  |  B.1 METHODOLOGY → 2  |  E.2 FINDINGS → 2
    """
    m = re.match(r"^([A-Z](?:\.\d+)*)\s{1,4}(\S.{0,80})$", text)
    if not m:
        return None
    prefix = m.group(1)
    rest   = m.group(2)
    if not rest[0].isupper():
        return None
    if rest.lower().strip(".") in _METRIC_ACRONYMS:
        return None
    return prefix.count(".") + 1


def _is_bare_section_prefix(text: str) -> bool:
    """
    Return True for lone numbering tokens like "B.1", "3.2", "A".
    These are the first half of a split heading and must be merged.
    """
    return bool(re.fullmatch(r"[A-Z](?:\.\d+)*|\d+(?:\.\d+)*", text.strip()))


def _all_caps_heading(text: str) -> bool:
    """
    ABSTRACT / INTRODUCTION / RELATED WORK — short ALL-CAPS lines.
    """
    if text != text.upper():
        return False
    words = text.split()
    if not (1 <= len(words) <= MAX_HEADING_WORDS):
        return False
    if text.endswith("."):
        return False
    if re.search(r"\d", text):      # digits in all-caps → risky (table label)
        return False
    if not any(c.isalpha() for c in text):
        return False
    # Single-word: must be in known list
    if len(words) == 1 and text.lower() not in _KNOWN_SINGLE_HEADINGS:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Main heading detector
# ─────────────────────────────────────────────────────────────────────────────

def detect_heading(item: dict, body_font_median: float):
    """
    Return (is_heading: bool, level: int | None).

    Decision order:
      Guard 0:  noise / caption → reject
      Guard 1:  fake-heading blacklist → reject
      Guard 2:  too many digits without a numbering pattern → reject
      Guard 3:  contains comma (not a numbered/appendix heading) → reject
      Guard 4:  single word not in known list → reject
      Rule  0:  bare prefix ("B.1" alone) → heading (for split-merge)
      Rule  1:  numbered section (1 / 2.1 / 3.2.1)
      Rule  2:  appendix letter (A / B.1 / E.2)
      Rule  3:  canonical section keyword (case-insensitive)
      Rule  4:  ALL-CAPS short line (with single-word guard)
      Rule  5:  font-size above body median (conservative)
    """
    text = normalize_text(item.get("text", ""))
    if not text or is_noise(text):
        return False, None

    if is_caption_like(text):
        return False, None

    text_lower = text.lower().strip(".")

    # ── Rule 0: bare section prefix → heading so merge_split_headings can join ──
    # Must come FIRST before the guards, so "B.1" and "3.2" aren't rejected.
    if _is_bare_section_prefix(text):
        dots = text.count(".")
        return True, dots + 1

    # ── Guard 1: fake-heading / table-label blacklist ──
    if text_lower in _FAKE_HEADING_WORDS:
        return False, None

    # ── Guard 2: numeric/table-like lines ──
    # Reject pure numeric/percentage content
    if re.fullmatch(r"[\d\s,.%±<>≤≥]+", text):
        return False, None
    # Reject lines with many digits that don't match a numbered heading pattern
    digit_count = sum(c.isdigit() for c in text)
    if (
        digit_count > 3
        and _numbered_heading_level(text) is None
        and _appendix_heading_level(text) is None
    ):
        return False, None

    # ── Guard 3: lines with commas are almost never headings ──
    # (unless they match a structured pattern like "2.1 Methods, Results")
    if "," in text:
        if _numbered_heading_level(text) is None and _appendix_heading_level(text) is None:
            return False, None

    # ── Guard 4: single-word lines not in the known section list ──
    words = text.split()
    if len(words) == 1 and text_lower not in _KNOWN_SINGLE_HEADINGS and text_lower not in _METRIC_ACRONYMS:
        # Still allow if it's truly a known section keyword (handled by Rule 3 below)
        # But disallow generic single-word capitalized labels
        if not text.isupper() or text_lower not in _KNOWN_SINGLE_HEADINGS:
            # Only allow if it matches numbered/appendix pattern
            if _numbered_heading_level(text) is None and _appendix_heading_level(text) is None:
                return False, None

    # ── Rule 1: numbered section heading ──
    lvl = _numbered_heading_level(text)
    if lvl is not None:
        return True, lvl

    # ── Rule 2: appendix letter heading ──
    lvl = _appendix_heading_level(text)
    if lvl is not None:
        return True, lvl

    # ── Rule 3: canonical section keyword (any case) ──
    if text_lower in _SECTION_STOPS or text_lower in _KNOWN_SINGLE_HEADINGS:
        return True, 1

    # ── Rule 4: ALL-CAPS short line ──
    if _all_caps_heading(text):
        return True, 1

    # ── Rule 5: font-size heuristic (conservative) ──
    font_size = item.get("font_size", 0)
    word_count = len(words)
    if (
        body_font_median > 0
        and font_size >= body_font_median * HEADING_FONT_RATIO
        and word_count <= MAX_HEADING_WORDS
        and not text.endswith(".")
        and not is_email(text)
        and not is_author_line(text)
        and not is_affiliation_line(text)
        # Extra guard: don't promote lines that look like sentence fragments
        and word_count >= 2                     # at least two words
        and text_lower not in _FAKE_HEADING_WORDS
    ):
        if font_size >= body_font_median * 1.4:
            return True, 1
        if font_size >= body_font_median * 1.2:
            return True, 1
        return True, 2

    return False, None


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Body font median
# ─────────────────────────────────────────────────────────────────────────────

def compute_body_font_median(raw: list) -> float:
    """
    Compute the dominant body font size, weighted by word count so that
    long body paragraphs dominate over short heading lines.
    """
    sizes = []
    for item in raw:
        text = normalize_text(item.get("text", ""))
        if not text or is_noise(text):
            continue
        fs = item.get("font_size", 0)
        if fs > 0:
            sizes.extend([fs] * max(len(text.split()), 1))
    return median(sizes) if sizes else 11.0


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Title extraction  (FIX: stop at ABSTRACT, use only top zone of page 1)
# ─────────────────────────────────────────────────────────────────────────────

def merge_title_lines(raw: list, body_font_median: float) -> str:
    """
    Extract and merge the document title from the top of page 1.

    Key fix: compute max_fs from the pre-abstract zone ONLY, not all of page 1.
    Previously, large fonts elsewhere on page 1 (section headings after ABSTRACT)
    raised the max_fs bar so the actual title lines didn't qualify.

    Steps:
    1. Walk page-1 lines in order; stop at author/affiliation/email or ABSTRACT.
    2. Compute max_fs from that zone only.
    3. Collect lines within TITLE_FS_TOL of that zone max → these are the title.
    4. Fallback: if no font info, take first 1-3 non-trivial lines.
    """
    page1 = [item for item in raw if item.get("page") == 1]
    if not page1:
        return ""

    # Step 1: pre-abstract zone
    pre_abstract = []
    for item in page1:
        t = normalize_text(item.get("text", ""))
        if not t or is_noise(t):
            continue
        if _is_section_stop(t):
            break
        if is_author_line(t) or is_affiliation_line(t) or is_email(t):
            break
        pre_abstract.append(item)

    if not pre_abstract:
        return ""

    # Step 2: max font size within zone (NOT from all of page 1)
    zone_max_fs = max((item.get("font_size", 0) for item in pre_abstract), default=0)

    # Step 3: collect title lines
    TITLE_FS_TOL = 1.5
    if zone_max_fs > 0:
        title_lines = [
            normalize_text(item["text"])
            for item in pre_abstract
            if abs(item.get("font_size", 0) - zone_max_fs) <= TITLE_FS_TOL
            and len(normalize_text(item["text"])) > 3
        ]
    else:
        # Fallback: no font size info, take first 1-3 lines
        title_lines = [
            normalize_text(item["text"])
            for item in pre_abstract[:3]
            if len(normalize_text(item["text"])) > 3
        ]

    return " ".join(title_lines).strip()


# ─────────────────────────────────────────────────────────────────────────────
# 7.  Metadata extraction  (FIX: scan only page-1 zone between title & ABSTRACT)
# ─────────────────────────────────────────────────────────────────────────────

def extract_metadata(raw: list, title: str) -> dict:
    """
    Extract authors, affiliation, emails, and source.

    Scans page 1 in order.  Stops at ABSTRACT / INTRODUCTION.
    Skips title lines by matching text content (not font size) — simpler and
    more reliable now that title extraction is correct.
    """
    metadata = {
        "authors": [],
        "affiliation": "",
        "emails": [],
        "source": "",
    }

    page1 = [item for item in raw if item.get("page") == 1]

    # Build a set of title constituent texts for fast skip lookup
    title_texts: set[str] = set()
    if title:
        # Each part of a joined title may be a separate line; split on common join
        for chunk in title.split("  "):   # double-space unlikely in title
            title_texts.add(chunk.strip())
        # Also add exact substrings that appear as line-length segments
        for item in page1:
            t = normalize_text(item.get("text", ""))
            if t and len(t) > 5 and t in title:
                title_texts.add(t)

    for item in page1:
        t = normalize_text(item.get("text", ""))
        if not t or is_noise(t):
            continue

        # Stop at section body
        if _is_section_stop(t):
            break

        # Skip title constituent lines
        if t in title_texts:
            continue

        if is_email(t):
            found = re.findall(r"[\w.+-]+@[\w.-]+\.\w{2,}", t)
            metadata["emails"].extend(found)

        elif is_author_line(t) and not metadata["authors"]:
            parts = [re.sub(r"[\d∗†‡§¶*,]+$", "", p).strip() for p in t.split(",")]
            metadata["authors"] = [p for p in parts if p]

        elif is_affiliation_line(t) and not metadata["affiliation"]:
            metadata["affiliation"] = t

        elif re.search(r"arxiv:", t, re.IGNORECASE) and not metadata["source"]:
            metadata["source"] = t

    return metadata


# ─────────────────────────────────────────────────────────────────────────────
# 8.  Split heading merge
# ─────────────────────────────────────────────────────────────────────────────

def merge_split_headings(items: list) -> list:
    """
    Merge two-line split headings:
      "B.1" + "METHODOLOGY"  →  "B.1 METHODOLOGY"
      "E.2" + "FINDINGS"     →  "E.2 FINDINGS"

    Only fires when the first heading is a bare prefix token.
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
                combined  = f"{current_text} {next_text}"
                lvl = (
                    _appendix_heading_level(combined)
                    or _numbered_heading_level(combined)
                    or item.get("level", 1)
                )
                merged.append({
                    **item,
                    "text": combined,
                    "level": lvl,
                })
                i += 2
                continue

        merged.append(item)
        i += 1

    return merged


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Paragraph merging  (unchanged — already correct)
# ─────────────────────────────────────────────────────────────────────────────

def merge_paragraphs(items: list) -> list:
    """
    Merge consecutive paragraph lines into single paragraph blocks.
    Preserves the page number of the first line in each block.
    """
    merged = []
    buffer_texts = []
    buffer_page  = None

    def flush():
        nonlocal buffer_texts, buffer_page
        if buffer_texts:
            full = normalize_text(" ".join(buffer_texts))
            if len(full.split()) >= MIN_PARAGRAPH_WORDS:
                merged.append({"type": "paragraph", "text": full, "page": buffer_page})
        buffer_texts.clear()
        buffer_page = None

    for item in items:
        if item["type"] == "heading":
            flush()
            merged.append(item)
        else:
            if buffer_page is None:
                buffer_page = item.get("page")
            buffer_texts.append(item["text"])

    flush()
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# 10.  Hierarchy tree  (unchanged — already correct)
# ─────────────────────────────────────────────────────────────────────────────

def build_hierarchy(items: list) -> list:
    """
    Stack-based nesting: each heading node becomes a child of the nearest
    ancestor with a strictly lower level number.
    """
    root = {"heading": "__ROOT__", "level": 0, "page": None, "content": [], "children": []}
    stack = [root]

    for item in items:
        if item["type"] == "heading":
            node = {
                "heading": item["text"],
                "level":   item.get("level", 1),
                "page":    item.get("page"),
                "content": [],
                "children": [],
            }
            while len(stack) > 1 and stack[-1]["level"] >= node["level"]:
                stack.pop()
            stack[-1]["children"].append(node)
            stack.append(node)
        else:
            stack[-1]["content"].append(item["text"])

    return root["children"]


# ─────────────────────────────────────────────────────────────────────────────
# 11.  Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_cleaner(raw: list) -> dict:
    """Full pipeline: raw extracted lines → structured document dict."""

    # A: body font median (used by detect_heading for relative size comparison)
    body_font_median = compute_body_font_median(raw)

    # B: title — top of page 1, stops at ABSTRACT
    title = merge_title_lines(raw, body_font_median)

    # C: metadata — page-1 zone between title and ABSTRACT
    metadata = extract_metadata(raw, title)

    # D: build set of title-constituent texts so we don't re-emit them as headings.
    # Match by text content only — simpler and immune to font-size edge cases.
    title_line_texts: set[str] = set()
    if title:
        for item in raw:
            if item.get("page") != 1:
                continue
            t = normalize_text(item.get("text", ""))
            if t and len(t) > 5 and t in title:
                title_line_texts.add(t)

    # E: classify every line
    classified = []
    for item in raw:
        text = normalize_text(item.get("text", ""))
        if not text or is_noise(text):
            continue
        if text in title_line_texts:
            continue   # already consumed as title

        is_head, level = detect_heading(item, body_font_median)
        if is_head:
            classified.append({
                "type":  "heading",
                "text":  text,
                "level": level if level else 1,
                "page":  item.get("page"),
            })
        else:
            classified.append({
                "type": "paragraph",
                "text": text,
                "page": item.get("page"),
            })

    # F: merge split headings ("B.1" + "METHODOLOGY" → "B.1 METHODOLOGY")
    classified = merge_split_headings(classified)

    # G: merge consecutive paragraph lines into blocks
    merged = merge_paragraphs(classified)

    # H: build nested section tree
    sections = build_hierarchy(merged)

    return {"title": title, "metadata": metadata, "sections": sections}


# ─────────────────────────────────────────────────────────────────────────────
# 12.  CLI
# ─────────────────────────────────────────────────────────────────────────────

def debug_page1(raw: list) -> None:
    """Print page-1 lines with font sizes — helps diagnose title/metadata issues."""
    print("\n=== DEBUG: page-1 lines (font_size | text) ===")
    page1 = [x for x in raw if x.get("page") == 1]
    for item in page1:
        t  = normalize_text(item.get("text", ""))
        fs = item.get("font_size", 0)
        if not t:
            continue
        tag = ""
        if _is_section_stop(t):       tag = "  [SECTION STOP]"
        elif is_author_line(t):       tag = "  [AUTHOR LINE]"
        elif is_affiliation_line(t):  tag = "  [AFFILIATION]"
        elif is_email(t):             tag = "  [EMAIL]"
        elif is_noise(t):             tag = "  [NOISE]"
        print(f"  {fs:6.2f}pt  {t[:80]}{tag}")
    body_med = compute_body_font_median(raw)
    print(f"\n  body_font_median = {body_med:.2f}pt")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Structure-aware cleaner: raw PDF JSON -> hierarchical document JSON"
    )
    parser.add_argument("--input",  "-i", default="pdf_structure.json")
    parser.add_argument("--output", "-o", default="document_structure.json")
    parser.add_argument(
        "--debug", "-d", action="store_true",
        help="Print page-1 font sizes to diagnose title/metadata issues",
    )
    args = parser.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))

    if args.debug:
        debug_page1(raw)

    result = run_cleaner(raw)

    Path(args.output).write_text(
        json.dumps(result, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    title_display = result["title"][:80] if result["title"] else "(empty — run with --debug to diagnose)"
    print(f"\u2713  Saved \u2192 {args.output}")
    print(f"   Title    : {title_display}")
    print(f"   Authors  : {len(result['metadata']['authors'])} found")
    print(f"   Sections : {len(result['sections'])} top-level")

if __name__ == "__main__":
    main()