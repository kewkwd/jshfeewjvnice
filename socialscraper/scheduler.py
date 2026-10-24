"""Planer: führt EIN Ziel der Automatik aus (Video erneut abrufen oder neue Videos entdecken und abrufen).

    result = run_target(root, target_id, 'daemon')

Die Sperre (runlock) nimmt NICHT diese Funktion, sondern der Aufrufer (Hintergrunddienst, Befehl
`run-target`, Fenster) – so lässt sich die Funktion auch aus einem schon gesperrten Ablauf aufrufen.
Jeder Lauf steht in der Tabelle `runs`; bricht der Prozess mitten im Lauf ab, räumt
`repo.recover_stale_runs` beim nächsten Start des Dienstes auf.
"""
import contextlib
import dataclasses
import logging
import pathlib
import re

from . import channels, cli, discovery, people_repo, repo, rotation
from .adapters import resolve
from .adapters.base import ScrapeContext
from .fetch_result import normalize_result
from .config import load, validate
from .net import humanize
from .net import accountproxy, proxymode
from .net.deadline import FetchTimeout, deadline
from .net.errors import explain
from .net.session_guard import SessionExpired, State
from .store import Store

log = logging.getLogger(__name__)

YOUTUBE_IDENTITY = 'youtube_main'
REDDIT_IDENTITY = 'reddit_main'
DEFAULT_COUNTRY = 'de'
_SECRET = re.compile(r'(?i)((?:token|password|passwd|secret|sessionid\w*|authorization|api[_-]?key|apikey|cookie|key)'
                     r'["\']?\s*[=:]\s*)[^\s,;&"\']+')
_BEARER = re.compile(r'(?i)(bearer\s+)\S+')
_URLCRED = re.compile(r'(?<=://)[^/\s:@]+:[^/\s@]+@')


def clean_error(error, limit=300):
    """Deutscher Fehlertext ohne Geheimnisse (Tokens, Passwörter, Zugangsdaten in Adressen); rohe Netzfehler werden übersetzt."""
    raw = str(error).strip()
    text = explain(error).strip()
    if raw and text == ' '.join(raw.split()):
        text = raw.splitlines()[0]   # eigener Text unverändert: nur die erste Zeile
    elif not raw:
        text = type(error).__name__
    text = accountproxy.scrub(text)   # bekannte Proxy-Passwörter (auch ohne Adressform) -> *****
    text = _URLCRED.sub('', text)
    text = _BEARER.sub(r'\1[entfernt]', text)
    text = _SECRET.sub(r'\1[entfernt]', text)
    return text[:limit]   # die übersetzten Meldungen sind kürzer als das Limit


def load_config(root):
    """settings.toml im Projektordner laden; relative Pfade (Datenbank, Profile, …) gelten ab `root`."""
    root = pathlib.Path(root)
    cfg = load(str(root / 'settings.toml'))
    for name in ('db_path', 'profiles_dir', 'notify_file', 'credentials_file'):
        value = getattr(cfg, name)
        if value and not pathlib.Path(value).is_absolute():
            setattr(cfg, name, str(root / value))
    return validate(cfg)


def account_for(db, target):
    """Konto des Ziels: das gewählte, sonst das Standardkonto der Plattform (YouTube hat keine Konten)."""
    if target['platform'] == 'youtube':
        return None
    account = repo.get_account(db, target['account_id']) if target.get('account_id') else None
    return account or repo.default_account(db, target['platform'])


def login_message(state, label):
    if state == State.EXPIRED:
        return f'Anmeldung abgelaufen für Konto „{label}“. Bitte unter „Konten“ neu anmelden.'
    if state == State.ERROR:
        return f'Die Anmeldung für Konto „{label}“ konnte nicht geprüft werden (Netz oder Browser). Später erneut versuchen.'
    return f'Anmeldung fehlt für Konto „{label}“. Bitte unter „Konten“ anmelden.'


def _cancel_check(cancel):
    if cancel is None:
        return lambda: False
    return cancel.is_set if hasattr(cancel, 'is_set') else cancel


def _need_browser(target, urls):
    if target['platform'] in ('tiktok', 'reddit', 'x'):
        return True
    return any(getattr(resolve(u), 'needs_browser', False) for u in urls)


def run_target(root, target_id, source='daemon', *, cfg=None, cancel=None):
    """Führt ein Ziel aus und gibt {'run_id','status','videos_seen','videos_new','comments_new','error'} zurück.

    `videos_seen` zählt die erfolgreich abgerufenen Videos, `videos_new` die davon neuen, `comments_new`
    die zum ersten Mal gesehenen Kommentare. `cancel` ist ein threading.Event oder eine Funktion, die
    True liefert, wenn abgebrochen werden soll (Prüfung zwischen den Videos).
    """
    cfg = cfg or load_config(root)
    cancelled = _cancel_check(cancel)
    with Store(cfg.db_path) as store:
        db = store.db
        repo.ensure_default_accounts(db)
        target = repo.get_target(db, target_id)
        if target is None:
            raise ValueError('Ziel nicht gefunden')
        chan = None
        if target['kind'] == 'channel':
            # Kanäle: Ruhezeit, Pause und Tagesgrenze zuerst prüfen. Ist jetzt nicht die Zeit, entfällt der Lauf ganz
            # (kein Eintrag im Laufprotokoll, `next_run_at` ist gesetzt).
            wait = channels.gate(db, target)
            if wait is not None:
                log.info('Kanal %s: %s', target['value'], wait[0])
                return {'run_id': None, 'target_id': target_id, 'status': 'ok', 'error': None, 'accounts_used': [],
                        'videos_seen': 0, 'videos_new': 0, 'comments_new': 0, 'waiting': wait[0]}
            chan = channels.ChannelRun(db, target, cfg)
            target = chan.target
        label = f"{target['kind']} {target['platform']} {target['value']}"[:120]
        run_id = repo.start_run(db, target_id, label, source)
        counts = {'videos_seen': 0, 'videos_new': 0, 'comments_new': 0}
        used = []   # Namen der in diesem Lauf benutzten Konten
        status, error = 'failed', None
        try:
            # Gesamtlimit pro Ziel (Standard 900 s): danach Browser schließen, Lauf endet mit „Zeitlimit erreicht ...“.
            with deadline(cfg.target_timeout, 'Das Ziel') as dl:
                status, error = _execute(cfg, store, target, cancelled, counts, dl, used, chan)
        except KeyboardInterrupt:
            status, error = 'cancelled', 'Abgebrochen'
            raise
        except SystemExit:
            status, error = 'cancelled', 'Abgebrochen'
            raise
        except Exception as exc:
            status, error = 'failed', clean_error(exc)
            log.warning('Ziel %s fehlgeschlagen: %s', target_id, error)
            log.debug('Einzelheiten', exc_info=True)
        finally:
            try:   # Kommentarautoren, die in diesem Lauf zum ersten Mal in der Datenbank auftauchen
                started = db.execute('SELECT started_at FROM runs WHERE id=?', (run_id,)).fetchone()
                counts['authors_new'] = people_repo.count_new(db, 'commenter', started[0]) if started and started[0] else 0
            except Exception:
                counts['authors_new'] = 0
            try:
                repo.finish_run(db, run_id, status, error=error, accounts_used=used or None, **counts)
            except Exception:
                log.exception('Lauf %s konnte nicht abgeschlossen werden', run_id)
            if chan is not None:
                try:   # Pause bei Sperre/CAPTCHA/429, sonst nächster Lauf mit Zufall; Reste zurück in die Warteschlange
                    chan.finish(status, error, chan.rot)
                except Exception:
                    log.exception('Kanal %s: Planung nach dem Lauf fehlgeschlagen', target_id)
        log.info('Ziel %s: %s (%s Videos, %s neu, %s neue Kommentare)', target_id, status,
                 counts['videos_seen'], counts['videos_new'], counts['comments_new'])
        return {'run_id': run_id, 'target_id': target_id, 'status': status, 'error': error,
                'accounts_used': list(used), **counts}


def target_mode(target):
    """Kontomodus eines Ziels: default (Standardkonto) | fixed (bestimmtes Konto) | rotate (Rotation)."""
    mode = target.get('account_mode')
    if mode in repo.ACCOUNT_MODES:
        return mode
    return 'fixed' if target.get('account_id') else 'default'


def _execute(cfg, store, target, cancelled, counts, dl=None, used=None, chan=None):
    db = store.db
    kind, platform = target['kind'], target['platform']
    used = used if used is not None else []
    anon = proxymode.is_anonymous(cfg)
    rot = None
    account = None
    if anon:
        # Kostenlose Proxys/Tor: nur Abrufe ohne Konto, nur mit der anonymen Identität (kein Cookie, kein Login).
        try:
            proxymode.assert_fetch_allowed(cfg, platform, int(target['max_comments']), kind)
        except proxymode.AccountsForbidden as exc:
            return 'failed', str(exc)
        identity, country = proxymode.anon_run(cfg)
        account_label = 'anonym'
    elif platform == 'youtube':
        identity, country, account_label = YOUTUBE_IDENTITY, DEFAULT_COUNTRY, 'YouTube'
    else:
        rot = rotation.Rotator(db, platform, target_mode(target), target.get('account_id'),
                               every=getattr(cfg, 'rotation_every', 5))
        rot.used = used
        if chan is not None:
            chan.rot = rot
        try:
            account = rot.begin()
        except rotation.NoAccount as exc:
            return 'failed', str(exc)
        if account is None and platform == 'reddit':
            # Reddit geht auch ohne Konto (der Browser ist dann nicht angemeldet).
            identity, country, account_label = REDDIT_IDENTITY, DEFAULT_COUNTRY, 'Reddit'
        elif account is None:
            return 'failed', f'Für {platform} gibt es kein Konto. Bitte unter „Konten“ eines anlegen.'
        else:
            identity, country, account_label = account['identity'], account['country'] or DEFAULT_COUNTRY, account['label']

    run_cfg = dataclasses.replace(cfg, max_comments=int(target['max_comments']))
    urls = [target['value']] if kind == 'video' else []
    im, backend, sessions = cli.make_runtime(run_cfg, _need_browser(target, urls))
    try:
        ctx = ScrapeContext(cfg=run_cfg, store=store, im=im, sessions=sessions, identity=identity,
                            country=country, mobile=False, authenticated=False, require_auth=run_cfg.require_auth)
        authed, blocked = {}, {}
        switches = [0]   # Zahl der Kontowechsel in diesem Lauf (der Abrufschritt merkt so, dass er nicht mehr sein Konto hat)

        def login_check():
            """Anmeldung des aktuellen Kontos prüfen, bevor der erste Browser läuft (kein Profil doppelt öffnen).
            None = in Ordnung (oder öffentlicher Abruf ist erlaubt), sonst (Meldung, Fehlerart)."""
            if platform in ('tiktok', 'x') and not anon:
                adapter = resolve('https://www.tiktok.com/' if platform == 'tiktok' else 'https://x.com/')
                try:
                    authed[platform], state = cli.auth_state(adapter, run_cfg, identity, country, False, backend, im)
                except SessionExpired:
                    authed[platform], state = False, State.EXPIRED
                except accountproxy.AccountProxyError as exc:
                    return str(exc), 'proxy'
                if not authed[platform]:
                    # X nur mit bestätigter Anmeldung; TikTok nur für Hashtags/Suche und Kommentare.
                    wants_login = (platform == 'x' or discovery.needs_login(kind, platform)
                                   or int(target['max_comments']) > 0)
                    if wants_login:
                        fault_kind = {State.EXPIRED: 'login', State.MISSING: 'missing'}.get(state)
                        return login_message(state, account_label), fault_kind
                    log.warning('Konto „%s“ ist nicht angemeldet; öffentlicher Abruf', account_label)
                ctx.authenticated = authed[platform]
            return None

        def url_checks():
            """Anmeldung für die Plattformen der Links (z. B. Instagram); fehlende Anmeldung sperrt nur diese Plattform."""
            for adapter in (resolve(url) for url in urls):
                if adapter and adapter.platform not in authed:
                    try:
                        authed[adapter.platform], state = cli.auth_state(
                            adapter, run_cfg, identity, country, False, backend, im)
                    except SessionExpired:
                        authed[adapter.platform], state = False, State.EXPIRED
                    except accountproxy.AccountProxyError as exc:
                        authed[adapter.platform], state = False, State.ERROR
                        blocked[adapter.platform] = str(exc)
                        continue
                    if state is None:
                        continue   # Adapter ohne Browser (Instagram): auth_state kann nichts prüfen, der Abruf prüft die Anmeldung selbst
                    if getattr(adapter, 'needs_auth', False) and not authed[adapter.platform] and (
                            run_cfg.require_auth or adapter.platform in ('instagram', 'x')):
                        blocked[adapter.platform] = login_message(state, account_label)

        def activate(acc):
            """Auf ein anderes Konto wechseln: Browser des alten schließen, Anmeldung des neuen prüfen.
            None = bereit, sonst (Meldung, Fehlerart) wie `login_check`."""
            nonlocal identity, country, account_label
            old = identity
            identity, country, account_label = acc['identity'], acc['country'] or DEFAULT_COUNTRY, acc['label']
            ctx.identity, ctx.country = identity, country
            if old != identity and sessions:
                try:
                    sessions.drop(old)
                except Exception:
                    log.warning('Browser-Session des vorigen Kontos konnte nicht geschlossen werden')
            authed.clear()
            blocked.clear()
            switches[0] += 1
            problem = login_check()
            if problem is None and urls:
                url_checks()
            return problem

        def failover(fault_kind, key):
            """Das aktuelle Konto ist ausgefallen. Vermerkt die Abkühlung; bei Rotation wird das nächste Konto
            vorbereitet, das dieses Video noch nicht versucht hat. True = anderes Konto bereit."""
            if rot is None:
                return False
            nxt = rot.fault(fault_kind, key)
            while nxt is not None:
                problem = activate(nxt)
                if problem is None:
                    return True
                if problem[1] is None:
                    return False
                nxt = rot.fault(problem[1], key)
            return False

        def settle(problem, key=''):
            """Probleme beim Anmelden des (ersten) Kontos: bei Rotation das nächste Konto versuchen.
            None = bereit, sonst die Meldung, mit der der Lauf endet."""
            while problem:
                message, fault_kind = problem
                if fault_kind and rot is not None:
                    nxt = rot.fault(fault_kind, key)
                    if nxt is not None:
                        problem = activate(nxt)
                        continue
                    if rot.rotating and rot.message:
                        return f'{message} {rot.message}'
                return message
            return None

        def has_proxy():
            return bool(rot is not None and rot.current and rot.current.get('proxy'))

        def refresh_account():
            """Nach einem Video, das kein Konto mehr hatte: für die weiteren Videos ein frisches Konto suchen.
            None = in Ordnung, sonst die Meldung, mit der der Lauf endet."""
            before = rot.current['id'] if rot.current else None
            nxt = rot.next_block()
            if nxt is None:
                return rot.message
            if nxt['id'] == before:
                return None                      # dasselbe Konto bleibt dran (kein anderes bereit): nichts neu vorbereiten
            return settle(activate(nxt))

        first_problem = login_check()
        if first_problem is not None:
            message = settle(first_problem)
            if message:
                return 'failed', message

        if kind != 'video':
            while True:
                try:
                    if chan is not None:
                        urls = chan.stage(ctx, cancelled)     # Videoliste (falls nötig) und das nächste Paket
                    else:
                        urls = discovery.discover(kind, platform, target['value'], ctx, int(target['max_videos']))
                    break
                except SessionExpired:
                    if failover('login', 'discover'):
                        continue
                    return 'failed', login_message(State.EXPIRED, account_label)
                except Exception as exc:
                    fault_kind = rotation.classify_for(rot, exc, has_proxy(), 'discover') if rot is not None else None
                    if fault_kind and failover(fault_kind, 'discover'):
                        continue
                    if fault_kind == 'proxy':
                        return 'failed', clean_error(exc)
                    raise
            if platform in authed:
                authed[platform] = authed[platform] and ctx.authenticated   # Verlust während der Suche einrasten
            log.info('%s neue/bekannte Videos gefunden', len(urls))
            if cancelled():
                return 'cancelled', 'Abgebrochen'

        ok = fail = incomplete = 0
        first_error = None
        item_error = [None]
        seen = set()
        url_checks()

        def stop_status():
            return 'partial' if counts['videos_seen'] else 'failed'

        def fault(fault_kind, url):
            """Ergebnis eines Kontofehlers: 'retry' (anderes Konto bereit), ('stop', Meldung) oder None (nur dieses Video verloren)."""
            if failover(fault_kind, url):
                return 'retry'
            if rot is not None and rot.rotating:
                message = refresh_account()      # für die weiteren Videos ein frisches Konto suchen
                if message:
                    return 'stop', message
            elif fault_kind in rotation.COOLDOWN:
                # Standardkonto/festes Konto: kein Wechsel möglich, das Konto ist gerade in die Abkühlung gegangen
                # (CAPTCHA, Sperre, Anmeldung, 429) oder der feste Proxy ist tot. Weitere Videos mit demselben Konto
                # würden die Sperre nur verschärfen: der Lauf endet hier, die restlichen Videos bleiben offen.
                return 'stop', None
            return None

        def _err(message):
            nonlocal first_error
            item_error[0] = item_error[0] or message
            first_error = first_error or message
            return first_error

        def fetch_one(url, adapter):
            """Ein Video abrufen. Gibt 'next', 'retry' (noch einmal mit dem neuen Konto) oder ('stop', Status, Meldung) zurück."""
            nonlocal ok, fail, incomplete, first_error
            if adapter.platform in blocked:
                verdict = (fault('proxy' if 'Proxy' in blocked[adapter.platform] else 'login', url)
                           if rot is not None and rot.rotating else None)
                if verdict == 'retry':
                    return 'retry'
                fail += 1
                _err(blocked[adapter.platform])
                if isinstance(verdict, tuple):
                    return 'stop', stop_status(), verdict[1] or first_error
                return 'next'
            ctx.authenticated = authed.get(adapter.platform, False)
            recorded = {'videos_new': 0, 'comments_new': 0}
            def saved(progress):
                if progress.video.uid not in seen:
                    seen.add(progress.video.uid)
                    counts['videos_seen'] += 1
                for key in ('videos_new', 'comments_new'):
                    value = getattr(progress, key)
                    counts[key] += value - recorded[key]
                    recorded[key] = value
                if kind != 'video' and target.get('group_id'):
                    repo.add_videos_to_group(db, target['group_id'], [progress.video.uid])
                if chan is not None:
                    chan.on_saved(progress.video)
            progress = cli.FetchProgress(store, saved)
            ctx.checkpoint = lambda video, comments: progress.save(video, comments)
            switched_before = switches[0]
            try:
                if chan is not None:   # Pausen nach dem Tempo-Profil des Kanals
                    humanize.pace(adapter.platform, bounds=chan.pace, stop=cancelled)
                else:
                    humanize.pace(adapter.platform, stop=cancelled)
                if cancelled():return 'stop','cancelled','Abgebrochen'
                if rot is not None:rot.attempt()
                # Hartes Limit pro Video im Browser (Standard 120 s)
                with (deadline(run_cfg.video_timeout, 'Der Abruf') if adapter.needs_browser else contextlib.nullcontext()):
                    result = normalize_result(adapter.fetch(url, ctx), run_cfg.max_comments)
                if dl is not None and dl.fired:
                    return 'stop', stop_status(), dl.message
                if result.video is None:
                    if progress.video is not None:
                        ok += 1; incomplete += 1
                        _err(result.message or 'Der Abruf blieb unvollständig; vorhandene Daten sind gespeichert.')
                        return 'next'
                    fault_kind = rotation.classify_for(rot, result.message, has_proxy(), url) if rot is not None and result.message else None
                    verdict = fault(fault_kind, url) if fault_kind else None
                    if verdict == 'retry':
                        return 'retry'
                    fail += 1
                    if fault_kind:
                        _err(clean_error(result.message))
                    else:
                        _err('Video nicht gefunden oder nicht abrufbar (die Plattform liefert keine Daten dazu)')
                    if isinstance(verdict, tuple):
                        return 'stop', stop_status(), verdict[1] or first_error
                    return 'next'
                # Verlaufspunkt bei jedem Abruf, unabhängig von der Einstellung „snapshot“.
                progress.save(result.video, result.comments, repo.iso())
                if result.comments_status in ('unavailable', 'partial'):
                    incomplete += 1
                    _err(clean_error(result.message or 'Kommentare konnten nicht vollständig abgerufen werden. Erneut versuchen.'))
            except SessionExpired:
                if sessions:
                    try: sessions.drop(identity, adapter.platform)
                    except Exception: log.warning('%s: Browser-Session konnte nicht geschlossen werden', adapter.platform)
                verdict = fault('login', url) if rot is not None else None
                if verdict == 'retry':
                    return 'retry'
                authed[adapter.platform] = False
                blocked[adapter.platform] = login_message(State.EXPIRED, account_label)
                first_error = login_message(State.EXPIRED, account_label)
                if progress.video is not None:
                    ok += 1; incomplete += 1
                else:
                    fail += 1
                if isinstance(verdict, tuple):
                    return 'stop', stop_status(), verdict[1] or first_error
                return 'next'
            except Exception as exc:
                if dl is not None and dl.fired:
                    return 'stop', stop_status(), dl.message
                message = clean_error(exc)
                log.warning('%s: %s', url, message)
                fault_kind = rotation.classify_for(rot, exc, has_proxy(), url) if rot is not None else None
                verdict = fault(fault_kind, url) if fault_kind else None
                if verdict == 'retry':
                    return 'retry'
                _err(message)
                if progress.video is not None:
                    ok += 1; incomplete += 1
                else:
                    fail += 1
                if isinstance(verdict, tuple):
                    return 'stop', stop_status(), verdict[1] or first_error
                return 'next'
            finally:
                ctx.checkpoint = None
                # Verlust der Sitzung einrasten: sonst meldet jedes weitere Video ihn erneut (Datei + Webhook).
                if switches[0] == switched_before:
                    authed[adapter.platform] = authed.get(adapter.platform, False) and ctx.authenticated
            ok += 1
            if rot is not None:
                rot.success()
            return 'next'

        index = 0
        while index < len(urls):
            url = urls[index]
            if cancelled():
                return 'cancelled', 'Abgebrochen'
            if dl is not None and dl.fired:
                return stop_status(), dl.message
            adapter = resolve(url)
            if adapter is None:
                fail += 1; first_error = first_error or 'Kein Adapter für diesen Link'
                if chan is not None:
                    chan.item_result(url, 'failed', 'Kein Adapter für diesen Link')
                index += 1
                continue
            before_ok, before_fail = ok, fail
            item_error[0] = None
            verdict = fetch_one(url, adapter)
            if verdict == 'retry':
                continue
            if chan is not None:
                chan.item_result(url, 'done' if ok > before_ok else ('failed' if fail > before_fail else None),
                                 item_error[0] or first_error)
            if isinstance(verdict, tuple):
                if chan is not None and rot is not None and rot.failures:
                    chan.stop_fault = rot.failures[-1][1]
                return verdict[1], verdict[2]
            index += 1
            if chan is not None and rot is not None and rot.current is not None and index < len(urls) and chan.account_full(rot.current):
                # Tagesgrenze dieses Kontos nach dem Tempo-Profil erreicht: bei Rotation zum nächsten, sonst Schluss für heute
                if not rot.rotating:
                    chan.wait_until = channels.next_midnight() + channels.datetime.timedelta(minutes=chan.rng.uniform(5, 90))
                    return stop_status(), 'Die Tagesgrenze des Kontos ist erreicht; morgen geht es weiter.'
                chan.exclude_account(rot)
                message = refresh_account()
                if message:
                    chan.wait_until = channels.next_midnight() + channels.datetime.timedelta(minutes=chan.rng.uniform(5, 90))
                    return stop_status(), message
            if rot is not None and rot.video_done() and index < len(urls):
                # Block voll: zum nächsten Konto wechseln (kann dasselbe bleiben, wenn kein anderes bereit ist).
                message = refresh_account()
                if message:
                    fail += len(urls) - index
                    first_error = first_error or message
                    return stop_status(), message
        if fail == 0 and incomplete == 0:
            return 'ok', None
        if ok == 0:
            return 'failed', first_error
        pieces = []
        if fail:
            pieces.append(f'{fail} von {ok + fail} Videos nicht abrufbar')
        if incomplete:
            pieces.append(f'{incomplete} Abrufe unvollständig; vorhandene Daten sind gespeichert')
        return 'partial', ', '.join(pieces) + f': {first_error}'
    finally:
        cli.shutdown_runtime(backend, sessions)
