"""Metadata-only durable work and delivery state; body content stays ephemeral."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import threading
import time

from .contracts import Channel, Destination, Event, identifier, timestamp


@dataclass(frozen=True)
class JobMetadata:
    channel: Channel
    account_id: str
    conversation_id: str
    thread_id: str | None
    event_id: str
    occurred_at: datetime
    ingressed_at: datetime
    provider_rowid: int | None = None

    def __post_init__(self):
        Destination(self.channel, self.account_id, self.conversation_id, self.thread_id)
        identifier(self.event_id)
        timestamp(self.occurred_at)
        timestamp(self.ingressed_at)
        if self.provider_rowid is not None and (type(self.provider_rowid) is not int or self.provider_rowid < 0):
            raise ValueError('invalid provider row id')

    @classmethod
    def from_event(cls, event, ingressed_at=None, provider_rowid=None):
        return cls(event.channel, event.account_id, event.conversation_id, event.thread_id,
                   event.event_id, event.occurred_at, ingressed_at or datetime.now(timezone.utc), provider_rowid)

    @property
    def job_id(self):
        return hashlib.sha256(json.dumps([self.channel, self.account_id, self.conversation_id,
                                         self.thread_id, self.event_id]).encode()).hexdigest()


@dataclass(frozen=True)
class Job:
    job_id: str
    metadata: JobMetadata
    status: str
    retries: int


@dataclass(frozen=True)
class Delivery:
    part_id: str
    job_id: str
    index: int
    destination: Destination
    status: str
    receipt: str | None


class Ledger:
    def __init__(self, path, *, clock=time.time, stale_after=600, lease_seconds=60,
                 max_retries=3, echo_ttl=3600, max_pending=1000):
        if not (0 < stale_after <= 86400 and 0 < lease_seconds <= 600 and
                0 <= max_retries <= 3 and 0 < echo_ttl <= 86400 and
                type(max_pending) is int and 0 < max_pending <= 100000):
            raise ValueError('invalid ledger limits')
        self.clock, self.stale_after, self.lease_seconds = clock, stale_after, lease_seconds
        self.max_retries, self.echo_ttl = max_retries, echo_ttl
        self.max_pending = max_pending
        self.lock = threading.RLock()
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._private(path.parent, directory=True)
        key_path = path.with_suffix(path.suffix + '.key')
        for target in (path, key_path):
            if target.is_symlink():
                raise ValueError('unsafe ledger path')
        if not key_path.exists():
            if path.exists() and path.stat().st_size:
                raise ValueError('ledger key missing')
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(os.urandom(32))
                stream.flush()
                os.fsync(stream.fileno())
        self._private(key_path)
        self.key = key_path.read_bytes()
        if len(self.key) != 32:
            raise ValueError('invalid ledger key')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self._private(path)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, channel TEXT, account TEXT, conversation TEXT, thread TEXT,
          event TEXT, occurred REAL, ingressed REAL, rowid_hint INTEGER,
          status TEXT, retries INTEGER, available REAL, lease REAL);
        CREATE TABLE IF NOT EXISTS checkpoints (key TEXT PRIMARY KEY, value INTEGER);
        CREATE TABLE IF NOT EXISTS job_lanes (id TEXT PRIMARY KEY, lane TEXT);
        CREATE TABLE IF NOT EXISTS deliveries (
          id TEXT PRIMARY KEY, job TEXT REFERENCES jobs(id), part INTEGER,
          channel TEXT, account TEXT, conversation TEXT, thread TEXT,
          fingerprint TEXT, status TEXT, receipt TEXT, created REAL,
          UNIQUE(job,part));
        ''')

    @staticmethod
    def _private(path, directory=False):
        st = path.lstat()
        import stat
        if st.st_uid != os.getuid() or not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)):
            raise ValueError('unsafe ledger storage')
        os.chmod(path, 0o700 if directory else 0o600)

    @contextmanager
    def _tx(self):
        with self.lock:
            try:
                self.db.execute('BEGIN IMMEDIATE')
                yield
                self.db.execute('COMMIT')
            except Exception as exc:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                if isinstance(exc, sqlite3.Error):
                    raise RuntimeError('ledger operation failed') from None
                raise

    def close(self):
        with self.lock:
            self.db.close()

    def _job(self, row):
        if row is None:
            raise ValueError('unknown job')
        m = JobMetadata(Channel(row['channel']), row['account'], row['conversation'], row['thread'],
                        row['event'], datetime.fromtimestamp(row['occurred'], timezone.utc),
                        datetime.fromtimestamp(row['ingressed'], timezone.utc), row['rowid_hint'])
        return Job(row['id'], m, row['status'], row['retries'])

    def get_job(self, job_id):
        with self._tx():
            return self._job(self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def _checkpoint(self, key, value):
        identifier(key)
        if type(value) is not int or value < 0:
            raise ValueError('invalid checkpoint')
        self.db.execute('INSERT INTO checkpoints VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=MAX(value,excluded.value)', (key,value))

    def accept(self, metadata, checkpoint=None, *, lane='normal'):
        if not isinstance(metadata, JobMetadata):
            raise ValueError('invalid job metadata')
        if lane not in ('normal', 'image'):
            raise ValueError('invalid job lane')
        m = metadata
        with self._tx():
            existing = self.db.execute('SELECT 1 FROM jobs WHERE id=?',(m.job_id,)).fetchone()
            if existing is None and self.db.execute("SELECT COUNT(*) FROM jobs WHERE status NOT IN ('complete','ignored')").fetchone()[0] >= self.max_pending:
                raise RuntimeError('queue capacity reached')
            self.db.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (m.job_id,m.channel,m.account_id,m.conversation_id,m.thread_id,m.event_id,
                 m.occurred_at.timestamp(),m.ingressed_at.timestamp(),m.provider_rowid,'queued',0,self.clock(),None))
            if existing is None:
                self.db.execute('INSERT INTO job_lanes VALUES (?,?)', (m.job_id, lane))
            if checkpoint is not None:
                self._checkpoint(*checkpoint)
            return self._job(self.db.execute('SELECT * FROM jobs WHERE id=?',(m.job_id,)).fetchone())

    def set_checkpoint(self, key, value):
        with self._tx():
            self._checkpoint(key,value)

    def get_checkpoint(self, key):
        with self._tx():
            row = self.db.execute('SELECT value FROM checkpoints WHERE key=?',(key,)).fetchone()
            return row[0] if row else None

    def _expire(self):
        # A leased job still belongs to its worker, even after its input expires.
        # Only that worker's recovery (or startup recovery) may release ownership.
        self.db.execute("UPDATE jobs SET status='interrupted',lease=NULL WHERE status='queued' AND occurred<?", (self.clock()-self.stale_after,))

    def claim(self, lane=None):
        if lane is not None and lane not in ('normal', 'image'):
            raise ValueError('invalid job lane')
        with self._tx():
            self._expire()
            row = self.db.execute("""
                SELECT candidate.* FROM jobs AS candidate
                WHERE candidate.status='queued' AND candidate.available<=?
                  AND (? IS NULL OR COALESCE(
                    (SELECT lane FROM job_lanes WHERE id=candidate.id),'normal')=?)
                  AND NOT EXISTS (
                    SELECT 1 FROM jobs AS blocker
                    WHERE blocker.channel=candidate.channel
                      AND blocker.account=candidate.account
                      AND blocker.conversation=candidate.conversation
                      AND blocker.thread IS candidate.thread
                      AND (blocker.status IN ('leased','running','interrupted')
                        OR (blocker.status='queued' AND
                          (blocker.ingressed,blocker.id)<(candidate.ingressed,candidate.id))))
                ORDER BY candidate.ingressed,candidate.id LIMIT 1
                """, (self.clock(), lane, lane)).fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE jobs SET status='leased',lease=? WHERE id=?",(self.clock()+self.lease_seconds,row['id']))
            return self._job(self.db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone())

    def claim_interrupted(self, lane=None):
        """Atomically own one interruption notice, before queued work in its scope."""
        if lane is not None and lane not in ('normal', 'image'):
            raise ValueError('invalid job lane')
        with self._tx():
            self._expire()
            row = self.db.execute("""
                SELECT candidate.* FROM jobs AS candidate
                WHERE candidate.status='interrupted'
                  AND (? IS NULL OR COALESCE(
                    (SELECT lane FROM job_lanes WHERE id=candidate.id),'normal')=?)
                  AND NOT EXISTS (
                    SELECT 1 FROM jobs AS blocker
                    WHERE blocker.channel=candidate.channel
                      AND blocker.account=candidate.account
                      AND blocker.conversation=candidate.conversation
                      AND blocker.thread IS candidate.thread
                      AND (blocker.status IN ('leased','running')
                        OR (blocker.status='interrupted' AND
                          (blocker.ingressed,blocker.id)<(candidate.ingressed,candidate.id))))
                ORDER BY candidate.ingressed,candidate.id LIMIT 1
                """, (lane, lane)).fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE jobs SET status='running',lease=NULL WHERE id=?", (row['id'],))
            return self._job(self.db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())

    def start(self, job_id):
        with self._tx():
            self._expire()
            row = self.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if (row is None or row['status'] != 'leased' or row['lease'] <= self.clock()
                    or row['occurred'] < self.clock()-self.stale_after):
                raise ValueError('job is not actively leased')
            self.db.execute("UPDATE jobs SET status='running',lease=NULL WHERE id=?",(job_id,))
        return self.get_job(job_id)

    def defer(self, job_id, delay):
        if not 0 <= delay <= self.stale_after:
            raise ValueError('invalid retry delay')
        with self._tx():
            row = self.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row is None or row['status'] != 'leased':
                raise ValueError('only leased jobs may retry')
            exhausted = row['retries'] >= self.max_retries or row['occurred']+self.stale_after <= self.clock()+delay
            self.db.execute('UPDATE jobs SET status=?,retries=?,available=?,lease=NULL WHERE id=?',
                ('interrupted' if exhausted else 'queued',row['retries'] if exhausted else row['retries']+1,self.clock()+delay,job_id))
        return self.get_job(job_id)

    def discard(self, job_id):
        """Retire a leased event no longer eligible for processing."""
        with self._tx():
            row = self.db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row is None or row[0] != 'leased':
                raise ValueError('only leased jobs may be discarded')
            if self.db.execute('SELECT 1 FROM deliveries WHERE job=?',(job_id,)).fetchone():
                raise ValueError('delivery already planned')
            self.db.execute("UPDATE jobs SET status='ignored',lease=NULL WHERE id=?",(job_id,))
        return self.get_job(job_id)

    def recover(self):
        """Startup-only: do not call while another worker owns live jobs."""
        with self._tx():
            self.db.execute("UPDATE deliveries SET status='uncertain' WHERE status='attempting'")
            self.db.execute("UPDATE deliveries SET status='abandoned' WHERE status='pending'")
            self.db.execute("UPDATE jobs SET status='interrupted',lease=NULL WHERE status='running'")
            self.db.execute("UPDATE jobs SET status='queued',lease=NULL WHERE status='leased'")
            self._expire()
        return self.pending_interrupted()

    def recover_job(self, job_id):
        """Recover only the supplied worker-owned job, without touching other work."""
        with self._tx():
            row = self.db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None or row['status'] not in ('leased','running'):
                raise ValueError('only active jobs may be recovered')
            self.db.execute("UPDATE deliveries SET status='uncertain' WHERE job=? AND status='attempting'", (job_id,))
            self.db.execute("UPDATE deliveries SET status='abandoned' WHERE job=? AND status='pending'", (job_id,))
            self.db.execute('UPDATE jobs SET status=?,lease=NULL WHERE id=?',
                            ('interrupted' if row['status'] == 'running' else 'queued', job_id))
            return self._job(self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def pending_interrupted(self):
        with self._tx():
            return tuple(self._job(r) for r in self.db.execute("SELECT * FROM jobs WHERE status='interrupted' ORDER BY ingressed"))

    def _fingerprint(self, text):
        if not isinstance(text, str):
            raise ValueError('invalid delivery text')
        return hmac.new(self.key,text.encode(),hashlib.sha256).hexdigest()

    def _delivery(self, row):
        if row is None:
            raise ValueError('unknown delivery')
        return Delivery(row['id'],row['job'],row['part'],Destination(Channel(row['channel']),row['account'],
                        row['conversation'],row['thread']),row['status'],row['receipt'])

    def plan(self, job_id, destination, text, index):
        if type(index) is not int or not 0 <= index < 10000 or not isinstance(destination, Destination):
            raise ValueError('invalid delivery plan')
        fingerprint = self._fingerprint(text)
        part_id = hashlib.sha256(f'{job_id}:{index}'.encode()).hexdigest()
        d = destination
        with self._tx():
            job = self.db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
            if job is None or job[0] not in ('running','interrupted'):
                raise ValueError('job cannot plan delivery')
            self.db.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (part_id,job_id,index,d.channel,d.account_id,d.conversation_id,d.thread_id,fingerprint,'pending',None,self.clock()))
            row = self.db.execute('SELECT * FROM deliveries WHERE id=?',(part_id,)).fetchone()
            result = self._delivery(row)
            if row['fingerprint'] != fingerprint or result.destination != destination:
                raise ValueError('delivery plan conflicts with existing part')
            return result

    def _transition(self, part_id, old, new, receipt=None):
        with self._tx():
            row = self.db.execute('SELECT * FROM deliveries WHERE id=?',(part_id,)).fetchone()
            if row is None or row['status'] != old:
                raise ValueError('invalid delivery transition')
            self.db.execute('UPDATE deliveries SET status=?,receipt=? WHERE id=?',(new,receipt,part_id))
            return self._delivery(self.db.execute('SELECT * FROM deliveries WHERE id=?',(part_id,)).fetchone())

    def abandon(self, part_id):
        return self._transition(part_id,'pending','abandoned')

    def attempting(self, part_id):
        return self._transition(part_id,'pending','attempting')

    def confirmed(self, part_id, receipt):
        identifier(receipt)
        return self._transition(part_id,'attempting','confirmed',receipt)

    def reconcile(self, part_id, receipt):
        """Caller must independently prove a receipt belongs to this exact part."""
        identifier(receipt)
        return self._transition(part_id,'uncertain','confirmed',receipt)

    def uncertain(self, part_id):
        return self._transition(part_id,'attempting','uncertain')

    def deliveries(self, job_id):
        with self._tx():
            return tuple(self._delivery(r) for r in self.db.execute('SELECT * FROM deliveries WHERE job=? ORDER BY part',(job_id,)))

    def complete(self, job_id):
        with self._tx():
            row = self.db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row is None or row[0] not in ('running','interrupted'):
                raise ValueError('job cannot complete')
            if self.db.execute("SELECT 1 FROM deliveries WHERE job=? AND status IN ('pending','attempting')",(job_id,)).fetchone():
                raise ValueError('delivery remains unresolved')
            self.db.execute("UPDATE jobs SET status='complete' WHERE id=?",(job_id,))

    def uncertain_deliveries(self, limit=100):
        """Return a bounded recent set for provider-side receipt lookup."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('invalid reconciliation limit')
        with self._tx():
            return tuple(self._delivery(row) for row in self.db.execute(
                "SELECT * FROM deliveries WHERE status='uncertain' AND created>=? ORDER BY created DESC,id LIMIT ?",
                (self.clock()-self.echo_ttl,limit)))

    def _echo_params(self, event, marker=None):
        occurred = event.occurred_at.timestamp()
        return (event.channel,event.account_id,event.conversation_id,event.thread_id,
                self._fingerprint(event.text if marker is None else marker),self.clock()-self.echo_ttl,
                occurred+2,occurred-self.echo_ttl)

    def observe_self_reply(self, event: Event, marker=None):
        """Reconcile one unambiguous uncertain part; never mutate active sends."""
        if not event.is_from_me:
            return ()
        with self._tx():
            rows = self.db.execute(
                "SELECT * FROM deliveries WHERE channel=? AND account=? AND conversation=? AND thread IS ? "
                "AND fingerprint=? AND created>=? AND created<=? AND created>=? "
                "AND status='uncertain' LIMIT 2",self._echo_params(event,marker)).fetchall()
            if len(rows) != 1:
                return ()
            row = rows[0]
            if self.db.execute('SELECT 1 FROM deliveries WHERE receipt=? AND id!=? LIMIT 1',
                               (event.event_id,row['id'])).fetchone():
                return ()
            self.db.execute("UPDATE deliveries SET status='confirmed',receipt=? WHERE id=?",
                            (event.event_id,row['id']))
            return (self._delivery(self.db.execute('SELECT * FROM deliveries WHERE id=?',
                                                  (row['id'],)).fetchone()),)

    def is_self_reply(self, event: Event, marker=None):
        if not event.is_from_me:
            return False
        with self._tx():
            if self.db.execute(
                'SELECT 1 FROM deliveries WHERE channel=? AND account=? AND conversation=? AND thread IS ? AND receipt=? LIMIT 1',
                (event.channel,event.account_id,event.conversation_id,event.thread_id,event.event_id)).fetchone():return True
            return self.db.execute(
                "SELECT 1 FROM deliveries WHERE channel=? AND account=? AND conversation=? AND thread IS ? "
                "AND fingerprint=? AND created>=? AND created<=? AND created>=? "
                "AND status IN ('pending','attempting','confirmed','uncertain') LIMIT 1",
                self._echo_params(event,marker)).fetchone() is not None

    def is_confirmed_reply(self, destination: Destination, receipt: str) -> bool:
        """Exact durable provider receipt, independent of echo fingerprint TTL."""
        identifier(receipt)
        with self._tx():
            return self.db.execute(
                "SELECT 1 FROM deliveries WHERE channel=? AND account=? AND conversation=? "
                "AND thread IS ? AND receipt=? AND status='confirmed' LIMIT 1",
                (destination.channel, destination.account_id, destination.conversation_id,
                 destination.thread_id, receipt)).fetchone() is not None

    def stats(self):
        with self._tx():
            return {'jobs': dict(self.db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status')),
                    'deliveries': dict(self.db.execute('SELECT status,COUNT(*) FROM deliveries GROUP BY status'))}
