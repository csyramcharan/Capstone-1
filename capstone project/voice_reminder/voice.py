"""On-demand native speech; no model downloads, recordings, or pip packages."""
import os
import shutil
import subprocess
import sys

LANGUAGE_MAP = {
    'English': 'en-US',
    'Hindi': 'hi-IN',
    'Tamil': 'ta-IN',
    'Telugu': 'te-IN',
    'Malayalam': 'ml-IN',
    'Kannada': 'kn-IN',
    'Bengali': 'bn-IN',
}

LANGUAGE_CODES = {code: code for code in LANGUAGE_MAP.values()}
LANGUAGE_CODES.update({'en': 'en-US', 'hi': 'hi-IN', 'ta': 'ta-IN', 'te': 'te-IN', 'ml': 'ml-IN', 'kn': 'kn-IN', 'bn': 'bn-IN'})

# Fixed code only: reminder text is data in the child environment, never PowerShell code.
WINDOWS_SPEECH = (
    "$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Speech; "
    "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "try { $voice.SetOutputToDefaultAudioDevice(); "
    "$lang = [Environment]::GetEnvironmentVariable('GENTLE_REMINDER_LANG'); "
    "$text = [Environment]::GetEnvironmentVariable('GENTLE_REMINDER_TEXT'); "
    "$fallback = [Environment]::GetEnvironmentVariable('GENTLE_REMINDER_FALLBACK_TEXT'); "
    "if ($lang) { $matching = $voice.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like \"$lang*\" -or $_.VoiceInfo.Language -like \"$lang*\" } | Select-Object -First 1; if ($matching) { $voice.SelectVoice($matching.VoiceInfo.Name) } else { $text = $fallback } }; "
    "$voice.Speak($text) } "
    "finally { $voice.Dispose() }"
)


class Voice:
    def __init__(self):
        self.process = None
        self.platform = sys.platform
        if self.platform == 'win32':
            self.command = shutil.which('powershell.exe')
        elif self.platform == 'darwin':
            self.command = shutil.which('say')
        else:
            self.command = shutil.which('espeak-ng') or shutil.which('espeak')

    @staticmethod
    def normalize_language(language):
        if not language:
            return 'en-US'
        value = str(language).strip()
        if value in LANGUAGE_CODES:
            return LANGUAGE_CODES[value]
        if value.lower() in {code.lower(): code for code in LANGUAGE_CODES.values()}:
            return {code.lower(): code for code in LANGUAGE_CODES.values()}[value.lower()]
        return LANGUAGE_MAP.get(value, 'en-US')

    def speak(self, text, language='en-US', fallback_text=None):
        self.stop()
        if not self.command:
            return False
        language = self.normalize_language(language)
        options = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self.platform == 'win32':
            args = [self.command, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', WINDOWS_SPEECH]
            options['env'] = dict(os.environ, GENTLE_REMINDER_TEXT=text, GENTLE_REMINDER_LANG=language,
                                  GENTLE_REMINDER_FALLBACK_TEXT=fallback_text or text)
            options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        else:
            if self.platform == 'darwin':
                args = [self.command, '-v', language.split('-')[0], text]
            else:
                lang_key = language.split('-')[0]
                if lang_key.lower() == 'en':
                    args = [self.command, '--', text]
                else:
                    args = [self.command, '-v', lang_key, '--', text]
        self.process = subprocess.Popen(args, **options)
        return True

    def result(self):
        return self.process.poll() if self.process else 0

    def stop(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = None
