"""Merkzettel der Oberfläche (ohne Tk): data/ui_state.json.

Hier steht nur Bequemlichkeit: Fenstergröße und -ort, zuletzt geöffneter Tab, zuletzt benutzte Auswahl, ob die
Erste-Schritte-Liste ausgeblendet ist. Nie Geheimnisse, nie Einstellungen (die stehen in settings.toml).
Die Datei wird atomar geschrieben und ist fehlertolerant: fehlt sie oder ist sie kaputt, gilt alles wie beim ersten Start.
"""
import json
import os
import pathlib
import threading

FILE_NAME = 'ui_state.json'
MAX_BYTES = 200_000

# Erlaubte Schlüssel und ihre Art; alles andere wird beim Laden verworfen.
SCHEMA = {
    'geometry': dict,       # {'w','h','x','y','zoomed'}
    'tab': str,
    'log_open': (bool, type(None)),
    'fetch': dict,          # {'accounts': {plattform: Anzeigename}, 'group': Name, 'mode': Reiter}
    'data': dict,           # {'platform','length','mode','sort_key','sort_desc'}
    'home': dict,           # {'checklist_hidden', 'skip_account', 'proxy_ok', 'viewed_data', 'dismissed': {schlüssel: iso}}
    'problems_seen_at': str,
    'last_backup_at': str,
    'help_opened': bool,
}


def path_for(root):
    return pathlib.Path(root) / 'data' / FILE_NAME


def sanitize(raw):
    """Nur bekannte Schlüssel mit der richtigen Art bleiben; sonst leeres Wörterbuch."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, kind in SCHEMA.items():
        if key in raw and isinstance(raw[key], kind) and not (kind is bool and isinstance(raw[key], int) and not isinstance(raw[key], bool)):
            out[key] = raw[key]
    return out


def load(path):
    try:
        path = pathlib.Path(path)
        if path.stat().st_size > MAX_BYTES:
            return {}
        return sanitize(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError):
        return {}


def save(path, state):
    """Atomar schreiben (Wegwerfdatei + Umbenennen). Gibt True zurück, wenn es geklappt hat; wirft nie."""
    path = pathlib.Path(path)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(sanitize(state), ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


class UiState:
    """Wörterbuch mit Speichern. `get('home', 'proxy_ok')`, `set('tab', 'Konten')`, `update_in('home', checklist_hidden=True)`."""

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.data = load(self.path)
        self._lock = threading.Lock()

    def get(self, key, sub=None, default=None):
        value = self.data.get(key, default)
        if sub is None:
            return value
        return value.get(sub, default) if isinstance(value, dict) else default

    def set(self, key, value, save_now=True):
        with self._lock:
            if self.data.get(key) == value:
                return
            self.data[key] = value
            if save_now:
                save(self.path, self.data)

    def update_in(self, key, **fields):
        current = dict(self.data.get(key) or {}) if isinstance(self.data.get(key), dict) else {}
        current.update(fields)
        self.set(key, current)

    def flush(self):
        with self._lock:
            return save(self.path, self.data)


def clamp_geometry(geo, screen_w, screen_h, min_w=860, min_h=620, screen_x=0, screen_y=0):
    """Gespeicherte Fenstergröße/-lage so zurechtrücken, dass das Fenster auf dem Bildschirm liegt.

    `screen_x`/`screen_y` ist die linke obere Ecke des Desktops (bei mehreren Bildschirmen der ganze Desktop; ein
    Monitor links vom Hauptbildschirm macht sie negativ). Gibt (w, h, x, y) zurück; x und y sind None, wenn die Lage
    nicht stimmt (dann entscheidet Windows). Ungültige Angaben ergeben None (dann gilt die Standardgröße)."""
    if not isinstance(geo, dict):
        return None
    try:
        w, h = int(geo['w']), int(geo['h'])
    except (KeyError, TypeError, ValueError):
        return None
    w = max(min_w, min(w, screen_w))
    h = max(min_h, min(h, screen_h))
    x = y = None
    try:
        x, y = int(geo['x']), int(geo['y'])
    except (KeyError, TypeError, ValueError):
        return w, h, None, None
    # Mindestens ein Stück der Titelleiste muss sichtbar bleiben (zweiter Bildschirm weg, Auflösung geändert …)
    if x + w < screen_x + 120 or x > screen_x + screen_w - 120 or y < screen_y - 10 or y > screen_y + screen_h - 80:
        return w, h, None, None
    return w, h, x, y
