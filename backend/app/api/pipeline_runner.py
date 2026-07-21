"""Run a long pipeline coroutine in a background daemon thread.

The admin/API pipeline-trigger endpoints must return immediately — a run takes
minutes to hours — so they hand the work to a daemon thread with its own event
loop. This is the single implementation of that spawn-loop → run → close dance,
previously copy-pasted across api/admin.py (senate/house/supplementary) and
api/pipeline.py.

Callers that need to run several pipelines in sequence pass a single async
factory that awaits them in order (they all share the one thread/loop), rather
than issuing multiple run_until_complete calls.
"""

import asyncio
import logging
import threading
from collections.abc import Awaitable
from typing import Callable

logger = logging.getLogger(__name__)


def run_pipeline_in_thread(
    coro_factory: Callable[[], Awaitable],
    *,
    name: str,
    error_label: str = "Background pipeline run failed",
) -> None:
    """Spawn a daemon thread that runs ``coro_factory()`` to completion on a
    fresh event loop, then returns immediately.

    Any exception — including ``BaseException`` (SystemExit/KeyboardInterrupt) —
    is logged under ``error_label`` and never propagated out of the thread, so a
    failed run can't take the process down.
    """
    def _run() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro_factory())
        except BaseException:
            logger.exception(error_label)
        finally:
            loop.close()

    threading.Thread(target=_run, daemon=True, name=name).start()
