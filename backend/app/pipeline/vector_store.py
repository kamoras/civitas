"""
Vector store for bill embeddings using ChromaDB and sentence-transformers.

Enables semantic search for:
- Promise-to-vote matching
- Donor-bill connections
- Cross-senator policy analysis
"""

import logging
from typing import Any

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
        logger.info("Initializing ChromaDB client (persistent storage: /app/data/chroma)")
        _client = chromadb.PersistentClient(
            path="/app/data/chroma",
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
        logger.warning("Bills collection not found in vector DB")
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
        logger.warning("Bills collection not found in vector DB")
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


def reset_vector_db() -> None:
    """Reset the entire vector database (useful for fresh starts)."""
    client = get_chroma_client()
    try:
        client.delete_collection(name="bills")
        logger.info("Reset vector database")
    except Exception as e:
        logger.warning("Could not reset vector DB: %s", e)
