"""Run: python3 -B -m unittest -v"""
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import time
import os
import sys
import subprocess
import shutil
import tkinter as tk
import unittest

from main import App, HomePage, parse_request, lock_instance
from types import SimpleNamespace
from unittest.mock import Mock, patch
from scheduler import Scheduler
from translation import translate_text
from voice import Voice, WINDOWS_SPEECH


class Timer:
    def __init__(self, now):
        self.now = now
        self.callbacks = {}
        self.serial = 0

    def after(self, delay, callback):
        self.serial += 1
        self.callbacks[self.serial] = (delay, callback)
        return self.serial

    def after_cancel(self, token):
        del self.callbacks[token]

    def fire(self):
        token = min(self.callbacks, key=lambda key: self.callbacks[key][0])
        delay, callback = self.callbacks.pop(token)
        self.now[0] += delay / 1000
        callback()


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.db'
        self.now = [datetime(2026, 9, 19, 8).timestamp()]
        self.scheduler = Scheduler(self.path, lambda: None, lambda: self.now[0])

    def tearDown(self):
        self.scheduler.close()
        self.temp.cleanup()

    def test_claims_due_only_once(self):
        rid = self.scheduler.add('Water', self.now[0] + 10)
        self.assertEqual(self.scheduler.claim_due(), [])
        self.now[0] += 10
        self.assertEqual(self.scheduler.claim_due(), [(rid, 'Water')])
        self.assertEqual(self.scheduler.claim_due(), [])

    def test_snooze_keeps_daily_anchor_and_same_row(self):
        rid = self.scheduler.add('Breakfast', self.now[0], daily=True)
        self.scheduler.claim_due()
        self.scheduler.respond(rid, snooze=True)
        self.assertEqual(len(self.scheduler.rows()), 1)
        self.now[0] += 600
        self.assertEqual(self.scheduler.claim_due()[0][0], rid)
        self.scheduler.respond(rid)
        tomorrow = datetime.fromtimestamp(self.scheduler.rows()[0][2])
        self.assertEqual((tomorrow.day, tomorrow.hour, tomorrow.minute), (20, 8, 0))

    def test_interval_skips_missed_periods_without_drifting(self):
        rid = self.scheduler.add('Stretch', self.now[0], interval=3600)
        self.scheduler.claim_due()
        self.now[0] += 3 * 3600 + 120
        self.scheduler.respond(rid)
        self.assertEqual(datetime.fromtimestamp(self.scheduler.rows()[0][2]).hour, 12)

    def test_pending_persists_and_done_removes_one_off(self):
        rid = self.scheduler.add('Appointment', self.now[0])
        self.scheduler.claim_due()
        self.scheduler.close()
        self.scheduler = Scheduler(self.path, lambda: None, lambda: self.now[0])
        self.assertEqual(self.scheduler.rows()[0][4], 'pending')
        self.scheduler.respond(rid)
        self.assertEqual(self.scheduler.rows(), [])

    def test_edit_moves_deadline(self):
        rid = self.scheduler.add('Old', self.now[0])
        self.scheduler.save('New', self.now[0] + 100, reminder_id=rid)
        self.assertEqual(self.scheduler.claim_due(), [])
        self.assertEqual(self.scheduler.rows()[0][1], 'New')

    def test_invalid_data_rejected(self):
        for title, due in [('', self.now[0]), ('x' * 241, self.now[0]), ('Bad', float('nan'))]:
            with self.assertRaises(ValueError):
                self.scheduler.add(title, due)

    def test_earlier_insert_replaces_later_deadline(self):
        timer = Timer(self.now)
        self.scheduler.start(timer)
        timer.fire()
        self.scheduler.add('Later', self.now[0] + 3600)
        self.scheduler.add('Soon', self.now[0] + .05)
        self.assertEqual(len(timer.callbacks), 1)
        timer.fire()
        self.assertEqual(self.scheduler.rows()[0][4], 'pending')
        self.assertEqual(self.scheduler.rows()[1][4], 'active')

    def test_pending_recovered_on_start(self):
        rid = self.scheduler.add('Overdue', self.now[0])
        self.scheduler.claim_due()
        self.scheduler.notify = Mock()
        timer = Timer(self.now)
        self.scheduler.start(timer)
        timer.fire()
        self.scheduler.notify.assert_called_once()
        self.assertEqual(self.scheduler.rows()[0][0], rid)
        self.assertEqual(timer.callbacks, {})

    def test_empty_schedule_has_no_background_timer(self):
        timer = Timer(self.now)
        self.scheduler.start(timer)
        timer.fire()
        self.assertFalse(timer.callbacks)
        rid = self.scheduler.add('Later', self.now[0] + 86400)
        self.assertEqual(next(iter(timer.callbacks.values()))[0], 300000)
        self.scheduler.delete(rid)
        self.assertFalse(timer.callbacks)

    def test_clock_jump_reconciles_on_wake(self):
        timer = Timer(self.now)
        rid = self.scheduler.add('Soon', self.now[0] + 3600)
        self.scheduler.start(timer)
        timer.fire()
        self.now[0] += 7200
        self.scheduler.wake()
        timer.fire()
        self.assertEqual(self.scheduler.rows()[0][0], rid)
        self.assertEqual(self.scheduler.rows()[0][4], 'pending')

    def test_old_database_migrates(self):
        path = Path(self.temp.name) / 'old.db'
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE reminders(id INTEGER PRIMARY KEY,title TEXT,due REAL,daily INTEGER,status TEXT)")
        db.execute("INSERT INTO reminders VALUES(1,'Existing',?,1,'active')", (self.now[0],))
        db.commit()
        db.close()
        migrated = Scheduler(path, lambda: None, lambda: self.now[0])
        try:
            self.assertEqual(migrated.rows()[0][1], 'Existing')
            migrated.claim_due()
            migrated.respond(1)
            self.assertGreater(migrated.rows()[0][2], self.now[0])
        finally:
            migrated.close()

    def test_sound_count_persists(self):
        rid = self.scheduler.add('Drink water', self.now[0] + 10, sound_count=3)
        self.assertEqual(self.scheduler.sound_count_for(rid), 3)
        self.scheduler.save('Drink water', self.now[0] + 20, sound_count=5, reminder_id=rid)
        self.assertEqual(self.scheduler.sound_count_for(rid), 5)

    def test_commands(self):
        now = datetime(2026, 9, 19, 10)
        title, due, daily, interval = parse_request('Remind me to drink water in twenty minutes', now)
        self.assertEqual((title, due.minute, daily, interval), ('drink water', 20, False, 0))
        self.assertEqual(parse_request('Stretch every two hours', now)[3], 7200)
        title, due, daily, _ = parse_request('Take a walk every day at 8 am', now)
        self.assertEqual((due.day, due.hour, daily), (20, 8, True))
        self.assertEqual(parse_request('Call tomorrow at 12 pm', now)[1].hour, 12)
        for text in ['Something sometime', 'Walk at 29:00', 'Walk at 0 pm', 'Walk in zero minutes']:
            with self.assertRaises(ValueError):
                parse_request(text, now)


class ButtonTests(unittest.TestCase):
    def test_alert_sound_repeats_with_a_delay(self):
        app = SimpleNamespace(root=Mock(), alert_sounds=[], voice_language=Mock())
        app.voice_language.get.return_value = 'English'
        with patch('main.Voice'):
            App.play_alert_sound(app, 'Drink water', repeat_count=3)
        delays = [call.args[0] for call in app.root.after.call_args_list]
        self.assertEqual(delays, [0, 1500, 3000])

    def test_translation_uses_selected_language(self):
        with patch('translation.urlopen') as urlopen:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = b'[[["Reminder in Telugu", "Reminder", null, null]], null, "en"]'
            urlopen.return_value = response
            self.assertEqual(translate_text('Reminder', 'Telugu'), 'Reminder in Telugu')
            self.assertIn('&tl=te', urlopen.call_args.args[0].full_url)

    def test_speak_passes_translated_text_to_voice(self):
        app = SimpleNamespace(root=Mock(), voice=Mock(), speech_timer=None,
                              voice_language=Mock(), status=Mock(), check_speech=Mock())
        app.voice_language.get.return_value = 'Telugu'
        app.voice.speak.return_value = True
        with patch('main.translate_text', return_value='Translated reminder'):
            App.speak(app, 'Drink water')
        app.voice.speak.assert_called_once_with('Translated reminder', 'Telugu', 'Drink water')

    def test_read_aloud_passes_selected_language(self):
        app = SimpleNamespace(title=Mock(), input_feedback=Mock(), speak=Mock(), entry=Mock(), voice_language=Mock())
        app.title.get.return_value = 'Drink water'
        app.voice_language.get.return_value = 'Telugu'
        App.read_aloud(app)
        app.speak.assert_called_once_with('Drink water', 'Telugu')

    def test_read_aloud_accepts_title_without_time(self):
        app = SimpleNamespace(title=Mock(), input_feedback=Mock(), speak=Mock(), entry=Mock())
        app.title.get.return_value = 'MY sugar tablet'
        App.read_aloud(app)
        app.speak.assert_called_once_with('MY sugar tablet')
        app.title.set.assert_not_called()

    def test_empty_read_aloud_gives_feedback_without_speech(self):
        app = SimpleNamespace(title=Mock(), input_feedback=Mock(), speak=Mock(), entry=Mock())
        app.title.get.return_value = '   '
        App.read_aloud(app)
        app.speak.assert_not_called()
        app.input_feedback.set.assert_called_once()
        app.entry.focus_set.assert_called_once()

    def test_time_parser_preserves_title_without_time(self):
        app = SimpleNamespace(title=Mock(), input_feedback=Mock(), when=Mock())
        app.title.get.return_value = 'MY sugar tablet'
        App.parse(app)
        app.title.set.assert_not_called()
        app.when.set.assert_not_called()
        app.input_feedback.set.assert_called_once()

    def test_quick_preset_sets_title_and_time(self):
        app = SimpleNamespace(title=Mock(), when=Mock(), input_feedback=Mock(), status=Mock())
        now = datetime(2026, 9, 19, 8, 20)
        App.apply_quick_preset(app, 'Drink water', 15, now=now)
        app.title.set.assert_called_once_with('Drink water')
        app.when.set.assert_called_once()
        self.assertEqual(app.when.set.call_args.args[0], '2026-09-19 08:35:00')

    def test_filter_rows_highlights_pending_and_overdue(self):
        now = datetime(2026, 9, 19, 8, 30).timestamp()
        rows = [
            (1, 'Due soon', now + 600, False, 'active', 0),
            (2, 'Overdue', now - 120, False, 'pending', 0),
            (3, 'Daily', now + 86400, True, 'active', 0),
            (4, 'Needs response', now + 100, False, 'pending', 0),
        ]
        self.assertEqual([r[0] for r in App.filter_rows(rows, 'Pending', now)], [2, 4])
        self.assertEqual([r[0] for r in App.filter_rows(rows, 'Overdue', now)], [2])

    def test_filter_rows_searches_reminder_text(self):
        now = datetime(2026, 9, 19, 8, 30).timestamp()
        rows = [
            (1, 'Drink water', now + 600, False, 'active', 0),
            (2, 'Doctor visit', now + 1200, False, 'active', 0),
            (3, 'Walk outside', now + 1800, False, 'active', 0),
        ]
        self.assertEqual([r[0] for r in App.filter_rows(rows, 'All', now, 'doctor')], [2])
        self.assertEqual([r[0] for r in App.filter_rows(rows, 'All', now, 'water')], [1])

    def test_dark_mode_switch_applies_theme(self):
        app = SimpleNamespace(root=Mock(), style=Mock(), summary_vars={}, search_var=Mock(), dark_mode=False)
        App.apply_theme(app, True)
        app.root.configure.assert_called()
        self.assertTrue(app.dark_mode)
        app.style.configure.assert_called()

    def test_daily_overview_summarizes_today(self):
        now = datetime(2026, 9, 19, 8, 30)
        rows = [
            (1, 'Drink water', now.replace(hour=9, minute=0).timestamp(), False, 'active', 0),
            (2, 'Doctor visit', now.replace(hour=15, minute=0).timestamp(), False, 'active', 0),
            (3, 'Walk outside', now.replace(hour=7, minute=15).timestamp(), False, 'pending', 0),
            (4, 'Finish brief', now.replace(hour=8, minute=15).timestamp(), False, 'active', 0),
        ]
        overview = App.build_today_overview(rows, now.timestamp())
        self.assertEqual(overview['count'], 4)
        self.assertEqual(overview['overdue'], 1)
        self.assertEqual(overview['next'], 'Finish brief')

    def test_priority_is_saved_and_loaded_for_reminders(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'priority.db'
            now = [datetime(2026, 9, 19, 8).timestamp()]
            scheduler = Scheduler(path, lambda: None, lambda: now[0])
            try:
                rid = scheduler.add('Medication', now[0] + 30, priority='High')
                self.assertEqual(rid, 1)
                self.assertEqual(scheduler.priority_for(rid), 'High')
            finally:
                scheduler.close()

    def test_today_view_filters_only_current_day(self):
        now = datetime(2026, 9, 19, 8, 30)
        rows = [
            (1, 'Today task', now.replace(hour=9, minute=0).timestamp(), False, 'active', 0),
            (2, 'Tomorrow task', now.replace(day=20, hour=9, minute=0).timestamp(), False, 'active', 0),
            (3, 'Another today task', now.replace(hour=17, minute=0).timestamp(), False, 'pending', 0),
        ]
        self.assertEqual([r[0] for r in App.filter_rows(rows, 'Today', now.timestamp())], [1, 3])


class HomePageTests(unittest.TestCase):
    def test_home_page_starts_main_app(self):
        root = tk.Tk()
        try:
            page = HomePage(root)
            app = page.open_app()
            self.assertIsInstance(app, App)
            self.assertIsNotNone(app.root)
        finally:
            root.destroy()


class PlatformTests(unittest.TestCase):
    def test_windows_speech_treats_text_as_data(self):
        text = "MY sugar tablet; $(Write-Host unexpected) ' \u0c28"
        with patch('voice.sys.platform', 'win32'), patch('voice.shutil.which', return_value='powershell.exe'), patch('voice.subprocess.Popen') as popen:
            voice = Voice()
            self.assertTrue(voice.speak(text))
            args, kwargs = popen.call_args
            self.assertEqual(args[0][-1], WINDOWS_SPEECH)
            self.assertNotIn(text, args[0])
            self.assertEqual(kwargs['env']['GENTLE_REMINDER_TEXT'], text)
            self.assertEqual(kwargs['env']['GENTLE_REMINDER_FALLBACK_TEXT'], text)
            self.assertNotIn('-ExecutionPolicy', args[0])
            self.assertFalse(kwargs.get('shell', False))

    def test_linux_prefers_espeak_ng_and_falls_back(self):
        for installed, expected in [({'espeak-ng': '/bin/espeak-ng'}, '/bin/espeak-ng'), ({'espeak': '/bin/espeak'}, '/bin/espeak')]:
            with patch('voice.sys.platform', 'linux'), patch('voice.shutil.which', side_effect=installed.get), patch('voice.subprocess.Popen') as popen:
                voice = Voice()
                voice.speak('-title with spaces')
                self.assertEqual(popen.call_args.args[0], [expected, '--', '-title with spaces'])

    def test_missing_speech_preserves_visual_mode(self):
        with patch('voice.shutil.which', return_value=None), patch('voice.subprocess.Popen') as popen:
            self.assertFalse(Voice().speak('Hello'))
            popen.assert_not_called()

    def test_voice_uses_selected_language(self):
        with patch('voice.sys.platform', 'win32'), patch('voice.shutil.which', return_value='powershell.exe'), patch('voice.subprocess.Popen') as popen:
            voice = Voice()
            voice.speak('Namaste', 'hi-IN')
            self.assertEqual(popen.call_args.kwargs['env']['GENTLE_REMINDER_LANG'], 'hi-IN')

        with patch('voice.sys.platform', 'linux'), patch('voice.shutil.which', side_effect=lambda cmd: '/usr/bin/espeak-ng' if cmd == 'espeak-ng' else None), patch('voice.subprocess.Popen') as popen:
            voice = Voice()
            voice.speak('Namaste', 'Telugu')
            self.assertEqual(popen.call_args.args[0], ['/usr/bin/espeak-ng', '-v', 'te', '--', 'Namaste'])

    def test_windows_byte_lock(self):
        backend = SimpleNamespace(LK_NBLCK=2, locking=Mock())
        with tempfile.TemporaryFile('w+b') as handle, patch('main.sys.platform', 'win32'), patch.dict(sys.modules, msvcrt=backend):
            lock_instance(handle)
            backend.locking.assert_called_once_with(handle.fileno(), 2, 1)
            self.assertEqual(handle.tell(), 0)
            self.assertEqual(handle.read(), b'\0')

    def test_windows_lock_conflict_is_reported(self):
        import errno
        backend = SimpleNamespace(LK_NBLCK=2, locking=Mock(side_effect=OSError(errno.EACCES, 'locked')))
        with tempfile.TemporaryFile('w+b') as handle, patch('main.sys.platform', 'win32'), patch.dict(sys.modules, msvcrt=backend):
            with self.assertRaises(BlockingIOError):
                lock_instance(handle)

    def test_native_lock_blocks_other_process_and_releases(self):
        script = ('from main import lock_instance; import sys; '
                  'f=open(sys.argv[1], "a+b"); lock_instance(f)')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.instance.lock'
            with path.open('a+b') as handle:
                lock_instance(handle)
                result = subprocess.run([sys.executable, '-B', '-c', script, str(path)], capture_output=True, cwd=Path(__file__).parent)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'BlockingIOError', result.stderr)
            result = subprocess.run([sys.executable, '-B', '-c', script, str(path)], capture_output=True, cwd=Path(__file__).parent)
            self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(sys.platform == 'win32', 'Requires native Windows speech')
    def test_native_windows_speech_service(self):
        code = WINDOWS_SPEECH.replace('$voice.SetOutputToDefaultAudioDevice();', '$voice.SetOutputToNull();')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', code],
                                env=dict(os.environ, GENTLE_REMINDER_TEXT='Reminder test'), capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Requires native Linux speech')
    def test_native_linux_speech_service(self):
        command = shutil.which('espeak-ng') or shutil.which('espeak')
        if not command:
            self.skipTest('Install espeak-ng to test native speech')
        result = subprocess.run([command, '--stdout', '--', 'Reminder test'], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(b'RIFF'))


@unittest.skipUnless(os.environ.get('RC_GUI_TEST') == '1', 'Set RC_GUI_TEST=1 for desktop checks')
class DesktopTests(unittest.TestCase):
    def test_real_timer_and_read_buttons(self):
        import tkinter as tk
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            app = App(root, Path(folder) / 'gui.db')
            app.voice = Mock()
            app.voice.speak.return_value = True
            app.voice.result.return_value = 0
            root.bell = lambda: None
            errors = []
            root.report_callback_exception = lambda *args: errors.append(args)
            try:
                app.title.set('MY sugar tablet')
                app.read_button.invoke()
                app.voice.speak.assert_called_with('MY sugar tablet')
                app.title.set('Water in 20 minutes')
                app.parse_button.invoke()
                self.assertEqual(app.title.get(), 'Water')
                rid = app.scheduler.add('A short reminder', time.time() + .05)
                deadline = time.monotonic() + 3
                while rid not in app.alerts and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertIn(rid, app.alerts)
                app.respond(rid, True)
                self.assertEqual(app.scheduler.rows()[0][4], 'active')
                self.assertIsNotNone(app.scheduler.timer_id)
                app.scheduler.delete(rid)
                root.update()
                self.assertIsNone(app.scheduler.timer_id)
                self.assertFalse(errors, errors)
            finally:
                app.close()


if __name__ == '__main__':
    unittest.main()
