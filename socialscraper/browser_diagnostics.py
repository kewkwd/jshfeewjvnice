"""Read browser compatibility settings; never spoof properties or visit a site."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from .jsonfiles import write_report

from .net.identity import validate_name

SCRIPT = '''() => ({
 locale: navigator.language,
 languages: Array.from(navigator.languages),
 timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
 user_agent: navigator.userAgent,
 viewport: {width: innerWidth, height: innerHeight},
 device_scale: devicePixelRatio
})'''


def report_path(root, engine, identity, slot):
    if engine not in ('chromium', 'firefox'):raise ValueError('Unbekannter Browser.')
    validate_name(identity)
    if slot != '_general':validate_name(slot)
    return Path(root) / '_diagnostics' / engine / identity / (slot + '.json')


def _plain(path):
    return not any(p.is_symlink() or p.is_junction() for p in (path, *path.parents))


def _text(value, limit=80):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError('Ungültiger Diagnosewert.')
    return value


def _viewport(value):
    if not isinstance(value, dict):raise ValueError('Ungültige Fenstergröße.')
    result = {key:value.get(key) for key in ('width', 'height')}
    if any(type(v) is not int or not 0 < v <= 30000 for v in result.values()):
        raise ValueError('Ungültige Fenstergröße.')
    return result


def measured(value):
    """Project a strict allowlist: page URLs, cookies and extra fields cannot leak."""
    if not isinstance(value, dict):raise ValueError('Diagnose nicht verfügbar.')
    langs = value.get('languages')
    if not isinstance(langs, list) or not 1 <= len(langs) <= 20:
        raise ValueError('Ungültige Sprachliste.')
    scale = value.get('device_scale')
    if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 < scale <= 10:
        raise ValueError('Ungültiger Skalierungsfaktor.')
    return {'locale':_text(value.get('locale')), 'languages':[_text(v) for v in langs],
            'timezone':_text(value.get('timezone')), 'user_agent':_text(value.get('user_agent'), 512),
            'viewport':_viewport(value.get('viewport')), 'device_scale':scale}


def capture(page, fp, *, resizable=False):
    expected = {'locale':_text(fp.locale), 'timezone':_text(fp.timezone),
                'viewport':_viewport(fp.viewport)}
    report = {'version':1, 'captured_at':datetime.now(timezone.utc).isoformat(),
              'expected':expected, 'resizable':bool(resizable), 'observed':{},
              'issues':[], 'status':'unavailable'}
    try:
        actual = measured(page.evaluate(SCRIPT))
    except Exception:
        return report  # Never copy a library error containing page contents.
    issues = []
    for key in ('locale', 'timezone'):
        if actual[key] != expected[key]:issues.append(key)
    if not resizable and actual['viewport'] != expected['viewport']:issues.append('viewport')
    report.update(observed=actual, issues=issues, status='different' if issues else 'consistent')
    return report


def observe(backend, handle):
    """Called once after open; diagnostic failure must not end a valid session."""
    selection = getattr(handle, '_profile_selection', None)
    if selection is None:return
    engine = 'firefox' if handle.backend == 'firefox' else 'chromium'
    root = Path(backend.profiles)
    path = report_path(root, engine, handle.identity.name, handle.platform or '_general')
    if not _plain(path):raise ValueError('Umgeleiteter Diagnosepfad.')
    report = capture(backend.page(handle), handle.identity.fp,
                     resizable=os.environ.get('SS_EMBED_BROWSER') == '1' and engine != 'firefox'
                     and not handle.identity.fp.is_mobile)
    report.update(generation=selection.generation, profile_path=str(selection.path.absolute()))
    write_report(path, report, prefix='diagnostic-')


def summary(root, engine, identity, slot, profile_path):
    """Stored observation only; a previous profile must never look freshly tested."""
    path = report_path(root, engine, identity, slot)
    try:
        if not _plain(path):return 'Diagnosepfad umgeleitet; nicht gelesen.'
        data = json.loads(path.read_text(encoding='utf8'))
        if not isinstance(data, dict) or data.get('version') != 1:raise ValueError()
        if data.get('profile_path') != str(Path(profile_path).absolute()):
            return 'Für diesen Profilordner noch keine Messung; älterer Bericht vorhanden.'
        stamp = datetime.fromisoformat(data['captured_at']).astimezone().strftime('%d.%m.%Y %H:%M:%S')
        if data.get('status') == 'unavailable':return 'Browsermessung nicht verfügbar · ' + stamp
        actual = measured(data.get('observed'))
        # Recompute comparisons; do not trust arbitrary text in a saved report.
        expected = data['expected']
        checks = [('Sprache', actual['locale'], _text(expected['locale'])),
                  ('Zeitzone', actual['timezone'], _text(expected['timezone']))]
        text = ['Gemessen: ' + stamp + ' · keine Konto- oder Proxyprüfung']
        differences = [label for label, actual_value, expected_value in checks if actual_value != expected_value]
        if data.get('resizable') is not True and actual['viewport'] != _viewport(expected['viewport']):
            differences.append('Fenstergröße')
        text.append('Abweichungen: ' + (', '.join(differences) or 'Keine bei den geprüften Einstellungen'))
        for label, actual_value, expected_value in checks:
            text.append(f'{label}: {actual_value} · Soll: {expected_value}')
        size = actual['viewport']; wanted = _viewport(expected['viewport'])
        suffix = ' · veränderbares Fenster' if data.get('resizable') is True else (
            f" · Soll: {wanted['width']} × {wanted['height']}")
        text.append(f"Fenster: {size['width']} × {size['height']}" + suffix)
        text.append('Browserkennung: ' + actual['user_agent'])
        return '\n'.join(text)
    except FileNotFoundError:
        return 'Noch keine Browsermessung gespeichert.'
    except (OSError, ValueError, TypeError, KeyError):
        return 'Gespeicherte Browserdiagnose ist nicht lesbar.'
