"""
Vector store for bill embeddings using ChromaDB and sentence-transformers.

Enables semantic search for:
- Promise-to-vote matching
- Donor-bill connections
- Cross-senator policy analysis
"""

import logging

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# Singleton instances
_client: chromadb.ClientAPI | None = None
_model: SentenceTransformer | None = None


def get_chroma_client() -> chromadb.ClientAPI:
    """Get or create ChromaDB client with persistent storage."""
    global _client
    if _client is None:
        logger.info("Initializing ChromaDB client (persistent storage: /data/chroma)")
        _client = chromadb.PersistentClient(
            path="/data/chroma",
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=True,
            ),
        )
    return _client


def get_embedding_model() -> SentenceTransformer:
    """Get or load sentence-transformers embedding model."""
    global _model
    if _model is None:
        logger.info("Loading sentence-transformers model: all-MiniLM-L6-v2")
        # Small, fast model (80MB) optimized for semantic similarity
        # 384-dimensional embeddings, works well on CPU
        _model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _model


def embed_bills(bills: list[dict]) -> None:
    """
    Embed and store bills in the vector database.

    Args:
        bills: List of classified bill dicts with:
            - billId: unique identifier
            - billName: bill title
            - description: summary
            - policyArea: policy domain
            - stance: specific position
            - impactedGroups: affected groups
            - congress: congress number
    """
    if not bills:
        return

    client = get_chroma_client()
    model = get_embedding_model()

    # Get or create collection for bills
    collection = client.get_or_create_collection(
        name="bills",
        metadata={"description": "Classified congressional bills with policy stances"},
    )

    # Build rich text representations for embedding
    documents = []
    metadatas = []
    ids = []

    for bill in bills:
        # Rich text: combine title, description, policy area, stance, impacted groups
        policy_area = bill.get("policyArea", "")
        stance = bill.get("stance", "")
        groups = bill.get("impactedGroups", [])
        groups_str = ", ".join(groups) if isinstance(groups, list) else ""

        text = (
            f"{bill.get('billName', '')} "
            f"{bill.get('description', '')} "
            f"Policy: {policy_area}. "
            f"Stance: {stance}. "
            f"Affects: {groups_str}"
        ).strip()

        documents.append(text)
        ids.append(bill["billId"])
        metadatas.append({
            "billId": bill["billId"],
            "billName": bill.get("billName", "")[:200],
            "policyArea": policy_area,
            "stance": stance,
            "congress": str(bill.get("congress", "")),
            "date": bill.get("date", ""),
        })

    # Generate embeddings
    logger.info("Generating embeddings for %d bills...", len(documents))
    embeddings = model.encode(documents, show_progress_bar=False).tolist()

    # Upsert to ChromaDB (upsert = add or update if exists)
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )

    logger.info("Stored %d bill embeddings in vector DB", len(bills))


def search_bills(
    query: str,
    n_results: int = 10,
    filter_congress: int | None = None,
) -> list[dict]:
    """
    Semantic search for bills matching a query.

    Args:
        query: Natural language query (e.g., "labor union protection", "gun control")
        n_results: Number of results to return
        filter_congress: Optional filter by congress number

    Returns:
        List of dicts with:
            - billId: bill identifier
            - billName: bill title
            - policyArea: policy domain
            - stance: specific position
            - congress: congress number
            - distance: similarity score (lower = more similar)
    """
    client = get_chroma_client()
    model = get_embedding_model()

    try:
        collection = client.get_collection(name="bills")
    except Exception:
        logger.error("Bills collection not found in vector DB — embed_bills() may not have run yet")
        return []

    # Generate query embedding
    query_embedding = model.encode([query], show_progress_bar=False)[0].tolist()

    # Build filter if congress specified
    where = {"congress": str(filter_congress)} if filter_congress else None

    # Search
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where,
    )

    # Parse results
    matches = []
    if results and results["ids"]:
        for i, bill_id in enumerate(results["ids"][0]):
            metadata = results["metadatas"][0][i] if results["metadatas"] else {}
            distance = results["distances"][0][i] if results["distances"] else 0.0

            matches.append({
                "billId": bill_id,
                "billName": metadata.get("billName", ""),
                "policyArea": metadata.get("policyArea", ""),
                "stance": metadata.get("stance", ""),
                "congress": metadata.get("congress", ""),
                "date": metadata.get("date", ""),
                "distance": distance,
            })

    return matches


def search_bills_by_bill_ids(bill_ids: list[str]) -> list[dict]:
    """
    Retrieve bill metadata by IDs (for cross-referencing).

    Args:
        bill_ids: List of bill IDs to retrieve

    Returns:
        List of bill metadata dicts
    """
    if not bill_ids:
        return []

    client = get_chroma_client()

    try:
        collection = client.get_collection(name="bills")
    except Exception:
        logger.error("Bills collection not found in vector DB — embed_bills() may not have run yet")
        return []

    # Retrieve by IDs
    results = collection.get(ids=bill_ids)

    matches = []
    if results and results["ids"]:
        for i, bill_id in enumerate(results["ids"]):
            metadata = results["metadatas"][i] if results["metadatas"] else {}
            matches.append({
                "billId": bill_id,
                "billName": metadata.get("billName", ""),
                "policyArea": metadata.get("policyArea", ""),
                "stance": metadata.get("stance", ""),
                "congress": metadata.get("congress", ""),
            })

    return matches


def embed_explore_documents(docs: list[dict]) -> int:
    """Embed explore documents for semantic search.

    Args:
        docs: list of dicts with keys: id (int), title, summary, body,
              doc_type, source, date, politician_name, chamber, policy_areas.

    Returns:
        Number of documents embedded.
    """
    if not docs:
        return 0

    client = get_chroma_client()
    model = get_embedding_model()

    collection = client.get_or_create_collection(
        name="explore_documents",
        metadata={"description": "Government activity documents for semantic search"},
    )

    documents = []
    metadatas = []
    ids = []

    for doc in docs:
        text = (
            f"{doc.get('title', '')} "
            f"{doc.get('summary', '')} "
            f"{doc.get('body', '')[:800]}"
        ).strip()
        if not text:
            continue

        documents.append(text)
        ids.append(str(doc["id"]))
        metadatas.append({
            "doc_type": doc.get("doc_type", ""),
            "source": doc.get("source", ""),
            "date": doc.get("date", ""),
            "title": doc.get("title", "")[:200],
            "politician_name": doc.get("politician_name") or "",
            "politician_id": doc.get("politician_id") or "",
            "chamber": doc.get("chamber") or "",
        })

    if not documents:
        return 0

    BATCH = 200
    for i in range(0, len(documents), BATCH):
        batch_docs = documents[i:i + BATCH]
        batch_ids = ids[i:i + BATCH]
        batch_meta = metadatas[i:i + BATCH]
        batch_emb = model.encode(batch_docs, show_progress_bar=False).tolist()
        collection.upsert(
            ids=batch_ids,
            embeddings=batch_emb,
            documents=batch_docs,
            metadatas=batch_meta,
        )

    logger.info("Embedded %d explore documents in vector DB", len(documents))
    return len(documents)


def search_explore_documents(
    query: str,
    n_results: int = 20,
    doc_type: str | None = None,
    chamber: str | None = None,
) -> list[dict]:
    """Semantic search over explore documents.

    Returns list of dicts with id, title, date, doc_type, source,
    politician_name, politician_id, chamber, distance.
    """
    client = get_chroma_client()
    model = get_embedding_model()

    try:
        collection = client.get_collection(name="explore_documents")
    except Exception:
        logger.warning("explore_documents collection not found")
        return []

    query_embedding = model.encode([query], show_progress_bar=False)[0].tolist()

    where_clause: dict | None = None
    conditions = []
    if doc_type:
        conditions.append({"doc_type": doc_type})
    if chamber:
        conditions.append({"chamber": chamber})
    if len(conditions) == 1:
        where_clause = conditions[0]
    elif len(conditions) > 1:
        where_clause = {"$and": conditions}

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where_clause,
    )

    matches = []
    if results and results["ids"]:
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            dist = results["distances"][0][i] if results["distances"] else 0.0
            doc_text = results["documents"][0][i] if results["documents"] else ""
            matches.append({
                "id": int(doc_id),
                "title": meta.get("title", ""),
                "date": meta.get("date", ""),
                "docType": meta.get("doc_type", ""),
                "source": meta.get("source", ""),
                "politicianName": meta.get("politician_name", ""),
                "politicianId": meta.get("politician_id", ""),
                "chamber": meta.get("chamber", ""),
                "distance": dist,
                "snippet": doc_text[:300] if doc_text else "",
            })

    return matches


def reset_vector_db() -> None:
    """Reset the entire vector database (useful for fresh starts)."""
    client = get_chroma_client()
    for name in ["bills", "explore_documents"]:
        try:
            client.delete_collection(name=name)
            logger.info("Reset vector DB collection: %s", name)
        except Exception as e:
            logger.warning("Could not reset vector DB collection %s: %s", name, e)
