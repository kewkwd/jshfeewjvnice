"""Abrufereignisse für die Oberfläche; Logtexte sind kein Erfolgsnachweis."""
import json

PREFIX = '[SS_FETCH] '
COMMENT_STATES = {'not_requested', 'received', 'empty', 'unavailable', 'partial'}
COMPLETE = {'not_requested', 'received', 'empty'}


def read_events(results):
    """Letzter bestätigter Speicherstand je Video/Link, auch nach einem Abbruch."""
    events = {}
    for _label, _code, lines in results:
        for line in lines:
            if not line.startswith(PREFIX):
                continue
            try:
                event = json.loads(line[len(PREFIX):])
                if (not isinstance(event, dict) or event.get('version') != 1
                        or event.get('stage') not in {'checkpoint', 'result'}
                        or event.get('comments_status') not in COMMENT_STATES
                        or type(event.get('video_saved')) is not bool
                        or type(event.get('comments_saved')) is not int
                        or event['comments_saved'] < 0
                        or not isinstance(event.get('url'), str) or not event['url']
                        or not isinstance(event.get('message', ''), str)):
                    continue
                # Der Original-Link bleibt der Wiederholungs-Schlüssel; UID fasst
                # Speicherpunkte und Abschluss auch bei aufgelösten Kurzlinks zusammen.
                key = event.get('uid') or event['url']
                alias = next((k for k, old in events.items() if old['url'] == event['url']), None)
                if alias is not None and alias != key:
                    events[key] = events.pop(alias)
                previous = events.get(key)
                if previous and previous['video_saved'] and not event['video_saved']:
                    event = {**event, 'video_saved': True,
                             'comments_saved': previous['comments_saved']}
                events[key] = event
            except (ValueError, TypeError):
                continue
    return list(events.values())


def complete(event):
    return (event['video_saved'] and event['stage'] == 'result'
            and event['comments_status'] in COMPLETE)


def retry_steps(steps, events, results, cancelled):
    """Nur unerledigte Links erneut abrufen; ursprüngliche Konto-/Abrufoptionen behalten."""
    from .. import guicore as core
    done = set()
    for event in events:
        if complete(event):
            done.add(core.canonical_video(event['url'])[0])
            if event.get('uid'):
                done.add(event['uid'])
    legacy_done = set()
    for label, code, lines in results:
        if any(line.startswith(PREFIX) for line in lines):
            continue
        summary = core.parse_summary(lines)
        if code == 0 and summary and summary[1] == 0:
            legacy_done.add(label)
    retry = []
    for label, command in steps:
        if label in legacy_done:
            continue
        if command[0] == 'scrape':
            end = next((i for i, arg in enumerate(command[1:], 1) if arg.startswith('--')), len(command))
            urls = [url for url in command[1:end] if core.canonical_video(url)[0] not in done]
            if urls:
                retry.append((label, ['scrape', *urls, *command[end:]]))
        elif command[0] == 'profile':
            # Nach Abbruch ist die Zahl noch nicht entdeckter Profilvideos unbekannt.
            # Upserts erhalten den Bestand bei einer erneuten Profilsuche.
            if cancelled or any(code != 0 for name, code, _ in results if name == label) or any(not complete(e) for e in events) or not results:
                retry.append((label, list(command)))
    return retry


def describe(event):
    name = {'tiktok': 'TikTok', 'instagram': 'Instagram', 'youtube': 'YouTube', 'reddit': 'Reddit',
            'x': 'X'}.get(event.get('platform'), 'Video')
    ident = str(event.get('uid') or '').split(':', 1)[-1] or event['url']
    prefix = f'{name} · {ident}: '
    if not event['video_saved']:
        return prefix + (event.get('message') or 'Nicht abgerufen. Anmeldung oder Verbindung prüfen und erneut versuchen.')
    count = event['comments_saved']
    saved = f'Videodaten und {count} ' + ('Kommentar gespeichert.' if count == 1 else 'Kommentare gespeichert.')
    state = event['comments_status']
    if state == 'not_requested':
        return prefix + 'Nur Videodaten gespeichert — so gewählt.'
    if state == 'empty':
        return prefix + 'Videodaten gespeichert. Die Plattform meldet keine Kommentare.'
    if state == 'received' and event['stage'] == 'result':
        return prefix + saved
    detail = event.get('message') or 'Kommentarabruf nicht abgeschlossen. Erneut versuchen; gespeicherte Daten bleiben erhalten.'
    return prefix + saved + ' ' + detail
