from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import unittest

from sebastian.contracts import Channel, Destination
from sebastian.ledger import JobMetadata, Ledger


class ConcurrentLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'private' / 'queue.sqlite'
        self.now = 2000000000.0
        self.first = Ledger(self.path, clock=lambda: self.now)
        self.second = Ledger(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.second.close()
        self.first.close()
        self.temp.cleanup()

    def accept(self, event, *, conversation='conversation', account='account',
               thread=None, channel=Channel.MESSAGES, ingress=0, age=0, lane='normal'):
        return self.first.accept(JobMetadata(
            channel, account, conversation, thread, event,
            datetime.fromtimestamp(self.now-age, timezone.utc),
            datetime.fromtimestamp(self.now+ingress, timezone.utc)), lane=lane)

    def simultaneous(self, method='claim'):
        barrier = threading.Barrier(2)

        def claim(ledger):
            barrier.wait(timeout=5)
            return getattr(ledger, method)()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim, ledger) for ledger in (self.first, self.second)]
            return [future.result(timeout=10) for future in futures]

    def test_atomic_distinct_conversation_claims(self):
        one = self.accept('one')
        two = self.accept('two', conversation='other')
        claimed = self.simultaneous()
        self.assertEqual({job.job_id for job in claimed}, {one.job_id, two.job_id})
        self.assertTrue(all(job.status == 'leased' for job in claimed))

    def test_same_scope_leased_and_running_block_second_claim(self):
        one = self.accept('one')
        two = self.accept('two', ingress=1)
        claimed = self.simultaneous()
        self.assertEqual([job.job_id for job in claimed if job], [one.job_id])
        self.first.start(one.job_id)
        self.assertIsNone(self.second.claim())
        self.first.complete(one.job_id)
        self.assertEqual(self.second.claim().job_id, two.job_id)

    def test_deferred_job_blocks_later_same_scope_but_not_other_scope(self):
        first = self.accept('one')
        later = self.accept('two', ingress=1)
        other = self.accept('three', conversation='other', ingress=2)
        self.assertEqual(self.first.claim().job_id, first.job_id)
        self.first.defer(first.job_id, 10)
        self.assertEqual(self.second.claim().job_id, other.job_id)
        self.assertIsNone(self.first.claim())
        self.now += 10
        self.assertEqual(self.first.claim().job_id, first.job_id)
        self.assertEqual(self.first.get_job(later.job_id).status, 'queued')

    def test_tied_ingress_orders_by_id_even_when_first_deferred(self):
        jobs = [self.accept('one'), self.accept('two')]
        first, later = sorted(jobs, key=lambda job: job.job_id)
        self.assertEqual(self.first.claim().job_id, first.job_id)
        self.first.defer(first.job_id, 10)
        self.assertIsNone(self.second.claim())
        self.assertEqual(self.first.get_job(later.job_id).status, 'queued')

    def test_scope_uses_exact_channel_account_conversation_and_thread(self):
        variants = [{}, {'account': 'other'}, {'conversation': 'other'},
                    {'thread': 'thread'}, {'channel': Channel.SLACK}]
        jobs = [self.accept(str(index), **scope) for index, scope in enumerate(variants)]
        claims = [self.first.claim() for _ in jobs]
        self.assertEqual({job.job_id for job in claims}, {job.job_id for job in jobs})

    def test_recover_running_job_isolates_other_active_deliveries(self):
        one = self.accept('one')
        two = self.accept('two', conversation='other')
        self.simultaneous()
        for job in (one, two):
            self.first.start(job.job_id)
            destination = Destination(job.metadata.channel, job.metadata.account_id,
                                      job.metadata.conversation_id, job.metadata.thread_id)
            attempting = self.first.plan(job.job_id, destination, 'synthetic one', 0)
            self.first.plan(job.job_id, destination, 'synthetic two', 1)
            self.first.attempting(attempting.part_id)
        self.assertEqual(self.first.recover_job(one.job_id).status, 'interrupted')
        self.assertEqual([part.status for part in self.first.deliveries(one.job_id)],
                         ['uncertain', 'abandoned'])
        self.assertEqual(self.second.get_job(two.job_id).status, 'running')
        self.assertEqual([part.status for part in self.second.deliveries(two.job_id)],
                         ['attempting', 'pending'])

    def test_recover_leased_job_does_not_expire_other_worker(self):
        one = self.accept('one')
        two = self.accept('two', conversation='other')
        self.simultaneous()
        self.now += 601
        self.assertEqual(self.first.recover_job(one.job_id).status, 'queued')
        self.assertEqual(self.second.get_job(two.job_id).status, 'leased')
        with self.assertRaises(ValueError):
            self.first.recover_job(one.job_id)

    def test_other_worker_cannot_expire_owned_lease(self):
        # A still-valid lease can outlive the event's remaining freshness.
        owned = self.accept('owned', age=590)
        self.assertEqual(self.first.claim().job_id, owned.job_id)
        self.now += 11
        later = self.accept('later', ingress=1)
        other = self.accept('other', conversation='other', ingress=2)
        self.assertEqual(self.second.claim().job_id, other.job_id)
        self.assertIsNone(self.second.claim_interrupted())
        self.assertEqual(self.second.get_job(owned.job_id).status, 'leased')
        with self.assertRaises(ValueError):
            self.first.start(owned.job_id)
        self.assertEqual(self.first.get_job(owned.job_id).status, 'leased')
        self.assertEqual(self.first.recover_job(owned.job_id).status, 'queued')
        interruption = self.second.claim_interrupted()
        self.assertEqual((interruption.job_id, interruption.status), (owned.job_id, 'running'))
        self.assertEqual(self.first.get_job(later.job_id).status, 'queued')
        self.assertIsNone(self.first.claim())
        self.second.complete(owned.job_id)
        self.assertEqual(self.first.claim().job_id, later.job_id)

    def test_interrupted_claim_is_atomic_and_blocks_normal_work(self):
        one = self.accept('one', age=601)
        two = self.accept('two', ingress=1)
        self.assertIsNone(self.first.claim())
        claims = self.simultaneous('claim_interrupted')
        self.assertEqual([job.job_id for job in claims if job], [one.job_id])
        self.assertEqual(self.first.get_job(one.job_id).status, 'running')
        self.assertIsNone(self.second.claim())
        self.first.complete(one.job_id)
        self.assertEqual(self.second.claim().job_id, two.job_id)

    def test_interrupted_fifo_and_other_scope_progress(self):
        one = self.accept('one', age=601)
        two = self.accept('two', age=601, ingress=1)
        other = self.accept('other', age=601, conversation='other', ingress=2)
        claims = self.simultaneous('claim_interrupted')
        self.assertEqual({job.job_id for job in claims}, {one.job_id, other.job_id})
        self.assertIsNone(self.first.claim_interrupted())
        self.first.complete(one.job_id)
        self.assertEqual(self.second.claim_interrupted().job_id, two.job_id)

    def test_interrupted_waits_for_active_same_scope(self):
        one = self.accept('one')
        self.first.claim()
        self.first.start(one.job_id)
        two = self.accept('two', age=601, ingress=1)
        self.assertIsNone(self.second.claim_interrupted())
        self.first.complete(one.job_id)
        self.assertEqual(self.second.claim_interrupted().job_id, two.job_id)

    def test_newer_interruption_does_not_deadlock_older_queue(self):
        one = self.accept('one')
        two = self.accept('two', age=601, ingress=1)
        self.assertIsNone(self.first.claim())
        self.assertEqual(self.second.claim_interrupted().job_id, two.job_id)
        self.second.complete(two.job_id)
        self.assertEqual(self.first.claim().job_id, one.job_id)

    def test_lane_reservation_and_cross_lane_fifo(self):
        image = self.accept('image', lane='image')
        later_normal = self.accept('normal', ingress=1)
        other_normal = self.accept('other', conversation='other', ingress=2)
        self.assertEqual(self.first.claim(lane='normal').job_id, other_normal.job_id)
        self.assertIsNone(self.first.claim(lane='normal'))
        self.assertEqual(self.second.claim(lane='image').job_id, image.job_id)
        self.second.start(image.job_id)
        self.second.complete(image.job_id)
        self.assertEqual(self.first.claim(lane='normal').job_id, later_normal.job_id)
        self.assertIsNone(self.second.claim(lane='image'))

    def test_duplicate_cannot_change_lane(self):
        original = self.accept('image', lane='image')
        self.first.accept(original.metadata, lane='normal')
        self.assertIsNone(self.first.claim(lane='normal'))
        self.assertEqual(self.second.claim(lane='image').job_id, original.job_id)

    def test_legacy_rows_default_normal_and_duplicate_cannot_reclassify(self):
        legacy = self.accept('legacy')
        with self.first._tx():
            self.first.db.execute('DELETE FROM job_lanes WHERE id=?', (legacy.job_id,))
        self.second.accept(legacy.metadata, lane='image')
        self.assertIsNone(self.first.claim(lane='image'))
        self.assertEqual(self.second.claim(lane='normal').job_id, legacy.job_id)

    def test_interrupted_lane_reservation_respects_cross_lane_fifo(self):
        image = self.accept('image', lane='image', age=601)
        normal = self.accept('normal', ingress=1, age=601)
        other = self.accept('other', conversation='other', ingress=2, age=601)
        self.assertEqual(self.first.claim_interrupted(lane='normal').job_id, other.job_id)
        self.assertIsNone(self.first.claim_interrupted(lane='normal'))
        self.assertEqual(self.second.claim_interrupted(lane='image').job_id, image.job_id)
        self.second.complete(image.job_id)
        self.assertEqual(self.first.claim_interrupted(lane='normal').job_id, normal.job_id)

    def test_invalid_lane_rejected_without_acceptance(self):
        for lane in ('other', 0, True, []):
            with self.assertRaises(ValueError):
                self.accept('invalid', lane=lane)
            with self.assertRaises(ValueError):
                self.first.claim(lane=lane)
            with self.assertRaises(ValueError):
                self.first.claim_interrupted(lane=lane)
        with self.assertRaises(ValueError):
            self.accept('invalid', lane=None)
        self.assertEqual(self.first.stats()['jobs'], {})


if __name__ == '__main__':
    unittest.main()
