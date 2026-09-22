"""Persistent deadlines driven by one Tk timer on Windows, macOS, and Linux."""
from datetime import datetime, timedelta
import math
import sqlite3
import time


class Scheduler:
    def __init__(self, path, notify, clock=time.time):
        self.notify, self.clock = notify, clock
        self.timer = None
        self.timer_id = None
        self.error = None
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('''CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, due REAL NOT NULL,
            daily INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
            anchor REAL, interval_seconds INTEGER NOT NULL DEFAULT 0,
            sound_count INTEGER NOT NULL DEFAULT 1, priority TEXT NOT NULL DEFAULT 'Normal')''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(reminders)')}
        for name, kind in [('anchor', 'REAL'), ('interval_seconds', 'INTEGER NOT NULL DEFAULT 0'), ('sound_count', 'INTEGER NOT NULL DEFAULT 1'), ('priority', "TEXT NOT NULL DEFAULT 'Normal'")]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE reminders ADD COLUMN {name} {kind}')
        self.db.execute('UPDATE reminders SET anchor=due WHERE anchor IS NULL')
        self.db.execute('UPDATE reminders SET sound_count=1 WHERE sound_count IS NULL')
        self.db.execute("UPDATE reminders SET priority='Normal' WHERE priority IS NULL OR priority = ''")
        self.db.execute('CREATE INDEX IF NOT EXISTS due_idx ON reminders(status, due)')
        self.db.commit()

    def save(self, title, due, daily=False, interval=0, reminder_id=None, sound_count=1, priority='Normal'):
        title = title.strip()
        if not title or len(title) > 240:
            raise ValueError('Enter a reminder between 1 and 240 characters.')
        if not math.isfinite(due) or not 0 < due < 253402214400:
            raise ValueError('Choose a valid date and time.')
        if interval < 0 or (daily and interval):
            raise ValueError('Choose one repeat option.')
        valid_priorities = {'Low', 'Normal', 'High'}
        priority = str(priority or 'Normal').strip().title()
        if priority not in valid_priorities:
            priority = 'Normal'
        sound_count = max(1, min(20, int(sound_count)))
        with self.db:
            values = (title, due, int(daily), due, interval, sound_count, priority)
            if reminder_id is None:
                cursor = self.db.execute('''INSERT INTO reminders(title,due,daily,anchor,interval_seconds,sound_count,priority)
                                            VALUES(?,?,?,?,?,?,?)''', values)
                reminder_id = cursor.lastrowid
            else:
                self.db.execute("""UPDATE reminders SET title=?,due=?,daily=?,anchor=?,interval_seconds=?,sound_count=?,priority=?,
                                   status='active' WHERE id=?""", (*values, reminder_id))
        self._arm()
        return reminder_id

    def add(self, title, due, daily=False, interval=0, sound_count=1, priority='Normal'):
        return self.save(title, due, daily, interval, sound_count=sound_count, priority=priority)

    def rows(self):
        return self.db.execute('SELECT id,title,due,daily,status,interval_seconds FROM reminders ORDER BY due').fetchall()

    def sound_count_for(self, reminder_id):
        row = self.db.execute('SELECT sound_count FROM reminders WHERE id=?', (reminder_id,)).fetchone()
        return int(row[0]) if row else 1

    def priority_for(self, reminder_id):
        row = self.db.execute('SELECT priority FROM reminders WHERE id=?', (reminder_id,)).fetchone()
        return row[0] if row else 'Normal'

    def delete(self, reminder_id):
        with self.db:
            self.db.execute('DELETE FROM reminders WHERE id=?', (reminder_id,))
        self._arm()

    def respond(self, reminder_id, snooze=False):
        with self.db:
            row = self.db.execute("SELECT anchor,daily,interval_seconds FROM reminders WHERE id=? AND status='pending'",
                                  (reminder_id,)).fetchone()
            if row is None:
                return
            anchor, daily, interval = row
            now = self.clock()
            if snooze:
                self.db.execute("UPDATE reminders SET due=?,status='active' WHERE id=?", (now + 600, reminder_id))
            elif daily or interval:
                if daily:
                    target = datetime.fromtimestamp(anchor)
                    local_now = datetime.fromtimestamp(now)
                    target += timedelta(days=max(1, (local_now.date() - target.date()).days))
                    if target <= local_now:
                        target += timedelta(days=1)
                    next_due = target.timestamp()
                else:
                    next_due = anchor + max(1, math.floor((now - anchor) / interval) + 1) * interval
                self.db.execute("UPDATE reminders SET due=?,anchor=?,status='active' WHERE id=?",
                                (next_due, next_due, reminder_id))
            else:
                self.db.execute('DELETE FROM reminders WHERE id=?', (reminder_id,))
        self._arm()

    def claim_due(self):
        with self.db:
            rows = self.db.execute("SELECT id,title FROM reminders WHERE status='active' AND due<=? ORDER BY due",
                                   (self.clock(),)).fetchall()
            if rows:
                self.db.executemany("UPDATE reminders SET status='pending' WHERE id=?", [(r[0],) for r in rows])
            return rows

    def next_delay(self):
        due = self.db.execute("SELECT MIN(due) FROM reminders WHERE status='active'").fetchone()[0]
        # Reconcile wall-clock changes/resume at most every five minutes.
        # No timer at all when there are no scheduled reminders.
        return None if due is None else min(300000, max(0, math.ceil((due - self.clock()) * 1000)))

    def start(self, timer):
        """timer supplies after(ms, callback) and after_cancel(id), as Tk does."""
        self.timer = timer
        self.wake()

    def _cancel(self):
        if self.timer_id is not None:
            self.timer.after_cancel(self.timer_id)
            self.timer_id = None

    def _arm(self):
        if self.timer is None or self.error:
            return
        self._cancel()
        delay = self.next_delay()
        if delay is not None:
            self.timer_id = self.timer.after(delay, self._tick)

    def wake(self):
        if self.timer is not None and not self.error:
            self._cancel()
            self.timer_id = self.timer.after(0, self._tick)

    def _tick(self):
        self.timer_id = None
        try:
            self.claim_due()
            self._arm()
        except sqlite3.Error as exc:
            self.error = str(exc)
            self._cancel()
        self.notify()

    def close(self):
        self._cancel()
        self.timer = None
        self.db.close()
