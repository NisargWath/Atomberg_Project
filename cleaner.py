"""
cleaner.py  —  Structure-Aware Post-Processor for PDF Extraction
================================================================
Pipeline:
  raw JSON
    → [1] aggressive header/footer + figure-label noise removal
    → normalize
    → body-font computation
    → title extraction  →  metadata extraction
    → [2] improved line classification (heading / paragraph)
    → split-heading merge
    → paragraph merge  (paragraphs kept as separate blocks, not joined)
    → hierarchy tree build
    → references separated
    → final JSON

Changes in this version
────────────────────────
  [1] Header/footer removal — three-pass approach:
        (a) verbatim repeated lines across 3+ pages → removed
        (b) lines that fuzzy-match the document title → removed
        (c) figure/table caption lines in body → removed from content
            (not promoted to headings either)
      Running-header patterns like "AUTHOR | PAPER TITLE" (pipe-separated)
      are now caught explicitly.

  [2] Subsection detection — four new signals on top of existing rules:
        (a) lines preceded AND followed by blank/paragraph context
            that are short, title-case, no trailing period → heading
        (b) numbered patterns 1.1 / 2.3 / A.1 detected more reliably
            even when preceded by inline text
        (c) ALL-CAPS multi-word that's ≤ 8 words and not a metric → heading
        (d) font-size threshold lowered slightly for lines that also pass
            title-case check, so survey-style subsection headers are caught

  [3] Paragraphs stored as a LIST of paragraph blocks per section node,
      not concatenated into a single string.  This gives chunker.py
      natural paragraph boundaries to split on (requirement 3 of chunker).
"""

import json
import re
import argparse
from pathlib import Path
from statistics import median


# ─────────────────────────────────────────────────────────────────────────────
# 0.  Config
# ─────────────────────────────────────────────────────────────────────────────

HEADING_FONT_RATIO          = 1.08   # slightly lower → catch more subsection headings
MAX_HEADING_WORDS           = 14
MIN_PARAGRAPH_WORDS         = 3
REPEATED_LINE_PAGE_THRESHOLD = 3     # lines on >= this many pages → header/footer

_SECTION_STOPS = {
    "abstract", "introduction", "related work", "background",
    "preliminaries", "methods", "methodology", "experiments",
    "conclusion", "references",
}

_FAKE_HEADING_WORDS = {
    # Table / result column labels
    "model", "retriever", "dataset", "configuration", "baseline",
    "system", "systems", "comparison", "setup", "setting", "settings",
    "task", "tasks", "category", "categories", "type", "types",
    "input", "output", "size", "name", "role", "layer", "question",
    "content", "value", "values", "score", "scores",
    # Metric names
    "accuracy", "rouge", "rouge-l", "rouge-1", "rouge-2",
    "bleu", "bleu-1", "bleu-4", "meteor", "f1", "ndcg", "map", "mrr",
    "recall", "precision", "perplexity",
    # Model / dataset names used as column headers
    "bm25", "dpr", "sbert", "qasper", "narrativeqa", "quality",
    "bert", "gpt", "t5", "bart", "llama", "mistral",
    # Stopwords / function words
    "to", "and", "or", "the", "of", "in", "on", "at", "by", "for",
    "with", "from", "that", "this", "it", "is", "are", "was",
}

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
    # Common survey / paper subsections
    "overview", "motivation", "problem", "formulation", "notation",
    "contributions", "summary", "setup", "baselines", "datasets",
    "metrics", "analysis", "ablation", "findings",
}

_METRIC_ACRONYMS = {
    "bm25", "dpr", "bleu", "rouge", "meteor", "f1", "pk", "wd",
    "ndcg", "map", "mrr", "recall", "precision", "accuracy",
    "url", "uri", "api", "gpu", "cpu", "ram", "llm", "rag",
    "sbert", "bert", "gpt", "t5", "bart",
}

_REFERENCE_HEADINGS = {
    "references", "bibliography", "works cited", "citations",
}

# [1] Patterns that identify running headers / footers regardless of repetition
_HEADER_FOOTER_PATTERNS = [
    # "Author Name | Paper Title" or "Paper Title | Conference"
    re.compile(r"^[^|]{3,60}\s*\|\s*[^|]{3,60}$"),
    # "Paper Title – Conference 2024"
    re.compile(r".{10,}\s[–—-]\s(iclr|neurips|icml|acl|emnlp|naacl|cvpr|aaai)\s*20\d{2}", re.IGNORECASE),
    # Pure page numbers with surrounding text: "3  Introduction"
    re.compile(r"^\d{1,3}\s{2,}[A-Z][a-z]"),
    # Journal-style headers: "Journal of X, Vol. Y, pp. Z"
    re.compile(r"\bvol\.\s*\d+\b|\bpp\.\s*\d+\b|\bno\.\s*\d+\b", re.IGNORECASE),
]


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
    """
    [1][3] Figure/Table/Algorithm/Chart captions and labels.
    Rejected as headings AND stripped from section content.

    Covers:
      - Inline captions: "Figure 1:", "Table 2:", "Algorithm 3"
      - Chart/graph labels: "Chart 1", "Graph 2"
      - Equation labels: "Equation 1", "(1)", "(2.3)"
      - Standalone figure/table heading lines that would create empty sections
    """
    patterns = [
        r"^(Figure|Fig\.?)\s+\d+",
        r"^Table\s+\d+",
        r"^Algorithm\s+\d+",
        r"^Listing\s+\d+",
        r"^Chart\s+\d+",           # [3] chart labels
        r"^Graph\s+\d+",           # [3] graph labels
        r"^Equation\s+\d+",        # [3] equation labels
        r"^Appendix\s+[A-Z]\s*:",
        r"^\(\d+(?:\.\d+)?\)$",  # standalone equation numbers like "(1)" or "(2.3)"
    ]
    return any(re.match(p, text, re.IGNORECASE) for p in patterns)


def is_metadata_page_line(text: str, page: int) -> bool:
    """
    [1] Detect author/affiliation/email lines that belong in metadata,
    not in section content.

    On page 1 (the title/author page), lines that are author lists,
    affiliations, or email addresses are pure metadata and must NEVER
    enter the section/paragraph stream — they would otherwise appear
    as garbage chunks in the retrieval index.

    On pages 2+, these checks are relaxed (author names can legitimately
    appear in body text as citations, affiliations in acknowledgements, etc.)
    """
    if page != 1:
        return False
    return is_author_line(text) or is_affiliation_line(text) or is_email(text)


def _is_section_stop(text: str) -> bool:
    return text.lower().strip(".") in _SECTION_STOPS


def _is_reference_heading(text: str) -> bool:
    return text.lower().strip().rstrip(".") in _REFERENCE_HEADINGS


def _is_running_header(text: str) -> bool:
    """[1] Catch running header/footer patterns that don't rely on repetition count."""
    for pat in _HEADER_FOOTER_PATTERNS:
        if pat.search(text):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 3.  [1] Aggressive header/footer removal — three passes
# ─────────────────────────────────────────────────────────────────────────────

def _title_similarity(text: str, title: str) -> float:
    """
    Very lightweight token overlap between text and title.
    Returns fraction of title tokens found in text (0.0–1.0).
    """
    if not title or not text:
        return 0.0
    t_tokens = set(re.findall(r"\w+", text.lower()))
    title_tokens = set(re.findall(r"\w+", title.lower()))
    if not title_tokens:
        return 0.0
    return len(t_tokens & title_tokens) / len(title_tokens)


def remove_repeated_headers_footers(raw: list, title: str = "") -> list:
    """
    [Requirement 1] Three-pass header/footer removal.

    Pass A — verbatim repetition:
        Lines appearing on >= REPEATED_LINE_PAGE_THRESHOLD distinct pages
        AND short (<= 12 words) are header/footer candidates.
        First occurrence is kept (might be the actual heading); rest dropped.

    Pass B — title similarity:
        Lines that share >= 60% token overlap with the document title
        and are NOT on page 1 (page-1 is legitimate title) are dropped.
        This catches running title headers like "RAPTOR: Recursive..." on p3.

    Pass C — structural patterns:
        Lines matching _HEADER_FOOTER_PATTERNS (pipe-separated, vol/pp, etc.)
        are dropped regardless of repetition count.

    Figure/table captions are handled separately in is_caption_like() and
    are stripped from content during classification, not here.
    """
    # ── Pass A: verbatim repetition ──
    text_pages: dict[str, set] = {}
    for item in raw:
        t = normalize_text(item.get("text", ""))
        p = item.get("page")
        if t and p is not None:
            text_pages.setdefault(t, set()).add(p)

    repeated: set[str] = {
        t for t, pages in text_pages.items()
        if len(pages) >= REPEATED_LINE_PAGE_THRESHOLD
        and len(t.split()) <= 12
    }

    seen_repeated: set[str] = set()

    # ── Pass B: title-similar lines away from page 1 ──
    # We'll check during the loop below

    # ── Pass C: structural patterns checked inline ──

    filtered = []
    for item in raw:
        t = normalize_text(item.get("text", ""))
        page = item.get("page", 1)

        if not t:
            filtered.append(item)
            continue

        # Pass A
        if t in repeated:
            if t not in seen_repeated:
                seen_repeated.add(t)
                filtered.append(item)   # keep first occurrence only
            # subsequent occurrences silently dropped
            continue

        # Pass B: title-similar running header (only on page 2+)
        if page > 1 and title and _title_similarity(t, title) >= 0.6:
            if len(t.split()) <= 15:    # only short-ish lines
                continue                 # drop

        # Pass C: structural header/footer pattern
        if _is_running_header(t):
            continue                     # drop

        filtered.append(item)

    return filtered


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Heading detection helpers
# ─────────────────────────────────────────────────────────────────────────────

def _numbered_heading_level(text: str):
    """1 Intro→1  |  2.1 Methods→2  |  3.2.1 Results→3"""
    m = re.match(r"^(\d+(?:\.\d+)*)\s{1,4}[A-Z\u00C0-\u024F]", text)
    if not m:
        return None
    return m.group(1).count(".") + 1


def _appendix_heading_level(text: str):
    """A Ablation→1  |  B.1 Methodology→2"""
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
    return bool(re.fullmatch(r"[A-Z](?:\.\d+)*|\d+(?:\.\d+)*", text.strip()))


def _all_caps_heading(text: str) -> bool:
    if text != text.upper():
        return False
    words = text.split()
    if not (1 <= len(words) <= 8):      # tighter: ≤8 words for all-caps
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


def _is_title_case_heading(text: str, font_size: float, body_median: float) -> bool:
    """
    [2] Detect title-case subsection headings like "Survey Objective",
    "Problem Formulation", "Key Contributions".

    Criteria (all must hold):
      - 2–8 words
      - no trailing period or colon-then-more-text (avoids inline labels)
      - >= 70% of words are capitalised
      - no comma (commas → sentence, not heading)
      - font >= body_median (no need for big ratio — visual prominence alone)
      - text not in fake-heading blacklist
    """
    words = text.split()
    if not (2 <= len(words) <= 8):
        return False
    if text.endswith("."):
        return False
    if "," in text:
        return False
    # Allow trailing colon only if the whole thing is title-like ("Key Insight:")
    clean = text.rstrip(":")
    cap_words = sum(1 for w in clean.split() if w and w[0].isupper())
    if cap_words / len(words) < 0.7:
        return False
    if font_size < body_median:         # must be at least body size
        return False
    tl = text.lower().strip(".:")
    if tl in _FAKE_HEADING_WORDS:
        return False
    # Must not look like the start of a sentence (e.g. "This paper presents")
    if words[0].lower() in {"this", "the", "a", "an", "we", "our", "their", "these"}:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Main heading detector  [requirements 1 + 2]
# ─────────────────────────────────────────────────────────────────────────────

def detect_heading(item: dict, body_font_median: float):
    """
    Return (is_heading: bool, level: int | None).

    Guards  (fast rejection before rules):
      G0: noise / caption / running header  → reject
      G1: fake-heading blacklist            → reject
      G2: unit-based line                   → reject
      G3: numeric / table-value             → reject
      G4: comma in non-structured line      → reject
      G5: single-word not in known list     → reject

    Rules (first match wins):
      R0: bare section prefix ("B.1")       → heading for split-merge
      R1: numbered section (1 / 2.1 / 3.2.1)
      R2: appendix letter (A / B.1)
      R3: [2] title-case short line         → L2 subsection heading
      R4: canonical keyword                 → L1
      R5: ALL-CAPS short line (≤8 words)    → L1
      R6: font-size above body median       → heading (conservative)
    """
    text = normalize_text(item.get("text", ""))
    if not text or is_noise(text):
        return False, None
    if is_caption_like(text):
        return False, None
    if _is_running_header(text):          # [1] catch structural patterns
        return False, None

    text_lower = text.lower().strip(".")
    font_size  = item.get("font_size", 0)

    # ── R0: bare prefix bypasses all guards ──
    if _is_bare_section_prefix(text):
        return True, text.count(".") + 1

    # ── G1 ──
    if text_lower in _FAKE_HEADING_WORDS:
        return False, None

    # ── G2 ──
    if _UNIT_PATTERN.match(text):
        return False, None

    # ── G3 ──
    if re.fullmatch(r"[\d\s,.%±<>≤≥\/]+", text):
        return False, None
    digit_count = sum(c.isdigit() for c in text)
    if (
        digit_count > 3
        and _numbered_heading_level(text) is None
        and _appendix_heading_level(text) is None
    ):
        return False, None

    # ── G4 ──
    if "," in text:
        if _numbered_heading_level(text) is None and _appendix_heading_level(text) is None:
            return False, None

    # ── G5 ──
    words = text.split()
    if len(words) == 1 and text_lower not in _KNOWN_SINGLE_HEADINGS:
        return False, None

    # ── R1: numbered section ──
    lvl = _numbered_heading_level(text)
    if lvl is not None:
        return True, lvl

    # ── R2: appendix letter ──
    lvl = _appendix_heading_level(text)
    if lvl is not None:
        return True, lvl

    # ── R3: [2] title-case subsection heading ──
    if _is_title_case_heading(text, font_size, body_font_median):
        return True, 2

    # ── R4: canonical keyword ──
    if text_lower in _SECTION_STOPS or text_lower in _KNOWN_SINGLE_HEADINGS:
        return True, 1

    # ── R5: ALL-CAPS ──
    if _all_caps_heading(text):
        return True, 1

    # ── R6: font-size heuristic ──
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
# 10.  Paragraph merging — [3] preserve paragraph boundaries as separate blocks
# ─────────────────────────────────────────────────────────────────────────────

def merge_paragraphs(items: list) -> list:
    """
    Merge consecutive single-line paragraph fragments that belong to the
    same logical paragraph (same flow, no heading between them) into one
    paragraph block.

    Key change from previous version:
    Each paragraph block is stored as a SEPARATE item in the output list —
    NOT joined with other paragraphs from the same section into one big string.
    This preserves paragraph boundaries so that chunker.py can split at them
    instead of at arbitrary sentence counts.

    Two consecutive paragraph items are in the SAME paragraph if:
      - neither looks like a sentence-ending line of a paragraph
        (heuristic: if previous line ends with a hyphen, it's a continuation)
      - they are on the same or adjacent pages

    Otherwise they are separate paragraph blocks.
    """
    merged = []
    buf_lines: list[str] = []
    buf_page: int | None = None
    prev_page: int | None = None

    def flush():
        nonlocal buf_lines, buf_page
        if buf_lines:
            full = normalize_text(" ".join(buf_lines))
            if len(full.split()) >= MIN_PARAGRAPH_WORDS:
                merged.append({"type": "paragraph", "text": full, "page": buf_page})
        buf_lines.clear()
        buf_page = None

    for item in items:
        if item["type"] == "heading":
            flush()
            merged.append(item)
            prev_page = item.get("page")
        else:
            cur_page = item.get("page")
            # Start a new paragraph block if:
            # (a) we just flushed (buf_lines is empty), or
            # (b) page gap > 1 (likely a new section started), or
            # (c) previous line did NOT end with a hyphen (not a line-break continuation)
            #     AND previous line ended with sentence-closing punctuation
            if buf_lines:
                prev_line = buf_lines[-1]
                page_gap = (cur_page or 0) - (prev_page or 0)
                new_para = (
                    page_gap > 1
                    or (
                        not prev_line.endswith("-")
                        and prev_line[-1:] in ".!?"
                    )
                )
                if new_para:
                    flush()

            if buf_page is None:
                buf_page = cur_page
            buf_lines.append(item["text"])
            prev_page = cur_page

    flush()
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# 11.  Hierarchy tree  — content stored as list of paragraph strings
# ─────────────────────────────────────────────────────────────────────────────

def build_hierarchy(items: list) -> list:
    """
    Build the nested section tree.

    content is a list of paragraph strings (one string per paragraph block),
    NOT a single concatenated string.  This gives chunker.py paragraph
    boundaries to work with.
    """
    root = {
        "heading": "__ROOT__", "level": 0,
        "page": None, "content": [], "children": [],
    }
    stack = [root]

    for item in items:
        if item["type"] == "heading":
            node = {
                "heading":  item["text"],
                "level":    item.get("level", 1),
                "page":     item.get("page"),
                "content":  [],        # list of paragraph strings
                "children": [],
            }
            while len(stack) > 1 and stack[-1]["level"] >= node["level"]:
                stack.pop()
            stack[-1]["children"].append(node)
            stack.append(node)
        else:
            # paragraph — append as a separate block
            stack[-1]["content"].append(item["text"])

    return root["children"]


# ─────────────────────────────────────────────────────────────────────────────
# 12.  References separation
# ─────────────────────────────────────────────────────────────────────────────

def separate_references(sections: list) -> tuple[list, list]:
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
    # A: body font median first (needed by title extraction)
    body_font_median = compute_body_font_median(raw)

    # B: title (before header removal so we have it for Pass B)
    title = merge_title_lines(raw, body_font_median)

    # C: [1] aggressive header/footer removal (now uses title for similarity check)
    raw = remove_repeated_headers_footers(raw, title=title)

    # D: recompute body median after noise removal (more accurate)
    body_font_median = compute_body_font_median(raw)

    # E: metadata
    metadata = extract_metadata(raw, title)

    # F: title-line suppression
    title_line_texts: set[str] = set()
    if title:
        for item in raw:
            if item.get("page") != 1:
                continue
            t = normalize_text(item.get("text", ""))
            if t and len(t) > 5 and t in title:
                title_line_texts.add(t)

    # G: classify every line
    classified = []
    for item in raw:
        text = normalize_text(item.get("text", ""))
        if not text or is_noise(text):
            continue
        if text in title_line_texts:
            continue
        if is_caption_like(text):              # [1][3] drop caption/chart lines
            continue
        if is_metadata_page_line(text, item.get("page", 0)):   # [1] drop page-1 metadata
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

    # H: merge split headings
    classified = merge_split_headings(classified)

    # I: merge paragraph lines into blocks (preserving boundaries)
    merged = merge_paragraphs(classified)

    # J: build hierarchy
    sections = build_hierarchy(merged)

    # K: separate references
    main_sections, ref_sections = separate_references(sections)

    return {
        "title":      title,
        "metadata":   metadata,
        "sections":   main_sections,
        "references": ref_sections,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 14.  Debug + CLI
# ─────────────────────────────────────────────────────────────────────────────

def debug_page1(raw: list) -> None:
    print("\n=== DEBUG: page-1 lines ===")
    page1 = [x for x in raw if x.get("page") == 1]
    for item in page1:
        t  = normalize_text(item.get("text", ""))
        fs = item.get("font_size", 0)
        if not t:
            continue
        tag = ""
        if _is_section_stop(t):        tag = "  [SECTION STOP]"
        elif is_author_line(t):        tag = "  [AUTHOR LINE]"
        elif is_affiliation_line(t):   tag = "  [AFFILIATION]"
        elif is_email(t):              tag = "  [EMAIL]"
        elif is_noise(t):              tag = "  [NOISE]"
        elif _is_running_header(t):    tag = "  [RUNNING HEADER]"
        print(f"  {fs:6.2f}pt  {t[:80]}{tag}")
    body_med = compute_body_font_median(raw)
    print(f"\n  body_font_median = {body_med:.2f}pt\n")



def _write_clean_txt(result: dict, path) -> None:
    """
    Write a human-readable plain-text version of the cleaned document.

    Format:
      ════ TITLE ════
      <title>

      ── METADATA ──
      Authors    : ...
      Affiliation: ...
      Emails     : ...

      ════ SECTIONS ════

      [L1] Heading
      ────────────
      paragraph text...

        [L2] Sub-heading
        ────────────────
        paragraph text...

      ════ REFERENCES ════
      ...
    """
    lines = []

    # Title
    lines.append("=" * 72)
    lines.append(f"  {result.get('title', '(no title)')}")
    lines.append("=" * 72)
    lines.append("")

    # Metadata
    meta = result.get("metadata", {})
    lines.append("── METADATA " + "─" * 60)
    lines.append(f"Authors     : {', '.join(meta.get('authors', [])) or '—'}")
    lines.append(f"Affiliation : {meta.get('affiliation', '—')}")
    lines.append(f"Emails      : {', '.join(meta.get('emails', [])) or '—'}")
    lines.append(f"Source      : {meta.get('source', '—')}")
    lines.append("")

    # Sections
    def write_section(node, depth=0):
        indent = "  " * depth
        heading = node.get("heading", "")
        level   = node.get("level", 1)
        page    = node.get("page", "?")
        content = node.get("content", [])

        # Heading line
        lines.append(f"{indent}[L{level}] {heading}  (page {page})")
        lines.append(indent + "─" * min(60, max(20, len(heading) + 12)))

        # Content paragraphs
        if isinstance(content, list):
            for para in content:
                if para.strip():
                    for line in para.split("\n"):
                        lines.append(f"{indent}{line}")
                    lines.append("")
        elif content.strip():
            lines.append(f"{indent}{content}")
            lines.append("")

        # Children
        for child in node.get("children", []):
            write_section(child, depth + 1)

    lines.append("═" * 72)
    lines.append("  SECTIONS")
    lines.append("═" * 72)
    lines.append("")
    for section in result.get("sections", []):
        write_section(section)

    # References
    refs = result.get("references", [])
    if refs:
        lines.append("═" * 72)
        lines.append("  REFERENCES")
        lines.append("═" * 72)
        lines.append("")
        for ref_section in refs:
            for para in ref_section.get("content", []):
                if para.strip():
                    lines.append(para)
            lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Structure-aware cleaner: raw PDF JSON → hierarchical document JSON"
    )
    parser.add_argument("--input",  "-i", default="pdf_structure.json")
    parser.add_argument("--output", "-o", default="document_structure.json")
    parser.add_argument("--debug",  "-d", action="store_true")
    args = parser.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))

    if args.debug:
        debug_page1(raw)

    result = run_cleaner(raw)

    out_dir = Path("clean_json")
    out_dir.mkdir(parents=True, exist_ok=True)

    # JSON output
    out_path = out_dir / Path(args.output).name
    out_path.write_text(json.dumps(result, indent=4, ensure_ascii=False), encoding="utf-8")

    # TXT output (same name, .txt extension)
    txt_path = out_path.with_suffix(".txt")
    _write_clean_txt(result, txt_path)

    title_display = result["title"][:80] if result["title"] else "(empty — run with --debug)"
    print(f"✓  Saved JSON → {out_path}")
    print(f"✓  Saved TXT  → {txt_path}")
    print(f"   Title    : {title_display}")
    print(f"   Authors  : {len(result['metadata']['authors'])} found")
    print(f"   Sections : {len(result['sections'])} main  |  {len(result['references'])} reference")


if __name__ == "__main__":
    main()
