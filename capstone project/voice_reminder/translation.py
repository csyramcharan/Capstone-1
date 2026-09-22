"""Small cached translation helper for spoken reminders."""
import json
from threading import Lock
from urllib.parse import quote
from urllib.request import Request, urlopen

LANGUAGE_TARGETS = {
    'English': 'en',
    'Hindi': 'hi',
    'Tamil': 'ta',
    'Telugu': 'te',
    'Malayalam': 'ml',
    'Kannada': 'kn',
    'Bengali': 'bn',
}

_CACHE = {}
_CACHE_LOCK = Lock()


def translate_text(text, language):
    """Translate text for a supported language, or return it unchanged on failure."""
    target = LANGUAGE_TARGETS.get(str(language).strip(), 'en')
    if target == 'en' or not text.strip():
        return text
    key = (text, target)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        url = ('https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto'
               f'&tl={target}&dt=t&q={quote(text)}')
        request = Request(url, headers={'User-Agent': 'GentleReminder/1.0'})
        with urlopen(request, timeout=5) as response:
            parts = json.loads(response.read().decode('utf-8'))[0]
        translated = ''.join(part[0] for part in parts if part and part[0])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return text
    with _CACHE_LOCK:
        _CACHE[key] = translated
    return translated
