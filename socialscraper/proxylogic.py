"""Logik der Proxy-Oberfläche ohne Tk: Modi und Texte, Prüfung, Farben, Hinweise für Konten/Abrufen/Automatik.

Die Regeln selbst („kostenlose Proxys/Tor nie mit einem Konto“) stehen in net/proxymode.py und werden hier nur
benutzt (`assert_fetch_allowed`), nicht noch einmal erfunden.
"""
import re
import types

from .config import PROXY_MODES

WARN_TEXT = ('Nur für öffentliche Abrufe ohne Konto (YouTube, Reddit, öffentliche TikTok-Videodaten). '
             'Konten, Anmeldung, Instagram, X und TikTok-Kommentare sind in diesem Modus gesperrt – aus Sicherheitsgründen.')

# (Wert, Titel der Karte, Erklärung, Warnkasten?)
MODE_CARDS = [
    ('aus', 'Aus (meine eigene Verbindung)',
     'Abrufe laufen über deine eigene Verbindung – oder über deine Proxy-Liste, falls eine eingetragen ist. Mit Konten möglich.', False),
    ('liste', 'Eigene Proxy-Liste (z. B. gekauft)',
     'Abrufe laufen über Proxys, die du selbst einträgst. Mit Konten möglich.', False),
    ('frei', 'Kostenlose Proxys',
     'Das Programm sucht kostenlose Proxys und testet sie. Nur ohne Konto.', True),
    ('tor', 'Tor',
     'Abrufe laufen über das Tor-Netzwerk, bei Bedarf automatisch gestartet. Nur ohne Konto.', True),
]
MODE_TITLES = {value: title for value, title, _text, _warn in MODE_CARDS}
FREE_HINT = 'Kostenlose Proxys sind langsam und unzuverlässig; sie sehen, welche Seiten du aufrufst.'
TOR_HINT = ('Das mitgelieferte Tor liegt im Ordner „tools\\tor“. Beim ersten Abruf im Modus „Tor“ wird es automatisch '
            'gestartet; mit den Knöpfen kannst du es auch von Hand starten und beenden.')
TOR_NOT_OURS = 'Tor läuft extern oder wurde nicht von diesem Programm gestartet – es wird nicht beendet.'
LIST_NOTE = ('Hinweis: Im Modus „Aus“ werden die eingetragenen Proxys trotzdem benutzt (wie bisher). '
             'Zum Bearbeiten oder Entfernen den Modus „Eigene Proxy-Liste“ wählen.')


def is_anonymous(mode):
    return mode in ('frei', 'tor')


def validate_mode(value):
    """(Modus, Fehlertext)."""
    if isinstance(value, str) and value in PROXY_MODES:
        return value, None
    return None, 'Bitte einen Proxy-Modus aus der Auswahl wählen.'


def current_mode(root):
    """Der gespeicherte Proxy-Modus (settings.toml, Umgebungsvariable SS_PROXY_MODE gilt auch); bei Fehlern 'aus'."""
    try:
        from . import guicore
        value = guicore.read_settings(root).proxy_mode
    except Exception:
        return 'aus'
    return value if value in PROXY_MODES else 'aus'


def banner_text(mode):
    """Hinweiszeile für Konten/Abrufen/Automatik, leer im Modus aus/liste."""
    if not is_anonymous(mode):
        return ''
    return f'Proxy-Modus ‚{MODE_TITLES[mode]}‘ aktiv: Konten sind gesperrt.'


BANNER_HINT = 'Anmelden, Instagram, X und TikTok-Kommentare gehen so nicht.'


def set_mode(root, mode):
    """Schreibt den Proxy-Modus nach settings.toml (genau wie das Einstellungsformular: Kommentare bleiben, Sicherung .bak).

    Wirft ValueError mit deutschem Text, wenn der Wert ungültig ist, die Datei nicht schreibbar ist oder die Umgebungsvariable
    SS_PROXY_MODE den Modus festlegt (dann würde die Änderung nichts bewirken)."""
    value, error = validate_mode(mode)
    if error:
        raise ValueError(error)
    from . import guicore
    guicore.write_settings({'proxy_mode': value}, root)
    if current_mode(root) != value:
        raise ValueError('Der Proxy-Modus wird von der Umgebungsvariable SS_PROXY_MODE festgelegt. '
                         'Entferne sie, damit die Einstellung gilt.')
    return value


def make_cfg(mode, proxies=(), root=None):
    """Einstellungen für describe/ensure_ready aus dem, was im Formular steht (auch ungespeichert)."""
    from .config import Settings
    cfg = Settings(proxy_mode=mode, proxies=list(proxies))
    if root is not None:
        import pathlib
        cfg.db_path = str(pathlib.Path(root) / 'data' / 'scrape.db')
    return cfg


def _gui_text(text):
    return re.sub(r'\s*\(--max-comments 0\)', ' (Kommentare pro Video auf 0 stellen)', text)


def fetch_problem(mode, platform, max_comments=0, kind='video'):
    """Warum dieser Abruf im Proxy-Modus nicht geht (Text) oder None. Nutzt die Regel aus net/proxymode.py."""
    from .net import proxymode
    cfg = types.SimpleNamespace(proxy_mode=mode)
    try:
        proxymode.assert_fetch_allowed(cfg, platform, max_comments, kind)
    except proxymode.AccountsForbidden as error:
        return _gui_text(str(error))
    return None


def target_problem(mode, target):
    """Wie `fetch_problem`, für ein Ziel der Automatik (Dict aus repo.targets)."""
    try:
        comments = int(target.get('max_comments') or 0)
    except (TypeError, ValueError):
        comments = 0
    return fetch_problem(mode, target.get('platform'), comments, target.get('kind') or 'video')


BLOCKED_TEXT = 'blockiert durch Proxy-Modus'


def status_kind(mode, ok):
    """Farbe der Statuszeile: ok = grün, sonst gelb (noch nicht bereit); bei Tor rot (fehlt/nicht erreichbar)."""
    if ok:
        return 'ok'
    return 'err' if mode == 'tor' else 'warn'


def free_summary(info):
    """Text über die Liste kostenloser Proxys aus freeproxy.status()."""
    if info.get('alter_s') is None:
        return 'Die Liste wurde noch nie aktualisiert.'
    count = info.get('vorhanden', 0)
    ms = [p['ms'] for p in info.get('proxys', []) if isinstance(p.get('ms'), (int, float))]
    age = info['alter_s'] // 60
    text = f'{count} funktionierende{"r" if count == 1 else ""} Proxy{"" if count == 1 else "s"}'
    if ms:
        text += f', schnellste Antwort {int(min(ms))} ms'
    text += f'. Liste {age} Minute{"" if age == 1 else "n"} alt'
    if not info.get('gueltig'):
        text += ' (abgelaufen – bitte aktualisieren)'
    return text + '.'


def refresh_summary(stats):
    """(ok, Text) aus der Statistik von freeproxy.refresh()."""
    found, working, bad_tls = stats.get('kandidaten', 0), stats.get('funktionierend', 0), stats.get('zertifikatsfehler', 0)
    text = f'{found} gefunden, {working} funktionieren, {bad_tls} wegen Zertifikatsfehler verworfen.'
    fastest = stats.get('schnellste') or []
    if working and fastest:
        text += f' Der schnellste braucht {fastest[0]["ms"]} ms.'
    if not working:
        text += ' Später noch einmal versuchen; ohne Proxy wird im Modus „Kostenlose Proxys“ nicht abgerufen.'
    return bool(working), text


def stop_tor_message(ok, message):
    """Antwort auf „Tor beenden“: ehrlich, wenn dieses Tor nicht von uns stammt."""
    if not ok and message.startswith('Es gibt kein von Socialscraper gestartetes Tor'):
        return False, TOR_NOT_OURS
    return ok, message


def tor_connection_text(found):
    """(ok, Text) aus tor.find(verify=True)."""
    if found.get('ok'):
        return True, f'Du bist über Tor verbunden (Anschluss {found["port"]}).'
    if found.get('port') is None:
        return False, 'Nicht verbunden: Tor läuft nicht. Mit „Tor starten“ lässt es sich starten.'
    return False, 'Nicht verbunden: ' + str(found.get('message') or 'Tor antwortet nicht.')
