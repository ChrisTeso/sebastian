from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest

from sebastian.contracts import Channel, Destination, Event
from sebastian.ledger import JobMetadata, Ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'private' / 'queue.sqlite'
        self.now = 2000000000.0
        self.ledger = Ledger(self.path, clock=lambda: self.now)
        self.destination = Destination(Channel.MESSAGES, 'account', 'conversation')

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def event(self, text='SYNTHETIC_PRIVATE_TEXT_a91dcb', **changes):
        values = dict(channel=Channel.MESSAGES, account_id='account', sender_id='owner',
                      conversation_id='conversation',thread_id=None,event_id='event',
                      occurred_at=datetime.fromtimestamp(self.now,timezone.utc),text=text,is_from_me=True)
        values.update(changes)
        return Event(**values)

    def accept(self, **changes):
        return self.ledger.accept(JobMetadata.from_event(self.event(**changes)))

    def running(self):
        job = self.accept()
        self.assertEqual(self.ledger.claim().job_id,job.job_id)
        return self.ledger.start(job.job_id)

    def test_dedup_atomic_checkpoint(self):
        meta = JobMetadata.from_event(self.event())
        a = self.ledger.accept(meta,('messages',42))
        b = self.ledger.accept(meta,('messages',43))
        self.assertEqual(a.job_id,b.job_id)
        self.assertEqual(self.ledger.get_checkpoint('messages'),43)
        self.ledger.set_checkpoint('messages',40)
        self.assertEqual(self.ledger.get_checkpoint('messages'),43)
        other = JobMetadata.from_event(self.event(event_id='other'))
        with self.assertRaises(ValueError):
            self.ledger.accept(other,('messages',-1))
        self.assertEqual(self.ledger.stats()['jobs'],{'queued':1})

    def test_crash_never_replays_running(self):
        job = self.running()
        self.ledger.close()
        self.ledger = Ledger(self.path,clock=lambda:self.now)
        self.assertEqual(self.ledger.recover()[0].job_id,job.job_id)
        self.assertIsNone(self.ledger.claim())
        self.assertEqual(self.ledger.get_job(job.job_id).status,'interrupted')

    def test_lease_recovery_staleness_and_expired_start(self):
        job = self.accept()
        self.ledger.claim()
        self.now += 61
        with self.assertRaises(ValueError):
            self.ledger.start(job.job_id)
        self.ledger.recover()
        self.assertEqual(self.ledger.claim().job_id,job.job_id)
        self.now += 600
        self.ledger.recover()
        self.assertIsNone(self.ledger.claim())
        self.assertEqual(self.ledger.pending_interrupted()[0].status,'interrupted')

    def test_bounded_retries(self):
        job = self.accept()
        for n in range(3):
            self.ledger.claim()
            self.assertEqual(self.ledger.defer(job.job_id,1).retries,n+1)
            self.assertIsNone(self.ledger.claim())
            self.now += 1
        self.ledger.claim()
        self.assertEqual(self.ledger.defer(job.job_id,1).status,'interrupted')
        self.assertIsNone(self.ledger.claim())

    def test_discard_only_leased(self):
        job = self.accept()
        with self.assertRaises(ValueError):
            self.ledger.discard(job.job_id)
        self.ledger.claim()
        self.assertEqual(self.ledger.discard(job.job_id).status,'ignored')
        self.assertIsNone(self.ledger.claim())
        self.assertEqual(self.ledger.recover(),())

    def test_proven_receipt_reconciliation(self):
        job = self.running()
        part = self.ledger.plan(job.job_id,self.destination,'reply',0)
        self.ledger.attempting(part.part_id)
        self.ledger.uncertain(part.part_id)
        self.assertEqual(self.ledger.reconcile(part.part_id,'proven-receipt').status,'confirmed')

    def test_running_cannot_defer(self):
        job = self.running()
        with self.assertRaises(ValueError):
            self.ledger.defer(job.job_id,1)
    def test_confirmed_reply_requires_exact_receipt_and_scope(self):
        job=self.running()
        part=self.ledger.plan(job.job_id,self.destination,'reply',0)
        self.assertFalse(self.ledger.is_confirmed_reply(self.destination,'receipt'))
        self.ledger.attempting(part.part_id)
        self.ledger.confirmed(part.part_id,'receipt')
        self.now+=86400*7
        self.assertTrue(self.ledger.is_confirmed_reply(self.destination,'receipt'))
        self.assertFalse(self.ledger.is_confirmed_reply(self.destination,'other'))
        for destination in (Destination(Channel.MESSAGES,'account','other'),
                            Destination(Channel.MESSAGES,'other','conversation'),
                            Destination(Channel.SLACK,'account','conversation'),
                            Destination(Channel.MESSAGES,'account','conversation','thread')):
            self.assertFalse(self.ledger.is_confirmed_reply(destination,'receipt'))

    def test_partial_delivery_crash_and_no_uncertain_retry(self):
        job = self.running()
        a = self.ledger.plan(job.job_id,self.destination,'part one',0)
        b = self.ledger.plan(job.job_id,self.destination,'part two',1)
        self.ledger.attempting(a.part_id)
        self.ledger.confirmed(a.part_id,'provider-id')
        self.ledger.attempting(b.part_id)
        self.ledger.recover()
        self.assertEqual([d.status for d in self.ledger.deliveries(job.job_id)],['confirmed','uncertain'])
        with self.assertRaises(ValueError):
            self.ledger.attempting(b.part_id)
        self.ledger.complete(job.job_id)
        self.assertEqual(self.ledger.pending_interrupted(),())

    def test_echo_scope_fingerprint_ttl_and_private_storage(self):
        job = self.running()
        event = self.event()
        part = self.ledger.plan(job.job_id,self.destination,event.text,0)
        self.assertTrue(self.ledger.is_self_reply(event))
        self.assertFalse(self.ledger.is_self_reply(self.event(is_from_me=False)))
        self.assertFalse(self.ledger.is_self_reply(self.event(conversation_id='elsewhere')))
        self.assertFalse(self.ledger.is_self_reply(self.event(account_id='different')))
        self.assertFalse(self.ledger.is_self_reply(self.event(text='other body')))
        self.ledger.attempting(part.part_id)
        self.ledger.uncertain(part.part_id)
        self.assertTrue(self.ledger.is_self_reply(event))
        self.now += 3601
        self.assertFalse(self.ledger.is_self_reply(event))
        for path in self.path.parent.iterdir():
            self.assertNotIn(event.text.encode(),path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777,0o700)

    def test_immutable_plan_and_unresolved_completion(self):
        job = self.running()
        a = self.ledger.plan(job.job_id,self.destination,'first',0)
        self.assertEqual(self.ledger.plan(job.job_id,self.destination,'first',0),a)
        with self.assertRaises(ValueError):
            self.ledger.plan(job.job_id,self.destination,'different',0)
        with self.assertRaises(ValueError):
            self.ledger.complete(job.job_id)

    def test_recovery_abandons_unsent_parts(self):
        job = self.running()
        self.ledger.plan(job.job_id,self.destination,'lost ephemeral body',0)
        self.ledger.recover()
        self.assertEqual(self.ledger.deliveries(job.job_id)[0].status,'abandoned')
        self.ledger.complete(job.job_id)

    def test_capacity_does_not_advance_checkpoint(self):
        self.ledger.max_pending = 1
        self.accept()
        meta = JobMetadata.from_event(self.event(event_id='overflow'))
        with self.assertRaises(RuntimeError):
            self.ledger.accept(meta,('messages',99))
        self.assertIsNone(self.ledger.get_checkpoint('messages'))
        self.assertEqual(self.ledger.stats()['jobs'],{'queued':1})

    def uncertain_part(self, job, text='reply', index=0):
        part = self.ledger.plan(job.job_id,self.destination,text,index)
        self.ledger.attempting(part.part_id)
        return self.ledger.uncertain(part.part_id)

    def test_observed_exact_uncertain_receipt(self):
        job = self.running()
        part = self.uncertain_part(job)
        event = self.event(text='reply',event_id='observed-outgoing')
        result = self.ledger.observe_self_reply(event)
        self.assertEqual(len(result),1)
        self.assertEqual((result[0].part_id,result[0].status,result[0].receipt),
                         (part.part_id,'confirmed','observed-outgoing'))
        self.assertEqual(self.ledger.observe_self_reply(event),())
        self.assertEqual(self.ledger.uncertain_deliveries(),())

    def test_ambiguous_identical_parts_do_not_reconcile(self):
        job = self.running()
        self.uncertain_part(job,index=0)
        self.uncertain_part(job,index=1)
        self.assertEqual(self.ledger.observe_self_reply(self.event(text='reply')),())
        self.assertEqual(len(self.ledger.uncertain_deliveries()),2)

    def test_observed_old_or_incoming_or_wrong_scope_cannot_reconcile(self):
        job = self.running()
        self.uncertain_part(job)
        old = self.event(text='reply',occurred_at=datetime.fromtimestamp(self.now-3,timezone.utc))
        self.assertEqual(self.ledger.observe_self_reply(old),())
        self.assertFalse(self.ledger.is_self_reply(old))
        late = self.event(text='reply',occurred_at=datetime.fromtimestamp(self.now+3601,timezone.utc))
        self.assertEqual(self.ledger.observe_self_reply(late),())
        self.assertFalse(self.ledger.is_self_reply(late))
        for changes in ({'is_from_me':False},{'account_id':'wrong'},
                        {'conversation_id':'wrong'},{'thread_id':'wrong'}):
            self.assertEqual(self.ledger.observe_self_reply(self.event(text='reply',**changes)),())
        self.assertEqual(len(self.ledger.uncertain_deliveries()),1)

    def test_observe_never_mutates_pending_or_attempting(self):
        job = self.running()
        part = self.ledger.plan(job.job_id,self.destination,'reply',0)
        self.assertEqual(self.ledger.observe_self_reply(self.event(text='reply')),())
        self.assertEqual(self.ledger.deliveries(job.job_id)[0].status,'pending')
        self.ledger.attempting(part.part_id)
        self.assertEqual(self.ledger.observe_self_reply(self.event(text='reply')),())
        self.assertEqual(self.ledger.deliveries(job.job_id)[0].status,'attempting')

    def test_observed_receipt_cannot_confirm_two_parts(self):
        job = self.running()
        self.uncertain_part(job,text='first',index=0)
        self.uncertain_part(job,text='second',index=1)
        self.assertEqual(len(self.ledger.observe_self_reply(self.event(text='first',event_id='receipt'))),1)
        self.assertEqual(self.ledger.observe_self_reply(self.event(text='second',event_id='receipt')),())
        self.assertEqual(len(self.ledger.uncertain_deliveries()),1)

    def test_abandon_only_pending(self):
        job = self.running()
        part = self.ledger.plan(job.job_id,self.destination,'reply',0)
        self.assertEqual(self.ledger.abandon(part.part_id).status,'abandoned')
        with self.assertRaises(ValueError):
            self.ledger.attempting(part.part_id)
        self.ledger.complete(job.job_id)

    def test_uncertain_lookup_bounded_and_recent(self):
        job = self.running()
        self.uncertain_part(job,index=0)
        self.uncertain_part(job,index=1)
        self.assertEqual(len(self.ledger.uncertain_deliveries(limit=1)),1)
        for limit in (0,-1,1001,True):
            with self.assertRaises(ValueError):
                self.ledger.uncertain_deliveries(limit)
        self.now += 3601
        self.assertEqual(self.ledger.uncertain_deliveries(),())
        self.assertEqual(self.ledger.observe_self_reply(self.event(text='reply')),())

    def test_missing_key_refuses_existing_database(self):
        self.path.with_suffix('.sqlite.key').unlink()
        with self.assertRaises(ValueError):
            Ledger(self.path)


if __name__ == '__main__':
    unittest.main()
