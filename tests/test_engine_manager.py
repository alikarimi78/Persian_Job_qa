"""`EngineManager.rebuild_async` — the part of it that is concurrency, not engines.

The build itself needs torch and a corpus and belongs in the REPL; what is testable here
is the promise the moderation queue now depends on. Approving a suggestion starts a
rebuild (`routers/admin.py`), and a queue is moderated one click after another, so a
request that arrives while a rebuild is running must not be dropped: the pass in flight
read the database before that row was committed, and dropping it would leave a record
approved but unsearchable until somebody pressed the button by hand.

`load` is replaced throughout — nothing here builds an engine.
"""

import threading

from src.engine_manager import EngineManager

TIMEOUT = 5  # generous: these waits are on an in-process event, not on any real work


def wait_until(predicate, timeout: float = TIMEOUT) -> bool:
    deadline = threading.Event()
    for _ in range(int(timeout * 100)):
        if predicate():
            return True
        deadline.wait(0.01)
    return predicate()


class FakeLoad:
    """Stands in for `EngineManager.load`, recording each pass and holding the first one
    open until the test lets go of it."""

    def __init__(self):
        self.calls: list[bool] = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, rebuild_embeddings: bool = False):
        self.calls.append(rebuild_embeddings)
        self.entered.set()
        # Only the first pass blocks; the queued one runs straight through.
        self.release.wait(TIMEOUT)


def test_a_rebuild_runs_and_reports_success():
    manager = EngineManager()
    manager.load = FakeLoad()
    manager.load.release.set()

    assert manager.rebuild_async() is True
    assert wait_until(lambda: not manager.rebuilding)
    assert manager.load.calls == [False]
    assert manager.last_result == "success"


def test_a_request_during_a_rebuild_is_queued_not_dropped():
    manager = EngineManager()
    manager.load = load = FakeLoad()

    assert manager.rebuild_async() is True
    assert load.entered.wait(TIMEOUT)

    # False means "not started now" — and is exactly the answer the approve handler
    # ignores, because the queued pass is what covers its record.
    assert manager.rebuild_async() is False
    assert manager.rebuild_async() is False       # a third click collapses into the same pass

    load.release.set()
    assert wait_until(lambda: not manager.rebuilding)
    assert load.calls == [False, False]           # two passes, not three and not one


def test_a_queued_force_is_not_downgraded():
    manager = EngineManager()
    manager.load = load = FakeLoad()

    manager.rebuild_async()                        # ordinary
    assert load.entered.wait(TIMEOUT)
    manager.rebuild_async(force_embeddings=True)   # arrives while it runs

    load.release.set()
    assert wait_until(lambda: not manager.rebuilding)
    assert load.calls == [False, True]


def test_a_failed_rebuild_still_clears_the_flag_and_runs_the_queued_pass():
    manager = EngineManager()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def failing_load(rebuild_embeddings: bool = False):
        calls.append(rebuild_embeddings)
        started.set()
        release.wait(TIMEOUT)
        raise RuntimeError("no approved job records")

    manager.load = failing_load

    manager.rebuild_async()
    assert started.wait(TIMEOUT)
    manager.rebuild_async()
    release.set()

    assert wait_until(lambda: not manager.rebuilding)
    assert len(calls) == 2
    # The old engine keeps serving and the reason is on the status endpoint, not in a 500.
    assert manager.last_result.startswith("failed: ")
