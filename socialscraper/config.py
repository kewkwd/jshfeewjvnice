import math
import os
import pathlib
import tomllib
from dataclasses import dataclass, field

PROXY_MODES = ('aus', 'liste', 'frei', 'tor')

@dataclass
class Settings:
    backend: str = 'local'
    engine: str = 'patchright'
    headless: bool = True
    pool_size: int = 4
    session_ttl: int = 1800
    proxy_sticky_ttl: int = 86400
    profiles_dir: str = 'data/profiles'
    db_path: str = 'data/scrape.db'
    log_level: str = 'INFO'
    max_comments: int = 500
    max_retries: int = 3
    retry_backoff: float = 2.0
    request_timeout: int = 30
    snapshot: bool = True
    impersonate: str = 'chrome'
    sphere_api_port: int = 40080
    sphere_presets: dict = field(default_factory=dict)
    proxies: list = field(default_factory=list)
    # aus | liste | frei | tor  (frei und tor: nur Abrufe ohne Konto, siehe net/proxymode.py)
    proxy_mode: str = 'aus'
    ms_token: str = ''
    ig_session_user: str = ''
    ig_session_file: str = 'ig_session'
    notify_file: str = 'data/alerts.log'
    notify_webhook: str = ''
    credentials_file: str = 'data/credentials.json'
    require_auth: bool = False
    session_max_age: float = 24.0   # Stunden; ist die Cookie-Datei älter, wird die Anmeldung im Browser neu geprüft (kein hartes Ablaufdatum), siehe net/session_guard.py
    video_timeout: int = 120    # hartes Gesamtlimit pro Video-Abruf im Browser (Sekunden)
    target_timeout: int = 900   # Gesamtlimit pro Ziel der Automatik / pro Befehl (Sekunden)
    rotation_every: int = 5     # Konto-Rotation: nach so vielen Videos zum nächsten Konto wechseln (1-100)
    length_short_s: int = 60    # Videolänge: „kurz“ = bis so viele Sekunden
    length_long_s: int = 600    # Videolänge: „lang“ = über so viele Sekunden (dazwischen „mittel“)


def _bool(value):
    normalized = value.strip().lower()
    if normalized in ('1', 'true', 'yes'): return True
    if normalized in ('0', 'false', 'no'): return False
    raise ValueError('Boolesche Einstellung muss true/false oder 1/0 sein')

_ENV = {
    'SS_BACKEND': ('backend', str), 'SS_ENGINE': ('engine', str),
    'SS_HEADLESS': ('headless', _bool), 'SS_POOL_SIZE': ('pool_size', int),
    'SS_LOG_LEVEL': ('log_level', str), 'SS_DB_PATH': ('db_path', str),
    'SS_PROFILES_DIR': ('profiles_dir', str),
    'SS_VIDEO_TIMEOUT': ('video_timeout', int), 'SS_TARGET_TIMEOUT': ('target_timeout', int),
    'SS_PROXY_MODE': ('proxy_mode', lambda v: v.strip().lower()),
    'SS_PROXIES': ('proxies', lambda v: [x.strip() for x in v.split(',') if x.strip()]),
    'SS_SPHERE_API_PORT': ('sphere_api_port', int), 'SS_MS_TOKEN': ('ms_token', str),
    'SS_IG_SESSION_USER': ('ig_session_user', str), 'SS_IG_SESSION_FILE': ('ig_session_file', str),
    'SS_SNAPSHOT': ('snapshot', _bool), 'SS_MAX_COMMENTS': ('max_comments', int),
    'SS_REQUIRE_AUTH': ('require_auth', _bool),
    'SS_CREDENTIALS_FILE': ('credentials_file', str),
    'SS_SESSION_MAX_AGE': ('session_max_age', float),
    'SS_ROTATION_EVERY': ('rotation_every', int),
}

def validate(cfg):
    if cfg.backend not in ('local', 'sphere'): raise ValueError('Unbekanntes Browser-Backend')
    if cfg.engine not in ('patchright', 'nodriver', 'firefox'): raise ValueError('Unbekannte Browser-Engine')
    for name in ('pool_size', 'session_ttl', 'max_retries', 'request_timeout', 'sphere_api_port', 'video_timeout', 'target_timeout'):
        value = getattr(cfg, name)
        if type(value) is not int or value < 1: raise ValueError(f'{name} muss eine positive ganze Zahl sein')
    if type(cfg.rotation_every) is not int or not 1 <= cfg.rotation_every <= 100:
        raise ValueError('rotation_every muss eine ganze Zahl von 1 bis 100 sein')
    for name in ('length_short_s', 'length_long_s'):
        if type(getattr(cfg, name)) is not int or not 1 <= getattr(cfg, name) <= 86400:
            raise ValueError(f'{name} muss eine ganze Zahl von 1 bis 86400 (Sekunden) sein')
    if cfg.length_short_s >= cfg.length_long_s:
        raise ValueError('length_short_s muss kleiner sein als length_long_s')
    for name in ('proxy_sticky_ttl', 'max_comments'):
        value = getattr(cfg, name)
        if type(value) is not int or value < 0: raise ValueError(f'{name} muss eine nicht negative ganze Zahl sein')
    for name in ('retry_backoff', 'session_max_age'):
        value = getattr(cfg, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'{name} muss eine positive Zahl sein')
    for name in ('headless', 'snapshot', 'require_auth'):
        if type(getattr(cfg, name)) is not bool: raise ValueError(f'{name} muss true oder false sein')
    if not isinstance(cfg.proxies, list) or any(not isinstance(p, str) for p in cfg.proxies):
        raise ValueError('proxies muss eine Liste von URLs sein')
    if cfg.proxy_mode not in PROXY_MODES:
        raise ValueError('proxy_mode muss aus, liste, frei oder tor sein')
    if not isinstance(cfg.sphere_presets, dict): raise ValueError('sphere_presets muss eine Tabelle sein')
    for name in ('profiles_dir', 'db_path', 'credentials_file', 'ig_session_file'):
        if not isinstance(getattr(cfg, name), str) or not getattr(cfg, name).strip(): raise ValueError(f'{name} fehlt')
    return cfg

def load(path='settings.toml'):
    p = pathlib.Path(path)
    data = tomllib.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else {}
    # Ältere Chat-Konfigurationen hatten die folgenden Werte versehentlich in der Preset-Tabelle.
    presets = data.get('sphere_presets')
    if isinstance(presets, dict):
        presets = dict(presets)
        for key in tuple(presets):
            if key in Settings.__dataclass_fields__ and key != 'sphere_presets':
                data.setdefault(key, presets.pop(key))
        data['sphere_presets'] = presets
    for env, (key, caster) in _ENV.items():
        if (raw := os.getenv(env)) is not None: data[key] = caster(raw)
    unknown = set(data) - set(Settings.__dataclass_fields__)
    if unknown: raise ValueError('Unbekannte Einstellung: ' + ', '.join(sorted(unknown)))
    return validate(Settings(**data))

