"""Tests for core.update(): the race-free read-modify-write primitive.

Real contention on track-state.json is cross-process (concurrent `track-state`
invocations / hooks on the same track), so these tests use multiprocessing
rather than threads. The positive case asserts update() loses zero updates
under N concurrent writers; the negative control shows the load()/save() pair
—which releases the lock between read and write— does lose updates.
"""
import multiprocessing as mp
import shutil
import tempfile
import time
from unittest import TestCase

from scripts.track_state.core import load, save, update


def _bump(state):
    state["counter"] = state.get("counter", 0) + 1
    return state["counter"]


def _update_worker(track_dir, n, barrier):
    barrier.wait()
    for _ in range(n):
        update(track_dir, _bump)


def _racy_worker(track_dir, n, barrier):
    """The old load()/save() pattern: lock released between read and write."""
    barrier.wait()
    for _ in range(n):
        s = load(track_dir)
        time.sleep(0.001)  # widen the load→save gap to expose the TOCTOU window
        s["counter"] = s.get("counter", 0) + 1
        save(track_dir, s)


class TestUpdateRmw(TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        save(self.d, {"counter": 0})

    def test_update_persists_in_place_and_returns_value(self):
        # Single-threaded contract: fn mutates in place and its return is forwarded.
        first = update(self.d, _bump)
        second = update(self.d, _bump)
        self.assertEqual(first, 1)
        self.assertEqual(second, 2)
        self.assertEqual(load(self.d)["counter"], 2)

    def test_update_serializes_concurrent_writers(self):
        # N processes each increment under update(); no update may be lost.
        n_proc, n_inc = 8, 50
        barrier = mp.Barrier(n_proc)
        procs = [mp.Process(target=_update_worker, args=(self.d, n_inc, barrier))
                 for _ in range(n_proc)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        self.assertTrue(all(p.exitcode == 0 for p in procs), "a worker crashed")

        expected = n_proc * n_inc
        self.assertEqual(
            load(self.d)["counter"], expected,
            f"lost updates under update(): expected {expected}, got {load(self.d)['counter']}",
        )

    def test_racy_load_save_loses_updates(self):
        # Negative control: the load()/save() pair leaves a gap and loses updates
        # under the same contention. Proves the setup can detect lost updates, so
        # the update() test above is not vacuously green.
        n_proc, n_inc = 8, 50
        barrier = mp.Barrier(n_proc)
        procs = [mp.Process(target=_racy_worker, args=(self.d, n_inc, barrier))
                 for _ in range(n_proc)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()

        expected = n_proc * n_inc
        self.assertLess(
            load(self.d)["counter"], expected,
            "expected the racy load()/save() pattern to lose updates under contention",
        )
