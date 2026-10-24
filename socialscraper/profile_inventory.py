"""Read-only inventory; stored markers are not evidence of a running session."""
from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
import sqlite3

from .android import avd
from .backends.profile_store import _read
from .net.identity import validate_name


@dataclass(frozen=True)
class ProfileRow:
    identity: str
    browser: str
    slot: str
    path: str
    status: str
    android: str
    accounts: str
    diagnostics: str = ''


def _plain(path):
    return not any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))


def inventory(profiles_dir, db_path, android_home=None):
    """Inspect owned metadata and shallow folder layouts, never cookies or credentials."""
    root = Path(profiles_dir).absolute()
    home = Path(android_home if android_home is not None else avd.HOME).absolute()
    warnings, rows, accounts, devices, seen = [], [], {}, set(), set()

    def children(path):
        try:
            if not _plain(path):
                warnings.append('Umgeleiteter Profilpfad ausgelassen.')
                return []
            if not path.exists():return []
            return sorted((p for p in path.iterdir() if _plain(p)), key=lambda p:p.name)
        except OSError:
            warnings.append('Ein Profilordner konnte nicht gelesen werden.')
            return []

    database = Path(db_path).absolute()
    try:
        if database.is_file() and _plain(database):
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                columns = {r[1] for r in db.execute('PRAGMA table_info(accounts)')}
                if columns:
                    extra = 'app_avd' if 'app_avd' in columns else 'NULL'
                    for identity, platform, label, device in db.execute(
                            'SELECT identity, platform, label, ' + extra + ' FROM accounts'):
                        validate_name(identity)
                        accounts.setdefault(identity, []).append((str(platform), str(label), device))
        else:
            warnings.append('Keine lesbare Kontendatenbank; Kontozuordnungen fehlen.')
    except (OSError, sqlite3.Error, ValueError):
        warnings.append('Kontozuordnungen konnten nicht vollständig gelesen werden.')

    for path in children(home):
        if path.is_dir() and path.suffix == '.avd':devices.add(path.stem)

    assigned = set()
    def add(identity, browser, slot, path, status):
        try:validate_name(identity)
        except ValueError:
            warnings.append('Ungültige Profilkennung ausgelassen.')
            return
        if path is not None:
            if not _plain(path):return
            key = str(path).casefold()
            if key in seen:return
            seen.add(key)
            if not path.is_dir():status += ' · Ordner fehlt'
        linked = accounts.get(identity, [])
        explicit = {d for _, _, d in linked if isinstance(d, str) and d}
        expected = avd.name_for(identity)
        android = []
        android_reports = []
        from .android.diagnostics import summary as android_summary
        for device in sorted(explicit):
            assigned.add(device)
            android.append(device + (' (zugeordnet)' if device in devices else ' (zugeordnet, fehlt)'))
            if device in devices:android_reports.append(android_summary(home, device))
        if expected not in explicit and expected in devices:
            assigned.add(expected)
            android.append(expected + ' (aus Profilkennung abgeleitet)')
            android_reports.append(android_summary(home, expected))
        diagnostic = ''
        if path is not None:
            from .browser_diagnostics import summary
            engine = 'firefox' if browser == 'Firefox' else 'chromium'
            try:diagnostic = summary(root, engine, identity, slot, path)
            except ValueError:diagnostic = 'Keine Diagnose für diesen älteren Profilbereich verfügbar.'
        diagnostic = '\n'.join(filter(None, [diagnostic, *android_reports]))
        rows.append(ProfileRow(identity, browser, slot, str(path) if path else '', status,
                               '; '.join(android) or 'Kein AVD vorhanden/zugeordnet',
                               '; '.join(p + ': ' + label for p, label, _ in linked) or 'Kein gespeichertes Konto',
                               diagnostic))

    for engine in ('chromium', 'firefox'):
        browser = 'Chrome/Chromium' if engine == 'chromium' else 'Firefox'
        for identity_dir in children(root / '_profile_status' / engine):
            if not identity_dir.is_dir():continue
            for state in children(identity_dir):
                if state.suffix != '.json' or not state.is_file():continue
                try:
                    validate_name(identity_dir.name)
                    slot = state.stem
                    if slot != '_general':validate_name(slot)
                    data = _read(state)
                except (ValueError, OSError):
                    warnings.append('Ein Browser-Profilstatus ist beschädigt.')
                    continue
                generation = data['generation']
                path = (root / '_retries' / engine / identity_dir.name / slot / generation if generation
                        else root / '_firefox' / identity_dir.name / slot if engine == 'firefox'
                        else root / identity_dir.name if slot == '_general'
                        else root / identity_dir.name / slot)
                status = {'clean':'Sauber beendet', 'failed':'Fehlgeschlagen',
                          'in_use':'Als verwendet markiert; Laufstatus ungeprüft'}[data['status']]
                add(identity_dir.name, browser, slot, path, status)
        for identity_dir in children(root / '_retries' / engine):
            if not identity_dir.is_dir():continue
            for slot_dir in children(identity_dir):
                if not slot_dir.is_dir():continue
                for path in children(slot_dir):
                    if path.is_dir():add(identity_dir.name, browser, slot_dir.name, path, 'Älterer Versuch / ohne aktuellen Status')

    # Legacy profiles may predate the status marker. Do not descend into browser storage.
    for path in children(root):
        if path.is_dir() and not path.name.startswith('_'):
            if (path / 'Local State').is_file() or (path / 'Default').is_dir():
                add(path.name, 'Chrome/Chromium', '_general', path, 'Ohne aktuellen Status')
            else:
                for slot in children(path):
                    if slot.is_dir():add(path.name, 'Chrome/Chromium', slot.name, slot, 'Ohne aktuellen Status')
    for identity_dir in children(root / '_firefox'):
        if identity_dir.is_dir():
            for slot in children(identity_dir):
                if slot.is_dir():add(identity_dir.name, 'Firefox', slot.name, slot, 'Ohne aktuellen Status')
    identities = set(accounts)
    for path in children(root.parent / 'identities'):
        if path.is_file() and path.suffix == '.json':identities.add(path.stem)
    for path in children(root.parent / 'profile_settings'):
        if path.is_file() and path.suffix == '.json':identities.add(path.stem)
    existing = {r.identity for r in rows}
    for identity in sorted(identities - existing):
        add(identity, 'Noch kein Browserprofil', '', None, 'Nur Identität/Kontozuordnung')
    for device in sorted(devices - assigned):
        from .android.diagnostics import summary as android_summary
        rows.append(ProfileRow('Nicht zugeordnet', 'Android', '', str(home / (device + '.avd')),
                               'Vorhandener Ordner; Betrieb ungeprüft', device, 'Keine belegte Zuordnung',
                               android_summary(home, device)))
    return rows, list(dict.fromkeys(warnings))
