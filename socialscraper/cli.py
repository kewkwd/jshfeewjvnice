import argparse
import contextlib
import json
import logging
import pathlib
import re
import shutil
import stat
import sys
import sqlite3
from .config import load, validate
from .store import Store
from .net.proxy import ProxyPool
from .net import proxymode
from .net.identity import IdentityManager, validate_name
from .net.pool import SessionPool
from .net.factory import build
from .net.cookies import CookieJar, parse_any
from .net import notify, humanize
from .net.accountproxy import AccountProxyError, Resolver, install_log_filter
from .net.errors import FinalMessage
from .net.deadline import deadline, FetchTimeout
from .net.errors import explain
from .net.session_guard import ensure, invalidate, State, SessionExpired
from .adapters import resolve
from .adapters.base import ScrapeContext
from .fetch_result import normalize_result
from . import commands, repo, rotation, runlock

SUBS = {'scrape', 'login', 'check', 'logout', 'import-cookies', 'profile'}
log = logging.getLogger('cli')


def _clean_message(message):
    # Der Planer nutzt dieselbe Bereinigung; erst zur Laufzeit importieren (er importiert cli).
    from .scheduler import clean_error
    return clean_error(message, limit=600)


class FetchProgress:
    """Speichert Zwischenstände sofort; zählt neue Daten und Kommentare niemals doppelt."""

    def __init__(self, store, on_saved=None):
        self.store, self.on_saved = store, on_saved
        self.video = None
        self.comments = set()
        self.videos_new = 0
        self.comments_new = 0

    def save(self, video, comments, captured_at=None):
        if video is None:
            return
        comments = list(comments)
        is_new, new_comments = self.store.save_result(video, comments, captured_at)
        self.video = video
        self.comments.update(c.uid for c in comments)
        self.videos_new += int(is_new)
        self.comments_new += new_comments
        if self.on_saved:
            self.on_saved(self)


def fetch_event(stage, platform, url, progress, comments_status, message='', success=True):
    """Maschinenlesbarer Beleg: Ausgabe erfolgt erst nach dem Datenbank-Commit."""
    video = progress.video
    payload = dict(version=1, stage=stage, platform=platform, url=str(url),
                   uid=video.uid if video is not None else None,
                   video_saved=video is not None, comments_saved=len(progress.comments),
                   comments_status=comments_status, success=bool(success),
                   message=_clean_message(message) if message else '')
    # Adressen können bei fehlerhaften CLI-Eingaben Zugangsdaten enthalten.
    from .scheduler import _URLCRED, _SECRET, _BEARER
    payload['url'] = _SECRET.sub(r'\1[entfernt]', _BEARER.sub(r'\1[entfernt]', _URLCRED.sub('', payload['url'])))
    print('[SS_FETCH] ' + json.dumps(payload, ensure_ascii=False), flush=True)

def setup_log(level):
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format='%(asctime)s %(levelname)-7s %(name)s | %(message)s', datefmt='%H:%M:%S')
    install_log_filter()   # Proxy-Passwörter erscheinen nie im Protokoll

def _nonnegative(value):
    number = int(value)
    if number < 0: raise argparse.ArgumentTypeError('Wert muss mindestens 0 sein')
    return number

def _positive(value):
    number = int(value)
    if number < 1: raise argparse.ArgumentTypeError('Wert muss mindestens 1 sein')
    return number

def _rotation_every(value):
    number = int(value)
    if not 1 <= number <= 100: raise argparse.ArgumentTypeError('Wert muss zwischen 1 und 100 liegen')
    return number

def _id_list(value):
    try:
        ids = [int(part) for part in str(value).split(',') if part.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError('Bitte Konto-Nummern mit Komma getrennt angeben, z. B. 1,2,3') from None
    if not ids or any(i < 1 for i in ids): raise argparse.ArgumentTypeError('Bitte Konto-Nummern mit Komma getrennt angeben, z. B. 1,2,3')
    return ids

def parser():
    ap = argparse.ArgumentParser(prog='socialscraper')
    sub = ap.add_subparsers(dest='cmd', required=True)
    def common(p, identity='default'):
        p.add_argument('--backend', choices=['local', 'sphere'])
        p.add_argument('--engine', choices=['patchright', 'nodriver', 'firefox'],
                       help='Browser-Steuerung: Firefox / Playwright unterstützt Desktop-Identitäten mit Proxy.')
        p.add_argument('--identity', default=identity)
        # Without explicit values the saved identity decides; new identities use us/desktop.
        p.add_argument('--country')
        p.add_argument('--mobile', action='store_true', default=None)
    def scrape_options(p):
        p.add_argument('--max-comments', type=_nonnegative)
        p.add_argument('--no-snapshot', action='store_true')
        p.add_argument('--export', metavar='DIR')
        p.add_argument('--require-auth', action='store_true', default=None)
        # Konten: ein bestimmtes Konto oder Rotation unter den Konten der Plattform (aus der Datenbank)
        p.add_argument('--rotate', action='store_true', help='Konten der Plattform abwechselnd benutzen')
        p.add_argument('--rotation-every', type=_rotation_every, metavar='N', help='Konto wechseln nach N Videos (1-100)')
        p.add_argument('--account-id', type=_positive, metavar='ID', help='bestimmtes Konto der Datenbank benutzen')
        p.add_argument('--accounts', type=_id_list, metavar='ID,ID', help='Rotation nur unter diesen Konten')
    scrape = sub.add_parser('scrape'); scrape.add_argument('urls', nargs='+')
    common(scrape); scrape_options(scrape)
    profile = sub.add_parser('profile'); profile.add_argument('username')
    common(profile, 'tiktok_main'); scrape_options(profile)
    profile.add_argument('--limit', type=_nonnegative, default=20)
    for command in ('login', 'check', 'logout', 'import-cookies'):
        p = sub.add_parser(command); common(p, 'tiktok_main')
        p.add_argument('--platform', choices=['tiktok', 'instagram', 'reddit', 'x', 'google', 'trustpilot', 'youtube'], default='tiktok')
        if command == 'login':
            p.add_argument('--timeout', type=_positive, default=300)
            p.add_argument('--mode', choices=['auto', 'qr', 'password'], default='auto')
        if command == 'import-cookies': p.add_argument('--file', required=True)
    commands.register_all(sub)
    return ap

def make_runtime(cfg, need_browser):
    """Identitäten, Browser-Backend und Session-Pool (Backend/Pool nur, wenn ein Browser gebraucht wird).

    Wird auch vom Planer (scheduler.py) und vom Hintergrunddienst benutzt.
    """
    pool = proxymode.make_pool(cfg)  # aus/liste: ProxyPool wie bisher; frei/tor: anonymer Pool (nie mit Konto)
    im = IdentityManager(pool, profiles_dir=cfg.profiles_dir)
    if not proxymode.is_anonymous(cfg) and getattr(cfg, 'db_path', None):
        im.account_proxies = Resolver(cfg.db_path)   # fester Proxy je Konto (hat Vorrang vor Proxy-Liste und „aus“)
    backend = sessions = None
    if need_browser:
        backend = build(cfg, im)
        sessions = SessionPool(backend, im, cfg.pool_size, cfg.session_ttl, headless=cfg.headless)
    return im, backend, sessions

def shutdown_runtime(backend, sessions):
    if sessions:
        try: sessions.shutdown()
        except Exception: log.warning('Browser-Sessions konnten nicht vollständig geschlossen werden')
    if backend and hasattr(backend, 'runner'):
        try: backend.runner.stop()
        except Exception: log.warning('Browser-Hintergrundlauf konnte nicht geschlossen werden')

def _ctx_and_backend(cfg, args, urls):
    adapters = [resolve(url) for url in urls]
    need = any(ad and ad.needs_browser for ad in adapters) or args.cmd in ('login', 'check', 'profile')
    return make_runtime(cfg, need)

def _resolve_identity(cfg, args):
    country, mobile = IdentityManager(None, profiles_dir=cfg.profiles_dir).stored(args.identity)
    if args.country is None: args.country = country or 'us'
    if args.mobile is None: args.mobile = bool(mobile)
    args.country = args.country.lower()

def _logout_path(root, path):
    """Refuse redirected profile/lock components before deleting account data."""
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError('Browserprofil liegt außerhalb des konfigurierten Profilordners.')
    for component in (path, *path.parents):
        if component == root:
            break
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or
                getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError('Verknüpfte Browserprofile können nicht automatisch abgemeldet werden.')


def _logout(cfg, args):
    identity = validate_name(args.identity)
    platform = validate_name(args.platform)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', platform):
        raise ValueError('Ungültige Plattform für das Browserprofil.')
    root = pathlib.Path(cfg.profiles_dir).resolve()
    profiles = (root / identity / platform, root / '_firefox' / identity / platform,
                *(root / '_retries' / engine / identity / platform for engine in ('chromium', 'firefox')))
    states = tuple(root / '_profile_status' / engine / identity / (platform + '.json')
                   for engine in ('chromium', 'firefox'))
    lock_path = root / '_firefox' / '_locks' / identity / (platform + '.lock')
    for path in (*profiles, *states, lock_path):
        _logout_path(root, path)
    lock = runlock.OsLock(lock_path)
    if not lock.try_acquire({'owner': 'Abmeldung'}):
        raise OSError('Firefox-Profil wird bereits verwendet; Browserfenster schließen und erneut abmelden')
    try:
        # The cookie export belongs to the account, so both persistent browser
        # profiles must be removed before clearing it. Firefox cannot reopen this
        # profile while the account's profile lock is held.
        for path in (*profiles, *states, lock_path):
            _logout_path(root, path)
        for profile in profiles:
            _logout_path(root, profile)
            if profile.exists():
                try:
                    shutil.rmtree(profile)
                except OSError:
                    raise OSError('Browserprofil ist noch geöffnet; Browserfenster schließen und erneut abmelden') from None
        for path in states:
            _logout_path(root, path)
            path.unlink(missing_ok=True)
        CookieJar.clear(identity, platform)
        invalidate(identity, platform)
    finally:
        lock.release()

def auth_state(adapter, cfg, identity, country, mobile, backend, im):
    """Anmeldung prüfen. Gibt (angemeldet, Zustand) zurück; Zustand ist None, wenn der Adapter keine Anmeldung braucht."""
    if not (getattr(adapter, 'needs_auth', False) and adapter.needs_browser): return False, None
    if proxymode.is_anonymous(cfg) or proxymode.is_anonymous_im(im): return False, None  # Proxy-Modus frei/tor: nie ein Konto prüfen oder öffnen
    state = ensure(backend, im, identity, adapter.platform, country, mobile, max_age=cfg.session_max_age)
    if state != State.OK:
        if cfg.require_auth: raise SessionExpired(f'Session für {adapter.platform} nicht verfügbar ({state})')
        log.warning('%s: ohne bestätigten Login (%s)', adapter.platform, state)
    return state == State.OK, state

def _auth(adapter, cfg, args, backend, im):
    return auth_state(adapter, cfg, args.identity, args.country, args.mobile, backend, im)[0]

def _anonymize(cfg, args, urls):
    """Proxy-Modus frei/tor: nur Abrufe ohne Konto, immer mit der anonymen Identität."""
    if args.cmd == 'profile':
        proxymode.assert_fetch_allowed(cfg, 'tiktok', cfg.max_comments, 'profile')
    for url in urls:
        adapter = resolve(url)
        if adapter is not None:
            proxymode.assert_fetch_allowed(cfg, adapter.platform, cfg.max_comments)
    args.identity, args.country = proxymode.anon_run(cfg)
    args.mobile = False

def _apply_account_options(cfg, args):
    """--account-id, --rotate, --accounts prüfen; --account-id setzt Konto und Land dieses Laufs."""
    if not (args.rotate or args.account_id or args.accounts):
        return
    if proxymode.is_anonymous(cfg):
        proxymode.refuse_accounts('Ein bestimmtes Konto und die Rotation gehen hier nicht.')   # frei/tor: nie ein Konto
    if args.rotate and args.account_id:
        raise ValueError('--rotate und --account-id passen nicht zusammen: entweder Rotation oder ein bestimmtes Konto.')
    if args.accounts and not args.rotate:
        raise ValueError('--accounts gehört zu --rotate.')
    if args.account_id:
        with Store(cfg.db_path) as store:
            account = repo.get_account(store.db, args.account_id)
        if account is None:
            raise ValueError(f'Das Konto {args.account_id} gibt es nicht.')
        stored_country, stored_mobile = IdentityManager(None, profiles_dir=cfg.profiles_dir).stored(account['identity'])
        args.identity = account['identity']
        args.country = args.country or stored_country or account['country'] or 'de'
        if args.mobile is None: args.mobile = bool(stored_mobile)
        platforms = {ad.platform for ad in (resolve(u) for u in (args.urls if args.cmd == 'scrape' else [])) if ad}
        if args.cmd == 'profile': platforms = {'tiktok'}
        if platforms - {account['platform']}:
            raise ValueError(f'Das Konto „{account["label"]}“ gehört zu {account["platform"]} und passt nicht zu diesen Links.')

def _execute(cfg, args):
    if args.cmd in ('login', 'check', 'import-cookies'):
        proxymode.require_no_anon(cfg, {'login': 'Anmelden', 'check': 'Anmeldung prüfen', 'import-cookies': 'Cookies importieren'}[args.cmd])
    if args.cmd == 'logout':
        _logout(cfg, args)
        print(f'Abgemeldet: {args.identity}/{args.platform} (Cookies und Browserprofil gelöscht)')
        return 0
    if args.cmd == 'import-cookies':
        cookies = CookieJar.for_platform(parse_any(pathlib.Path(args.file).read_text(encoding='utf-8-sig')), args.platform, strict=True)
        if not cookies: raise ValueError('Keine Cookies in der Importdatei gefunden')
        CookieJar.save(args.identity, args.platform, cookies)
        _clear_login_cooldown(cfg, args.identity)   # eine Pause wegen abgelaufener Anmeldung entfällt mit der neuen Anmeldung
        print(f'Cookies importiert: {args.identity}/{args.platform}')
        return 0
    urls = args.urls if args.cmd == 'scrape' else []
    if proxymode.is_anonymous(cfg): _anonymize(cfg, args, urls)
    im, backend, sessions = _ctx_and_backend(cfg, args, urls)
    try:
        with contextlib.ExitStack() as stack:
            # Nur ein Browserauftrag gleichzeitig (Hintergrunddienst, Fenster und Befehle teilen sich die Sperre).
            if backend is not None:
                stack.enter_context(runlock.acquire(runlock.root_for_db(cfg.db_path), 'cli'))
            # Gesamtlimit für den ganzen Befehl; danach wird sauber beendet und die Sperre frei.
            whole = stack.enter_context(deadline(cfg.target_timeout, 'Der Befehl'))
            return _run_locked(cfg, args, urls, im, backend, sessions, whole)
    finally:
        shutdown_runtime(backend, sessions)

def _clear_login_cooldown(cfg, identity):
    """Nach erfolgreicher Anmeldung/Prüfung: eine Abkühlung wegen abgelaufener Anmeldung entfällt."""
    try:
        with Store(cfg.db_path) as store:
            rotation.clear_login_cooldown(store.db, identity)
    except Exception:
        log.debug('Abkühlung konnte nicht aufgehoben werden', exc_info=True)


def _run_locked(cfg, args, urls, im, backend, sessions, whole=None):
    if args.cmd == 'login':
        from .login import login
        result = login(backend, im, args.identity, args.platform, country=args.country,
                       mobile=args.mobile, timeout=args.timeout, mode=args.mode, cfg=cfg)
        if result is not False:
            _clear_login_cooldown(cfg, args.identity)
        return 0 if result is not False else 2
    if args.cmd == 'check':
        from .login import check
        good = check(backend, im, args.identity, args.platform, args.country, args.mobile, cfg=cfg)
        if good:
            _clear_login_cooldown(cfg, args.identity)
        return 0 if good else 2
    with Store(cfg.db_path) as store:
        ctx = ScrapeContext(cfg=cfg, store=store, im=im, sessions=sessions,
                           identity=args.identity, country=args.country, mobile=args.mobile,
                           authenticated=False, require_auth=cfg.require_auth)
        auth_states, auth_errors = {}, {}
        login_failed = [False]
        switches = [0]
        rotating = bool(getattr(args, 'rotate', False))
        fixed_id = getattr(args, 'account_id', None)
        rotators = {}

        def rotator_for(platform):
            """Konto-Begleiter der Plattform (Rotation oder bestimmtes Konto); None, wenn die Plattform keine Konten hat
            oder weder --rotate noch --account-id gegeben ist. Wirft rotation.NoAccount."""
            if not (rotating or fixed_id) or platform not in repo.LOGIN_PLATFORMS:
                return None
            rot = rotators.get(platform)
            if rot is None:
                repo.ensure_default_accounts(store.db)
                rot = rotation.Rotator(store.db, platform, 'rotate' if rotating else 'fixed', fixed_id,
                                       every=cfg.rotation_every, allowed=getattr(args, 'accounts', None))
                rot.begin()
                rotators[platform] = rot
                apply(rot, platform)
            return rot

        def apply(rot, platform):
            """Das aktuelle Konto der Plattform zur Identität dieses Laufs machen (alten Browser schließen)."""
            account = rot.current
            if account is None or account['identity'] == args.identity:
                return
            old = args.identity
            stored_country, stored_mobile = im.stored(account['identity'])
            args.identity = account['identity']
            args.country = (stored_country or account['country'] or 'de').lower()
            args.mobile = bool(stored_mobile)
            ctx.identity, ctx.country, ctx.mobile = args.identity, args.country, args.mobile
            if sessions:
                try:
                    sessions.drop(old)
                except Exception:
                    log.warning('Browser-Session des vorigen Kontos konnte nicht geschlossen werden')
            auth_states.pop(platform, None)
            auth_errors.pop(platform, None)
            switches[0] += 1

        def switch(kind, key, platform):
            """Konto der Plattform ist ausgefallen: Abkühlung vermerken, bei Rotation das nächste Konto nehmen."""
            rot = rotators.get(platform)
            if rot is None:
                return False
            nxt = rot.fault(kind, key)
            if nxt is None:
                return False
            apply(rot, platform)
            return True

        def refresh(platform):
            """Neues Konto für die weiteren Videos (Block voll oder alle für dieses Video versucht)."""
            rot = rotators.get(platform)
            if rot is None or not rot.rotating:
                return
            before = rot.current['id'] if rot.current else None
            nxt = rot.next_block()
            if nxt is None:
                auth_errors[platform] = rot.message
            elif nxt['id'] != before:
                apply(rot, platform)

        def probe(adapter):
            """Anmeldung des aktuellen Kontos prüfen. Gibt die Fehlerart (login/proxy) oder None zurück."""
            platform = adapter.platform
            try:
                auth_states[platform] = _auth(adapter, cfg, args, backend, im)
            except Exception as error:
                auth_states[platform] = False
                auth_errors[platform] = _clean_message(error)
                if isinstance(error, SessionExpired):
                    login_failed[0] = True
                    auth_errors[platform] = ('Anmeldung nicht bestätigt: ' + auth_errors[platform] +
                                             ' Bitte unter „Konten“ neu anmelden.')
                    return 'login'
                return 'proxy' if isinstance(error, AccountProxyError) else None
            if rotating and not auth_states[platform] and getattr(adapter, 'needs_auth', False) and (
                    cfg.require_auth or platform in ('instagram', 'x')):
                auth_errors[platform] = 'Anmeldung nicht bestätigt. Bitte unter „Konten“ neu anmelden.'
                return 'login'
            return None

        def prepare(adapter, key):
            """Rotation: Anmeldung prüfen, bei Problemen zum nächsten Konto wechseln. True = bereit."""
            platform = adapter.platform
            while True:
                fault_kind = probe(adapter)
                if fault_kind is None:
                    return platform not in auth_errors
                if not switch(fault_kind, key, platform):
                    rot = rotators.get(platform)
                    if rot is not None and rot.rotating and rot.message:
                        auth_errors[platform] = FinalMessage(auth_errors[platform] + ' ' + rot.message)
                    return False

        if args.cmd == 'profile':
            from .adapters.tiktok import TikTokAdapter
            adapter = TikTokAdapter()
            profile_url = f'https://www.tiktok.com/@{args.username}'
            while True:
                try:
                    rotator_for(adapter.platform)
                    auth_states[adapter.platform] = _auth(adapter, cfg, args, backend, im)
                    ctx.authenticated = auth_states[adapter.platform]
                    urls = adapter.profile_urls(args.username, ctx, args.limit)
                    break
                except Exception as error:
                    fault_kind = None
                    if rotators.get(adapter.platform) is not None:
                        has_proxy = bool(rotators[adapter.platform].current and rotators[adapter.platform].current.get('proxy'))
                        fault_kind = rotation.classify_for(rotators[adapter.platform], error, has_proxy, 'profile')
                    if fault_kind and switch(fault_kind, 'profile', adapter.platform):
                        continue
                    message = _clean_message(error)
                    if isinstance(error, SessionExpired):
                        message = 'Anmeldung nicht bestätigt: ' + message + ' Bitte unter „Konten“ neu anmelden.'
                    rot = rotators.get(adapter.platform)
                    if rot is not None and rot.rotating and rot.message and fault_kind:
                        message += ' ' + rot.message
                    fetch_event('result', adapter.platform, profile_url, FetchProgress(store),
                                'unavailable', message, success=False)
                    print('\nfertig: 0 ok, 1 fehler'); print(store.stats())
                    return 3 if isinstance(error, SessionExpired) else 1
            auth_states[adapter.platform] = auth_states[adapter.platform] and ctx.authenticated   # Verlust einrasten
            log.info('%s Videos gefunden', len(urls))
        # Probe vor dem ersten gepoolten Browserlauf, damit kein Profil doppelt geöffnet wird.
        # (Bei Rotation prüft `prepare` die Anmeldung des jeweils aktuellen Kontos erst direkt vor dem Abruf.)
        for adapter in (resolve(url) for url in urls):
            if adapter and adapter.platform not in auth_states and not (rotating and adapter.platform in repo.LOGIN_PLATFORMS):
                try:
                    auth_states[adapter.platform] = _auth(adapter, cfg, args, backend, im)
                except Exception as error:
                    auth_states[adapter.platform] = False
                    auth_errors[adapter.platform] = _clean_message(error)
                    if isinstance(error, SessionExpired):
                        login_failed[0] = True
                        auth_errors[adapter.platform] = ('Anmeldung nicht bestätigt: ' + auth_errors[adapter.platform] +
                                                         ' Bitte unter „Konten“ neu anmelden.')
        ok = fail = incomplete = 0
        index = 0
        while index < len(urls):
            url = urls[index]
            progress = FetchProgress(store)
            adapter = resolve(url)
            if adapter is None:
                message = 'Kein Adapter für diesen Link. Bitte einen Link von TikTok, Instagram, YouTube, Reddit oder X verwenden.'
                log.error('Kein Adapter für diesen Link')
                fetch_event('result', '', url, progress, 'unavailable', message, success=False)
                fail += 1; index += 1; continue
            if whole is not None and whole.fired:
                fetch_event('result', adapter.platform, url, progress, 'unavailable', whole.message, success=False)
                fail += 1; index += 1; continue
            platform = adapter.platform
            rot = None
            if (rotating or fixed_id) and platform in repo.LOGIN_PLATFORMS and platform not in auth_errors:
                try:
                    rot = rotator_for(platform)
                except rotation.NoAccount as error:
                    auth_errors[platform] = FinalMessage(str(error))
                except (ValueError, OSError) as error:
                    auth_errors[platform] = _clean_message(error)
            if rot is not None and rotating and platform not in auth_states and platform not in auth_errors:
                prepare(adapter, url)
            if platform in auth_errors:
                fetch_event('result', platform, url, progress, 'unavailable', auth_errors[platform], success=False)
                fail += 1; index += 1
                continue
            ctx.authenticated = auth_states.get(platform, False)
            def save_checkpoint(video, comments):
                progress.save(video, comments)
                fetch_event('checkpoint', adapter.platform, url, progress, 'partial',
                            'Zwischenstand gespeichert. Der Abruf läuft noch.')
            ctx.checkpoint = save_checkpoint
            result = None
            error_message = None
            retry = False
            switched_before = switches[0]
            if rot is not None:
                rot.attempt()

            def has_proxy():
                return bool(rot is not None and rot.current and rot.current.get('proxy'))

            def account_fault(kind, key):
                """True: anderes Konto ist bereit (dann dasselbe Video noch einmal versuchen)."""
                if rot is None or not kind:
                    return False
                if switch(kind, key, platform):
                    return True
                if rot.rotating:
                    refresh(platform)   # für die weiteren Videos ein frisches Konto suchen
                elif kind in rotation.COOLDOWN and rot.current:   # Standard-/festes Konto: wie der Planer anhalten, nicht weitermachen
                    auth_errors[platform] = FinalMessage(   # fertige Meldung: nie erneut anhand von „CAPTCHA“ übersetzt
                        f'Konto „{rot.current["label"]}“ pausiert ({rotation.COOLDOWN[kind][0]}); die übrigen '
                        'Videos dieser Plattform wurden nicht abgerufen. Später erneut versuchen.')
                return False
            try:
                humanize.pace(adapter.platform)  # zufaellige Pause zwischen Abrufen derselben Plattform
                # Hartes Limit pro Video im Browser (Standard 120 s): danach wird der Browser beendet.
                with (deadline(cfg.video_timeout, 'Der Abruf', kill_browser=True) if adapter.needs_browser
                      else contextlib.nullcontext()):
                    result = normalize_result(adapter.fetch(url, ctx), cfg.max_comments)
                if whole is not None and whole.fired:
                    raise FetchTimeout(whole.message)
                if result.video is None:
                    error_message = result.message or 'Video nicht gefunden oder nicht abrufbar (die Plattform liefert keine Daten dazu).'
                    if result.message and progress.video is None:
                        retry = account_fault(rotation.classify_for(rot, result.message, has_proxy(), url), url)
                else:
                    captured_at = repo.iso() if cfg.snapshot else None   # dasselbe Format wie der Planer: JJJJ-MM-TTTHH:MM:SSZ
                    progress.save(result.video, result.comments, captured_at)
                    if rot is not None:
                        rot.success()
            except SessionExpired as error:
                if sessions:
                    try: sessions.drop(args.identity, adapter.platform)
                    except Exception: log.warning('%s: Browser-Session konnte nicht geschlossen werden', adapter.platform)
                error_message = _clean_message(error) + ' Bitte unter „Konten“ erneut anmelden.'
                retry = account_fault('login', url)
                if not retry:
                    auth_states[adapter.platform] = False
                    auth_errors[adapter.platform] = error_message
                    login_failed[0] = True
            except Exception as error:
                if whole is not None and whole.fired and not isinstance(error, FetchTimeout):
                    error = FetchTimeout(whole.message)
                error_message = _clean_message(error)
                if progress.video is None:
                    fault_kind = rotation.classify_for(rot, error, has_proxy(), url) if rot is not None else None
                    retry = account_fault(fault_kind, url)
                    if fault_kind == 'proxy' and not retry:
                        auth_errors[platform] = error_message   # fester Proxy tot: die übrigen Links dieser Plattform nicht mehr versuchen
            finally:
                ctx.checkpoint = None
                # Ging die Sitzung während des Abrufs verloren, bleibt sie für die weiteren Links verloren
                # (sonst meldet jeder Link den Verlust erneut: Datei + Webhook je URL).
                if switches[0] == switched_before:
                    auth_states[adapter.platform] = auth_states.get(adapter.platform, False) and ctx.authenticated
            if retry:
                log.info('Das Konto hat nicht geklappt; dasselbe Video wird mit dem nächsten Konto noch einmal versucht.')
                continue
            if error_message:
                log.error('%s: %s', adapter.platform, error_message)
                saved = progress.video is not None
                fetch_event('result', adapter.platform, url, progress, 'partial' if saved else 'unavailable',
                            error_message, success=False)
                if saved:
                    ok += 1; incomplete += 1
                else:
                    fail += 1
            else:
                is_partial = result.comments_status in ('unavailable', 'partial')
                fetch_event('result', adapter.platform, url, progress, result.comments_status, result.message)
                log.info('%s -> %s | %s Kommentare%s', adapter.platform, progress.video.video_id,
                         len(progress.comments), ' (Kommentarabruf unvollständig)' if is_partial else '')
                ok += 1
                incomplete += int(is_partial)
            index += 1
            if rot is not None and rot.video_done() and index < len(urls):
                refresh(platform)   # Block voll: nächstes Konto (kann dasselbe bleiben, wenn kein anderes bereit ist)
        used = [label for rot in rotators.values() for label in rot.used]
        if used:
            print('Benutzte Konten: ' + ', '.join(used))
        print(f'\nfertig: {ok} ok, {fail} fehler' + (f', {incomplete} teilabrufe' if incomplete else ''))
        print(store.stats())
        if args.export:
            store.dump_jsonl('videos', pathlib.Path(args.export) / 'videos.jsonl')
            store.dump_jsonl('comments', pathlib.Path(args.export) / 'comments.jsonl')
            print(f'export -> {args.export}/')
        if fail == 0 and incomplete == 0:
            return 0
        return 3 if login_failed[0] and ok == 0 else 1


def _log_daemon_failure(args, error):
    """Der Hintergrunddienst hat unter pythonw keine Konsole: Fehler zusätzlich ins daemon.log schreiben."""
    if getattr(args, 'cmd', None) == 'daemon' and 'daemon' in commands.REGISTRY:
        commands.REGISTRY['daemon'].log_failure(args, error)

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = parser()
    if not argv:
        ap.print_help(); return 0
    if argv[0] not in SUBS | set(commands.REGISTRY) and argv[0] not in ('-h', '--help'):
        argv = ['scrape'] + argv
    args = ap.parse_args(argv)
    try:
        cfg = load()
        for key in ('backend', 'engine', 'max_comments', 'require_auth', 'rotation_every'):
            value = getattr(args, key, None)
            if value is not None: setattr(cfg, key, value)
        if getattr(args, 'no_snapshot', False): cfg.snapshot = False
        validate(cfg)
        import os
        network = os.environ.get('SS_AUDIT_NETWORK')
        if network in ('proxy', 'offline'):
            from .net import testpolicy
            testpolicy.enable(cfg, offline=network == 'offline')
        setup_log(cfg.log_level)
        notify.configure(cfg)
        if args.cmd in commands.REGISTRY:
            return commands.REGISTRY[args.cmd].run(args, cfg)
        if args.cmd in ('scrape', 'profile'): _apply_account_options(cfg, args)
        validate_name(args.identity)
        _resolve_identity(cfg, args)
        return _execute(cfg, args)
    except runlock.Busy as error:
        log.error('%s', error); _log_daemon_failure(args, error); return 4
    except SessionExpired as error:
        log.error('%s', error); _log_daemon_failure(args, error); return 3
    except (OSError, ValueError, ImportError, RuntimeError, sqlite3.Error) as error:
        log.error('%s', error); _log_daemon_failure(args, error); return 1
    except KeyboardInterrupt:
        # Aufgeräumt ist schon (Sperre, Browser, Lauf als „abgebrochen“): nur noch eine Zeile statt Stacktrace.
        log.error('Abgebrochen (Strg+C).'); return 130

if __name__ == '__main__': sys.exit(main())



