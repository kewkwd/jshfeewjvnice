import logging, re, secrets, time
from dataclasses import dataclass, field

from .. import accountvault, repo, streuung, warmup as warmup_modul
from ..net import proxymode
from ..net.cookies import CookieJar
from ..net.errors import FinalError, precheck_local_proxy
from ..net.identity import IdentityManager, validate_name
from ..net.session_guard import invalidate
from ..store import Store
log = logging.getLogger(__name__)

MODULE = {}
ASSISTED = {'instagram', 'x', 'youtube', 'google'}
_FILL_JS = """([sels, val]) => { for (const s of sels) { for (const el of document.querySelectorAll(s)) {
 if (!['INPUT','TEXTAREA'].includes(el.tagName) || el.matches(':disabled') || el.readOnly ||
     !el.getClientRects().length || ['hidden','collapse'].includes(getComputedStyle(el).visibility) ||
     (el.tagName === 'INPUT' && !['text','password','email','url','search','tel','number','date','time','datetime-local','month','week'].includes(el.type))) continue;
 el.focus(); const p = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
 Object.getOwnPropertyDescriptor(p, 'value').set.call(el, val);
 el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true}));
 if (el.value === String(val)) return s; } } return null; }"""
_CLICK_TEXT_JS = """(texts) => { const els = Array.from(document.querySelectorAll('button, a, div[role="button"], label'));
 for (const t of texts) { const el = els.find(x => (x.innerText || '').trim().toLowerCase() === t);
 if (el) { el.click(); return t; } } return null; }"""
_DECLINE = ['decline optional cookies', 'optionale cookies ablehnen', 'only allow essential cookies',
            'nur erforderliche cookies erlauben', 'not now', 'jetzt nicht', 'nicht jetzt',
            'reject non-essential', 'nicht notwendige ablehnen']

def _lade_module():
    if MODULE:
        return
    import importlib
    for name in ('reddit', 'tiktok', 'x', 'instagram', 'youtube', 'google', 'trustpilot'):
        try:
            MODULE[name] = importlib.import_module(f'{__name__}.{name}')
        except Exception as fehler:
            log.warning('Registrierungs-Modul %s nicht geladen: %s', name, fehler)

def _neues_passwort():
    from ..password_supply import generate
    return generate()

def _native_controls(page, selector, check=None):
    """Yield usable native matches, including those after hidden duplicates."""
    if check:check()
    try:
        matches=page.locator(selector)
        counter=getattr(matches, 'count', None)
        count=counter() if callable(counter) else matches.first.count()
    except Exception:
        if check:check()
        return
    if type(count) is not int or count < 0 or count > 200:
        raise FinalError('Die Formularziele sind nicht sicher begrenzt lesbar. Keine Eingabe oder Aktion ausgeführt.')
    usable_controls = []
    for index in range(count):
        if check:check()
        try:
            control=matches.first if index==0 else matches.nth(index)
            enabled=getattr(control, 'is_enabled', None)
            usable=control.is_visible() and (not callable(enabled) or enabled())
        except Exception:
            if check:check()
            continue
        if usable:
            usable_controls.append((control, index, count))
    if len(usable_controls) > 1:
        raise FinalError('Mehrere sichtbare Felder oder Schaltflächen passen zu diesem Schritt. Bitte den Formularstand prüfen; kein Ziel geraten.')
    yield from usable_controls


def _fill(page, auswahl, wert, *, check=None):
    if check:check()
    from ..net.form_guard import remember, require_origin
    require_origin(page)
    locator=getattr(page, 'locator', None)
    if callable(locator) and not type(page).__module__.startswith('unittest.mock'):
        for selector in auswahl:
            for field,index,count in _native_controls(page,selector,check):
                if check:check()
                try:
                    editable=getattr(field, 'is_editable', None)
                    if callable(editable) and not editable():continue
                    state = field.evaluate("el => ({text: el.getAttribute('role') !== 'combobox' && (el.isContentEditable || ['INPUT','TEXTAREA'].includes(el.tagName)), value: el.isContentEditable ? el.innerText : el.value})", timeout=1500)
                    if not state['text']:
                        continue  # SELECT/combobox needs a verified selection, never fill().
                    if state['value'] not in ('', str(wert)):
                        raise FinalError('Das Eingabefeld enthält bereits eine andere Angabe. Nichts überschrieben; bitte den Formularstand prüfen.')
                    from ..net.input_behavior import fill
                    if state['value'] != str(wert):
                        require_origin(page)
                        fill(page, field, wert, check=check, timeout=1500)
                    remember(page, field, wert, check=check)
                except FinalError:
                    raise
                except Exception:
                    if check:check()
                    continue
                if check:check()
                # Credential tracking must identify the actual filled element.
                return selector if count==1 else f'{selector} >> nth={index}'
        if check:check()
        return None  # Never override a failed native action with JavaScript.
    raise FinalError('Dieser Browser unterstützt die abgesicherte Registrierungseingabe nicht. Bitte einen Browser mit nativen Formularfunktionen wählen.')


def _select(page, auswahl, wert, *, check=None):
    from ..net.form_choices import select
    return select(page, auswahl, wert, check=check)


def _passwords(ctx, selectors, password):
    found = False
    # Password and confirmation are distinct fields, not selector alternatives.
    for selector in selectors:
        found = bool(ctx.fill([selector], password)) or found
    return found

def _google_start(page, ctx, selectors, first_name):
    if ctx.fill(selectors, first_name):
        return
    ctx.click(['button:has-text("Konto erstellen")', 'button:has-text("Create account")'], navigate=True)
    ctx.warte(1000)
    ctx.click(['text="Für mich"', 'text="For my personal use"', 'text="Für meine private Nutzung"'], navigate=True)
    ctx.warte(1500)
    if not ctx.fill(selectors, first_name):
        raise RuntimeError('Google: Registrierungsformular nicht erreichbar; Anmeldung nicht als Registrierung bestätigt.')

def _click(page, auswahl, *, check=None, navigate=False):
    from ..net.form_guard import before_click, require_origin
    require_origin(page)
    native=callable(getattr(page,'locator',None)) and not type(page).__module__.startswith('unittest.mock')
    for sel in auswahl:
        if check:check()
        if native:
            deadline=time.monotonic()+5
            while True:
                attempted=False
                for control,_index,_count in _native_controls(page,sel,check):
                    if check:check()
                    attempted=True
                    try:
                        from ..net.input_behavior import click
                        click(page, control, check=check, timeout=max(1,int((deadline-time.monotonic())*1000)),
                              before_click=lambda: before_click(page, control, navigate=navigate, check=check))
                    except FinalError:
                        raise
                    except Exception:
                        if check:check()
                        continue
                    if check:check()
                    return sel
                remaining=deadline-time.monotonic()
                waiter=getattr(page,'wait_for_timeout',None)
                if attempted or remaining<=0 or not callable(waiter):break
                # Preserve native click's bounded waiting when a button is
                # still loading. Re-scan duplicates; never force a hidden one.
                if check:check()
                waiter(min(100,remaining*1000))
                if check:check()
        else:
            raise FinalError('Dieser Browser unterstützt die abgesicherte Formularbedienung nicht. Kein Formular abgesendet.')
    if check:check()
    return None


def _dismiss(page, versuche=3, *, check=None, cookie_choices=True):
    labels = _DECLINE if cookie_choices else ['not now', 'jetzt nicht', 'nicht jetzt']
    for _ in range(versuche):
        if check:check()
        try:
            dismissed=page.evaluate(_CLICK_TEXT_JS,labels)
        except Exception:
            if check:check()
            return
        if check:check()
        if not dismissed:return
        page.wait_for_timeout(1200)
        if check:check()

@dataclass
class Ctx:
    platform: str; email: str; password: str; username: str; mode: str; country: str; mobile: bool
    timeout: int; backend: object; handle: object = None; identity: str = ''; box: object = None
    melden: callable = log.info; stop: callable = None; notizen: list = field(default_factory=list)
    cookies: object = None                                            # FIX A3
    tiktok_username: str = ''  # Explicit provider handle; identity is only a local key.
    accept_privacy: bool = False
    person: dict = field(default_factory=dict, repr=False)
    profile_tempo: dict = field(default_factory=dict, repr=False)
    _credentials_guard: object = field(default=None, init=False, repr=False)
    _credentials_changed: bool = field(default=False, init=False, repr=False)
    _password_filled: bool = field(default=False, init=False, repr=False)

    def check_cancelled(self):
        from ..operation import check_cancelled
        check_cancelled(self.stop)

    def hinweis(self, text):
        self.melden(text)

    def fill(self, auswahl, wert):
        self.check_cancelled()
        page = self.backend.page(self.handle)
        credential = wert == self.email or (wert == self.password and any('pass' in s.casefold() for s in auswahl))
        if credential and self._credentials_guard is not None:
            if not self._credentials_guard.valid():
                raise FinalError('Die bisherige Zugangseingabe ist nicht mehr zuverlässig bestätigt. Bitte den vorhandenen Stand prüfen; nichts erneut eingetragen.')
        if credential and self._credentials_changed:
            raise FinalError('Die Zugangsdaten wurden im Fenster verändert. Bitte den vorhandenen Zugang prüfen; nichts überschrieben.')
        result = _fill(page, auswahl, wert, check=self.check_cancelled)
        if result and credential:
            if self._credentials_guard is None:
                from ..mailanbieter.credentials import PasswordChangeGuard
                self._credentials_guard = PasswordChangeGuard(self._credentials_edited)
            if not self._credentials_guard.watch(page, result, str(wert)):
                raise FinalError('Die Zugangseingabe kann nicht zuverlässig überwacht werden. Bitte den vorhandenen Stand prüfen.')
            if wert == self.password:
                self._password_filled = True
        self.check_cancelled()
        return result

    def _credentials_edited(self):
        self._credentials_changed = True

    def require_known_credentials(self):
        if (not self._password_filled or self._credentials_changed or self._credentials_guard is None
                or not self._credentials_guard.valid()):
            raise FinalError('Eine angemeldete Browsersitzung wurde erkannt, aber die gespeicherten Zugangsdaten sind nicht zuverlässig belegt. '
                             'Bitte den bestehenden Zugang prüfen und übernehmen; kein fertiger Zugang mit unbestätigtem Kennwort gespeichert.')

    def click(self, auswahl, *, navigate=False):
        self.check_cancelled()
        page = self.backend.page(self.handle)
        from ..offer_policy import guard_submit
        if guard_submit(page, self):
            return None
        result = _click(page, auswahl, check=self.check_cancelled, navigate=navigate)
        self.check_cancelled()
        return result

    def dismiss(self):
        self.check_cancelled()
        from ..offer_policy import decline_known, offer_pending
        page = self.backend.page(self.handle)
        if decline_known(page, self):
            return
        if offer_pending(page):
            return  # never use a generic dismissal on an unresolved paid offer
        if self.accept_privacy:
            from ..privacy_consent import accept_known
            if not accept_known(self.backend.page(self.handle), self):
                _dismiss(self.backend.page(self.handle), check=self.check_cancelled, cookie_choices=False)
        else:
            _dismiss(self.backend.page(self.handle), check=self.check_cancelled)
        self.check_cancelled()

    def warte(self, ms):
        self.check_cancelled()
        self.backend.page(self.handle).wait_for_timeout(ms)
        self.check_cancelled()

    def code_holen(self, *, timeout=240, ab=None):
        self.check_cancelled()
        if self.box is None:
            raise FinalError('Für diese Plattform wird ein Postfach gebraucht.')
        result = self.box.wait_for_code(platform=self.platform, timeout=timeout, newer_than=ab, stop=self.stop)[0]
        self.check_cancelled()
        return result

    def link_holen(self, *, timeout=300, ab=None):
        self.check_cancelled()
        if self.box is None:
            raise FinalError('Für diese Plattform wird ein Postfach gebraucht.')
        result = self.box.wait_for_link(platform=self.platform, timeout=timeout, newer_than=ab, stop=self.stop)[0]
        self.check_cancelled()
        return result

    def warte_auf_cookies(self, *, still=True, tick=None):
        self.check_cancelled()
        page, frist, letzte = self.backend.page(self.handle), time.monotonic() + self.timeout, 0.0
        while time.monotonic() < frist:
            if self.stop is not None and self.stop():
                raise FinalError('Abgebrochen.')
            try:
                cookies = self.backend.read_cookies(self.handle)
            except Exception:
                cookies = []
            self.check_cancelled()
            if CookieJar.authenticated(cookies, self.platform):
                return CookieJar.for_platform(cookies, self.platform)
            if tick is not None:
                remaining = frist - time.monotonic()
                if remaining <= 0:
                    break
                progressed = tick(remaining)
                if self.stop is not None and self.stop():
                    raise FinalError('Abgebrochen.')
                if time.monotonic() >= frist:
                    break
                if progressed:
                    # A submitted step can establish the session immediately.
                    # Check it before consuming the remaining time in a wait.
                    continue
            if not still and time.monotonic() - letzte > 15:
                letzte = time.monotonic()
                self.melden(f'Warte auf Abschluss im Fenster … ({int(frist - time.monotonic())} s)')
            try:
                self.dismiss()
            except FinalError:
                raise
            except Exception:
                pass
            self.warte(1500)
        raise TimeoutError(f'Registrierung nicht innerhalb von {self.timeout} s abgeschlossen')

def _browser_lauf(modul, ctx, person, proxy, fenster):             # FIX A3
    ctx.check_cancelled()
    with ctx.backend.session(person, proxy, headless=not fenster, platform=ctx.platform) as handle:
        ctx.handle = handle
        ctx.check_cancelled()
        page = ctx.backend.page(handle)
        if CookieJar.authenticated(ctx.backend.read_cookies(handle), ctx.platform):
            raise FinalError('Im Browser besteht bereits eine Anmeldung bei dieser Plattform. Bitte den vorhandenen Zugang prüfen und übernehmen; keine neue Registrierung gestartet.')
        from ..entry_rules import open_registration
        open_registration(page, ctx.platform, modul.REGISTER_URL, ctx)
        page.wait_for_timeout(2500)
        ctx.dismiss()
        from ..net.form_guard import bind
        bind(page, ctx.platform)
        from .. import signup_attempts
        signup_attempts.begin(ctx.db_path, ctx.identity, ctx.email, platform=ctx.platform)
        ctx.cookies = modul.run(page, ctx)
        if not CookieJar.authenticated(ctx.cookies or [], ctx.platform):
            raise FinalError('Die Registrierung hat noch keine bestätigte Browsersitzung geliefert. Bitte den bestehenden Stand prüfen.')
        signup_attempts.session_seen(ctx.db_path, ctx.identity, ctx.email, platform=ctx.platform)
        ctx.require_known_credentials()
    ctx.handle = None
    return True

from ..operation import exclusive, browser_owned, positive_timeout, check_cancelled

@exclusive
@browser_owned
def create(cfg, platform, *, name=None, mode=None, country='de', mobile=False, timeout=600, email=None,
           password=None, backend=None, im=None, melden=log.info, stop=None, warmup='normal',
           warmup_umfang='liken', avd=None):
    _lade_module()
    if platform not in repo.ACCOUNT_PLATFORMS:
        raise FinalError(f'Unbekannte Plattform: {platform}')
    modul = MODULE.get(platform)
    if modul is None:
        raise FinalError(f'Die Kontoerzeugung für „{platform}“ ist noch nicht gebaut.')
    proxymode.require_no_anon(cfg, 'Konto anlegen')
    if mode is None:
        mode = 'assisted' if platform in ASSISTED else 'auto'
    if mode not in ('auto', 'assisted'):
        raise ValueError('Modus muss „auto“ oder „assisted“ sein')
    positive_timeout(timeout)
    if warmup not in warmup_modul.MODI or warmup_umfang not in warmup_modul.UMFANG:
        raise ValueError('Aufwärmplan oder Zeitlimit ist ungültig.')
    if stop is not None and stop():
        raise FinalError('Abgebrochen.')
    name = validate_name(name) if name else streuung.naechster_name(cfg.db_path, platform)
    from .. import profile_settings
    profile = profile_settings.load(cfg.profiles_dir, name)
    personal = profile_settings.normalize_person(profile['person'], required=True)
    if platform in ('google', 'youtube', 'instagram', 'x', 'tiktok'):
        profile_settings.birthdate(personal)
    if im is None:
        im = _identity_manager(cfg)
    if _konto_da(cfg, platform, name):
        raise FinalError(f'Das Konto „{name}“ ist schon angelegt.')
    from .. import signup_attempts
    signup_attempts.ensure_available(cfg.db_path, name, email.strip() if email else None, platform=platform)

    from ..mailbox import build as mailbox_build
    tresor = accountvault.load(cfg.db_path)
    email = email.strip() if email else None
    pool_postfach = (next((e for e in tresor['postfaecher'] if e.get('adresse', '').strip().casefold() == email.casefold()), None)
                    if email else accountvault.nimm_freies(cfg.db_path))
    box = None
    mailbox_settings = None
    if pool_postfach:
        if pool_postfach.get('belegt_von') not in (None, '', name):
            raise FinalError('Dieses Postfach gehört bereits zu einem anderen Konto.')
        email = pool_postfach['adresse']
        mailbox_settings = accountvault.als_postfach(pool_postfach)
    elif tresor.get('postfach'):
        mailbox_settings = tresor['postfach']
        if email:
            art = mailbox_settings.get('art') or 'imap'
            address = (mailbox_settings.get('adresse') or mailbox_settings.get('benutzer') or '').strip()
            if art == 'wegwerf' and not (mailbox_settings.get('adresse') and mailbox_settings.get('passwort')):
                raise FinalError('Für die gewählte E-Mail-Adresse fehlt ein gespeicherter Postfachzugang.')
            if '@' in address and address.casefold() != email.casefold():
                raise FinalError('Die gewählte E-Mail-Adresse passt nicht zum gespeicherten Postfach. '
                                 'Bitte den passenden Postfachzugang hinzufügen oder auswählen.')
    elif not email:
        raise FinalError('Für die Kontoerzeugung fehlt ein Postfach (data/account_secrets.json).')

    if platform == 'tiktok':
        from .. import signup_attempts
        signup_attempts.ensure_available(cfg.db_path, name, email)
        if mailbox_settings is None:
            raise FinalError('Für TikTok fehlt der passende gespeicherte Postfachzugang. Bitte zuerst den Mailabruf einrichten; keine Registrierung gestartet.')
        mobile = True
    from .. import password_supply
    password = password_supply.profile_password(cfg.db_path, name, explicit=password or None)
    person = im.get(name, country, mobile)
    proxy = im.proxy_for(person)
    proxy_url = proxy.url if proxy is not None else None
    precheck_local_proxy(proxy)
    if mailbox_settings is not None:
        box = mailbox_build(mailbox_settings, proxy=proxy)
        if not email:
            email = box.address()
    for schwere, text in streuung.pruefe_neues_konto(cfg.db_path, identity=name,
                                                     proxy=proxy_url,
                                                     email=email, tresor=tresor):
        melden(f'Achtung ({schwere}): {text}')
    warten = streuung.wartezeit_bis_registrierung(cfg.db_path, platform=platform, proxy=proxy_url)
    if warten > 0:
        melden(f'Warte {warten / 60:.1f} min Abstand …')
        ende = time.monotonic() + warten
        while time.monotonic() < ende:
            if stop is not None and stop():
                raise FinalError('Abgebrochen.')
            time.sleep(min(5, ende - time.monotonic()))

    if box is not None:
        from ..mailbox import MailboxError
        check_cancelled(stop)
        melden('Prüfe den Posteingang über die für diesen Auftrag gewählte Verbindung …')
        try:
            box.check_access()
        except (MailboxError, OSError):
            check_cancelled(stop)
            raise FinalError('Der Mailabruf konnte nicht bestätigt werden; die Kontoregistrierung wurde nicht gestartet. '
                             'Bitte Postfachzugang, IMAP-Freigabe und Proxyverbindung prüfen.') from None
        check_cancelled(stop)
        melden('Posteingang erreichbar. Die Kontoregistrierung kann beginnen.')

    if backend is None and platform != 'tiktok':
        from ..net.factory import build as build_backend
        backend = build_backend(cfg, im)
    from ..privacy_consent import enabled
    ctx = Ctx(platform=platform, email=email, password=password, username=name, mode=mode, country=country,
              mobile=mobile, timeout=timeout, backend=backend, identity=name, box=box, melden=melden, stop=stop,
              accept_privacy=enabled(cfg.db_path), person=personal, profile_tempo=profile['tempo'])
    ctx.db_path = cfg.db_path
    melden(f'Konto „{name}“ wird angelegt ({platform}, {mode}).')
    fenster = mode == 'assisted' or not cfg.headless
    app_success = False

    if platform == 'tiktok':
        if not callable(getattr(modul, 'registrieren_app', None)):
            raise FinalError('Die TikTok-Registrierung benötigt den Android-Emulator.')
        try:
            ok, avd = modul.registrieren_app(cfg, ctx, proxy=proxy_url, avd=avd)
        except FinalError:
            raise
        except Exception as fehler:
            if stop is not None and stop():
                raise FinalError('Abgebrochen.') from None
            raise FinalError('Der TikTok-Auftrag wurde unterbrochen. Der vorhandene App-Stand bleibt erhalten. '
                             'Bitte prüfen, ob bereits ein Konto entstanden ist, bevor du eine neue Registrierung versuchst.') from fehler
        ctx.check_cancelled()
        app_success = bool(ok and isinstance(avd, str) and avd.strip())
        if not app_success:
            raise FinalError('Die TikTok-Registrierung im Android-Emulator wurde nicht bestätigt.')
        if not {'email', 'password'} <= getattr(ctx, '_tiktok_verified_credentials', set()):
            raise FinalError('Ein TikTok-Profil wurde erkannt, aber die vorbereitete E-Mail- und Kennworteingabe ist nicht vollständig belegt. '
                             'Bitte den bestehenden Zugang prüfen und übernehmen; keine neue Registrierung starten.')
        if getattr(ctx, 'android_credentials_uncertain', False):
            raise FinalError('Während der manuellen Eingabe war ein Kennwortfeld geöffnet. '
                             'Das tatsächlich verwendete Kennwort muss geprüft werden; kein fertiger Kontoeintrag gespeichert. '
                             'Den bestehenden Kontoabschluss prüfen, bevor eine neue Registrierung gestartet wird.')
        try:
            ctx.tiktok_username = accountvault.normalize_tiktok_username(ctx.tiktok_username, required=True)
        except ValueError:
            raise FinalError('Der tatsächliche TikTok-Benutzername wurde nach der Registrierung '
                             'nicht eindeutig bestätigt; kein Konto gespeichert. Bitte den Stand im Emulator '
                             'prüfen, bevor eine neue Registrierung gestartet wird.') from None
    else:
        _browser_lauf(modul, ctx, person, proxy, fenster)

    if stop is not None and stop():
        raise FinalError('Abgebrochen.')
    if not ctx.cookies and not app_success:
        raise FinalError('Die Registrierung wurde nicht bestätigt.')
    if ctx.cookies:
        CookieJar.save(name, platform, ctx.cookies)
    invalidate(name, platform)
    konto_id = _konto_anlegen(cfg, platform, name, country, mode, email)
    if proxy_url:
        from ..net.accountproxy import set_entry, mask
        set_entry(cfg.db_path, name, proxy_url)
        with Store(cfg.db_path) as store:
            repo.set_account_proxy_ref(store.db, konto_id, mask(proxy_url))
    accountvault.put(cfg.db_path, name, email=email, passwort=password, notiz=f'{platform} ({mode})',
                     **({'tiktok_username': ctx.tiktok_username} if app_success else {}))
    if pool_postfach and email == pool_postfach['adresse']:
        accountvault.belege(cfg.db_path, pool_postfach['adresse'], name)
    streuung.setze_fenster(cfg.db_path, konto_id)
    if app_success and warmup == 'aus':
        warmup_modul.setzen(cfg.db_path, konto_id, modus='aus', phase='app', app_avd=avd)
    if warmup != 'aus':
        ist_app = app_success
        warmup_modul.setzen(cfg.db_path, konto_id, modus=warmup, umfang=warmup_umfang,
                            phase='app' if ist_app else 'web', app_avd=avd if ist_app else None)
    signup_attempts.stored(cfg.db_path, name, email, ctx.tiktok_username, platform=platform)
    melden(f'Konto „{name}“ fertig und angemeldet.')
    return {'identity': name, 'platform': platform, 'email': email, 'konto_id': konto_id,
            'cookies': len(ctx.cookies or []), 'mode': mode, 'notizen': ctx.notizen}

def _identity_manager(cfg):
    from ..net.factory import build_identity_manager
    return build_identity_manager(cfg)

def _konto_da(cfg, platform, identity):
    with Store(cfg.db_path) as store:
        return store.db.execute('SELECT 1 FROM accounts WHERE platform=? AND identity=?',
                                (platform, identity)).fetchone() is not None

def _konto_anlegen(cfg, platform, identity, country, mode, email):
    with Store(cfg.db_path) as store:
        db = store.db
        account = repo.add_account(db, platform, identity, country, f'{mode}; {email}')
        konto_id = account['id']
        zeile = db.execute('SELECT identity FROM accounts WHERE id=?', (konto_id,)).fetchone()
        if zeile and zeile[0] != identity:
            db.execute('UPDATE accounts SET identity=? WHERE id=?', (identity, konto_id))
            db.commit()
        return konto_id
