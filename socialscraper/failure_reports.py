"""Small local registration incident reports with an explicit last reached step."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
from . import accountvault, repo
from .net.errors import FinalError, NetworkError
from .problemlogic import scrub

FILE_NAME = 'registration_failures.json'


def _read(db_path):
    try:data = json.loads((Path(db_path).parent / FILE_NAME).read_text(encoding='utf8'))
    except FileNotFoundError:return []
    except (OSError, ValueError):raise ValueError('Die bisherigen Fehlerberichte sind nicht lesbar.') from None
    if not isinstance(data, list) or any(not isinstance(item, dict) or
            any(not isinstance(item.get(key), str) for key in ('provider', 'stage', 'notice', 'message', 'next'))
            for item in data):
        raise ValueError('Die bisherigen Fehlerberichte haben ein ungültiges Format.')
    return data


def latest(db_path, provider):
    return next((item for item in reversed(_read(db_path)) if item.get('provider') == provider), None)


def render(item):
    if not item:return 'Für diesen Anbieter ist noch kein neuer Fehlerbericht gespeichert.'
    return '\n\n'.join((f"Anbieter: {item.get('provider', '')}",
        'Letzter Schritt: ' + item.get('stage', 'unbekannt'),
        'Letzter Hinweis: ' + (item.get('notice') or 'Kein zusätzlicher Hinweis'),
        'Fehlermeldung: ' + item.get('message', 'unbekannt'),
        'Weiteres Vorgehen: ' + item.get('next', 'Bitte den letzten Schritt gemeinsam prüfen.')))


@dataclass
class Progress:
    provider: str
    stage: str = 'Vorbereitung'
    notice: str = ''

    def phase(self, stage):
        self.stage = stage
        self.notice = ''

    def note(self, notice):
        self.notice = notice

    def publish_failure(self, db_path, error, melden, *, secrets=(), next_step=None):
        try:text = render(self.failure(db_path, error, secrets=secrets, next_step=next_step))
        except Exception:text = 'Fehlerbericht nicht gespeichert. Letzter Schritt: ' + self.stage
        try:melden(text)
        except Exception:pass  # A broken UI callback must not replace the original error.

    def failure(self, db_path, error, *, secrets=(), next_step=None):
        def clean(value):
            text = str(value)
            for secret in secrets:
                if isinstance(secret, str) and secret:text = text.replace(secret, '[entfernt]')
            return scrub(text)
        # Keep specific program messages; unknown library errors may contain
        # form contents, proxy credentials or a request dump.
        if isinstance(error, (FinalError, NetworkError)):
            message = clean(error)
        else:
            code = re.search(r'\b(?:ERR_[A-Z_]+|HTTP [45]\d\d)\b', str(error))
            message = type(error).__name__ + (': ' + code.group() if code else ': Technischer Fehler; Ursache noch nicht geklärt.')
        blocked = bool(re.search(r'\bkonto (?:ist|wurde) (?:vorübergehend |dauerhaft )?gesperrt\b|'
                                 r'\baccount (?:is|has been) (?:suspended|locked)\b', message, re.I))
        if re.search(r'\b(?:nicht|not|unklar|unbekannt|unknown|ob|whether)\b', message, re.I):blocked = False
        next_step = next_step or ('Die gemeldete Kontosperre gemeinsam prüfen und den offiziellen Wiederherstellungsweg klären. '
                     'Kein automatischer neuer Registrierungsversuch.' if blocked else
                     'Diesen Schritt und die Meldung gemeinsam prüfen. Beim nächsten Browserversuch wird ein neues Profil verwendet. '
                     'Ein neues Profil belegt nicht, dass die Ursache behoben ist.')
        item = dict(provider=self.provider, time=repo.iso(), stage=clean(self.stage), notice=clean(self.notice),
                    message=message, kind='reported_account_block' if blocked else 'registration_failure', next=clean(next_step))
        items = _read(db_path)
        accountvault._write(Path(db_path).parent / FILE_NAME, [*items, item][-20:])
        return item
