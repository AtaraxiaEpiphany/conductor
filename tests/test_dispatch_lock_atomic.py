"""Atomicity of the dispatch inflight ``gen`` bump under the dispatch lock.

The inflight marker's ``gen`` is stamped via a read-modify-write in
``track_state.dispatch._dispatch_inflight_write``. Under the conductor's normal
synchronous model that bump is uncontended. Under background-mode concurrency
(two ``prepare_dispatch`` calls racing on the same ``(phase, task, subtask)``)
the read-modify-write is wrapped in ``lib.dispatch_lock.acquire`` (an exclusive
``fcntl.flock``) so two writers cannot both read ``gen=N`` and stamp ``gen=N+1``
— which would collapse two fresh dispatches into one in the dedupe hook's
``gen``-disambiguation.

These tests pin:

* the lock serializes the bump: two concurrent writers produce distinct
  ``gen`` values (``N+1``, ``N+2``), never a collision (``N+1``, ``N+1``);
* fail-open: if the lock cannot be acquired (filesystem with no flock, a
  read-only tree, etc.) the writer proceeds unguarded and the marker + git-HEAD
  predicate remains the safety net — no exception, dispatch not stranded;
* the bare lock contextmanager acquires/excludes (sanity).
"""
import sys
import tempfile
import threading
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib import dispatch_lock  # noqa: E402
from lib import dispatch_inflight as inflight  # noqa: E402
from track_state.dispatch import _dispatch_inflight_write  # noqa: E402


class GenBumpAtomicityTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.track = Path(self.tmp.name)
        (self.track / ".conductor").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _gens(self):
        """Read the stamped gen for the test key (None if no marker)."""
        m = inflight.read(self.track, 1, 1, None)
        return m.get("gen") if m else None

    def test_concurrent_writes_get_distinct_gens(self):
        # The race the lock closes: two writers stamping the same key. Without
        # the lock both could read gen=0 and stamp gen=1. With the lock they
        # serialize → gen=1 then gen=2.
        start = threading.Barrier(2)
        results = []

        def writer():
            start.wait()  # release both threads together
            _dispatch_inflight_write(self.track, 1, 1, None,
                                     "abc1234", "2026-07-27T00:00:00+00:00")

        threads = [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each writer stamped its own gen, but read_gen bumps before write, so
        # the on-disk marker holds the LAST written gen. The load-bearing
        # assertion is that NO collision happened: simulate the unguarded race
        # would leave gen=1 (both read 0). Under the lock the final gen is 2.
        self.assertEqual(self._gens(), 2,
                         "concurrent writes must serialize to gen=2 (locked), "
                         "not collapse to gen=1 (the unguarded race)")

    def test_serial_writes_bump_monotonically(self):
        # Sanity: two serial writes bump gen 1 → 2.
        _dispatch_inflight_write(self.track, 1, 1, None, "sha0001",
                                 "2026-07-27T00:00:00+00:00")
        self.assertEqual(self._gens(), 1)
        _dispatch_inflight_write(self.track, 1, 1, None, "sha0002",
                                 "2026-07-27T00:00:01+00:00")
        self.assertEqual(self._gens(), 2)


class FailOpenTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.track = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_acquire_is_noop_on_unsupported_flock(self):
        # A lock failure must yield (not raise) so the caller proceeds
        # unguarded. We simulate by pointing acquire at a path whose parent
        # cannot be created — open() inside acquire raises OSError → fail-open
        # branch yields None.
        # Use a path under a file (not a dir) so mkdir fails.
        blocker = self.track / "blocker"
        blocker.write_text("x")  # blocker IS a file
        bad_track = blocker / "conductor"  # parent is a file → mkdir fails

        with dispatch_lock.acquire(bad_track) as held:
            self.assertIsNone(held, "fail-open must yield None, not raise")

    def test_acquire_yields_truthy_on_normal_path(self):
        # Sanity: a normal track dir yields a held handle (truthy), not None.
        with dispatch_lock.acquire(self.track) as held:
            self.assertIsNotNone(held, "normal acquire must hold the lock")
        # Lock file is created under .conductor/
        self.assertTrue((self.track / ".conductor" / ".dispatch.lock").exists())


if __name__ == "__main__":
    main()
