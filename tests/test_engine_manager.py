import threading

from src.engine_manager import EngineManager

TIMEOUT = 5


def wait_until(predicate, timeout: float = TIMEOUT) -> bool:
    deadline = threading.Event()
    for _ in range(int(timeout * 100)):
        if predicate():
            return True
        deadline.wait(0.01)
    return predicate()


class FakeLoad:
    def __init__(self):
        self.calls: list[bool] = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, rebuild_embeddings: bool = False):
        self.calls.append(rebuild_embeddings)
        self.entered.set()
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

    assert manager.rebuild_async() is False
    assert manager.rebuild_async() is False

    load.release.set()
    assert wait_until(lambda: not manager.rebuilding)
    assert load.calls == [False, False]


def test_a_queued_force_is_not_downgraded():
    manager = EngineManager()
    manager.load = load = FakeLoad()

    manager.rebuild_async()
    assert load.entered.wait(TIMEOUT)
    manager.rebuild_async(force_embeddings=True)

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
    assert manager.last_result.startswith("failed: ")
