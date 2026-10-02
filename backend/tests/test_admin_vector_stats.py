"""The admin vector-index panel degrades to a stated failure, never a 500,
when either half of its stats can't be read."""

from types import SimpleNamespace

from app.api import admin


def test_an_unreadable_index_and_learning_store_are_reported_not_raised(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("index gone")

    monkeypatch.setattr(admin.vector_store, "collection_stats", boom)
    broken_db = SimpleNamespace(query=boom)
    stats = admin._collect_vector_db_stats(broken_db)
    assert stats["status"] == "unavailable"
    assert stats["error"] == "collection failed; see server logs"
    assert stats["learningStore"] == {"error": "collection failed; see server logs"}
