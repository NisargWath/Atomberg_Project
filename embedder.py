"""
embedder.py  —  Structure-Aware RAG Embedding Pipeline
=======================================================
Reads the chunked JSON produced by chunker.py, filters bad chunks,
builds structure-aware embed_text, generates BGE-M3 dense embeddings,
stores them in a FAISS index, and supports query-time retrieval.

Pipeline:
  chunks.json
    → load & filter (bad chunks, reference_chunks excluded)
    → build embed_text per chunk  (title > breadcrumb > heading \\n\\n content)
    → embed with BGE-M3  (or any swap-in model)
    → L2-normalise vectors
    → store in FAISS IndexFlatIP  (inner product = cosine after normalisation)
    → save FAISS index + metadata JSON
    → query function: embed query → top-k FAISS search → return chunks

Design goals
────────────
  • Model-agnostic: swap BGE-M3 for Qwen3-Embedding-8B or text-embedding-3-large
    by changing one line (or passing --model on CLI).
  • Qdrant-ready: every chunk's metadata dict is already shaped for Qdrant
    payload; migration is swapping FAISS search for a Qdrant client call.
  • Sparse/hybrid-ready: BGE-M3 outputs dense + sparse (lexical) weights.
    Dense-only is used now; sparse weights are available on the returned
    object for later hybrid retrieval.

Usage:
  # Index a document
  python embedder.py index \\
      --input  chunker_json/chunks.json \\
      --outdir embeddings/ \\
      --title  "YOLOv8: A Comprehensive Survey"

  # Query the index
  python embedder.py query \\
      --index    embeddings/index.faiss \\
      --metadata embeddings/metadata.json \\
      --query    "How does the YOLOv8 backbone work?" \\
      --topk     5

Requirements:
  pip install faiss-cpu FlagEmbedding numpy
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

# ── Optional imports (guard so the file can be imported for testing without GPU) ──
try:
    import faiss
    _FAISS_AVAILABLE = True
except ImportError:
    _FAISS_AVAILABLE = False
    print("[WARN] faiss not installed.  Run: pip install faiss-cpu", file=sys.stderr)

# [LOCAL-MODEL] Import both FlagEmbedding classes needed for the two model families.
# BGEM3FlagModel  → BGE-M3  (--model-kind bge-m3)
# FlagModel       → BGE v1.5 (--model-kind bge-v15)
# Neither class contacts Hugging Face when a local folder path is supplied.
try:
    from FlagEmbedding import BGEM3FlagModel, FlagModel
    _FLAG_AVAILABLE = True
except ImportError:
    _FLAG_AVAILABLE = False
    print("[WARN] FlagEmbedding not installed.  Run: pip install FlagEmbedding", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# 0.  Config  (all tuneable in one place)
# ─────────────────────────────────────────────────────────────────────────────

# [LOCAL-MODEL] Default local folder path for the embedding model.
# Set this to wherever your IT/security team has placed the approved model files.
# Do NOT use a Hugging Face repo name here — that would trigger a download.
#
# Examples:
#   Windows : r"C:\approved_models\bge-m3"
#   Linux   : "/opt/models/bge-m3"
#
# Override at runtime with:  --model /path/to/model
DEFAULT_MODEL = r"C:\approved_models\bge-m3"

# [LOCAL-MODEL] Default model family.  Determines which FlagEmbedding class is used.
#   "bge-m3"  → BGEM3FlagModel  (supports dense + sparse + ColBERT)
#   "bge-v15" → FlagModel       (BGE v1.5 family: bge-base-en-v1.5, bge-large-en-v1.5)
DEFAULT_MODEL_KIND = "bge-m3"

# Max input tokens.  BGE-M3 supports up to 8192; BGE v1.5 supports 512.
# load_model() sets this automatically based on model kind.
BGE_M3_MAX_TOKENS  = 8192
BGE_V15_MAX_TOKENS = 512

# Minimum word count below which a chunk is considered too short to embed.
# (chunker.py's RETRIEVAL_MIN_WORDS is 20; keep this consistent or tighter.)
EMBED_MIN_WORDS = 20

# Batch size for embedding.  Lower if you hit OOM on GPU.
EMBED_BATCH_SIZE = 16

# FAISS index type.
# "flat_ip"  — exact inner-product search (cosine after normalisation).
#              Best for < ~1M vectors; no training required.
# "ivf_flat" — approximate; faster for large collections (train first).
FAISS_INDEX_TYPE = "flat_ip"

# Output file names inside --outdir
FAISS_INDEX_FILE    = "index.faiss"
METADATA_FILE       = "metadata.json"
EMBED_TEXTS_FILE    = "embed_texts.txt"   # one embed_text per line (for inspection)


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Text helpers
# ─────────────────────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    """
    Light cleanup applied to content before building embed_text.

    - Collapse multiple whitespace / newlines into single spaces.
    - Strip leading/trailing whitespace.
    - Remove null bytes and control characters that confuse tokenisers.
    - Normalise unicode dashes and quotes to ASCII equivalents.
    Does NOT remove punctuation or do stemming — that would hurt BGE-M3.
    """
    if not text:
        return ""
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # normalise unicode punctuation
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "--")
    text = text.replace("\u00a0", " ")
    # collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)   # max one blank line
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def estimate_tokens(text: str) -> int:
    """
    Rough token estimate: ~0.75 tokens per word for English.
    Used for pre-flight warnings only; the tokeniser does the real count.
    """
    return int(len(text.split()) * 0.75 * 1.3)   # ×1.3 for punctuation overhead


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Chunk filtering
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that identify metadata / noise headings that slipped through
_NOISE_HEADING_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^(Figure|Fig\.?)\s+\d+",
        r"^Table\s+\d+",
        r"^Algorithm\s+\d+",
        r"^Chart\s+\d+",
        r"^Graph\s+\d+",
        r"^Equation\s+\d+",
        r"^\(\d+(?:\.\d+)?\)$",
    ]
]

_METADATA_KEYWORDS = {
    "author", "affiliation", "email", "abstract acknowledgement",
    "acknowledgements", "acknowledgments",
}


def should_skip_chunk(chunk: dict) -> tuple[bool, str]:
    """
    Decide whether a chunk should be excluded from the embedding index.

    Returns (skip: bool, reason: str).

    Checks (in order):
      1. reference chunks  → skip (handled separately)
      2. empty content     → skip
      3. word_count below EMBED_MIN_WORDS → skip
      4. heading matches figure/table/chart pattern → skip
      5. content looks like author/metadata lines → skip
    """
    # 1. Reference chunk
    if chunk.get("is_reference"):
        return True, "reference"

    # 2. Empty content
    content = clean_text(chunk.get("content", "") or "")
    if not content:
        return True, "empty-content"

    # 3. Too short
    wc = len(content.split())
    if wc < EMBED_MIN_WORDS:
        return True, f"too-short ({wc}w)"

    # 4. Caption / figure heading
    heading = chunk.get("heading", "")
    if any(pat.match(heading) for pat in _NOISE_HEADING_PATTERNS):
        return True, "caption-heading"

    # 5. Metadata-like content: email addresses, affiliation lines
    if re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", content):
        lines = content.splitlines()
        # If most lines are short and contain emails/names → metadata noise
        if len(lines) <= 5 and sum(1 for l in lines if "@" in l) >= 1:
            return True, "metadata-email"

    return False, "ok"


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Embed-text construction
# ─────────────────────────────────────────────────────────────────────────────

def build_embed_text(chunk: dict, title: str = "") -> str:
    """
    Build the text that will be passed to the embedding model.

    Format:
      <Title> > <Breadcrumb> > <Heading>

      <Content>

    Why this format:
      BGE-M3 only sees text.  By prepending the full navigation path
      (title → parent sections → current heading) before the content,
      we encode structural position as semantic signal.  A query about
      "YOLOv8 backbone" will match this even if the word "backbone" only
      appears in the heading, not in the content paragraph.

    The chunker already computes context_prefix; we use it directly if
    available (it already contains title > breadcrumb > heading), then
    append the cleaned content.
    """
    context_prefix = chunk.get("context_prefix", "").strip()

    # Fallback: build context_prefix manually if not present
    if not context_prefix:
        parts: list[str] = []
        if title:
            parts.append(title)
        breadcrumb = chunk.get("breadcrumb", [])
        if breadcrumb:
            parts.extend(breadcrumb)
        heading = chunk.get("heading", "")
        if heading:
            parts.append(heading)
        context_prefix = " > ".join(parts)

    content = clean_text(chunk.get("content", "") or "")

    if content:
        return f"{context_prefix}\n\n{content}"
    return context_prefix


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Metadata record construction
# ─────────────────────────────────────────────────────────────────────────────

def build_metadata(
    chunk: dict,
    doc_id: str,
    title: str,
    embed_text: str,
    vector_id: int,
) -> dict:
    """
    Build the metadata payload for one chunk.

    This dict is saved to metadata.json and is what gets returned at
    query time alongside the retrieved text.

    Shaped for Qdrant migration: every field here maps directly to a
    Qdrant PointStruct payload field.
    """
    return {
        # Identity
        "vector_id":    vector_id,      # position in FAISS index
        "doc_id":       doc_id,         # document identifier (filename stem)
        # Document structure
        "title":        title,
        "heading":      chunk.get("heading", ""),
        "breadcrumb":   chunk.get("breadcrumb", []),
        "level":        chunk.get("level", 1),
        "page":         chunk.get("page"),
        # Chunk provenance
        "chunk_index":  chunk.get("chunk_index", 0),
        "total_chunks": chunk.get("total_chunks", 1),
        "is_split":     chunk.get("is_split", False),
        "split_reason": chunk.get("split_reason", "none"),
        "word_count":   chunk.get("word_count", 0),
        # Text payload
        "content":      clean_text(chunk.get("content", "") or ""),
        "embed_text":   embed_text,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Model loader  (swap-point for different embedding models)
# ─────────────────────────────────────────────────────────────────────────────

def load_model(
    model_name: str = DEFAULT_MODEL,
    use_fp16: bool = True,
    model_kind: str = DEFAULT_MODEL_KIND,   # [LOCAL-MODEL] "bge-m3" | "bge-v15"
):
    """
    [LOCAL-MODEL] Load an embedding model from a LOCAL folder path.

    model_name must be an absolute or relative path to a folder that
    contains the model files (config.json, pytorch_model.bin / model.safetensors,
    tokenizer files, etc.).  It must NOT be a Hugging Face repo name like
    "BAAI/bge-m3" — that would attempt a network download.

    model_kind selects the correct FlagEmbedding class:
      "bge-m3"  → BGEM3FlagModel  supports dense + sparse + ColBERT output.
                  Encode returns a dict: output["dense_vecs"] is the matrix.
      "bge-v15" → FlagModel       for BGE v1.5 family (bge-base/large-en-v1.5).
                  Encode returns a numpy array directly (no dict wrapper).

    The returned model object is tagged with a ._kind attribute so that
    embed_texts() knows how to unpack the output without an extra argument.

    Path validation:
      Raises FileNotFoundError immediately if the local folder does not exist,
      so you get a clear error instead of a cryptic download-attempt failure.

    To benchmark a different local model:
      1. Copy the approved model files to your local folder.
      2. Change DEFAULT_MODEL and DEFAULT_MODEL_KIND in the config section, or
         pass --model /path/to/model --model-kind bge-v15 on the CLI.
    """
    if not _FLAG_AVAILABLE:
        raise ImportError("FlagEmbedding is not installed. Run: pip install FlagEmbedding")

    # [LOCAL-MODEL] Validate the local path before attempting to load.
    model_path = Path(model_name)
    if not model_path.exists():
        raise FileNotFoundError(
            f"Local model folder not found: {model_path.resolve()}\n"
            f"Place the model files in that folder, or pass --model /correct/path"
        )
    if not model_path.is_dir():
        raise NotADirectoryError(
            f"--model must point to a FOLDER containing model files, not a file: {model_path}"
        )

    kind = model_kind.lower().strip()
    print(f"[INFO] Loading local model: {model_path.resolve()}")
    print(f"[INFO] Model kind : {kind}  (fp16={use_fp16})")
    t0 = time.time()

    if kind == "bge-m3":
        # [LOCAL-MODEL] BGEM3FlagModel accepts a local folder path directly.
        # Passing a folder path suppresses any Hugging Face Hub lookup.
        model = BGEM3FlagModel(str(model_path), use_fp16=use_fp16)

    elif kind == "bge-v15":
        # [LOCAL-MODEL] FlagModel for BGE v1.5 family.
        # query_instruction_for_retrieval is the BGE v1.5 recommended prefix.
        model = FlagModel(
            str(model_path),
            query_instruction_for_retrieval="Represent this sentence for searching relevant passages: ",
            use_fp16=use_fp16,
        )

    else:
        raise ValueError(
            f"Unknown --model-kind {kind!r}.  "
            f"Choose 'bge-m3' (BGEM3FlagModel) or 'bge-v15' (FlagModel)."
        )

    # [LOCAL-MODEL] Tag the model so embed_texts() can unpack output correctly.
    model._kind = kind
    print(f"[INFO] Model loaded in {time.time() - t0:.1f}s")
    return model


def embed_texts(
    model,
    texts: list[str],
    batch_size: int = EMBED_BATCH_SIZE,
    max_length: int | None = None,     # [LOCAL-MODEL] None → auto-select per model kind
) -> np.ndarray:
    """
    Generate dense embeddings for a list of texts using the loaded model.

    Returns np.ndarray of shape (len(texts), embedding_dim), dtype float32.

    [LOCAL-MODEL] Output unpacking differs between model families:
      bge-m3  → model.encode() returns a dict; vectors are in output["dense_vecs"]
      bge-v15 → model.encode() returns a numpy array directly

    The model._kind tag set by load_model() controls which path is taken,
    so no extra argument is needed at the call site.

    [LOCAL-MODEL] max_length defaults:
      bge-m3  → BGE_M3_MAX_TOKENS  (8192)
      bge-v15 → BGE_V15_MAX_TOKENS (512)
    Passing an explicit max_length overrides the default for both families.

    ── Extension point for sparse/hybrid retrieval (BGE-M3 only) ──
    BGE-M3 also produces sparse weights and ColBERT multi-vectors:
        output = model.encode(..., return_sparse=True, return_colbert_vecs=True)
        sparse_weights = output["lexical_weights"]   # list of dicts
        colbert_vecs   = output["colbert_vecs"]      # list of np.ndarray
    """
    # [LOCAL-MODEL] Pick the right token limit for each model family
    kind = getattr(model, "_kind", "bge-m3")
    if max_length is None:
        max_length = BGE_V15_MAX_TOKENS if kind == "bge-v15" else BGE_M3_MAX_TOKENS

    long_texts = [t for t in texts if estimate_tokens(t) > max_length]
    if long_texts:
        print(f"[WARN] {len(long_texts)} texts may exceed {max_length} tokens and will be truncated.")

    print(f"[INFO] Embedding {len(texts)} texts  (kind={kind}  batch_size={batch_size}  max_length={max_length}) …")
    t0 = time.time()

    if kind == "bge-m3":
        # [LOCAL-MODEL] BGE-M3 path: encode() returns a dict.
        # return_sparse / return_colbert_vecs are False for dense-only mode.
        output = model.encode(
            texts,
            batch_size=batch_size,
            max_length=max_length,
            return_dense=True,
            return_sparse=False,      # flip to True for hybrid retrieval
            return_colbert_vecs=False,
        )
        vectors = np.array(output["dense_vecs"], dtype=np.float32)

    elif kind == "bge-v15":
        # [LOCAL-MODEL] BGE v1.5 path: encode() returns a numpy array directly.
        # batch_size is passed; max_length is set at model init level by FlagModel.
        vectors = model.encode(texts, batch_size=batch_size)
        vectors = np.array(vectors, dtype=np.float32)

    else:
        raise ValueError(f"Unknown model kind {kind!r} on loaded model object.")

    print(f"[INFO] Embedded {len(texts)} texts → shape {vectors.shape}  ({time.time()-t0:.1f}s)")
    return vectors


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Vector normalisation
# ─────────────────────────────────────────────────────────────────────────────

def normalise_vectors(vectors: np.ndarray) -> np.ndarray:
    """
    L2-normalise each vector so that inner product == cosine similarity.

    After normalisation, IndexFlatIP gives exact cosine similarity search,
    which is what BGE-M3 is trained for.
    """
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)   # avoid division by zero
    return (vectors / norms).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# 7.  FAISS index builder
# ─────────────────────────────────────────────────────────────────────────────

def build_faiss_index(vectors: np.ndarray, index_type: str = FAISS_INDEX_TYPE):
    """
    Build and populate a FAISS index.

    index_type options:
      "flat_ip"  — IndexFlatIP: exact cosine (after normalisation), no training.
                   Best for small-to-medium collections (< 500k vectors).
      "ivf_flat" — IndexIVFFlat: approximate, much faster at query time for
                   large collections.  Requires training; n_list ≈ sqrt(N).

    ── Qdrant migration note ──
    When migrating to Qdrant, this function is replaced by:
        client.create_collection(...)
        client.upsert(collection_name, points=[...])
    The normalised vectors and metadata payloads remain identical.
    """
    if not _FAISS_AVAILABLE:
        raise ImportError("faiss is not installed. Run: pip install faiss-cpu")

    dim = vectors.shape[1]
    n   = vectors.shape[0]

    if index_type == "flat_ip":
        index = faiss.IndexFlatIP(dim)
        index.add(vectors)
        print(f"[INFO] Built IndexFlatIP  dim={dim}  n={n}")

    elif index_type == "ivf_flat":
        n_list = max(1, int(n ** 0.5))
        quantiser = faiss.IndexFlatIP(dim)
        index = faiss.IndexIVFFlat(quantiser, dim, n_list, faiss.METRIC_INNER_PRODUCT)
        index.train(vectors)
        index.add(vectors)
        index.nprobe = max(1, n_list // 10)
        print(f"[INFO] Built IndexIVFFlat  dim={dim}  n={n}  n_list={n_list}  nprobe={index.nprobe}")

    else:
        raise ValueError(f"Unknown index_type: {index_type!r}.  Choose 'flat_ip' or 'ivf_flat'.")

    return index


# ─────────────────────────────────────────────────────────────────────────────
# 8.  Save / load
# ─────────────────────────────────────────────────────────────────────────────

def save_index(index, metadata: list[dict], embed_texts: list[str], out_dir: Path) -> None:
    """Save FAISS index, metadata JSON, and embed_texts for inspection."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # FAISS binary index
    idx_path = out_dir / FAISS_INDEX_FILE
    faiss.write_index(index, str(idx_path))
    print(f"[INFO] Saved FAISS index  → {idx_path}")

    # Metadata JSON (one record per vector, same order as FAISS index)
    meta_path = out_dir / METADATA_FILE
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[INFO] Saved metadata     → {meta_path}  ({len(metadata)} records)")

    # embed_texts (one per line, for human inspection / debugging)
    et_path = out_dir / EMBED_TEXTS_FILE
    et_path.write_text("\n\n---\n\n".join(embed_texts), encoding="utf-8")
    print(f"[INFO] Saved embed_texts  → {et_path}")


def load_index(index_path: Path, metadata_path: Path) -> tuple:
    """Load a saved FAISS index and its metadata."""
    if not _FAISS_AVAILABLE:
        raise ImportError("faiss is not installed.")
    index    = faiss.read_index(str(index_path))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    print(f"[INFO] Loaded index  ntotal={index.ntotal}  metadata={len(metadata)} records")
    return index, metadata


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Query-time retrieval
# ─────────────────────────────────────────────────────────────────────────────

def retrieve(
    query: str,
    model,
    index,
    metadata: list[dict],
    top_k: int = 5,
) -> list[dict]:
    """
    Embed the query, search the FAISS index, return top-k results.

    Each result dict contains:
      score      : cosine similarity (0–1, higher is better)
      vector_id  : position in FAISS index
      + all metadata fields from build_metadata()

    ── Qdrant migration ──
    Replace the FAISS search with:
        results = client.search(
            collection_name = "...",
            query_vector    = query_vec[0].tolist(),
            limit           = top_k,
        )
    The normalised query_vec is identical; only the search call changes.
    """
    # Embed and normalise the query (batch of 1)
    raw = embed_texts(model, [query], batch_size=1)
    query_vec = normalise_vectors(raw)      # shape (1, dim)

    # FAISS search
    scores, ids = index.search(query_vec, top_k)

    results = []
    for score, idx in zip(scores[0], ids[0]):
        if idx == -1:
            continue   # FAISS returns -1 for unfilled slots
        result = {
            "score":      float(score),
            "vector_id":  int(idx),
            **metadata[idx],
        }
        results.append(result)

    return results


def print_results(results: list[dict]) -> None:
    """Pretty-print retrieval results to stdout."""
    print(f"\n{'═'*72}")
    print(f"  Retrieved {len(results)} chunks")
    print(f"{'═'*72}\n")
    for i, r in enumerate(results, 1):
        crumb = " > ".join(r.get("breadcrumb", [])) or "(top)"
        print(f"  [{i}]  score={r['score']:.4f}  L{r['level']}  p{r['page']}")
        print(f"       {r['title']}")
        print(f"       {crumb} > {r['heading']}")
        print(f"       {r['content'][:200].replace(chr(10), ' ')}...")
        print()


# ─────────────────────────────────────────────────────────────────────────────
# 10.  Top-level pipeline functions
# ─────────────────────────────────────────────────────────────────────────────

def run_index(
    chunks_json_path: Path,
    out_dir: Path,
    title: str = "",
    doc_id: str = "",
    model_name: str = DEFAULT_MODEL,
    model_kind: str = DEFAULT_MODEL_KIND,   # [LOCAL-MODEL]
    use_fp16: bool = True,
    batch_size: int = EMBED_BATCH_SIZE,
    index_type: str = FAISS_INDEX_TYPE,
) -> tuple:
    """
    Full indexing pipeline.

    1. Load chunks.json
    2. Filter bad chunks
    3. Build embed_text per chunk
    4. Embed with BGE-M3
    5. Normalise vectors
    6. Build FAISS index
    7. Save index + metadata

    Returns (index, metadata_list).
    """
    # ── Step 1: Load ──
    print(f"[INFO] Loading chunks from {chunks_json_path}")
    raw = json.loads(chunks_json_path.read_text(encoding="utf-8"))

    # Support both flat list and {"chunks": [...]} dict
    if isinstance(raw, dict):
        all_chunks = raw.get("chunks", [])
        # Infer title from first chunk's embed_text or doc_id
        if not title and all_chunks:
            prefix = all_chunks[0].get("context_prefix", "")
            if " > " in prefix:
                title = prefix.split(" > ")[0].strip()
    else:
        all_chunks = raw

    if not doc_id:
        doc_id = chunks_json_path.stem

    print(f"[INFO] Total chunks in file : {len(all_chunks)}")
    print(f"[INFO] doc_id={doc_id!r}  title={title!r}")

    # ── Step 2: Filter ──
    good_chunks: list[dict] = []
    skip_summary: dict[str, int] = {}
    for chunk in all_chunks:
        skip, reason = should_skip_chunk(chunk)
        if skip:
            skip_summary[reason] = skip_summary.get(reason, 0) + 1
        else:
            good_chunks.append(chunk)

    print(f"[INFO] Chunks after filter  : {len(good_chunks)}  (skipped: {sum(skip_summary.values())})")
    for reason, count in sorted(skip_summary.items()):
        print(f"         skip reason {reason!r:25s} : {count}")

    if not good_chunks:
        raise ValueError("No chunks passed the filter.  Check your chunks.json.")

    # ── Step 3: Build embed_text ──
    embed_text_list: list[str] = []
    for chunk in good_chunks:
        et = build_embed_text(chunk, title=title)
        embed_text_list.append(et)

    # ── Step 4: Load model + embed ──
    # [LOCAL-MODEL] Pass model_kind so load_model picks the right class
    model = load_model(model_name, use_fp16=use_fp16, model_kind=model_kind)
    vectors = embed_texts(model, embed_text_list, batch_size=batch_size)

    # ── Step 5: Normalise ──
    vectors = normalise_vectors(vectors)
    print(f"[INFO] Vectors normalised   : shape={vectors.shape}  dtype={vectors.dtype}")

    # ── Step 6: Build FAISS index ──
    index = build_faiss_index(vectors, index_type=index_type)

    # ── Step 7: Build metadata list + save ──
    metadata_list: list[dict] = []
    for vid, (chunk, et) in enumerate(zip(good_chunks, embed_text_list)):
        metadata_list.append(
            build_metadata(
                chunk=chunk,
                doc_id=doc_id,
                title=title,
                embed_text=et,
                vector_id=vid,
            )
        )

    save_index(index, metadata_list, embed_text_list, out_dir)
    return index, metadata_list


def run_query(
    query: str,
    index_path: Path,
    metadata_path: Path,
    top_k: int = 5,
    model_name: str = DEFAULT_MODEL,
    model_kind: str = DEFAULT_MODEL_KIND,   # [LOCAL-MODEL]
    use_fp16: bool = True,
) -> list[dict]:
    """Load a saved index and run a single query."""
    index, metadata = load_index(index_path, metadata_path)
    # [LOCAL-MODEL] Forward model_kind so the correct class is used at query time
    model = load_model(model_name, use_fp16=use_fp16, model_kind=model_kind)
    results = retrieve(query, model, index, metadata, top_k=top_k)
    print_results(results)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 11.  CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli_index(args: argparse.Namespace) -> None:
    run_index(
        chunks_json_path = Path(args.input),
        out_dir          = Path(args.outdir),
        title            = args.title,
        doc_id           = args.doc_id or Path(args.input).stem,
        model_name       = args.model,
        model_kind       = args.model_kind,   # [LOCAL-MODEL]
        use_fp16         = not args.no_fp16,
        batch_size       = args.batch_size,
        index_type       = args.index_type,
    )


def _cli_query(args: argparse.Namespace) -> None:
    run_query(
        query         = args.query,
        index_path    = Path(args.index),
        metadata_path = Path(args.metadata),
        top_k         = args.topk,
        model_name    = args.model,
        model_kind    = args.model_kind,   # [LOCAL-MODEL]
        use_fp16      = not args.no_fp16,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Structure-aware RAG embedding pipeline using BGE-M3 + FAISS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── index sub-command ──
    idx = sub.add_parser("index", help="Embed chunks and build FAISS index")
    idx.add_argument("--input",      "-i", required=True,
                     help="Path to chunks.json from chunker.py")
    idx.add_argument("--outdir",     "-o", default="embeddings/",
                     help="Output directory for index + metadata (default: embeddings/)")
    idx.add_argument("--title",      "-t", default="",
                     help="Document title (override auto-detection)")
    idx.add_argument("--doc-id",     default="",
                     help="Document identifier (default: input filename stem)")
    idx.add_argument("--model",      "-m", default=DEFAULT_MODEL,
                     # [LOCAL-MODEL] Must be a local folder path, not a HF repo name
                     help=f"Local model folder path (default: {DEFAULT_MODEL})")
    idx.add_argument("--model-kind", default=DEFAULT_MODEL_KIND,
                     choices=["bge-m3", "bge-v15"],
                     # [LOCAL-MODEL] Selects BGEM3FlagModel vs FlagModel
                     help=f"Model family (default: {DEFAULT_MODEL_KIND})")
    idx.add_argument("--no-fp16",    action="store_true",
                     help="Disable fp16 (use if model errors on your hardware)")
    idx.add_argument("--batch-size", type=int, default=EMBED_BATCH_SIZE,
                     help=f"Embedding batch size (default: {EMBED_BATCH_SIZE})")
    idx.add_argument("--index-type", default=FAISS_INDEX_TYPE,
                     choices=["flat_ip", "ivf_flat"],
                     help=f"FAISS index type (default: {FAISS_INDEX_TYPE})")

    # ── query sub-command ──
    qry = sub.add_parser("query", help="Query an existing FAISS index")
    qry.add_argument("--index",    required=True, help="Path to index.faiss")
    qry.add_argument("--metadata", required=True, help="Path to metadata.json")
    qry.add_argument("--query",    "-q", required=True, help="Query string")
    qry.add_argument("--topk",     "-k", type=int, default=5,
                     help="Number of results to return (default: 5)")
    qry.add_argument("--model",      "-m", default=DEFAULT_MODEL,
                     # [LOCAL-MODEL] local folder path
                     help=f"Local model folder path (default: {DEFAULT_MODEL})")
    qry.add_argument("--model-kind", default=DEFAULT_MODEL_KIND,
                     choices=["bge-m3", "bge-v15"],
                     help=f"Model family (default: {DEFAULT_MODEL_KIND})")
    qry.add_argument("--no-fp16",  action="store_true")

    args = parser.parse_args()

    if args.command == "index":
        _cli_index(args)
    elif args.command == "query":
        _cli_query(args)


if __name__ == "__main__":
    main()
