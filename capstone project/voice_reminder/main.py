"""Gentle Reminder — Python desktop app for Windows, macOS, and Linux."""
from datetime import datetime, timedelta
from pathlib import Path
import errno
import sys
import os
import re
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox

from scheduler import Scheduler
from translation import translate_text
from voice import Voice

DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
ALERT_SOUND_DELAY_MS = 1500
WORDS = dict(zip('one two three four five six seven eight nine ten eleven twelve'.split(), range(1, 13)))
WORDS.update(a=1, an=1, fifteen=15, twenty=20, thirty=30, forty=40, fifty=50, sixty=60, ninety=90)


def parse_request(text, now=None):
    """Deliberately limited local grammar; reject ambiguity instead of guessing."""
    now = now or datetime.now()
    text = re.sub(r'^remind me to\s+', '', text.strip(), flags=re.I).rstrip('.!')
    relative = re.fullmatch(r'(.+?)\s+(in|every)\s+(\d+|[a-z]+)\s+(seconds?|minutes?|hours?|days?)', text, re.I)
    if relative:
        title, mode, amount, unit = relative.groups()
        amount = int(amount) if amount.isdigit() else WORDS.get(amount.lower(), 0)
        seconds = amount * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit[0].lower()]
        if not 0 < seconds <= 366 * 86400:
            raise ValueError('Use a positive duration of at most 366 days.')
        return title.strip(), (now + timedelta(seconds=seconds)).replace(microsecond=0), False, seconds if mode.lower() == 'every' else 0
    absolute = re.fullmatch(r'(.+?)\s+(?:(tomorrow|every day|daily)\s+)?at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', text, re.I)
    if absolute:
        title, repeat, hour, minute, period = absolute.groups()
        hour, minute = int(hour), int(minute or 0)
        if minute > 59 or (period and not 1 <= hour <= 12) or (not period and hour > 23):
            raise ValueError('Use a valid time, such as 8 am or 18:30.')
        if period:
            hour = hour % 12 + (12 if period.lower() == 'pm' else 0)
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if (repeat or '').lower() == 'tomorrow' or due <= now:
            due += timedelta(days=1)
        return title.strip(), due, (repeat or '').lower() in ('daily', 'every day'), 0
    raise ValueError('Try “Drink water in 20 minutes”, “Stretch every two hours”, or “Take a walk every day at 8 am”.')


class HomePage:
    def __init__(self, root, database=None):
        self.root = root
        self.database = database
        self.app = None
        root.title('Gentle Reminder')
        root.geometry('560x420')
        root.minsize(500, 360)
        root.configure(bg='#f3f6ff')
        style = ttk.Style(root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('Modern.TFrame', background='#f3f6ff')
        style.configure('Card.TFrame', background='#ffffff', relief='flat')
        style.configure('Title.TLabel', background='#ffffff', foreground='#1d2a4f', font=('Segoe UI', 28, 'bold'))
        style.configure('Body.TLabel', background='#ffffff', foreground='#52627d', font=('Segoe UI', 12))
        style.configure('Footer.TLabel', background='#ffffff', foreground='#6d7b95', font=('Segoe UI', 9))
        style.configure('Primary.TButton', background='#5767f5', foreground='#ffffff', font=('', 11, 'bold'))
        style.map('Primary.TButton', background=[('active', '#4759e6')], foreground=[('disabled', '#e7ebff')])
        style.configure('Secondary.TButton', background='#edf3ff', foreground='#1d2a4f', font=('', 11))
        style.map('Secondary.TButton', background=[('active', '#e2ebff')])

        outer = ttk.Frame(root, padding=18, style='Modern.TFrame')
        outer.pack(fill='both', expand=True)
        card = ttk.Frame(outer, padding=28, style='Card.TFrame')
        card.pack(fill='both', expand=True)

        ttk.Label(card, text='Gentle Reminder', style='Title.TLabel').pack(anchor='center', pady=(8, 10))
        ttk.Label(card, text='Make room for what matters. We\'ll remember the little things.',
                  style='Body.TLabel', wraplength=420, justify='center').pack(anchor='center', pady=(0, 18))
        ttk.Label(card, text='A simple place for daily reminders, short notes, and gentle check-ins.',
                  style='Body.TLabel', wraplength=420, justify='center').pack(anchor='center', pady=(0, 28))

        button_row = ttk.Frame(card, style='Card.TFrame')
        button_row.pack(anchor='center')
        ttk.Button(button_row, text='Open reminder home', command=self.open_app, width=22, style='Primary.TButton').pack(side='left', padx=(0, 10))
        ttk.Button(button_row, text='Exit', command=self.close, width=12, style='Secondary.TButton').pack(side='left')

        ttk.Label(card, text='Your reminders stay on your device and are checked automatically.',
              style='Footer.TLabel', wraplength=420, justify='center').pack(anchor='center', pady=(26, 0))

    def open_app(self):
        if self.app is not None:
            return self.app
        for child in self.root.winfo_children():
            child.destroy()
        self.app = App(self.root, self.database)
        return self.app

    def close(self):
        if self.app is not None:
            self.app.close()
            return
        self.root.destroy()


class App:
    def __init__(self, root, database=None):
        self.root = root
        self.editing = None
        self.alerts = {}
        self.closed = False
        root.title('Gentle Reminder')
        root.geometry('940x760')
        root.minsize(860, 680)
        root.configure(bg='#f4f6fb')
        self.voice = Voice()
        self.speech_timer = None
        self.alert_sounds = []
        self.scheduler = Scheduler(database or Path(__file__).with_name('reminders.db'), self.on_alert)
        style = ttk.Style(root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('Card.TFrame', background='#ffffff')
        style.configure('Metric.TFrame', background='#f7f9fd', relief='flat')
        style.configure('Toolbar.TFrame', background='#f7f9fd')
        style.configure('Header.TLabel', background='#ffffff', foreground='#17213a', font=('Segoe UI', 26, 'bold'))
        style.configure('Subhead.TLabel', background='#ffffff', foreground='#62708b', font=('Segoe UI', 11))
        style.configure('Section.TLabel', background='#ffffff', foreground='#17213a', font=('Segoe UI', 12, 'bold'))
        style.configure('Muted.TLabel', background='#ffffff', foreground='#68758c', font=('Segoe UI', 9))
        style.configure('StatLabel.TLabel', background='#f7f9fd', foreground='#68758c', font=('Segoe UI', 9, 'bold'))
        style.configure('StatValue.TLabel', background='#f7f9fd', foreground='#17213a', font=('Segoe UI', 22, 'bold'))
        style.configure('Overview.TLabel', background='#f7f9fd', foreground='#17213a', font=('Segoe UI', 10, 'bold'))
        style.configure('Action.TLabel', background='#ffffff', foreground='#52617b', font=('Segoe UI', 9, 'bold'))
        style.configure('Input.TEntry', fieldbackground='#ffffff', foreground='#17213a', padding=8)
        style.configure('TEntry', fieldbackground='#ffffff', foreground='#17213a', padding=6)
        style.configure('Treeview', rowheight=36, font=('Segoe UI', 10), fieldbackground='#ffffff', background='#ffffff', foreground='#27334d')
        style.configure('Treeview.Heading', font=('Segoe UI', 9, 'bold'), background='#edf1f8', foreground='#52617b', padding=(8, 7))
        style.configure('Accent.TButton', foreground='#ffffff', background='#315bea', font=('Segoe UI', 10, 'bold'), padding=(12, 8))
        style.map('Accent.TButton', background=[('active', '#4157ea')], foreground=[('disabled', '#eaf0ff')])
        style.configure('Card.TButton', background='#edf1f8', foreground='#27334d', font=('Segoe UI', 9, 'bold'), padding=(10, 7))
        style.map('Card.TButton', background=[('active', '#dfe6f3')])
        style.configure('TButton', font=('Segoe UI', 9), padding=(10, 7))
        style.configure('TCombobox', fieldbackground='#ffffff', foreground='#17213a', padding=5)
        self.style = style
        self.dark_mode = False
        frame = ttk.Frame(root, padding=(34, 28), style='Card.TFrame')
        frame.pack(fill='both', expand=True)

        header = ttk.Frame(frame, style='Card.TFrame')
        header.pack(fill='x')
        ttk.Label(header, text='Gentle Reminder', style='Header.TLabel').pack(anchor='w')
        ttk.Label(header, text='Keep your day steady, calm, and on time.', style='Subhead.TLabel').pack(anchor='w', pady=(4, 20))

        stats = ttk.Frame(frame, style='Card.TFrame')
        stats.pack(fill='x', pady=(0, 16))
        self.summary_vars = {
            'upcoming': tk.StringVar(value='0'),
            'pending': tk.StringVar(value='0'),
            'daily': tk.StringVar(value='0'),
        }
        for label, key in [('Upcoming', 'upcoming'), ('Pending', 'pending'), ('Daily', 'daily')]:
            card = ttk.Frame(stats, padding=(16, 13), style='Metric.TFrame')
            card.pack(side='left', fill='x', expand=True, padx=(0, 10))
            ttk.Label(card, text=label, style='StatLabel.TLabel').pack(anchor='w')
            ttk.Label(card, textvariable=self.summary_vars[key], style='StatValue.TLabel').pack(anchor='w')

        ttk.Label(frame, text='Create a reminder', style='Section.TLabel').pack(anchor='w', pady=(2, 8))
        entry_row = ttk.Frame(frame, style='Card.TFrame')
        entry_row.pack(fill='x')
        self.title = tk.StringVar()
        self.entry = ttk.Entry(entry_row, textvariable=self.title, font=('', 15), width=40, style='Input.TEntry')
        self.entry.pack(side='left', fill='x', expand=True)
        self.read_button = ttk.Button(entry_row, text='Read aloud', command=self.read_aloud, style='Accent.TButton')
        self.read_button.pack(side='left', padx=(8, 0))
        self.parse_button = ttk.Button(entry_row, text='Set time from text', command=self.parse)
        self.parse_button.pack(side='left', padx=(8, 0))
        self.voice_language = tk.StringVar(value='English')
        voice_box = ttk.Frame(entry_row, style='Card.TFrame')
        voice_box.pack(side='left', padx=(8, 0))
        self.language_box = ttk.Combobox(voice_box, textvariable=self.voice_language,
                                        values=('English', 'Hindi', 'Tamil', 'Telugu', 'Malayalam', 'Kannada', 'Bengali'),
                                        state='readonly', width=12)
        self.language_box.pack(anchor='center')
        self.input_feedback = tk.StringVar(value='Read aloud speaks your text. To set a time, try “Drink water in 20 minutes”.')
        ttk.Label(frame, textvariable=self.input_feedback, wraplength=690, style='Muted.TLabel').pack(anchor='w', pady=(7, 16))

        preset_frame = ttk.Frame(frame, style='Card.TFrame')
        preset_frame.pack(fill='x', pady=(0, 18))
        ttk.Label(preset_frame, text='Quick reminders', style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        for title, minutes in [('Drink water', 15), ('Stretch', 30), ('Medication', 60), ('Take a break', 10)]:
            ttk.Button(preset_frame, text=f'{title}  +{minutes}m', style='Card.TButton', command=lambda t=title, m=minutes: App.apply_quick_preset(self, t, m)).pack(side='left', padx=(0, 8))

        controls = ttk.Frame(frame, style='Card.TFrame')
        controls.pack(fill='x', pady=(2, 0))
        time_box = ttk.Frame(controls, style='Card.TFrame')
        time_box.pack(side='left')
        ttk.Label(time_box, text='Date and time · local').pack(anchor='w', pady=(0, 5))
        self.when = tk.StringVar(value=(datetime.now() + timedelta(minutes=5)).replace(microsecond=0).strftime(DATE_FORMAT))
        ttk.Entry(time_box, textvariable=self.when, width=22).pack(anchor='w')
        repeat_box = ttk.Frame(controls, style='Card.TFrame')
        repeat_box.pack(side='left', padx=18)
        ttk.Label(repeat_box, text='Repeat').pack(anchor='w', pady=(0, 5))
        self.repeat = tk.StringVar(value='Once')
        self.repeat_box = ttk.Combobox(repeat_box, textvariable=self.repeat, values=('Once', 'Daily', 'Every hour', 'Every 2 hours'), state='readonly', width=17)
        self.repeat_box.pack(anchor='w')
        sound_box = ttk.Frame(controls, style='Card.TFrame')
        sound_box.pack(side='left', padx=18)
        ttk.Label(sound_box, text='Sound repeats').pack(anchor='w', pady=(0, 5))
        self.sound_count = tk.StringVar(value='1')
        self.sound_count_box = ttk.Combobox(sound_box, textvariable=self.sound_count, values=('1', '2', '3', '5', '10'), state='readonly', width=8)
        self.sound_count_box.pack(anchor='w')
        priority_box = ttk.Frame(controls, style='Card.TFrame')
        priority_box.pack(side='left', padx=18)
        ttk.Label(priority_box, text='Priority').pack(anchor='w', pady=(0, 5))
        self.priority = tk.StringVar(value='Normal')
        self.priority_box = ttk.Combobox(priority_box, textvariable=self.priority, values=('Low', 'Normal', 'High'), state='readonly', width=10)
        self.priority_box.pack(anchor='w')
        self.save_button = ttk.Button(controls, text='Add reminder', command=self.add, style='Accent.TButton')
        self.save_button.pack(side='right', anchor='s')

        toolbar = ttk.Frame(frame, padding=(12, 10), style='Toolbar.TFrame')
        toolbar.pack(fill='x', pady=(24, 8))
        self.count = tk.StringVar(value='Your reminders')
        ttk.Label(toolbar, textvariable=self.count, font=('', 12, 'bold')).pack(side='left')
        self.view_filter = tk.StringVar(value='All')
        self.filter_box = ttk.Combobox(toolbar, textvariable=self.view_filter,
                                      values=('All', 'Today', 'Pending', 'Overdue', 'Upcoming', 'Daily'),
                                      state='readonly', width=12)
        self.filter_box.pack(side='right')
        self.filter_box.bind('<<ComboboxSelected>>', lambda _event: self.refresh())
        self.search_var = tk.StringVar(value='')
        search_box = ttk.Entry(toolbar, textvariable=self.search_var, width=18, style='Input.TEntry')
        search_box.pack(side='right', padx=(0, 8))
        search_box.bind('<KeyRelease>', lambda _event: self.refresh())
        ttk.Button(toolbar, text='Test voice', command=lambda: self.speak('Your voice reminders are ready.', self.voice_language.get())).pack(side='right', padx=(0, 8))
        self.theme_button = ttk.Button(toolbar, text='Dark mode', command=lambda: self.apply_theme(self, not self.dark_mode))
        self.theme_button.pack(side='right', padx=(0, 8))

        self.today_panel = ttk.Frame(frame, padding=(12, 10), style='Metric.TFrame')
        self.today_panel.pack(fill='x', pady=(0, 12))
        self.today_label = tk.StringVar(value='Today: 0 reminders · next: No reminders scheduled')
        ttk.Label(self.today_panel, textvariable=self.today_label, style='Overview.TLabel').pack(anchor='w')

        actions = ttk.Frame(frame, padding=(0, 8, 0, 8), style='Card.TFrame')
        actions.pack(fill='x')
        ttk.Label(actions, text='Selected reminder', style='Action.TLabel').pack(side='left', padx=(0, 14))
        ttk.Button(actions, text='Edit selected', command=self.edit, style='Card.TButton').pack(side='left')
        ttk.Button(actions, text='Delete selected', command=self.delete, style='Card.TButton').pack(side='left', padx=6)
        ttk.Button(actions, text='Clear form', command=self.reset_form, style='Card.TButton').pack(side='right')

        self.table = ttk.Treeview(frame, columns=('time', 'state'), show='tree headings', selectmode='browse', height=10)
        self.table.heading('#0', text='Reminder')
        self.table.heading('time', text='Next alert')
        self.table.heading('state', text='Repeat / status')
        self.table.column('#0', width=290, minwidth=160)
        self.table.column('time', width=185, minwidth=150)
        self.table.column('state', width=170, minwidth=125)
        self.table.pack(fill='both', expand=True)
        self.table.bind('<Double-1>', lambda _: self.edit())
        self.status = tk.StringVar(value='Ready. Keep this app open and your computer awake for alerts.')
        ttk.Label(frame, textvariable=self.status, wraplength=720, style='Muted.TLabel').pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<FocusIn>', lambda _: self.scheduler.wake())
        root.bind('<Command-Return>' if sys.platform == 'darwin' else '<Control-Return>', lambda _: self.add())
        self.refresh()
        self.scheduler.start(root)
        self.entry.focus_set()

    def on_alert(self):
        if self.scheduler.error:
            self.status.set('Scheduling stopped: ' + self.scheduler.error + '. Restart the app.')
            return
        self.refresh()
        pending = [r for r in self.scheduler.rows() if r[4] == 'pending' and r[0] not in self.alerts]
        for rid, title, due, daily, state, interval in pending:
            popup = tk.Toplevel(self.root)
            self.alerts[rid] = popup
            popup.title('Time for a little reminder')
            popup.attributes('-topmost', True)
            box = ttk.Frame(popup, padding=28)
            box.pack(fill='both', expand=True)
            ttk.Label(box, text=datetime.fromtimestamp(due).strftime('Scheduled for %d %b, %H:%M')).pack(anchor='w')
            ttk.Label(box, text=title, font=('', 21), wraplength=460).pack(anchor='w', pady=20)
            buttons = ttk.Frame(box)
            buttons.pack(fill='x')
            ttk.Button(buttons, text='Done', command=lambda i=rid: self.respond(i)).pack(side='left', padx=(0, 8))
            ttk.Button(buttons, text='Snooze 10 min', command=lambda i=rid: self.respond(i, True)).pack(side='left', padx=8)
            ttk.Button(buttons, text='Read aloud', command=lambda t=title: self.speak(t, self.voice_language.get())).pack(side='left', padx=8)
            popup.protocol('WM_DELETE_WINDOW', lambda i=rid: self.respond(i, True))
            self.play_alert_sound(title, self.scheduler.sound_count_for(rid), self.voice_language.get())
        if pending:
            self.root.bell()

    def play_alert_sound(self, text, repeat_count=1, language=None):
        count = max(1, int(repeat_count))
        selected_language = language or self.voice_language.get()
        spoken_text = translate_text(text, selected_language)
        for index in range(count):
            voice = Voice()
            self.alert_sounds.append(voice)
            self.root.after(index * ALERT_SOUND_DELAY_MS,
                            lambda v=voice, t=spoken_text, original=text, lang=selected_language:
                            v.speak(t, lang, original))

    def get_sound_count(self):
        try:
            value = int(self.sound_count.get())
        except ValueError:
            return 1
        return max(1, min(20, value))

    def speak(self, text, language=None):
        selected_language = language or self.voice_language.get()
        spoken_text = translate_text(text, selected_language)
        if self.speech_timer is not None:
            self.root.after_cancel(self.speech_timer)
            self.speech_timer = None
        try:
            if not self.voice.speak(spoken_text, selected_language, text):
                self.status.set('Speech unavailable. Linux needs espeak-ng; Windows needs Windows PowerShell. Visual alerts stay on.')
                return
            self.speech_timer = self.root.after(250, self.check_speech)
        except OSError:
            self.status.set('Could not start speech. Check that the system speech service is installed.')

    def check_speech(self):
        self.speech_timer = None
        result = self.voice.result()
        if result is None:
            self.speech_timer = self.root.after(250, self.check_speech)
        elif result != 0:
            self.status.set('Speech failed. Check your audio output and installed system voices. Visual alerts stay on.')

    def respond(self, rid, snooze=False):
        self.scheduler.respond(rid, snooze)
        self.dismiss(rid)
        self.refresh()
        self.status.set('Snoozed for 10 minutes.' if snooze else 'Done. Take care.')

    def dismiss(self, rid):
        popup = self.alerts.pop(rid, None)
        if popup:
            popup.destroy()
        self.voice.stop()
        for sound in self.alert_sounds:
            sound.stop()
        self.alert_sounds.clear()

    @staticmethod
    def repeat_label(daily, interval):
        if daily:
            return 'Daily'
        if interval:
            for unit, divisor in [('day', 86400), ('hour', 3600), ('minute', 60), ('second', 1)]:
                if interval % divisor == 0:
                    count = interval // divisor
                    return f'Every {count} {unit}' + ('s' if count != 1 else '')
        return 'Once'

    @staticmethod
    def status_label(daily, interval, sound_count=1, pending=False):
        repeat = App.repeat_label(daily, interval)
        sound = f'{int(sound_count)}x sound' if int(sound_count) > 1 else '1x sound'
        if pending:
            return f'Needs response · {repeat} · {sound}'
        return f'{repeat} · {sound}'

    @staticmethod
    def filter_rows(rows, view='All', now=None, search=''):
        now_value = datetime.now().timestamp() if now is None else float(now)
        search_value = (search or '').strip().lower()
        filtered = []
        for row in rows:
            rid, title, due, daily, state, interval = row
            due_value = float(due)
            match = True
            if search_value:
                match = search_value in str(title).lower()
            if not match:
                continue
            if view == 'All':
                filtered.append(row)
            elif view == 'Today':
                current_day = datetime.fromtimestamp(now_value).date()
                if datetime.fromtimestamp(due_value).date() == current_day:
                    filtered.append(row)
            elif view == 'Pending' and state == 'pending':
                filtered.append(row)
            elif view == 'Overdue' and due_value < now_value:
                filtered.append(row)
            elif view == 'Upcoming' and state == 'active' and due_value >= now_value:
                filtered.append(row)
            elif view == 'Daily' and daily:
                filtered.append(row)
        return filtered

    @staticmethod
    def apply_theme(app, dark=False):
        if not hasattr(app, 'root'):
            return
        palette = {
            'background': '#111827' if dark else '#eef4ff',
            'surface': '#1f2937' if dark else '#ffffff',
            'input': '#273449' if dark else '#ffffff',
            'text': '#f3f4f6' if dark else '#1d2a4f',
            'muted': '#aab6ca' if dark else '#4c5f87',
            'border': '#374151' if dark else '#eef3ff',
            'button': '#263449' if dark else '#f1f5ff',
        }
        if dark:
            app.root.configure(bg=palette['background'])
        else:
            app.root.configure(bg=palette['background'])
        style = getattr(app, 'style', None)
        if style is not None:
            style.configure('Card.TFrame', background=palette['surface'])
            style.configure('Metric.TFrame', background=palette['button'])
            style.configure('Toolbar.TFrame', background=palette['button'])
            style.configure('Header.TLabel', background=palette['surface'], foreground=palette['text'])
            style.configure('Subhead.TLabel', background=palette['surface'], foreground=palette['muted'])
            style.configure('Section.TLabel', background=palette['surface'], foreground=palette['text'])
            style.configure('Muted.TLabel', background=palette['surface'], foreground=palette['muted'])
            style.configure('StatLabel.TLabel', background=palette['button'], foreground=palette['muted'])
            style.configure('StatValue.TLabel', background=palette['button'], foreground=palette['text'])
            style.configure('Overview.TLabel', background=palette['button'], foreground=palette['text'])
            style.configure('Action.TLabel', background=palette['surface'], foreground=palette['muted'])
            style.configure('TLabel', background=palette['surface'], foreground=palette['text'])
            style.configure('Input.TEntry', fieldbackground=palette['input'], foreground=palette['text'])
            style.configure('TEntry', fieldbackground=palette['input'], foreground=palette['text'])
            style.configure('TCombobox', fieldbackground=palette['input'], foreground=palette['text'])
            style.configure('Treeview', fieldbackground=palette['surface'], background=palette['surface'], foreground=palette['text'])
            style.configure('Treeview.Heading', background=palette['border'], foreground=palette['text'])
            style.configure('Card.TButton', background=palette['button'], foreground=palette['text'])
        app.dark_mode = bool(dark)
        if hasattr(app, 'theme_button'):
            app.theme_button.configure(text='Light mode' if app.dark_mode else 'Dark mode')
        return dark

    @staticmethod
    def build_today_overview(rows, now=None):
        current_time = datetime.now().timestamp() if now is None else float(now)
        current_date = datetime.fromtimestamp(current_time).date()
        today = []
        overdue = 0
        next_title = 'No reminders scheduled'
        next_time = None
        for row in rows:
            _, title, due, _, state, _ = row
            due_value = float(due)
            due_date = datetime.fromtimestamp(due_value).date()
            if due_date != current_date:
                continue
            today.append((due_value, title, state))
            if due_value < current_time and state == 'pending':
                overdue += 1
        if today:
            past_due = [item for item in today if item[0] <= current_time]
            if past_due:
                next_due, next_title, _ = max(past_due, key=lambda item: item[0])
            else:
                next_due, next_title, _ = min(today, key=lambda item: item[0])
            next_time = datetime.fromtimestamp(next_due)
        return {'count': len(today), 'overdue': overdue, 'next': next_title, 'next_time': next_time}

    @staticmethod
    def apply_quick_preset(app, title, minutes=15, now=None):
        current = now or datetime.now()
        due = (current + timedelta(minutes=minutes)).replace(microsecond=0)
        if hasattr(app, 'title'):
            app.title.set(title)
        if hasattr(app, 'when'):
            app.when.set(due.strftime(DATE_FORMAT))
        if hasattr(app, 'input_feedback'):
            app.input_feedback.set(f'Quick reminder set for {due:%a %d %b, %H:%M}.')
        if hasattr(app, 'status'):
            app.status.set(f'Quick reminder ready for {due:%d %b %H:%M}.')
        return due

    def set_repeat(self, daily, interval):
        label = self.repeat_label(daily, interval)
        values = ('Once', 'Daily', 'Every hour', 'Every 2 hours')
        self.repeat_box.configure(values=values if label in values else (*values, label))
        self.repeat.set(label)

    def read_aloud(self):
        text = self.title.get().strip()
        if not text:
            self.input_feedback.set('Enter a reminder first, then press Read aloud.')
            self.entry.focus_set()
            return
        self.input_feedback.set('Reading your reminder aloud. Its time has not changed.')
        language = getattr(self, 'voice_language', None)
        if language is not None:
            self.speak(text, language.get())
        else:
            self.speak(text)

    def parse(self):
        try:
            title, due, daily, interval = parse_request(self.title.get())
        except ValueError:
            self.input_feedback.set('No supported time found. Enter the date/time below, or try “Drink water in 20 minutes”.')
            return
        self.title.set(title)
        self.when.set(due.strftime(DATE_FORMAT))
        self.set_repeat(daily, interval)
        self.input_feedback.set(f'Time set to {due:%d %b, %H:%M:%S}. Check the details, then press ' +
                                ('Save changes.' if self.editing else 'Add reminder.'))

    def add(self):
        try:
            raw = self.when.get().strip()
            due = datetime.strptime(raw, DATE_FORMAT if len(raw) > 16 else '%Y-%m-%d %H:%M').timestamp()
            if due <= datetime.now().timestamp():
                raise ValueError('Choose a future date and time.')
            repeat = self.repeat.get()
            interval = {'Every hour': 3600, 'Every 2 hours': 7200}.get(repeat, 0)
            custom = re.fullmatch(r'Every (\d+) (seconds?|minutes?|hours?|days?)', repeat)
            if custom:
                interval = int(custom[1]) * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[custom[2][0]]
            sound_count = self.get_sound_count()
            rid = self.scheduler.save(self.title.get(), due, repeat == 'Daily', interval, self.editing,
                                     sound_count=sound_count, priority=self.priority.get())
        except (ValueError, sqlite3.Error) as exc:
            self.status.set('Could not save: ' + str(exc))
            return
        self.dismiss(rid)
        self.reset_form()
        self.refresh()
        self.status.set('Saved. Your reminder stays here when you restart the app.')

    def edit(self):
        selected = self.table.selection()
        if not selected:
            self.status.set('Select a reminder first.')
            return
        row = next((r for r in self.scheduler.rows() if str(r[0]) == selected[0]), None)
        if row:
            self.editing, title, due, daily, state, interval = row
            self.title.set(title)
            self.when.set(datetime.fromtimestamp(due).strftime(DATE_FORMAT))
            self.set_repeat(daily, interval)
            self.sound_count.set(str(self.scheduler.sound_count_for(self.editing)))
            self.priority.set(self.scheduler.priority_for(self.editing))
            self.save_button.configure(text='Save changes')
            self.status.set('Editing selected reminder. Set a future time before saving.')
            self.entry.focus_set()

    def reset_form(self):
        self.editing = None
        self.title.set('')
        self.when.set((datetime.now() + timedelta(minutes=5)).replace(microsecond=0).strftime(DATE_FORMAT))
        self.set_repeat(False, 0)
        self.sound_count.set('1')
        self.priority.set('Normal')
        self.save_button.configure(text='Add reminder')

    def delete(self):
        selected = self.table.selection()
        if selected:
            rid = int(selected[0])
            self.scheduler.delete(rid)
            self.dismiss(rid)
            if self.editing == rid:
                self.reset_form()
            self.refresh()
            self.status.set('Reminder deleted.')

    def refresh(self):
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        rows = self.scheduler.rows()
        now = datetime.now().timestamp()
        view = self.view_filter.get() if hasattr(self, 'view_filter') else 'All'
        search = self.search_var.get() if hasattr(self, 'search_var') else ''
        visible_rows = self.filter_rows(rows, view, now, search)
        self.count.set(f'Your reminders · {len(visible_rows)} / {len(rows)}')
        for rid, title, due, daily, state, interval in visible_rows:
            sound_count = self.scheduler.sound_count_for(rid)
            priority = self.scheduler.priority_for(rid)
            status = self.status_label(daily, interval, sound_count, state == 'pending')
            display_status = f'{status} · {priority}'
            self.table.insert('', 'end', iid=str(rid), text=title,
                              values=(datetime.fromtimestamp(due).strftime('%d %b %Y, %H:%M'), display_status))
        upcoming = sum(1 for _, _, due, _, state, _ in rows if state == 'active' and float(due) >= now)
        pending = sum(1 for _, _, _, _, state, _ in rows if state == 'pending')
        daily = sum(1 for _, _, _, daily, _, _ in rows if daily)
        overdue = sum(1 for _, _, due, _, state, _ in rows if state == 'pending' and float(due) < now)
        self.summary_vars['upcoming'].set(str(upcoming))
        self.summary_vars['pending'].set(str(pending + overdue))
        self.summary_vars['daily'].set(str(daily))

        overview = self.build_today_overview(rows, now)
        next_text = overview['next'] if overview['next'] else 'No reminders scheduled'
        if overview['next_time']:
            next_text = f"next: {overview['next']} · {overview['next_time'].strftime('%H:%M')}"
        self.today_label.set(f"Today: {overview['count']} reminders · {overview['overdue']} overdue · {next_text}")
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.scheduler.close()
        self.voice.stop()
        if self.speech_timer is not None:
            self.root.after_cancel(self.speech_timer)
        self.root.destroy()


def lock_instance(handle):
    """The OS releases the lock even after a crash; do not delete the lock file."""
    if sys.platform == 'win32':
        import msvcrt
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise BlockingIOError('Another instance holds the lock') from exc
            raise
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def destroy_root(root):
    try:
        if root.winfo_exists():
            root.destroy()
    except tk.TclError:
        pass


def main():
    root = tk.Tk()
    try:
        with Path(__file__).with_name('.instance.lock').open('a+b') as lock:
            try:
                lock_instance(lock)
            except BlockingIOError:
                root.withdraw()
                messagebox.showinfo('Already open', 'Gentle Reminder is already running.')
                destroy_root(root)
                return
            page = HomePage(root)
            try:
                root.mainloop()
            finally:
                if page.app is not None:
                    page.app.close()
                else:
                    destroy_root(root)
    except (OSError, sqlite3.Error) as exc:
        messagebox.showerror('Could not start', f'{exc}\nKeep the app in a folder you can write to.')
        destroy_root(root)


if __name__ == '__main__':
    main()
