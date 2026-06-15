"""
cleaner.py  —  Structure-Aware Post-Processor for PDF Extraction
================================================================
Pipeline:
  raw JSON  →  normalize  →  repeated header/footer removal (NEW)
            →  noise filter  →  false-heading filter (IMPROVED)
            →  body-font computation
            →  title extraction  →  metadata extraction
            →  line classification  →  split-heading merge
            →  paragraph merge  →  hierarchy tree build
            →  references separated (NEW)  →  final JSON

Changes vs previous version
────────────────────────────
  [1] False-heading filter: table values, unit lines, numeric-only lines,
      repeated column headers now rejected before heading detection.
  [2] Subsection detection: isolated title-like lines and numbered subsection
      patterns separated from body text more reliably.
  [3] Repeated page header/footer removal: lines appearing on 3+ pages at
      the same vertical position (or identical text) stripped before hierarchy.
  [6] References section separated into its own top-level key, not mixed
      into the retrieval sections list.

Usage:
  python cleaner.py --input pdf_structure.json --output document_structure.json
  python cleaner.py --input pdf_structure.json --output document_structure.json --debug
"""

import json
import re
import argparse
from collections import Counter
from pathlib import Path
from statistics import median


# ─────────────────────────────────────────────────────────────────────────────
# 0.  Config
# ─────────────────────────────────────────────────────────────────────────────

HEADING_FONT_RATIO   = 1.10   # min font-size ratio over body median → heading
MAX_HEADING_WORDS    = 14     # hard cap on heading word count
MIN_PARAGRAPH_WORDS  = 2      # min words to keep a paragraph block

# A line is a repeated header/footer if it appears on >= this many pages.
REPEATED_LINE_PAGE_THRESHOLD = 3

_SECTION_STOPS = {
    "abstract", "introduction", "related work", "background",
    "preliminaries", "methods", "methodology", "experiments",
    "conclusion", "references",
}

# ── [1] Extended false-heading blacklist ─────────────────────────────────────
# Covers table column labels, metric names, unit labels, common paragraph
# openers that get promoted by font-size heuristic on some PDFs.
_FAKE_HEADING_WORDS = {
    # Table / result labels
    "model", "retriever", "dataset", "configuration", "baseline",
    "system", "systems", "comparison", "setup", "setting", "settings",
    "task", "tasks", "category", "categories", "type", "types",
    "input", "output", "size", "name", "role", "layer", "question",
    "content", "value", "values", "score", "scores",
    # Metric names
    "accuracy", "rouge", "rouge-l", "rouge-1", "rouge-2",
    "bleu", "bleu-1", "bleu-4", "meteor", "f1", "ndcg", "map", "mrr",
    "recall", "precision", "perplexity",
    # Model/dataset names used as column headers
    "bm25", "dpr", "sbert", "qasper", "narrativeqa", "quality",
    "bert", "gpt", "t5", "bart", "llama", "mistral",
    # Stopwords that slip through
    "to", "and", "or", "the", "of", "in", "on", "at", "by", "for",
    "with", "from", "that", "this", "it", "is", "are", "was",
}

# Unit patterns — lines like "ms", "GB", "tokens/s" must not be headings
_UNIT_PATTERN = re.compile(
    r"^[\d.,\s]*(ms|s|sec|min|hr|gb|mb|kb|tokens?|words?|chars?|"
    r"bytes?|fps|hz|khz|mhz|ghz|mm|cm|m|km|°c|°f|%|k|m|b|t)\s*$",
    re.IGNORECASE,
)

_KNOWN_SINGLE_HEADINGS = {
    "abstract", "introduction", "background", "preliminaries",
    "methods", "methodology", "approach", "model",
    "experiments", "evaluation", "results", "discussion",
    "conclusion", "conclusions", "limitations",
    "acknowledgements", "acknowledgments", "references",
    "appendix", "supplementary", "checklist",
}

_METRIC_ACRONYMS = {
    "bm25", "dpr", "bleu", "rouge", "meteor", "f1", "pk", "wd",
    "ndcg", "map", "mrr", "recall", "precision", "accuracy",
    "url", "uri", "api", "gpu", "cpu", "ram", "llm", "rag",
    "sbert", "bert", "gpt", "t5", "bart",
}

# Heading keywords that signal the References section (any case)
_REFERENCE_HEADINGS = {
    "references", "bibliography", "works cited", "citations",
}


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Text normalization
# ─────────────────────────────────────────────────────────────────────────────

def normalize_text(text: str) -> str:
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
    t = text.strip()
    if not t:
        return True
    if len(t) <= 2 and not t.isalpha():
        return True
    if re.fullmatch(r"[\d\s.,%±~<>≤≥]+", t):
        return True
    if re.fullmatch(r"[∗†‡§¶*†,\d\s]{1,6}", t):
        return True
    # [1] Unit-based lines  →  "42 ms", "3.2 GB"
    if _UNIT_PATTERN.match(t):
        return True
    for pat in _NOISE_PATTERNS:
        if pat.search(t):
            return True
    return False


def is_email(text: str) -> bool:
    return bool(re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", text))


def is_author_line(text: str) -> bool:
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
    return any(k in text.lower() for k in keywords)


def is_caption_like(text: str) -> bool:
    patterns = [
        r"^(Figure|Fig\.?)\s+\d+",
        r"^Table\s+\d+",
        r"^Algorithm\s+\d+",
        r"^Listing\s+\d+",
        r"^Appendix\s+[A-Z]\s*:",
    ]
    return any(re.match(p, text, re.IGNORECASE) for p in patterns)


def _is_section_stop(text: str) -> bool:
    return text.lower().strip(".") in _SECTION_STOPS


def _is_reference_heading(text: str) -> bool:
    return text.lower().strip().rstrip(".") in _REFERENCE_HEADINGS


# ─────────────────────────────────────────────────────────────────────────────
# 3.  [NEW] Repeated page header / footer removal
# ─────────────────────────────────────────────────────────────────────────────

def remove_repeated_headers_footers(raw: list) -> list:
    """
    [Requirement 3] Remove lines that appear verbatim on 3 or more distinct
    pages — these are running headers or footers (journal name, paper title
    repeated in header, page footer text, etc.).

    Strategy:
    - Count how many distinct pages each normalized text appears on.
    - Any text appearing on >= REPEATED_LINE_PAGE_THRESHOLD pages is a
      header/footer candidate.
    - Extra guard: only suppress if the line is SHORT (≤ 10 words) to avoid
      accidentally removing repeated legitimate sentences in a survey paper.
    """
    # Map: normalized_text → set of page numbers it appears on
    text_pages: dict[str, set] = {}
    for item in raw:
        t = normalize_text(item.get("text", ""))
        p = item.get("page")
        if t and p is not None:
            text_pages.setdefault(t, set()).add(p)

    # Build suppression set: short lines on 3+ pages
    suppressed: set[str] = {
        t for t, pages in text_pages.items()
        if len(pages) >= REPEATED_LINE_PAGE_THRESHOLD
        and len(t.split()) <= 10
    }

    if suppressed:
        # Keep first occurrence only (it may be the actual section heading)
        seen: set[str] = set()
        filtered = []
        for item in raw:
            t = normalize_text(item.get("text", ""))
            if t in suppressed:
                if t not in seen:
                    seen.add(t)
                    filtered.append(item)  # keep first occurrence
                # subsequent occurrences → silently dropped
            else:
                filtered.append(item)
        return filtered

    return raw


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Heading detection helpers
# ─────────────────────────────────────────────────────────────────────────────

def _numbered_heading_level(text: str):
    """1 Intro → 1  |  2.1 Methods → 2  |  3.2.1 Results → 3"""
    m = re.match(r"^(\d+(?:\.\d+)*)\s{1,4}[A-Z\u00C0-\u024F]", text)
    if not m:
        return None
    return m.group(1).count(".") + 1


def _appendix_heading_level(text: str):
    """A Ablation → 1  |  B.1 Methodology → 2  |  E.2 Findings → 2"""
    m = re.match(r"^([A-Z](?:\.\d+)*)\s{1,4}(\S.{0,80})$", text)
    if not m:
        return None
    prefix, rest = m.group(1), m.group(2)
    if not rest[0].isupper():
        return None
    if rest.lower().strip(".") in _METRIC_ACRONYMS:
        return None
    return prefix.count(".") + 1


def _is_bare_section_prefix(text: str) -> bool:
    """Return True for lone tokens like "B.1", "3.2" — first half of split heading."""
    return bool(re.fullmatch(r"[A-Z](?:\.\d+)*|\d+(?:\.\d+)*", text.strip()))


def _all_caps_heading(text: str) -> bool:
    if text != text.upper():
        return False
    words = text.split()
    if not (1 <= len(words) <= MAX_HEADING_WORDS):
        return False
    if text.endswith("."):
        return False
    if re.search(r"\d", text):
        return False
    if not any(c.isalpha() for c in text):
        return False
    if len(words) == 1 and text.lower() not in _KNOWN_SINGLE_HEADINGS:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Main heading detector  (requirements 1 + 2)
# ─────────────────────────────────────────────────────────────────────────────

def detect_heading(item: dict, body_font_median: float):
    """
    Return (is_heading: bool, level: int | None).

    Guard order (all guards before rules to fail fast):
      G0: noise / caption          → reject
      G1: fake-heading blacklist   → reject
      G2: [1] unit-based line      → reject
      G3: [1] numeric/table-like   → reject
      G4: comma-containing line    → reject (unless structured number pattern)
      G5: [2] single-word guard    → reject unless in known list

    Rule order:
      R0: bare section prefix      → heading (for split-merge)
      R1: numbered section         → heading
      R2: appendix letter          → heading
      R3: [2] titled-case short line with context clues → heading
      R4: canonical keyword        → heading
      R5: ALL-CAPS short line      → heading
      R6: font-size heuristic      → heading (conservative)
    """
    text = normalize_text(item.get("text", ""))
    if not text or is_noise(text):
        return False, None
    if is_caption_like(text):
        return False, None

    text_lower = text.lower().strip(".")

    # ── R0 first: bare prefix must bypass all guards ──
    if _is_bare_section_prefix(text):
        return True, text.count(".") + 1

    # ── G1: fake-heading blacklist ──
    if text_lower in _FAKE_HEADING_WORDS:
        return False, None

    # ── G2: [1] unit-based lines ──
    if _UNIT_PATTERN.match(text):
        return False, None

    # ── G3: [1] numeric / table-value lines ──
    if re.fullmatch(r"[\d\s,.%±<>≤≥\/]+", text):
        return False, None
    digit_count = sum(c.isdigit() for c in text)
    if (
        digit_count > 3
        and _numbered_heading_level(text) is None
        and _appendix_heading_level(text) is None
    ):
        return False, None

    # ── G4: comma-containing lines (almost never headings) ──
    if "," in text:
        if _numbered_heading_level(text) is None and _appendix_heading_level(text) is None:
            return False, None

    # ── G5: [2] single-word guard ──
    words = text.split()
    if len(words) == 1:
        if text_lower not in _KNOWN_SINGLE_HEADINGS:
            return False, None

    # ── R1: numbered section ──
    lvl = _numbered_heading_level(text)
    if lvl is not None:
        return True, lvl

    # ── R2: appendix letter ──
    lvl = _appendix_heading_level(text)
    if lvl is not None:
        return True, lvl

    # ── R3: [2] title-case short line — improved subsection detection ──
    # A line is a likely subsection heading if:
    #   - 2–6 words, title-case (most words capitalised)
    #   - does NOT end with a period (sentences do; headings don't)
    #   - NOT in the fake-heading blacklist (already checked above)
    #   - font size is at least equal to body median
    if 2 <= len(words) <= 6 and not text.endswith("."):
        cap_words = sum(1 for w in words if w and w[0].isupper())
        cap_ratio = cap_words / len(words)
        font_size = item.get("font_size", 0)
        if cap_ratio >= 0.7 and font_size >= body_font_median * HEADING_FONT_RATIO:
            return True, 2   # treat as subsection level

    # ── R4: canonical keyword ──
    if text_lower in _SECTION_STOPS or text_lower in _KNOWN_SINGLE_HEADINGS:
        return True, 1

    # ── R5: ALL-CAPS short line ──
    if _all_caps_heading(text):
        return True, 1

    # ── R6: font-size heuristic (conservative) ──
    font_size = item.get("font_size", 0)
    if (
        body_font_median > 0
        and font_size >= body_font_median * HEADING_FONT_RATIO
        and len(words) <= MAX_HEADING_WORDS
        and len(words) >= 2
        and not text.endswith(".")
        and not is_email(text)
        and not is_author_line(text)
        and not is_affiliation_line(text)
        and text_lower not in _FAKE_HEADING_WORDS
    ):
        if font_size >= body_font_median * 1.4:
            return True, 1
        if font_size >= body_font_median * 1.2:
            return True, 1
        return True, 2

    return False, None


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Body font median
# ─────────────────────────────────────────────────────────────────────────────

def compute_body_font_median(raw: list) -> float:
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
# 7.  Title extraction
# ─────────────────────────────────────────────────────────────────────────────

def merge_title_lines(raw: list, body_font_median: float) -> str:
    page1 = [item for item in raw if item.get("page") == 1]
    if not page1:
        return ""

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

    zone_max_fs = max((item.get("font_size", 0) for item in pre_abstract), default=0)
    TITLE_FS_TOL = 1.5

    if zone_max_fs > 0:
        title_lines = [
            normalize_text(item["text"])
            for item in pre_abstract
            if abs(item.get("font_size", 0) - zone_max_fs) <= TITLE_FS_TOL
            and len(normalize_text(item["text"])) > 3
        ]
    else:
        title_lines = [
            normalize_text(item["text"])
            for item in pre_abstract[:3]
            if len(normalize_text(item["text"])) > 3
        ]

    return " ".join(title_lines).strip()


# ─────────────────────────────────────────────────────────────────────────────
# 8.  Metadata extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_metadata(raw: list, title: str) -> dict:
    metadata = {"authors": [], "affiliation": "", "emails": [], "source": ""}
    page1 = [item for item in raw if item.get("page") == 1]

    title_texts: set[str] = set()
    if title:
        for item in page1:
            t = normalize_text(item.get("text", ""))
            if t and len(t) > 5 and t in title:
                title_texts.add(t)

    for item in page1:
        t = normalize_text(item.get("text", ""))
        if not t or is_noise(t):
            continue
        if _is_section_stop(t):
            break
        if t in title_texts:
            continue
        if is_email(t):
            metadata["emails"].extend(re.findall(r"[\w.+-]+@[\w.-]+\.\w{2,}", t))
        elif is_author_line(t) and not metadata["authors"]:
            parts = [re.sub(r"[\d∗†‡§¶*,]+$", "", p).strip() for p in t.split(",")]
            metadata["authors"] = [p for p in parts if p]
        elif is_affiliation_line(t) and not metadata["affiliation"]:
            metadata["affiliation"] = t
        elif re.search(r"arxiv:", t, re.IGNORECASE) and not metadata["source"]:
            metadata["source"] = t

    return metadata


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Split heading merge
# ─────────────────────────────────────────────────────────────────────────────

def merge_split_headings(items: list) -> list:
    if not items:
        return items
    merged = []
    i = 0
    while i < len(items):
        item = items[i]
        if item.get("type") == "heading" and i + 1 < len(items):
            cur = normalize_text(item.get("text", ""))
            nxt = items[i + 1]
            if _is_bare_section_prefix(cur) and nxt.get("type") == "heading":
                nxt_text = normalize_text(nxt.get("text", ""))
                combined = f"{cur} {nxt_text}"
                lvl = (
                    _appendix_heading_level(combined)
                    or _numbered_heading_level(combined)
                    or item.get("level", 1)
                )
                merged.append({**item, "text": combined, "level": lvl})
                i += 2
                continue
        merged.append(item)
        i += 1
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# 10.  Paragraph merging
# ─────────────────────────────────────────────────────────────────────────────

def merge_paragraphs(items: list) -> list:
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
# 11.  Hierarchy tree
# ─────────────────────────────────────────────────────────────────────────────

def build_hierarchy(items: list) -> list:
    root = {"heading": "__ROOT__", "level": 0, "page": None, "content": [], "children": []}
    stack = [root]

    for item in items:
        if item["type"] == "heading":
            node = {
                "heading":  item["text"],
                "level":    item.get("level", 1),
                "page":     item.get("page"),
                "content":  [],
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
# 12.  [NEW] References separation  (requirement 6)
# ─────────────────────────────────────────────────────────────────────────────

def separate_references(sections: list) -> tuple[list, list]:
    """
    [Requirement 6] Pull the References section (and any sections after it)
    out of the main sections list and return them separately.

    Returns (main_sections, reference_sections).

    References are kept so they can be stored/searched differently (e.g. a
    separate FAISS index, BM25 lookup, or just metadata) rather than polluting
    the main semantic retrieval index.
    """
    main: list = []
    refs: list = []
    in_refs = False

    for section in sections:
        if _is_reference_heading(section.get("heading", "")):
            in_refs = True
        if in_refs:
            refs.append(section)
        else:
            main.append(section)

    return main, refs


# ─────────────────────────────────────────────────────────────────────────────
# 13.  Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_cleaner(raw: list) -> dict:
    """Full pipeline: raw extracted lines → structured document dict."""

    # A: [3] Remove repeated page headers / footers before anything else
    raw = remove_repeated_headers_footers(raw)

    # B: body font median
    body_font_median = compute_body_font_median(raw)

    # C: title
    title = merge_title_lines(raw, body_font_median)

    # D: metadata
    metadata = extract_metadata(raw, title)

    # E: title-line suppression set
    title_line_texts: set[str] = set()
    if title:
        for item in raw:
            if item.get("page") != 1:
                continue
            t = normalize_text(item.get("text", ""))
            if t and len(t) > 5 and t in title:
                title_line_texts.add(t)

    # F: classify every line
    classified = []
    for item in raw:
        text = normalize_text(item.get("text", ""))
        if not text or is_noise(text):
            continue
        if text in title_line_texts:
            continue

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

    # G: merge split headings
    classified = merge_split_headings(classified)

    # H: merge paragraph lines into blocks
    merged = merge_paragraphs(classified)

    # I: build hierarchy
    sections = build_hierarchy(merged)

    # J: [6] separate references
    main_sections, ref_sections = separate_references(sections)

    return {
        "title":      title,
        "metadata":   metadata,
        "sections":   main_sections,
        "references": ref_sections,   # kept separate from retrieval index
    }


# ─────────────────────────────────────────────────────────────────────────────
# 14.  Debug + CLI
# ─────────────────────────────────────────────────────────────────────────────

def debug_page1(raw: list) -> None:
    print("\n=== DEBUG: page-1 lines (font_size | classification | text) ===")
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
        description="Structure-aware cleaner: raw PDF JSON → hierarchical document JSON"
    )
    parser.add_argument("--input",  "-i", default="pdf_structure.json")
    parser.add_argument("--output", "-o", default="document_structure.json")
    parser.add_argument("--debug",  "-d", action="store_true",
                        help="Print page-1 font sizes to diagnose title/metadata issues")
    args = parser.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))

    if args.debug:
        debug_page1(raw)

    result = run_cleaner(raw)

    # Always save into clean_json/ folder (auto-created if missing)
    out_dir = Path("clean_json")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / Path(args.output).name
    out_path.write_text(
        json.dumps(result, indent=4, ensure_ascii=False), encoding="utf-8"
    )
    print(f"✓  Saved → {out_path}")
    print(f"   Title      : {title_display}")
    print(f"   Authors    : {len(result['metadata']['authors'])} found")
    print(f"   Sections   : {len(result['sections'])} main  |  "
          f"{len(result['references'])} reference")


if __name__ == "__main__":
    main()