import json
import logging
import os
import pathlib
import re
import tempfile
from dataclasses import dataclass, field
from .fingerprint import build, Fingerprint
from ..runlock import OsLock

log = logging.getLogger(__name__)
BASE = pathlib.Path('data/identities')


def _read(filename):
    """Inhalt der Identitätsdatei als Wörterbuch, oder None, wenn sie leer oder kein gültiges JSON ist (abgebrochener
    Schreibvorgang, Stromausfall). Eine solche Datei blockiert das Konto nicht; sie wird neu angelegt."""
    try:
        data = json.loads(filename.read_text(encoding='utf-8-sig'))
    except ValueError:
        log.warning('Identitätsdatei %s ist leer oder unlesbar und wird neu angelegt', filename)
        return None
    return data


def _write(filename, data):
    """Atomar schreiben (erst Hilfsdatei, dann umbenennen), damit nie eine halbe Datei zurückbleibt."""
    contents = json.dumps(data, indent=2)
    fd, tmp = tempfile.mkstemp(prefix=filename.name + '.', suffix='.tmp', dir=filename.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, filename)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass

def validate_name(name):
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', name)
            or name.endswith('.') or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', name)):
        raise ValueError('Identity muss ein sicherer Dateiname aus Buchstaben, Zahlen, _, - oder . sein')
    return name

@dataclass
class Identity:
    name: str
    country: str
    mobile: bool
    fp: Fingerprint
    settings: dict = field(default=None, repr=False, compare=False)

class IdentityManager:
    def __init__(self, pool, profiles_dir=None):
        self.pool = pool
        self.account_proxies = None   # optional: net.accountproxy.Resolver (fester Proxy je Konto), gesetzt von cli.make_runtime
        self.base = pathlib.Path(profiles_dir).parent / 'identities' if profiles_dir is not None else BASE
        self.settings_profiles = pathlib.Path(profiles_dir) if profiles_dir is not None else BASE.parent / 'profiles'

    def _with_settings(self, identity):
        from ..profile_settings import load
        identity.settings = load(self.settings_profiles, identity.name)
        return identity

    def stored(self, name):
        """Return (country, mobile) of an existing identity, or (None, None)."""
        validate_name(name)
        filename = self.base / f'{name}.json'
        if not filename.exists():
            return None, None
        data = _read(filename)
        if data is None:
            return None, None
        try:
            return str(data['country']), bool(data['mobile'])
        except (KeyError, TypeError):
            raise ValueError(f'Identity-Datei {filename} ist beschädigt') from None

    def get(self, name, country='us', mobile=False):
        validate_name(name)
        if getattr(self.pool, 'anonymous', False) is True:
            # Kostenlose Proxys/Tor: nur die anonyme Identität, nie ein Konto (siehe net/proxymode.py).
            from . import proxymode
            if name != proxymode.ANON_IDENTITY:
                proxymode.refuse_accounts()
        country = country.lower()
        filename = self.base / f'{name}.json'
        data = _read(filename) if filename.exists() else None
        if data is not None:
            return self._with_settings(self._from_stored(name, country, mobile, data))
        # Auch getrennte Laufzeiten mit gemeinsamer profiles_dir müssen dieselbe
        # Erstanlage sehen. Die atomare Speicherung allein verhindert kein Überschreiben.
        base = self.base.resolve()
        # Sperrdateien bleiben außerhalb des Verzeichnisses der Identitätsdaten.
        lock = OsLock(base.with_name(base.name + '.locks') / f'{name}.lock')
        if not lock.try_acquire({}, tries=101):
            raise RuntimeError('Identity wird gerade angelegt; bitte den Auftrag erneut starten')
        try:
            data = _read(filename) if filename.exists() else None
            if data is not None:
                return self._with_settings(self._from_stored(name, country, mobile, data))
            # Der Fingerabdruck ist aus Name, Land und Gerät bestimmt.
            fp = build(name, country, mobile)
            self.base.mkdir(parents=True, exist_ok=True)
            _write(filename, {'name': name, 'country': country, 'mobile': mobile, 'fp': fp.to_dict()})
            return self._with_settings(Identity(name, country, mobile, fp))
        finally:
            lock.release()

    @staticmethod
    def _from_stored(name, country, mobile, data):
        if data['country'] != country or data['mobile'] != mobile:
            raise ValueError('Identity besitzt bereits ein anderes Land oder Gerät; verwende einen anderen Identity-Namen')
        return Identity(name, data['country'], data['mobile'], Fingerprint.from_dict(data['fp']))

    def browser_context(self, identity, *, engine, create=False):
        """Persist a distinct desktop context without rewriting an Android identity.

        Only an explicit handoff creates this context. Later Firefox sessions
        may reuse it; requesting an unsupported mobile browser alone cannot
        silently create a different device configuration. Account/proxy/cookie
        keys remain the original name. Firefox continues using its native UA.
        """
        if engine != 'firefox':
            raise ValueError('Dieser Browserkontext ist nur für Firefox vorgesehen.')
        validate_name(identity.name)
        country = identity.country.lower()
        if not identity.mobile and not identity.fp.is_mobile:
            return identity
        context = IdentityManager(self.pool)
        context.base = self.base / '_browser' / engine
        context.settings_profiles = self.settings_profiles
        filename = context.base / f'{identity.name}.json'
        if not create and not filename.exists():
            return None
        return context.get(identity.name, country, mobile=False)

    def proxy_for(self, identity):
        # Hat das Konto einen festen Proxy, gilt NUR dieser (sticky, nie ein anderer, nie direkt): ist er nicht
        # erreichbar, wirft der Resolver AccountProxyError. Er hat Vorrang vor Pool und Proxy-Liste.
        resolver = getattr(self, 'account_proxies', None)
        if resolver is not None:
            fixed = resolver.proxy_for(identity.name)
            if fixed is not None:
                return fixed
        return self.pool.acquire(identity.name, country=identity.country)
