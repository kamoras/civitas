"""Request and queue helpers for the visit-counting tests. Shared by
test_visits, test_visitor_hash_privacy and test_admin_dashboard_trends;
not a test module, so nothing here is collected.
"""

from unittest.mock import MagicMock

from app.api.visits import _visit_queue, _write_visit_batch, track_visit


def _make_request(peer_ip: str = "203.0.113.5", user_agent: str = "Mozilla/5.0") -> MagicMock:
    req = MagicMock()
    req.client.host = peer_ip
    req.headers = {"User-Agent": user_agent}
    return req


async def _view(request, path: str) -> None:
    """A page opened in a browser that runs it, as the frontend's proxy
    reports it: the page request, then the router's first request."""
    await track_visit(request, kind="page", path=path)
    await track_visit(request, kind="router", path="/")


def _drain_queue_and_write(db) -> int:
    """Test stand-in for run_visit_consumer's drain loop: synchronously
    pulls everything currently queued and writes it via the same
    _write_visit_batch the real background consumer uses. Returns how
    many events were written."""
    batch = []
    while not _visit_queue.empty():
        batch.append(_visit_queue.get_nowait())
    if batch:
        _write_visit_batch(batch, db)
    return len(batch)
