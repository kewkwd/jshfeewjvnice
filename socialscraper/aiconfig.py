"""Einstellungen der KI-Stimmung: `data/ai.json` (enthält den API-Schlüssel von Anthropic).

Wie `integration.py`: die Datei wird nur hier gelesen und geschrieben, atomar und mit eingeschränkten Rechten.
Der Schlüssel gelangt weder in Fehlermeldungen noch ins Protokoll; für Anzeige und Protokoll gibt es `redacted(cfg)`
und `scrub(text, cfg)`. Alternativ zum Schlüssel in der Datei gilt die Umgebungsvariable `ANTHROPIC_API_KEY`.

Alle Funktionen haben einen Parameter `root` (Programmordner, in dem `data/` liegt; Standard: aktueller Ordner).
"""
import copy
import json
import math
import os
import pathlib
import re
import threading
import time

from . import integration

SCHEMA_VERSION = 1
FILE_NAME = 'ai.json'
MASK = '********'
ENV_KEY = 'ANTHROPIC_API_KEY'
MODELS = ('claude-haiku-5-5', 'claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-1')
DEFAULT_MODEL = MODELS[0]
DEFAULT_MAX_PER_RUN = 5000
MAX_MAX_PER_RUN = 1_000_000
DEFAULTS = {'api_key': '', 'model': DEFAULT_MODEL, 'max_per_run': DEFAULT_MAX_PER_RUN,
            'price_in_per_mtok': None, 'price_out_per_mtok': None, 'auto_after_runs': False}
_LOCK = threading.RLock()
_MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{1,79}$')


class AiConfigError(ValueError):
    """Verständliche deutsche Meldung für Anwender."""


def path(root=None):
    return integration.resolve_root(root) / 'data' / FILE_NAME


def default_config():
    return copy.deepcopy(DEFAULTS)


def _merge(raw):
    cfg = default_config()
    if isinstance(raw, dict):
        for key in cfg:
            if key in raw:
                cfg[key] = copy.deepcopy(raw[key])
    return cfg


def load_with_status(root=None):
    """(Konfiguration, Problem). Fehlende Datei: Standardwerte ohne Problem. Kaputte Datei: Standardwerte und eine Meldung."""
    p = path(root)
    for attempt in range(integration.READ_RETRIES):
        try:
            text = p.read_text(encoding='utf-8-sig')
            break
        except FileNotFoundError:
            return default_config(), None
        except UnicodeDecodeError:   # z. B. als UTF-16 gespeichert: beschädigt, kein Absturz
            return default_config(), (f'Die Datei {FILE_NAME} ist beschädigt und wird ignoriert. '
                                      'Beim nächsten Speichern wird sie ersetzt (eine Kopie bleibt als .defekt erhalten).')
        except OSError as error:
            if attempt == integration.READ_RETRIES - 1:
                return default_config(), f'Die Datei {FILE_NAME} lässt sich nicht lesen ({error.strerror or "Fehler"}).'
            time.sleep(integration.READ_WAIT)
    try:
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError('kein Objekt')
    except ValueError:
        return default_config(), (f'Die Datei {FILE_NAME} ist beschädigt und wird ignoriert. '
                                  'Beim nächsten Speichern wird sie ersetzt (eine Kopie bleibt als .defekt erhalten).')
    return _merge(raw), None


def load(root=None):
    return load_with_status(root)[0]


def load_for_update(root=None):
    """Wie load, wirft aber bei nicht lesbarer Datei (damit ein Speichern den Schlüssel nicht überschreibt)."""
    cfg, problem = load_with_status(root)
    if problem and 'beschädigt' not in problem:
        raise AiConfigError(problem + ' Es wurde nichts verändert; bitte gleich noch einmal versuchen.')
    return cfg


def _price(value):
    if value is None or value == '':
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    if not math.isfinite(value) or value < 0 or value > 1_000_000:
        raise ValueError
    return float(value)


def validate(cfg):
    """Liste verständlicher Fehlermeldungen (leer = in Ordnung)."""
    problems = []
    key = cfg.get('api_key')
    if not isinstance(key, str):
        problems.append('Der API-Schlüssel muss Text sein.')
    elif key and (re.search(r'\s', key.strip()) or not 8 <= len(key.strip()) <= 400):
        problems.append('Der API-Schlüssel sieht nicht richtig aus (zu kurz oder mit Leerzeichen). Bitte noch einmal einfügen.')
    model = cfg.get('model')
    if not isinstance(model, str) or not _MODEL.fullmatch(model.strip()):
        problems.append('Bitte ein Modell aus der Liste wählen (oder einen gültigen Modellnamen eintippen).')
    n = cfg.get('max_per_run')
    if type(n) is not int or not 1 <= n <= MAX_MAX_PER_RUN:
        problems.append(f'„Höchstens … Kommentare pro Lauf“ muss eine ganze Zahl von 1 bis {MAX_MAX_PER_RUN} sein.')
    for field, label in (('price_in_per_mtok', 'Eingabe'), ('price_out_per_mtok', 'Ausgabe')):
        try:
            _price(cfg.get(field))
        except ValueError:
            problems.append(f'Der Preis pro Million {label}-Tokens muss eine Zahl ab 0 sein (oder leer).')
    if type(cfg.get('auto_after_runs')) is not bool:
        problems.append('„Automatisch einstufen“ muss ein Ja/Nein-Wert sein.')
    return problems


def _clean(cfg):
    cfg = _merge(cfg)
    if isinstance(cfg['api_key'], str):
        cfg['api_key'] = cfg['api_key'].strip()
    if isinstance(cfg['model'], str):
        cfg['model'] = cfg['model'].strip()
    for field in ('price_in_per_mtok', 'price_out_per_mtok'):
        if cfg[field] == '':
            cfg[field] = None
    return cfg


def save(cfg, root=None):
    """Prüft und speichert. Wirft AiConfigError mit allen Problemen; schreibt dann nichts."""
    cfg = _clean(cfg)
    problems = validate(cfg)
    if problems:
        raise AiConfigError(' '.join(problems))
    _write(cfg, root)
    return cfg


def _write(cfg, root=None):
    p = path(root)
    with _LOCK:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                _, problem = load_with_status(root)
                if problem and 'beschädigt' in problem:
                    try:
                        os.replace(p, p.with_name(FILE_NAME + '.defekt'))
                    except OSError:
                        pass
            tmp = p.with_name(f'{FILE_NAME}.{os.getpid()}.{threading.get_ident()}.tmp')
            data = json.dumps({'schema_version': SCHEMA_VERSION, **cfg}, ensure_ascii=False, indent=2)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
                integration._replace(tmp, p)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        except OSError as error:
            raise AiConfigError(f'Die KI-Einstellungen konnten nicht gespeichert werden ({error.strerror or "Fehler"}).') from None


def update(root=None, **fields):
    """Ändert nur die genannten Felder auf dem aktuellen Dateistand (z. B. nur den Schalter für die Automatik)."""
    if set(fields) - set(DEFAULTS):
        raise ValueError('Unbekanntes Feld')
    with _LOCK:
        cfg = load_for_update(root)
        cfg.update(fields)
        return save(cfg, root)


# ---------- Schlüssel ----------

def api_key(cfg=None, root=None):
    """(Schlüssel oder '', Herkunft 'datei' | 'umgebung' | 'fehlt'). Die Datei geht vor der Umgebungsvariable."""
    cfg = cfg if cfg is not None else load(root)
    key = cfg.get('api_key')
    if isinstance(key, str) and key.strip():
        return key.strip(), 'datei'
    env = os.environ.get(ENV_KEY, '').strip()
    if env:
        return env, 'umgebung'
    return '', 'fehlt'


def has_key(cfg=None, root=None):
    return bool(api_key(cfg, root)[0])


# ---------- Anzeige ----------

def redacted(cfg):
    """Kopie ohne den Schlüssel – für Anzeige und Protokoll."""
    out = copy.deepcopy(cfg)
    if out.get('api_key'):
        out['api_key'] = MASK
    return out


def scrub(text, cfg=None):
    """Entfernt bekannte Schlüssel (aus der Datei und der Umgebung) aus einem Text."""
    text = str(text)
    secrets_ = {(cfg or {}).get('api_key'), os.environ.get(ENV_KEY)}
    for secret in secrets_:
        if isinstance(secret, str) and len(secret.strip()) >= 6:
            text = text.replace(secret.strip(), MASK)
    return text
