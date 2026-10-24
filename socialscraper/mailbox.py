import contextlib, email, email.utils, http.client, imaplib, json, logging, math, os, re, ssl, time, urllib.error, urllib.request
import html
from html.parser import HTMLParser
from urllib.parse import urlsplit
from email.header import decode_header, make_header
log = logging.getLogger(__name__)

DEFAULT_PATTERN = r'\b(\d{4,8})\b'
PLATFORM_PATTERNS = {
    'tiktok': (r'(?:tiktok|verifizier|bestätig|code)[^\d\n]{0,80}?(\d{4,8})', DEFAULT_PATTERN),
    'instagram': (r'(?:instagram|bestätig|verifizier|code)[^\d\n]{0,80}?(\d{4,8})', DEFAULT_PATTERN),
    'google': (r'(?:google|bestätig|verifizier|code)[^\d\n]{0,80}?(\d{4,8})', DEFAULT_PATTERN),
    'youtube': (r'(?:google|bestätig|verifizier|code)[^\d\n]{0,80}?(\d{4,8})', DEFAULT_PATTERN),
    'reddit': (r'(?:reddit|verifizier|code)[^\d\n]{0,80}?(\d{4,8})', DEFAULT_PATTERN),
    'x': (r'(?:twitter|x\.com|verifizier|code)[^\d\n]{0,80}?(\d{4,8})', DEFAULT_PATTERN),
}
FROM_HINTS = {                                                # FIX B10
    'tiktok': ('tiktok',), 'instagram': ('instagram', 'mail.instagram'),
    'google': ('google', 'accounts.google'), 'youtube': ('google',),
    'reddit': ('reddit',), 'x': ('twitter', 'x.com', 'info.x.com'),
    'trustpilot': ('trustpilot',),
}
FROM_DOMAINS = {'tiktok': ('tiktok.com',), 'instagram': ('instagram.com',),
                'google': ('google.com',), 'youtube': ('google.com',),
                'reddit': ('reddit.com', 'redditmail.com'),
                'x': ('twitter.com', 'x.com'), 'trustpilot': ('trustpilot.com',)}

def _sender_matches(sender, platform):
    roots = FROM_DOMAINS.get(platform)
    if not roots:
        return True
    for _, address in email.utils.getaddresses([sender]):
        domain = address.rpartition('@')[2].lower()
        if any(domain == root or domain.endswith('.' + root) for root in roots):
            return True
    return False
LINK_MUSTER = {
    'trustpilot': (r'https://[^\s"<>]*trustpilot\.com/[^\s"<>]*(?:verify|confirm|activate)[^\s"<>]*',
                   r'https://[^\s"<>]*trustpilot\.com/[^\s"<>]+'),
    'google': (r'https://[^\s"<>]*accounts\.google\.com/[^\s"<>]+',),
}
ANBIETER = {
    'web.de': {'host': 'imap.web.de', 'port': 993, 'ssl': True, 'ordner': 'INBOX'},
    'gmx.net': {'host': 'imap.gmx.net', 'port': 993, 'ssl': True, 'ordner': 'INBOX'},
    'gmx.de': {'host': 'imap.gmx.net', 'port': 993, 'ssl': True, 'ordner': 'INBOX'},
    'gmail.com': {'host': 'imap.gmail.com', 'port': 993, 'ssl': True, 'ordner': 'INBOX',
                  'hinweis': 'Google verlangt ein App-Passwort.'},
    'outlook.com': {'host': 'outlook.office365.com', 'port': 993, 'ssl': True, 'ordner': 'INBOX',
                    'hinweis': 'Microsoft verlangt bei Zwei-Faktor ein App-Passwort.'},
    'outlook.de': {'host': 'outlook.office365.com', 'port': 993, 'ssl': True, 'ordner': 'INBOX',
                   'hinweis': 'Microsoft verlangt bei Zwei-Faktor ein App-Passwort.'},
}

def vorlage(adresse):
    return dict(ANBIETER.get(str(adresse).rpartition('@')[2].lower()) or {})

class MailboxError(RuntimeError):
    pass

class MailboxLoginError(MailboxError):
    """The server rejected the credentials; polling cannot fix that login."""
    retryable = False

def _checked_limit(limit):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise MailboxError('Die Nachrichtenanzahl muss eine ganze Zahl ab null sein.')
    return limit

def _collection(data):
    """Accept JSON and JSON-LD collection formats returned by mailbox APIs."""
    if isinstance(data, dict):
        data = data.get('hydra:member', data.get('member'))
    if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
        raise MailboxError('Der Postfach-Dienst hat keine gültige Eintragsliste geliefert.')
    return data

def _mail_tunnel(proxy, host, port, timeout=20):
    if proxy is not None:
        from .net.tunnel import open_tunnel
        return open_tunnel(proxy, host, port, timeout=timeout)
    from .net.testpolicy import open_route
    return open_route(host, port, timeout=timeout)

def _decode_body(teil):
    try:
        roh = teil.get_payload(decode=True) or b''
    except Exception:
        return ''
    try:
        return roh.decode(teil.get_content_charset() or 'utf-8', errors='replace')
    except LookupError:
        return roh.decode('utf-8', errors='replace')

def _strip_html(text):
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', text, flags=re.S | re.I)
    text = re.sub(r'<br\s*/?>|</p>|</div>', '\n', text, flags=re.I)
    return re.sub(r'[ \t]{2,}', ' ', re.sub(r'<[^>]+>', ' ', text).replace('&nbsp;', ' ').replace('&amp;', '&'))


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for key, value in attrs:
                if key == 'href' and value and value.startswith('https://'):
                    self.links.append(value)


def _html_text(text):
    parser = _Links()
    parser.feed(text)
    return html.unescape(_strip_html(text)) + '\n' + '\n'.join(parser.links)

def _text_of(message):
    # Prune an entire attachment subtree, including text inside attached EMLs.
    if message.get_content_disposition() == 'attachment':
        return ''
    if message.is_multipart() and message.get_content_maintype() == 'multipart':
        return '\n'.join(_text_of(part) for part in message.get_payload())
    if message.get_content_type() == 'text/html':
        return _html_text(_decode_body(message))
    if message.get_content_type() == 'text/plain':
        return _decode_body(message)
    # Encapsulated messages and binary payloads are not the current mail body.
    return ''


def _api_text(message):
    plain = message.get('text') or ''
    html_parts = message.get('html') or []
    if isinstance(html_parts, str):
        html_parts = [html_parts]
    if (not isinstance(plain, str) or not isinstance(html_parts, list)
            or any(not isinstance(part, str) for part in html_parts)):
        raise MailboxError('Der Postfach-Dienst hat ungültige Nachrichteninhalte geliefert.')
    # A nonempty plain alternative can still omit the HTML button's URL.
    return '\n'.join(part for part in [plain, *map(_html_text, html_parts)] if part)

def _subject(message):
    try:
        return str(make_header(decode_header(message.get('Subject') or '')))
    except Exception:
        return message.get('Subject') or ''

def _delivery_stamp(parts, message):
    # INTERNALDATE is server metadata, not text in the RFC822 message body.
    for part in parts:
        metadata = part[0] if isinstance(part, tuple) and part else part
        if not isinstance(metadata, bytes):
            continue
        try:
            delivered = imaplib.Internaldate2tuple(metadata)
            if delivered is not None:
                return time.mktime(delivered)
        except (KeyError, ValueError, OverflowError, OSError):
            continue
    try:
        return email.utils.parsedate_to_datetime(message.get('Date') or '').timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None

def _patterns(pattern, platform):
    if pattern:
        return (pattern,) if isinstance(pattern, str) else tuple(pattern)
    return PLATFORM_PATTERNS.get(platform or '', (DEFAULT_PATTERN,))

def find_code(text, patterns):
    for muster in patterns:
        treffer = re.search(muster, text, flags=re.I | re.S)
        if treffer:
            gruppen = [g for g in treffer.groups() if g]
            if gruppen:
                return gruppen[0]
    return None

def find_link(text, muster):
    for m in muster:
        treffer = re.search(m, text, flags=re.I)
        if treffer:
            return treffer.group(0).rstrip(').,;')
    return None

class Mailbox:
    name = 'postfach'

    def address(self):
        raise NotImplementedError

    def fetch(self, limit=15):
        raise NotImplementedError

    def check_access(self):
        """Read at most one message; an accessible empty inbox is valid too."""
        messages = self.fetch(limit=1)
        if not isinstance(messages, list) or any(not isinstance(item, dict) for item in messages):
            raise MailboxError('Der Postfach-Dienst hat keine gültige Nachrichtenliste geliefert.')
        return len(messages)

    def _warte(self, sucher, *, platform, timeout, interval, newer_than, stop, fehlertext):
        try:
            valid = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) for value in (timeout, interval))
        except OverflowError:
            valid = False
        if not valid or timeout < 0 or interval <= 0:
            raise MailboxError('Zeitlimit und Prüfintervall sind ungültig.')
        frist = time.monotonic() + timeout
        while True:
            if stop is not None and stop():
                raise MailboxError('Abgebrochen.')
            interrupted = False
            try:
                for mail in self.fetch():
                    if stop is not None and stop():
                        interrupted = True
                        break
                    if newer_than and (mail.get('stamp') or 0) < newer_than:
                        continue
                    if not _sender_matches(mail.get('from') or '', platform):
                        continue
                    wert = sucher(mail)
                    if stop is not None and stop():
                        interrupted = True
                        break
                    if wert:
                        return wert, mail
            except MailboxLoginError:
                raise
            except MailboxError:
                pass
            if interrupted or (stop is not None and stop()):
                raise MailboxError('Abgebrochen.')
            if time.monotonic() >= frist:
                raise MailboxError(fehlertext.format(minuten=timeout // 60))
            from .operation import wait
            if not wait(min(interval, max(0, frist - time.monotonic())), stop):
                raise MailboxError('Abgebrochen.')

    def wait_for_code(self, *, platform=None, pattern=None, timeout=240, interval=5, newer_than=None, stop=None):
        muster = _patterns(pattern, platform)
        return self._warte(lambda m: find_code(m['subject'] + '\n' + m['text'], muster), platform=platform,
                           timeout=timeout, interval=interval, newer_than=newer_than, stop=stop,
                           fehlertext='Keine Mail mit Code innerhalb von {minuten} Minuten.')

    def wait_for_link(self, *, platform=None, muster=None, timeout=300, interval=5, newer_than=None, stop=None):
        regeln = muster or LINK_MUSTER.get(platform or '', ())
        if not regeln:
            raise MailboxError(f'Für {platform} ist kein Linkmuster hinterlegt.')
        def search(mail):
            link = find_link(mail['subject'] + '\n' + mail['text'], regeln)
            if link and platform in ('google', 'trustpilot'):
                parsed = urlsplit(link)
                roots = FROM_DOMAINS[platform]
                host = parsed.hostname or ''
                if parsed.scheme != 'https' or parsed.username or not any(host == root or host.endswith('.' + root) for root in roots):
                    return None
            return link
        return self._warte(search, platform=platform,
                           timeout=timeout, interval=interval, newer_than=newer_than, stop=stop,
                           fehlertext='Kein Bestätigungslink innerhalb von {minuten} Minuten.')

class ImapBox(Mailbox):
    name = 'imap'

    def __init__(self, host, user, password, *, port=993, folder='INBOX', ssl_on=True, proxy=None):
        self.host, self.user, self.password = host, user, password
        self.port, self.folder, self.ssl_on = int(port), folder, ssl_on
        self.proxy = proxy

    def address(self):
        return self.user

    def fetch(self, limit=15):
        return self._fetch(limit)

    def check_access(self):
        return len(self._fetch(1, strict=True))

    def _fetch(self, limit, *, strict=False):
        if _checked_limit(limit) == 0:
            return []
        try:
            if self.proxy is not None or os.environ.get('SS_REQUIRE_PROXY') == '1':
                class ProxiedImap(imaplib.IMAP4):
                    def _create_socket(inner, timeout):
                        return _mail_tunnel(self.proxy,inner.host,inner.port,timeout=timeout or 20)
                class ProxiedImapSSL(imaplib.IMAP4_SSL):
                    def _create_socket(inner, timeout):
                        raw=_mail_tunnel(self.proxy,inner.host,inner.port,timeout=timeout or 20)
                        try:
                            return inner.ssl_context.wrap_socket(raw,server_hostname=inner.host)
                        except BaseException:
                            raw.close();raise
                plain,secure=ProxiedImap,ProxiedImapSSL
            else:
                plain,secure=imaplib.IMAP4,imaplib.IMAP4_SSL
            box = (secure(self.host, self.port, ssl_context=ssl.create_default_context(), timeout=20)
                   if self.ssl_on else plain(self.host, self.port, timeout=20))
        except OSError as fehler:
            raise MailboxError(f'Das Postfach {self.host} ist nicht erreichbar ({fehler}).') from None
        try:
            try:
                box.login(self.user, self.password)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error:
                raise MailboxLoginError('Die Anmeldung am Postfach wurde abgelehnt. '
                                        'Bitte Postfachzugang und IMAP-Freigabe prüfen.') from None
            typ, _ = box.select(self.folder, readonly=True)
            if typ != 'OK':
                raise MailboxError('Der Postfachordner konnte nicht geöffnet werden.')
            typ, daten = box.search(None, 'ALL')
            if typ != 'OK':
                raise MailboxError('Das Postfach ließ sich nicht durchsuchen.')
            ausgabe = []
            for nummer in reversed((daten[0] or b'').split()[-limit:]):
                typ, roh = box.fetch(nummer, '(RFC822 INTERNALDATE)')
                if typ != 'OK' or not roh:
                    if strict:
                        raise MailboxError('Die Nachricht im Posteingang konnte nicht gelesen werden.')
                    continue
                payload = next((r[1] for r in roh if isinstance(r, tuple) and len(r) > 1 and isinstance(r[1], bytes)), None)
                if payload is None:
                    if strict:
                        raise MailboxError('Der Postfach-Dienst hat keinen lesbaren Nachrichteninhalt geliefert.')
                    continue
                nachricht = email.message_from_bytes(payload)
                stamp = _delivery_stamp(roh, nachricht)
                ausgabe.append({'subject': _subject(nachricht), 'text': _text_of(nachricht),
                                'from': nachricht.get('From') or '', 'date': nachricht.get('Date') or '',
                                'stamp': stamp})
            return ausgabe
        except imaplib.IMAP4.error as fehler:
            raise MailboxError(f'Die Anmeldung am Postfach schlug fehl ({fehler}).') from None
        except OSError:
            raise MailboxError('Die Verbindung zum Postfach wurde unterbrochen.') from None
        finally:
            try:
                box.logout()
            except Exception:
                pass

class TempMailBox(Mailbox):
    name = 'wegwerf'
    BASIS = 'https://api.mail.tm'

    def __init__(self, address, password, *, basis=None, domain=None, proxy=None):
        self._address, self._password = address, password
        self.basis, self.domain, self._token = (basis or self.BASIS).rstrip('/'), domain, None
        self.proxy = proxy

    def address(self):
        return self._address

    @classmethod
    def create(cls, *, basis=None, domain=None, proxy=None):
        import random
        basis = (basis or cls.BASIS).rstrip('/')
        kennung = random.randrange(10 ** 9, 10 ** 10)
        if not domain:
            eintraege = _collection(_json_get(f'{basis}/domains', proxy=proxy))
            if not eintraege or not eintraege[0].get('domain'):
                raise MailboxError('Der Wegwerf-Dienst nennt keine Domain.')
            domain = eintraege[0]['domain']
        passwort = f'ss-{kennung}-{kennung}'
        adresse = f'socialscraper{kennung}@{domain}'
        _json_post(f'{basis}/accounts', {'address': adresse, 'password': passwort}, proxy=proxy)
        return cls(adresse, passwort, basis=basis, domain=domain, proxy=proxy)

    def _login(self):
        if self._token:
            return self._token
        self._token = (_json_post(f'{self.basis}/token',
                                  {'address': self._address, 'password': self._password}, proxy=self.proxy) or {}).get('token')
        if not self._token:
            raise MailboxError('Der Wegwerf-Dienst hat keinen Zugang ausgegeben.')
        return self._token

    def fetch(self, limit=15):
        if _checked_limit(limit) == 0:
            return []
        import datetime
        kopf = {'Authorization': f'Bearer {self._login()}'}
        eintraege = _collection(_json_get(f'{self.basis}/messages?page=1', kopf, proxy=self.proxy))
        ausgabe = []
        for eintrag in eintraege[:limit]:
            stamp = None
            try:
                stamp = datetime.datetime.fromisoformat(
                    (eintrag.get('createdAt') or '').replace('Z', '+00:00')).timestamp()
            except Exception:
                pass
            voll = _json_get(f'{self.basis}/messages/{eintrag["id"]}', kopf, proxy=self.proxy) or {}
            ausgabe.append({'subject': voll.get('subject') or '',
                            'text': _api_text(voll),
                            'from': ((voll.get('from') or {}).get('address') or ''),
                            'date': voll.get('createdAt') or '', 'stamp': stamp})
        return ausgabe

def _proxy_request(anfrage, proxy):
    """Bind every request to the selected route, regardless of NO_PROXY."""
    parts = urlsplit(anfrage.full_url)
    if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username is not None:
        raise MailboxError('Ungültige Adresse des Postfach-Dienstes.')
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    with contextlib.ExitStack() as cleanup:
        if parts.scheme == 'https':
            context = ssl.create_default_context()
            connection = http.client.HTTPSConnection(parts.hostname, port, timeout=20, context=context)
        else:
            connection = http.client.HTTPConnection(parts.hostname, port, timeout=20)
        cleanup.callback(connection.close)
        raw = _mail_tunnel(proxy, parts.hostname, port, timeout=20)
        cleanup.callback(raw.close)
        if parts.scheme == 'https':
            raw = context.wrap_socket(raw, server_hostname=parts.hostname)
            cleanup.callback(raw.close)
        connection.sock = raw
        path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        connection.request(anfrage.get_method(), path, body=anfrage.data, headers=dict(anfrage.header_items()))
        response = connection.getresponse()
        cleanup.callback(response.close)
        # Do not move credentials to a redirected service or retry directly.
        if not 200 <= response.status < 300:
            raise MailboxError(f'Der Dienst antwortete mit {response.status}.')
        return response.read()

def _request(url, *, data=None, kopf=None, proxy=None):
    koerper = json.dumps(data).encode('utf-8') if data is not None else None
    anfrage = urllib.request.Request(url, data=koerper, method='POST' if koerper else 'GET')
    anfrage.add_header('Accept', 'application/json')
    if koerper:
        anfrage.add_header('Content-Type', 'application/json')
    for name, wert in (kopf or {}).items():
        anfrage.add_header(name, wert)
    try:
        if proxy is not None or os.environ.get('SS_REQUIRE_PROXY') == '1':
            roh = _proxy_request(anfrage, proxy).decode('utf-8', errors='replace')
        else:
            with urllib.request.urlopen(anfrage, timeout=20) as antwort:
                roh = antwort.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as fehler:
        raise MailboxError(f'Der Dienst antwortete mit {fehler.code}.') from None
    except OSError as fehler:
        raise MailboxError(f'Keine Verbindung zum Postfach-Dienst ({fehler}).') from None
    except http.client.HTTPException:
        raise MailboxError('Der Postfach-Dienst hat keine gültige HTTP-Antwort geliefert.') from None
    try:
        return json.loads(roh) if roh else {}
    except ValueError:
        raise MailboxError('Der Postfach-Dienst hat keine lesbare Antwort geliefert.') from None

def _json_get(url, kopf=None, *, proxy=None):
    return _request(url, kopf=kopf, proxy=proxy)

def _json_post(url, daten, kopf=None, *, proxy=None):
    return _request(url, data=daten, kopf=kopf, proxy=proxy)

def build(cfg, *, proxy=None):                                 # FIX A7
    cfg = dict(cfg or {})
    cfg = {**vorlage(cfg.get('benutzer') or cfg.get('adresse') or ''), **cfg}
    art = cfg.get('art') or 'imap'
    if art == 'imap':
        if not cfg.get('host') or not cfg.get('benutzer') or not cfg.get('passwort'):
            raise MailboxError('Für das Postfach fehlen Server, Benutzer oder Passwort.')
        return ImapBox(cfg['host'], cfg['benutzer'], cfg['passwort'],
                       port=cfg.get('port', 993), folder=cfg.get('ordner', 'INBOX'), ssl_on=cfg.get('ssl', True), proxy=proxy)
    if art == 'wegwerf':
        if cfg.get('adresse') and cfg.get('passwort'):
            return TempMailBox(cfg['adresse'], cfg['passwort'], basis=cfg.get('basis'), domain=cfg.get('domain'), proxy=proxy)
        neu = TempMailBox.create(basis=cfg.get('basis'), domain=cfg.get('domain'), proxy=proxy)
        cfg['adresse'], cfg['passwort'] = neu.address(), neu._password
        return neu
    raise MailboxError(f'Unbekannte Postfach-Art: {art}')
