"""
Vector storage via Chroma (persistent, embedded -- no separate server to
run). Embeddings are computed ourselves via llm_client.embed_texts() and
passed in directly, so Chroma is used purely as a proven local vector
database, independent of which embedding model is configured.
"""
import chromadb

from app.config import CHROMA_COLLECTION, CHROMA_DIR, ensure_data_dirs

_client = None
_collection = None


def get_collection():
    global _client, _collection
    if _collection is None:
        ensure_data_dirs()
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = _client.get_or_create_collection(
            name=CHROMA_COLLECTION, metadata={"hnsw:space": "cosine"}
        )
    return _collection


def add_chunks(document_id: int, device_id: int, chunks: list[dict], embeddings: list[list[float]]) -> None:
    """chunks: list of {"text": str, "page": int, "filename": str}"""
    collection = get_collection()
    ids = [f"doc{document_id}-chunk{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "device_id": device_id,
            "document_id": document_id,
            "page": c["page"],
            "filename": c["filename"],
        }
        for c in chunks
    ]
    documents = [c["text"] for c in chunks]
    collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)


def delete_document(document_id: int) -> None:
    collection = get_collection()
    collection.delete(where={"document_id": document_id})


def get_document_chunks(document_id: int) -> list[dict]:
    """All stored chunks for a document, read back from Chroma ({text,
    page, filename}). Used by the one-time chunks-table backfill so
    installations indexed before the text index existed gain keyword
    search and viewer support without re-uploading anything."""
    collection = get_collection()
    results = collection.get(where={"document_id": document_id}, include=["documents", "metadatas"])
    chunks = []
    for text, meta in zip(results.get("documents", []), results.get("metadatas", [])):
        chunks.append({"text": text, "page": (meta or {}).get("page", 1), "filename": (meta or {}).get("filename", ""), "section": (meta or {}).get("section", "")})
    return chunks


def delete_device(device_id: int) -> None:
    collection = get_collection()
    collection.delete(where={"device_id": device_id})


def query(device_id: int, query_embedding: list[float], top_k: int, allowed_document_ids: list[int] | None = None) -> list[dict]:
    collection = get_collection()
    count = collection.count()
    if count == 0:
        return []
    if allowed_document_ids is not None:
        if not allowed_document_ids:
            return []
        where = {"$and": [{"device_id": device_id}, {"document_id": {"$in": allowed_document_ids}}]}
    else:
        where = {"device_id": device_id}
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, count),
        where=where,
    )
    hits = []
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    for text, meta, dist in zip(docs, metas, dists):
        hits.append({"text": text, "meta": meta, "distance": dist})
    return hits
