# Gentle Reminder

A small Python desktop reminder app for **Windows, Linux, and macOS**. SQLite storage, native speech, and optional online translation for spoken reminders.

## Clone and run

Clone or copy the project into a folder you can write to. Open `voice_reminder` inside it. Python **3.10 or newer**, Tk, a desktop session, and working speakers are required.

### Windows

1. Install Python from [python.org](https://www.python.org/downloads/windows/), including its Tcl/Tk component and launcher.
2. Double-click **Start.bat**, or open a terminal in this folder and run:

```bat
py -3 -B main.py
```

If the `py` launcher is unavailable, use `python -B main.py`. Speech uses Windows PowerShell and the installed .NET `System.Speech` voices; no Python speech package is needed. Windows PowerShell is distinct from PowerShell 7 (`pwsh`). The app starts it only when speaking, without a console window, and does not change execution policies.

### Linux

Install Python's Tk bindings and eSpeak NG using your distribution's package manager. On Ubuntu/Debian:

```sh
sudo apt update
sudo apt install python3 python3-tk espeak-ng
```

On Fedora:

```sh
sudo dnf install python3 python3-tkinter espeak-ng
```

Then run from this folder:

```sh
sh start.sh
```

Or use `python3 -B main.py`. The app prefers `espeak-ng` and falls back to `espeak`. A desktop display and audio session are required; this is not a headless server app. Wayland environments need XWayland support for standard Tk builds.

### macOS

Use Python with Tk from [python.org](https://www.python.org/downloads/macos/). Double-click **Start.command**, or run `python3 -B main.py`. Speech uses the built-in `say` service.

### Check the installation

`python -m tkinter` (or `python3 -m tkinter` / `py -3 -m tkinter`) should open a small test window. Use **Test voice** inside the app to check spoken output. If speech is missing or fails, the app shows a message and still displays reminder alerts.

## Use it

1. Enter a reminder and local date/time, choose a repeat option, then press **Add reminder**.
2. **Read aloud** speaks the entered text, including a title such as `MY sugar tablet`. It does not change the schedule.
3. **Set time from text** fills the time/repeat fields from a supported sentence. Check the result before adding the reminder.
4. Select a reminder to edit or delete it; double-clicking also opens it for editing.
5. At alert time, choose **Done** or **Snooze 10 min**. Closing an alert snoozes it. Snoozing preserves the original daily/interval schedule.

Keyboard shortcut to save: **Ctrl+Enter** on Windows/Linux, **Command+Enter** on macOS.

Supported sentences:

- `Remind me to drink water in 20 minutes`
- `Stretch every two hours`
- `Take a walk every day at 8 am`
- `Call the clinic tomorrow at 10:30 am`
- `Take a break at 18:30`

Sentence interpretation uses a small, explicit grammar, not an AI model. Type into the input or use your operating system's dictation if available. Built-in microphone capture, conversational commands, spoken confirmation, and caregiver messaging are not implemented. Linux voice input needs a separate dictation tool. Translation uses an online service when a non-English language is selected; if it is unavailable, the original reminder is spoken. The app stores no audio.

## Scheduling and efficiency

- Three core files: `main.py` (interface/parser/platform lock), `scheduler.py` (database/deadlines), `voice.py` (native speech).
- **One deadline timer**, supported by Tk on all three platforms. No scheduling threads, Unix pipes, or Windows UI polling workaround.
- An empty schedule has no scheduling timer. A schedule change immediately replaces the next deadline. With scheduled work, a five-minute maximum wait reconciles wall-clock changes/resume. Earlier deadlines fire at their own time; focusing the app reconciles immediately.
- Speech runs in a separate on-demand system process. Its status is checked only during speech so backend failures can be displayed.
- SQLite indexes active deadlines and saves changes before alerts appear. Pending alerts survive restart. Existing prototype databases migrate automatically.
- Completed one-off reminders are removed. SQLite reuses freed pages but can retain its high-water file size. No unbounded history or stored recordings.
- Native file locks prevent two app instances from operating on the same local folder. The lock releases when a process exits, including after a crash.
- Launchers use `-B` to avoid Python bytecode caches.

The database `reminders.db` starts at about 12 KB and lives beside the Python files. `.instance.lock` is a zero/one-byte local lock file, not an error. Keep the folder writable. Back up the database with the app closed; it is not encrypted. Cloning source alone does not copy personal reminders because the database is ignored by Git.

The app must stay open, the computer awake, and audio enabled. It does not install a background service or wake the computer. Overdue reminders reappear after restart. Daily repeats use local calendar time; interval repeats use elapsed seconds and skip missed intervals. While an occurrence awaits acknowledgement, the next occurrence is not scheduled. Done creates the next future occurrence; snooze repeats the current one after ten minutes.

This is not hard real-time scheduling: OS load, computer sleep, and clock changes can delay delivery. Review reminder times after changing time zones; daylight-saving transitions follow system conversion rules. Acknowledgement records a response, not verified medication intake.

## Tests and verification status

Run from this folder:

```sh
python3 -B -m unittest -v
```

On Windows use `py -3 -B -m unittest -v`. To include actual desktop controls/timers, set `RC_GUI_TEST=1`:

```sh
RC_GUI_TEST=1 python3 -B -m unittest -v
```

Windows PowerShell equivalent:

```powershell
$env:RC_GUI_TEST = '1'
py -3 -B -m unittest -v
```

The suite covers reminder persistence, migration, snooze cadence, clock reconciliation, empty-schedule idleness, deadline replacement, parsing, Read aloud, cross-process locking, Windows speech data handling, and Linux speech selection. Native speech checks run only on their matching OS. Desktop checks use temporary databases and mocked speech; they do not change your reminders.

**Local verification:** on macOS, 22 tests passed, including the real desktop test; the two native Windows/Linux speech checks were skipped. Windows branches and Linux backend selection were also tested with mocks. No Windows or Linux machine was available locally, so native execution on those systems is not yet claimed.

The repository-level `.github/workflows/desktop-tests.yml` is configured to run on Windows, Ubuntu, and macOS with Python 3.10 and 3.13, including desktop checks and native speech checks where applicable. It will run after you push this project to GitHub with Actions enabled; it has not been run here. Linux GUI checks use a virtual display.

Earlier macOS measurements (before the portable timer change) observed about 0.20–0.26 seconds to construct the interface and about 118 MiB peak process memory including Python/Tk. Those are historical development observations, not Windows/Linux benchmarks. Installed Python/Tk and system voices use additional disk space beyond these small source files.

## Implementation references

- [Python Tkinter](https://docs.python.org/3/library/tkinter.html)
- [Windows byte-range locking](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking)
- [Windows SpeechSynthesizer](https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer?view=netframework-4.8.1)
- [eSpeak NG](https://github.com/espeak-ng/espeak-ng)
- [Ubuntu Tk package](https://packages.ubuntu.com/noble/python3-tk) and [eSpeak NG package](https://packages.ubuntu.com/noble/espeak-ng)
