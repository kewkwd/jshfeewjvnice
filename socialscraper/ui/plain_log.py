"""Kurze Alltagssprache im Fenster; die unveränderte Ausgabe bleibt separat erhalten."""
import re

from .. import guicore as core
from . import fetch_feedback

_HEADER = re.compile(r'^\d{2}:\d{2}:\d{2}\s+[A-Z]+\s+[\w.]+\s*\|\s*')
_NAMES = {'youtube': 'YouTube', 'tiktok': 'TikTok', 'instagram': 'Instagram', 'reddit': 'Reddit', 'x': 'X'}
_TECHNICAL = re.compile(r'(?:\b(?:curl_cffi|yt-dlp|yt_dlp|patchright|playwright|Traceback|\w+Error|State\.\w+)\b|net::|\[Errno)')


def registration_message(text):
    """Explain registration states without inventing success or a retry."""
    low = text.casefold()
    if 'kontoabschluss und mailabruf bestätigt' in low:
        return 'Fertig: Das Postfach ist eingerichtet und der E-Mail-Zugriff funktioniert. Die Zugangsdaten sind gespeichert.'
    if ('kontoabschluss wurde bestätigt' in low or 'konto bestätigt' in low) and ('mailabruf noch nicht' in low or 'mailabruf noch offen' in low):
        return 'Das Konto ist erstellt, aber der E-Mail-Zugriff ist noch nicht bestätigt. Prüfe das Kennwort und die Freigabe für E-Mail-Programme. Starte keine neue Registrierung.'
    if 'kein pool-eintrag' in low or 'kein fertiges postfach im pool' in low:
        return everyday_terms(text)
    if 'android-kennwort ist noch nicht bestätigt' in low or ('tatsächlich verwendete kennwort' in low and any(word in low for word in ('nicht', 'muss', 'bestätigen'))):
        return 'Das verwendete Kennwort ist noch nicht sicher bekannt. Bitte trage das tatsächlich verwendete Kennwort beim bestehenden Konto nach. Starte keine neue Registrierung.'
    if 'apk fehlt' in low or 'apk-vorrat' in low:
        return 'Eine benötigte App-Datei fehlt. Ergänze sie im App-Vorrat von Profilwerk und starte den Auftrag danach erneut.'
    if 'avd gehört nicht' in low or 'android-gerät gehört nicht' in low or 'avd-sitzung' in low and 'eigene' in low:
        return 'Das virtuelle Handy passt nicht eindeutig zum ausgewählten Profil. Der Auftrag wurde angehalten. Prüfe die Zuordnung in Profilwerk.'
    if 'headless' in low and ('sichtbar' in low or 'fenster' in low):
        return 'Für diesen Schritt wird eine sichtbare Geräteansicht benötigt. Öffne sie über die angezeigte Rückfrage in Profilwerk.'
    if any(word in low for word in ('feldbaum', 'formularansicht', 'eingabefeld', 'feldfokus', 'android-felder')) and any(word in low for word in ('nicht', 'fehlt', 'unklar', 'geändert')):
        return 'Die sichtbaren Eingabefelder konnten nicht sicher zugeordnet werden. Bitte prüfe die aktuelle Geräteansicht. Profilwerk wartet mit weiteren Eingaben.'
    if 'kein zweiter registrierungsversuch' in low or 'keine neue registrierung gestartet' in low:
        return everyday_terms(text)
    if 'captcha' in low and any(word in low for word in ('selbst lösen', 'im fenster lösen', 'manuell lösen')):
        return 'Deine Hilfe ist nötig: Bitte löse die Sicherheitsaufgabe im geöffneten Fenster. Danach prüft Profilwerk den nächsten Schritt.'
    if 'telefon-' in low or 'sms-bestätigung' in low:
        if any(word in low for word in ('bitte', 'verlangt', 'übernehmen', 'selbst')):
            return 'Deine Hilfe ist nötig: Bitte bestätige die Telefonnummer oder gib den empfangenen SMS-Code in der Geräteansicht ein. Ein E-Mail-Code wird dafür nicht verwendet.'
    if 'abgebrochen' in low and ('registrierung' in low or 'konto' in low):
        return everyday_terms(text)
    return None


def everyday_terms(text):
    replacements = (
        ('IMAP-Freigabe', 'Freigabe für E-Mail-Programme'), ('IMAP-Zugriff', 'E-Mail-Zugriff'),
        ('IMAP-Abruf', 'Abruf von E-Mails'), ('IMAP', 'Zugriff mit einem E-Mail-Programm'),
        ('AVD-Sitzung', 'Verbindung zum virtuellen Handy'), ('AVD', 'Android-Gerät'),
        ('APK-Vorrat', 'App-Vorrat'), ('APK', 'App-Datei'), ('Headless', 'Betrieb im Hintergrund'),
        ('headless', 'Betrieb im Hintergrund'), ('UI-Baum', 'Ansicht der Eingabefelder'),
        ('Feldbaum', 'Ansicht der Eingabefelder'), ('Feldfokus', 'ausgewähltes Eingabefeld'),
        ('Pool-Eintrag', 'Eintrag bei den gespeicherten Postfächern'),
        ('im Pool gespeichert', 'bei den Postfächern gespeichert'), ('im Pool', 'bei den gespeicherten Postfächern'),
        ('Checkpoint', 'gespeicherter Zwischenstand'), ('Proxyzuordnung', 'Zuordnung der Internetverbindung'),
        ('Der feste Proxy', 'Die fest zugeordnete Internetverbindung'),
        ('Fester Proxy vermerkt', 'Feste Internetverbindung gespeichert'),
        ('Proxy', 'Verbindungsweg'),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def simple_message(text):
    text = ' '.join(text.split())
    low = text.lower()
    explained = registration_message(text)
    if explained is not None:
        return explained
    notices = {
        '[session_missing]': 'Du bist noch nicht angemeldet. Melde dich unter „Konten“ an.',
        '[session_expired]': 'Die Anmeldung ist abgelaufen. Melde dich unter „Konten“ erneut an.',
        '[session_error]': 'Die Anmeldung konnte nicht geprüft werden. Prüfe deine Internetverbindung und versuche es erneut.',
        '[session_lost_runtime]': 'Die Anmeldung ist während des Abrufs verloren gegangen. Prüfe sie unter „Konten“.',
    }
    for prefix, message in notices.items():
        if low.startswith(prefix):
            return message
    if 'der feste proxy' in low or 'fester proxy vermerkt' in low:
        return everyday_terms(text)
    if 'hydrat' in low:
        return 'TikTok hat keine Angaben zu diesem Video geliefert. Versuche es später erneut.'
    if low.startswith('kein adapter'):
        return 'Dieser Link wird nicht unterstützt. Nutze einen Link von TikTok, Instagram, YouTube, Reddit oder X.'
    if 'ohne bestätigten login' in low or ('session für' in low and 'nicht verfügbar' in low):
        return 'Die Anmeldung konnte nicht bestätigt werden. Prüfe sie unter „Konten“.'
    if 'session verloren' in low and 'ohne anmeldung weiter' in low:
        return 'Du bist bei TikTok nicht mehr angemeldet. Wir versuchen es ohne Anmeldung weiter.'
    if 'cookies' in low and ('keine passenden' in low or 'enthält keine' in low):
        return 'Die Anmeldung konnte aus dieser Datei nicht übernommen werden. Wähle eine neue Datei aus einem angemeldeten Browser.'
    if 'csrf' in low or ('session' in low and any(word in low for word in ('bestätigt', 'anmeldung', 'erneut anmelden', 'verloren'))):
        return 'Die Anmeldung konnte nicht bestätigt werden. Melde dich unter „Konten“ erneut an.'
    if 'eingeloggt;' in low or low.startswith('[login_ok]'):
        return 'Du bist angemeldet. Die Anmeldung ist gespeichert.'
    if low.startswith('cookies importiert:'):
        return 'Die Anmeldedatei wurde übernommen. Prüfe die Anmeldung unter „Konten“.'
    if low.startswith('abgemeldet:'):
        return 'Du bist abgemeldet. Die gespeicherte Anmeldung wurde entfernt.'
    if 'frische cookies konnten nicht gespeichert' in low:
        return 'Die aktuelle Anmeldung konnte nicht gespeichert werden. Melde dich vor dem nächsten Abruf erneut an.'
    if 'cookie-datei konnte nicht gelesen' in low:
        return 'Die gespeicherte Anmeldung konnte nicht gelesen werden. Melde dich unter „Konten“ erneut an.'
    if re.search(r'\d+ ungültige cookies .* übersprungen', low):
        return 'Einige Angaben in der Anmeldedatei waren ungültig und wurden ausgelassen.'
    if low.startswith('bestätigungszeit der anmeldung konnte nicht vermerkt'):
        return 'Die Prüfung der Anmeldung konnte nicht gespeichert werden. Beim nächsten Abruf wird sie erneut geprüft.'
    if low.startswith('login-fenster öffnet;'):
        return 'Das Anmeldefenster öffnet sich. Melde dich dort an und beantworte mögliche Sicherheitsfragen.'
    if ('session' in low or 'browser-hintergrundlauf' in low) and ('geschlossen' in low or 'schließen' in low):
        return 'Ein Browserfenster konnte nicht geschlossen werden. Schließe es bitte selbst.'
    if 'zertifikat' in low or 'ssl' in low or 'certificate' in low:
        return 'Die sichere Internetverbindung konnte nicht geprüft werden. Versuche es mit einer anderen Verbindung.'
    if 'dns' in low or 'servername konnte nicht aufgelöst' in low:
        return 'Die Internetadresse konnte nicht gefunden werden. Prüfe deine Internetverbindung.'
    if 'http 429' in low or 'zu viele anfragen' in low:
        return 'Es waren zu viele Abrufe auf einmal. Warte etwas und versuche es später erneut.'
    if 'http 403/401' in low or 'zugriff verweigert' in low:
        return 'Die Plattform lässt diesen Abruf nicht zu. Versuche es später oder mit einer anderen Verbindung.'
    if 'captcha' in low and 'bitte' in low and any(
            instruction in low for instruction in ('selbst lösen', 'im fenster lösen', 'manuell lösen')):
        return text
    if 'captcha' in low or 'bist du ein mensch' in low:
        return 'Die Plattform möchte prüfen, ob du ein Mensch bist. Versuche es später erneut.'
    if 'zeitüberschreitung' in low or 'zeitlimit von' in low or 'timed out' in low:
        return 'Die Antwort hat zu lange gedauert. Prüfe deine Internetverbindung und versuche es erneut.'
    if 'proxy' in low and ('nicht erreichbar' in low or 'verbindung verweigert' in low):
        return 'Die gewählte Internetverbindung funktioniert nicht. Prüfe unter „Einstellungen“ den Bereich „Proxys“.'
    if 'kein' in low and 'verbindung zum server' in low:
        return 'Die Plattform ist gerade nicht erreichbar. Prüfe deine Internetverbindung und versuche es erneut.'
    if 'kommentarliste nicht gefunden' in low or 'kommentarliste konnte nicht geladen' in low:
        return 'Die Kommentare konnten nicht geladen werden. Prüfe die Anmeldung unter „Konten“ und versuche es erneut.'
    if 'das belegt keine leere kommentarliste' in low:
        return 'Es wurden keine Kommentare geladen. Ob das Video Kommentare hat, wissen wir noch nicht. Versuche es erneut.'
    if low.startswith('kommentarabruf unvollständig:'):
        return simple_message(text.split(':', 1)[1])
    text = text.replace('Gespeichert insgesamt:', 'In deiner Sammlung:')
    text = text.replace('Kommentare nicht abrufbar.', 'Die Kommentare konnten nicht geladen werden.')
    text = text.replace('Kommentarabruf unvollständig.', 'Der Abruf der Kommentare ist noch nicht fertig.')
    text = text.replace('Kommentarabruf', 'Abruf der Kommentare').replace('Browser-Session', 'Browserfenster')
    text = text.replace('Videometadaten', 'Angaben zum Video').replace('Datenbank', 'Sammlung')
    text = text.replace('Login', 'Anmeldung').replace('Session', 'Anmeldung')
    text = re.sub(r'\s*\(\w+(?:Error|Exception)\)', '', text)
    if _TECHNICAL.search(text):
        return 'Dabei gab es ein Problem. Öffne „Technische Details“, wenn du Hilfe bei diesem Fehler brauchst.'
    return everyday_terms(text)


class PlainLog:
    def __init__(self):
        self.reset()

    def reset(self):
        self.videos = {}
        self.in_traceback = False
        self.previous = None

    def format(self, line, target_name=None):
        if line is None:
            return None, ''
        line = str(line)
        if '\n' in line:
            rows = [self.format(part, target_name) for part in line.splitlines()]
            shown = [text for text, _tag in rows if text is not None]
            order = {'ERROR': 5, 'CRITICAL': 5, 'WARNING': 4, 'OK': 3, 'INFO': 2, 'MUTED': 1, '': 0}
            tag = max((tag for text, tag in rows if text is not None), key=lambda tag: order.get(tag, 0), default='')
            return ('\n'.join(shown) if shown else None), tag
        shown, tag = self._format(line, target_name)
        if shown is not None:
            key = shown.rstrip(' .…')
            if key == self.previous and tag not in ('ERROR', 'CRITICAL'):
                return None, tag
            self.previous = key
        return shown, tag

    def _format(self, line, target_name=None):
        """(Text oder None, Farbkennung). Verändert keine Rohzeile oder Abrufauswertung."""
        if not line or not line.strip():
            return None, ''
        text = line.strip()
        from ..registration_progress import PREFIX as REG_PREFIX, parse as parse_registration, description
        if text.startswith(REG_PREFIX):
            event = parse_registration(text)
            if event is None:
                return None, ''
            provider = {'web.de': 'WEB.DE', 'gmx.net': 'GMX', **_NAMES}.get(event['provider'], event['provider'])
            tag = 'OK' if event['phase'] == 'complete' else 'WARNING' if event['phase'] in ('waiting_confirmation', 'pending_mail', 'review_required') else 'MUTED'
            return provider + ': ' + description(event['phase'], event['provider']), tag
        from ..android.progress import PREFIX as ANDROID_PREFIX, parse as parse_android, LABELS
        if text.startswith(ANDROID_PREFIX):
            event = parse_android(text)
            return (LABELS[event['phase']] + '.' if event else None), 'MUTED'
        if text.startswith('[SS_') and not text.startswith('[SS_FETCH]'):
            return None, ''
        tag = core.log_level(text)
        if tag == 'DEBUG':
            return None, tag
        if _HEADER.match(text) or text.startswith(fetch_feedback.PREFIX):
            self.in_traceback = False
        if text.startswith('Traceback ('):
            self.in_traceback = True
            return 'Dabei gab es ein Problem. Einzelheiten stehen unter „Technische Details“.', 'WARNING'
        if self.in_traceback:
            if line[:1].isspace() or re.match(r'^\w+(?:Error|Exception):', text) or text.startswith(('During handling', 'The above exception', '^')):
                return None, tag
            self.in_traceback = False
        if text.startswith('[SS_FETCH]'):
            events = fetch_feedback.read_events([('', 0, [text])])
            if not events:
                return None, ''
            event = events[0]
            url = event['url']
            number = self.videos.setdefault(url, len(self.videos) + 1)
            platform = _NAMES.get(event.get('platform'), 'Abruf')
            label = f'{platform} · Video {number}: '
            count = core.plural(event['comments_saved'], 'Kommentar', 'Kommentare')
            if not event['video_saved']:
                return label + simple_message(event.get('message') or 'Der Abruf hat nicht geklappt. Versuche es erneut.'), 'ERROR'
            if event['stage'] == 'checkpoint':
                return label + f'Die Angaben zum Video und {count} sind gespeichert. Der Abruf läuft weiter.', 'MUTED'
            state = event['comments_status']
            if state == 'not_requested':
                return label + 'Die Angaben zum Video sind gespeichert. Kommentare waren nicht ausgewählt.', 'OK'
            if state == 'empty':
                return label + 'Die Angaben zum Video sind gespeichert. Die Plattform meldet: Dieses Video hat keine Kommentare.', 'OK'
            if state == 'received':
                return label + f'Die Angaben zum Video und {count} sind gespeichert.', 'OK'
            result = f'Die Angaben zum Video und {count} sind gespeichert. Die gewünschten Kommentare konnten noch nicht vollständig geladen werden.'
            detail = simple_message(event.get('message', ''))
            return label + result + ' ' + (detail or 'Klicke auf „Erneut versuchen“. Die gespeicherten Daten bleiben erhalten.'), 'WARNING'
        shown = core.display_line(text, target_name)
        if shown is None:
            return None, tag
        shown = _HEADER.sub('', shown)
        if shown.startswith(('{', '[{', 'fertig: {')):
            return 'Der Auftrag hat zusätzliche Ergebnisdaten geliefert. Sie stehen unter „Technische Details“.', tag
        if shown.startswith('YouTube-JavaScript:'):
            return None, tag
        if shown.startswith(('Konto: ', core.USED_ACCOUNTS_PREFIX)):   # Kontonamen nie als Fehlertext deuten
            return shown, tag
        pause = re.fullmatch(r'Konto „(.+)“ pausiert bis (\S+Z) \((.+)\)', shown)
        if pause:
            from .. import rotation, repo
            try:
                until = rotation.fmt_when(repo.parse(pause[2]))
            except ValueError:
                until = pause[2]
            return f'Das Konto „{pause[1]}“ macht eine Pause bis {until} ({pause[3]}). Die Rotation nimmt solange ein anderes.', 'WARNING'
        retry = re.search(r'fehlgeschlagen \((\d+)/(\d+)\) - retry in ([\d.]+)s', shown)
        if retry:
            seconds = f'{float(retry[3]):g}'.replace('.', ',')
            return f'Es hat noch nicht geklappt (Versuch {retry[1]} von {retry[2]}). In {seconds} Sekunden versuchen wir es erneut.', tag
        loaded = re.fullmatch(r'(youtube|tiktok|instagram|reddit|x)(?: -> | )[^ |:]+(?: \||:) (\d+) Kommentare(.*)', shown, re.I)
        if loaded:
            suffix = ' Der Abruf ist noch nicht fertig.' if loaded[3] else ''
            return f'{_NAMES[loaded[1].lower()]}: {core.plural(int(loaded[2]), "Kommentar wurde", "Kommentare wurden")} geladen.' + suffix, tag
        if shown.startswith('export -> '):
            return 'Die Dateien wurden exportiert nach: ' + shown[len('export -> '):], tag
        if shown.startswith('YouTube-Impersonation deaktiviert:'):
            return 'Ein Hilfsprogramm für YouTube fehlt oder ist zu alt. Starte „Einrichten.cmd“, um es zu aktualisieren.', 'WARNING'
        if shown.startswith('Kostenloser Proxy gewählt:'):
            return 'Wir nutzen eine kostenlose Zwischenstation für deine Internetverbindung.', tag
        if re.fullmatch(r'Tor-Anschluss \d+ wird benutzt', shown):
            return 'Die Internetverbindung über Tor wird benutzt.', tag
        proxy_test = re.fullmatch(r'(\d+) Kandidaten aus (\d+) Listen, teste parallel \.\.\.', shown)
        if proxy_test:
            return f'{proxy_test[1]} mögliche Internetverbindungen werden geprüft. Bitte warte kurz.', tag
        proxy_result = re.fullmatch(r'(\d+) funktionieren, (\d+) behalten, (\d+) mit Zertifikatsfehler', shown)
        if proxy_result:
            return f'{proxy_result[1]} Verbindungen funktionieren. Davon werden {proxy_result[2]} gespeichert. {proxy_result[3]} Verbindungen konnten nicht sicher geprüft werden.', tag
        if shown.startswith('YouTube über diesen Proxy fehlgeschlagen;'):
            return 'YouTube ist über diese Verbindung nicht erreichbar. Wir versuchen eine andere.', tag
        if 'Handy-Emulation konnte' in shown:
            return 'Die Seite konnte nicht in der Handyansicht geöffnet werden.', tag
        if 'Headless-Zusatz' in shown:
            return 'Die Chrome-Version konnte nicht erkannt werden. Das kann den Abruf erschweren.', tag
        platform = re.match(r'^(youtube|tiktok|instagram|reddit|x): (.*)$', shown, re.I)
        if platform:
            shown = _NAMES[platform[1].lower()] + ': ' + simple_message(platform[2])
        else:
            shown = simple_message(shown)
        if _TECHNICAL.search(shown):
            shown = 'Dabei gab es ein Problem. Öffne „Technische Details“, wenn du Hilfe bei diesem Fehler brauchst.'
        return shown, tag
