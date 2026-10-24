import importlib, logging, re, secrets
from dataclasses import dataclass, field
from .. import accountvault, repo
from ..net import proxymode
from ..net.errors import FinalError
from ..signup import _click, _dismiss, _fill
log = logging.getLogger(__name__)
ANBIETER = {}

def _lade():
    if ANBIETER:
        return
    for name in ('gmx', 'webde', 'gmail', 'outlook'):
        try:
            ANBIETER[{'gmx': 'gmx.net', 'webde': 'web.de', 'gmail': 'gmail.com', 'outlook': 'outlook.com'}[name]] = importlib.import_module(f'{__name__}.{name}')
        except Exception as fehler:
            log.warning('Postfach-Anbieter %s nicht geladen: %s', name, fehler)

@dataclass
class MailCtx:
    anbieter: str; vorname: str; nachname: str; adresse: str; passwort: str; mode: str; land: str
    mobile: bool; timeout: int; backend: object; domaene: str = ''            # FIX B2
    handle: object = None; identity: str = ''; melden: callable = log.info
    stop: callable = None; notizen: list = field(default_factory=list)
    password_filled: bool = False
    password_confirmed_by_user: bool = False
    account_confirmed: bool = False
    registrierungsdaten: dict = field(default_factory=dict, repr=False)
    report: object = field(default=None, repr=False)
    accept_privacy: bool = False
    input_pause_ms: int = 0
    person: dict = field(default_factory=dict, repr=False)
    profile_tempo: dict = field(default_factory=dict, repr=False)
    _password_guard: object = field(default=None, init=False, repr=False)
    _password_changed: bool = field(default=False, init=False, repr=False)

    def check_cancelled(self):
        from ..operation import check_cancelled
        check_cancelled(self.stop)

    def fill(self, auswahl, wert):
        self.check_cancelled()
        from ..form_pacing import before_input
        before_input(self)
        password_input=wert==self.passwort and any('pass' in s.lower() for s in auswahl)
        if password_input and self._password_guard is not None:
            if not self._password_guard.valid():
                raise FinalError('Die bisherige Kennworteingabe ist nicht mehr zuverlässig bestätigt. Bitte den vorhandenen Stand prüfen; nichts erneut eingetragen.')
            self.check_cancelled()
        if password_input and self._password_changed:
            return None
        page=self.backend.page(self.handle)
        result=_fill(page, auswahl, wert, check=self.check_cancelled)
        self.check_cancelled()
        if result and password_input:
            self.password_filled=True
            if self._password_guard is None:
                from .credentials import PasswordChangeGuard
                self._password_guard=PasswordChangeGuard(self._password_edited)
            if not self._password_guard.watch(page,result,self.passwort):
                raise FinalError('Die Kennworteingabe kann nicht zuverlässig überwacht werden. Bitte den Formularstand prüfen; kein Weiter-Klick ausgeführt.')
            self.check_cancelled()
        return result

    def _password_edited(self):
        self.password_filled=False
        self._password_changed=True

    def require_known_password(self):
        self.check_cancelled()
        if (self._password_guard is not None and self._password_guard.valid()
                and self.password_filled and not self._password_changed):
            self.check_cancelled()
            return
        raise FinalError('Das tatsächlich verwendete Kennwort ist nicht zuverlässig bestätigt. '
                         'Füge das Postfach mit dem verwendeten Kennwort selbst unter Postfächer hinzu; '
                         'kein Pool-Eintrag gespeichert.')

    def click(self, auswahl, *, navigate=False):
        self.check_cancelled()
        page = self.backend.page(self.handle)
        from ..offer_policy import guard_submit
        if guard_submit(page, self):
            return None
        result=_click(page, auswahl, check=self.check_cancelled, navigate=navigate)
        self.check_cancelled()
        return result

    def dismiss(self):
        self.check_cancelled()
        from ..offer_policy import decline_known, offer_pending
        page = self.backend.page(self.handle)
        if decline_known(page, self):
            return
        if offer_pending(page):
            return
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

    def hinweis(self, text):
        if self.report is not None:self.report.note(text)
        self.melden(text); self.notizen.append(text)

    def state(self, phase):
        from ..registration_progress import update
        update(self,phase)

    def progress(self, stage):
        if self.report is not None:self.report.phase(stage)

    def bestaetigen(self, platform, tick=None):
        self.check_cancelled()
        from ..net.cookies import CookieJar
        from urllib.parse import urlsplit
        import time
        until = time.monotonic() + self.timeout
        self.hinweis('Bitte die Registrierung im Fenster abschließen. Warte auf Bestätigung …')
        checked=False
        while time.monotonic() < until:
            if self.stop and self.stop():
                raise FinalError('Abgebrochen.')
            page=self.backend.page(self.handle)
            self.dismiss()
            if tick:
                tick()
            self.check_cancelled()
            host=urlsplit(str(page.url)).hostname
            authenticated = (CookieJar.authenticated(self.backend.read_cookies(self.handle),'google')
                             if platform=='google' else host in ('account.microsoft.com','outlook.live.com'))
            self.check_cancelled()
            if authenticated:
                if not checked:
                    page.goto('https://myaccount.google.com/' if platform=='google'
                              else 'https://outlook.live.com/mail/',wait_until='domcontentloaded')
                    self.check_cancelled()
                    checked=True
                confirmed=_confirmed_address(page,platform,self.adresse)
                self.check_cancelled()
                if confirmed:
                    self.account_confirmed = True
                    self.require_known_password()
                    return
            self.warte(1500)
        raise FinalError('Die Postfach-Registrierung wurde nicht bestätigt; es wurde kein Pool-Eintrag angelegt.')

def _confirmed_address(page,platform,address):
    """A generic Microsoft/Google login does not prove the requested mailbox."""
    from urllib.parse import urlsplit
    parts=urlsplit(str(page.url))
    allowed = (parts.hostname=='myaccount.google.com' if platform=='google' else
               parts.hostname=='outlook.live.com' and parts.path.startswith('/mail/'))
    if parts.scheme != 'https' or not allowed or '@' not in address:
        return False
    labels=page.locator('[aria-label*="@"], [title*="@"], [data-email]').evaluate_all(
        '(xs)=>xs.map(x=>[x.getAttribute("aria-label"),x.getAttribute("title"),x.getAttribute("data-email")].filter(Boolean).join(" "))')
    pattern=r'(?<![\w.+-])'+re.escape(address.casefold())+r'(?![\w.-])'
    return any(re.search(pattern,str(label).casefold()) for label in labels)

from ..operation import exclusive, browser_owned, cancelled, positive_timeout

@exclusive
def create(cfg, anbieter, *, adresse=None, vorname=None, nachname=None, passwort=None, mode='assisted',
           land='de', mobile=False, timeout=900, backend=None, im=None, melden=log.info, stop=None, route='browser',
           registrierungsdaten=None, failure_window=0, profile_identity=None):
    if cancelled(stop):
        raise FinalError('Abgebrochen.')
    positive_timeout(timeout)
    from ..registration_review import validate_duration
    validate_duration(failure_window)
    if failure_window and (mode!='assisted' or route!='browser'):
        raise ValueError('Ein Diagnosefenster ist nur für die begleitete Browserregistrierung verfügbar.')
    _lade()
    proxymode.require_no_anon(cfg, 'Postfach anlegen')
    if profile_identity is not None:
        from ..net.identity import validate_name
        from .. import profile_settings
        validate_name(profile_identity)
        prepared = profile_settings.load(cfg.profiles_dir, profile_identity)
        profile_settings.normalize_person(prepared['person'], required=True)
    if anbieter not in ANBIETER:
        raise FinalError(f'Unbekannter Anbieter: {anbieter} (erlaubt: {", ".join(ANBIETER)})')
    if mode not in ('auto', 'assisted'):
        raise ValueError('Modus muss „auto“ oder „assisted“ sein')
    if route not in ('browser','android') or (route=='android' and anbieter not in ('gmail.com', 'web.de')):
        raise ValueError('Der Android-Weg ist für WEB.DE und Gmail verfügbar; sonst Browser wählen.')
    if route=='android' and mode!='assisted':
        raise ValueError('Der Android-Weg unterstützt derzeit die geführte Registrierung (assisted). Automatische Registrierung ist noch nicht verfügbar.')
    if route == 'android' and anbieter == 'web.de' and not profile_identity:
        raise ValueError('Die WEB.DE-App benötigt ein vorbereitetes Profil mit Personendaten und Proxy.')
    if route == 'android' and anbieter == 'web.de':
        mobile = True
    from .. import registration_attempts
    from ..failure_reports import Progress
    saved=registration_attempts.pending(cfg.db_path,anbieter,adresse)
    if saved:
        key,draft=saved
        if passwort and passwort not in (draft['password'],draft['completion']['entry']['passwort']):
            raise ValueError('Das Kennwort des bestätigten Postfachs ist bereits gespeichert. '
                             'Einen abweichenden IMAP-Zugang bitte lokal als vorhandenes Postfach hinzufügen.')
        return _resume_completion(cfg,key,draft,melden,stop,Progress(anbieter))
    modul = ANBIETER[anbieter]
    details = {}
    if anbieter in ('web.de', 'gmx.net', 'gmail.com', 'outlook.com'):
        from .. import registration_data
        details = registration_data.normalize(registration_data.load(cfg.db_path, anbieter) if registrierungsdaten is None else registrierungsdaten)
        if vorname is not None: details['vorname'] = vorname
        if nachname is not None: details['nachname'] = nachname
        details = registration_data.normalize(details, required=True)
        if profile_identity is not None:
            if profile_settings.from_registration(details) != prepared['person']:
                raise ValueError('Registrierungsdaten stimmen nicht mit dem ausgewählten Profil überein.')
        vorname, nachname = details['vorname'], details['nachname']
    explicit_address, explicit_password = bool(adresse), bool(passwort)
    kennung = secrets.token_hex(3)
    adresse = adresse or modul.wunsch_adresse(vorname, nachname, kennung)
    if not isinstance(adresse, str):
        raise ValueError('Die Postfachadresse muss als Text angegeben werden.')
    adresse = adresse.strip()
    if '@' not in adresse:
        adresse += '@'+anbieter
    if (adresse.count('@') != 1 or adresse.rpartition('@')[2].casefold()!=anbieter
            or not adresse.partition('@')[0]
            or any(char.isspace() or ord(char)<32 or ord(char)==127 for char in adresse)):
        raise ValueError(f'Für diesen Registrierungsweg wird eine Adresse bei {anbieter} benötigt.')
    existing_addresses = {entry['adresse'].strip().casefold() for entry in accountvault.postfaecher(cfg.db_path)}
    if adresse.casefold() in existing_addresses:
        raise FinalError('Dieses Postfach ist bereits im Pool. Den vorhandenen Eintrag verwenden.')
    from .. import registration_attempts
    from .. import password_supply
    key, draft, reused = registration_attempts.get_or_create(cfg.db_path, anbieter,
        address=adresse, password=passwort, identity=kennung, explicit_address=explicit_address,
        explicit_password=explicit_password, details=details or {'vorname': vorname, 'nachname': nachname},
        route=route, country=land, mobile=mobile, engine=getattr(cfg, 'engine', 'patchright'),
        profile_identity=profile_identity,
        password_factory=lambda draft_key: password_supply.reserve(cfg.db_path, 'mail-attempt:' + draft_key))
    adresse, passwort, kennung = draft['address'], draft['password'], draft['identity']
    if adresse.casefold() in existing_addresses:
        raise FinalError('Dieses Postfach ist bereits im Pool. Den vorhandenen Eintrag verwenden.')
    if reused and draft['status'] != 'awaiting_mail':
        melden('Gespeicherte Zieladresse verwenden. Nach einem Fehler startet der Browser mit einem neuen Profil.')
    from ..failure_reports import Progress
    report = Progress(anbieter)
    if draft['status'] == 'awaiting_mail':
        return _resume_completion(cfg,key,draft,melden,stop,report)
    account_reached = False
    review_requested = False
    known_secrets=[passwort,adresse]
    def account_seen(password):
        nonlocal account_reached
        account_reached=True
        known_secrets.append(password)
    def request_review():
        nonlocal review_requested
        review_requested=True
    def checkpoint(entry, proxy):
        account_seen(entry['passwort'])
        registration_attempts.confirmed(cfg.db_path,key,entry,proxy)
    registration_attempts.status(cfg.db_path, key, 'running')
    try:
        result = _create_validated(cfg, anbieter, kennung=kennung, adresse=adresse,
            vorname=vorname, nachname=nachname, passwort=passwort, mode=mode, land=land,
            mobile=mobile, timeout=timeout, backend=backend, im=im, melden=melden, stop=stop, route=route,
            registrierungsdaten=details, report=report, on_confirmed=checkpoint, on_account_seen=account_seen,
            failure_window=failure_window, on_review=request_review, profile_identity=profile_identity)
    except BaseException as error:
        aborted = cancelled(stop)
        pending_mail = False
        try:
            state = registration_attempts._load(cfg.db_path)['drafts'][key]['status']
            pending_mail = state == 'awaiting_mail'
            if not pending_mail:
                registration_attempts.status(cfg.db_path, key, 'needs_review' if account_reached or aborted or review_requested else 'failed')
        except Exception:
            error.add_note('Versuchsstatus nicht aktualisiert. Unterbrochenen Kontoabschluss prüfen; keine automatische neue Registrierung.')
        if not aborted:
            report.publish_failure(cfg.db_path,error,melden,secrets=known_secrets,
                next_step=('Der Kontoabschluss wurde erkannt. Keine neue Registrierung starten; '
                           'gespeicherten Stand und Postfachzugang prüfen. Beim Fortsetzen nur den Mailabruf prüfen.'
                           if pending_mail else 'Ein Kontoabschluss ist möglich. Den gespeicherten Stand prüfen und '
                           'ein vorhandenes Postfach lokal übernehmen; keine automatische neue Registrierung.'
                           if account_reached or review_requested else None))
        raise
    registration_attempts.completed(cfg.db_path, key)
    return result


def _resume_completion(cfg,key,draft,melden,stop,report):
    from .. import registration_attempts
    from ..net.proxy import Proxy
    completion=draft['completion'];entry=completion['entry']
    existing=next((x for x in accountvault.postfaecher(cfg.db_path)
                   if x['adresse'].strip().casefold()==entry['adresse'].casefold()),None)
    if existing and any(existing.get(key)!=entry.get(key) for key in ('passwort','anbieter','erstellt_am','imap')):
        raise FinalError('Im Pool ist ein abweichender Postfachzugang gespeichert. Den vorhandenen Eintrag lokal prüfen; nichts überschrieben.')
    try:proxy=Proxy.parse(completion['proxy'])
    except Exception:
        raise FinalError('Der gespeicherte Prüfproxy ist ungültig; keine Direktverbindung und keine neue Registrierung.') from None
    melden('Kontoabschluss bereits bestätigt. Nur den Mailabruf erneut prüfen; keine neue Registrierung.')
    try:result=_verify_mailbox(cfg,entry,proxy,melden,stop,report,already_saved=existing is not None)
    except BaseException as error:
        if not cancelled(stop):
            report.publish_failure(cfg.db_path,error,melden,secrets=(entry['passwort'],entry['adresse']),
                next_step='Das Konto ist bereits bestätigt. Postfachzugang und IMAP-Freigabe lokal prüfen. '
                          'Beim Fortsetzen wird nur der Mailabruf geprüft, keine neue Registrierung gestartet.')
        raise
    registration_attempts.completed(cfg.db_path,key)
    return result


@browser_owned
def _create_validated(cfg, anbieter, *, kennung, adresse, vorname, nachname, passwort,
                      mode, land, mobile, timeout, backend=None, im=None, melden=log.info,
                      stop=None, route='browser', registrierungsdaten=None, report=None, on_confirmed=None, on_account_seen=None, failure_window=0, on_review=None, profile_identity=None):
    if report is not None:report.phase('Browser und Proxy vorbereiten')
    modul = ANBIETER[anbieter]
    from ..privacy_consent import enabled
    accept_privacy = enabled(cfg.db_path)
    input_pause_ms = 0  # Input timing is stored on this profile, not on the provider.
    if im is None:
        im = _identity_manager(cfg)
    if backend is None and route=='browser':
        from ..net.factory import build as build_backend
        backend = build_backend(cfg, im)
    ident = profile_identity or f'mail_{anbieter.replace(".", "")}_{kennung}'
    from .. import profile_settings
    personal = profile_settings.from_registration(registrierungsdaten or
        {'vorname': vorname, 'nachname': nachname})
    stored_profile = profile_settings.update(cfg.profiles_dir, ident, person=personal, fill_empty=True)
    profile_settings.normalize_person(stored_profile['person'], required=True)
    person=im.get(ident,land,mobile)
    if route == 'android' and anbieter == 'web.de':
        resolver = getattr(im, 'account_proxies', None)
        proxy = resolver.proxy_for(ident) if resolver is not None else None
        if proxy is None:
            raise FinalError('Für dieses Profil fehlt ein fest zugeordneter Proxy. Bitte unter Profile → Proxy zuordnen festlegen.')
    else:
        proxy=im.proxy_for(person)
    ctx = MailCtx(anbieter=anbieter, vorname=vorname, nachname=nachname, adresse=adresse, passwort=passwort,
                  mode=mode, land=land, mobile=mobile, timeout=timeout, backend=backend, identity=ident,
                  domaene=getattr(modul, 'DOMAENE', ''), melden=melden, stop=stop,
                  registrierungsdaten=dict(registrierungsdaten or {}), report=report, accept_privacy=accept_privacy,
                  input_pause_ms=input_pause_ms, person=stored_profile['person'], profile_tempo=stored_profile['tempo'])
    ctx.check_cancelled()
    if proxy is None:
        raise FinalError('Die Registrierung benötigt einen eingerichteten Proxy; keine Direktverbindung gestartet.')
    from ..registration_progress import update
    update(ctx,'opening')
    melden(f'Lege Postfach {adresse} bei {anbieter} an ({mode}).')
    try:
        if route=='android':
            ctx.progress('Android-Registrierung')
            if anbieter == 'web.de':
                from ..android.webde_app import run
                eintrag = None
                def confirmed_before_close(result, actual_proxy):
                    nonlocal eintrag
                    eintrag = _confirmed_entry(ctx, result, android=True)
                    if on_confirmed:on_confirmed(eintrag, actual_proxy)
                ergebnis, proxy = run(cfg, ctx, proxy, on_review=on_review,
                                     on_confirmed=confirmed_before_close)
                if eintrag is None:
                    raise FinalError('Der WEB.DE-App-Abschluss wurde nicht dauerhaft übernommen.')
            else:
                from ..android.google_mail import run
                ergebnis=run(cfg,ctx,proxy)
                eintrag = _confirmed_entry(ctx,ergebnis,android=True)
                if on_confirmed:on_confirmed(eintrag,proxy)
        else:
          ctx.progress('Browser öffnen')
          with backend.session(person, proxy, headless=not (mode == 'assisted' or not cfg.headless), platform='mail') as handle:
            ctx.handle = handle
            ctx.check_cancelled()
            page = backend.page(handle)
            try:
                open_signup = getattr(modul, 'open_signup', None)
                if callable(open_signup):
                    open_signup(page, ctx)
                else:
                    from ..entry_rules import open_registration
                    open_registration(page, anbieter, modul.SIGNUP_URL, ctx)
                ctx.warte(2500); ctx.dismiss()
                from .unitedinternet import raise_if_rejected
                raise_if_rejected(page)
                ctx.progress('Registrierungsformular')
                update(ctx,'form')
                from ..net.form_guard import bind
                bind(page, anbieter)
                if on_review:on_review()
                ergebnis = modul.run(page, ctx)
                eintrag = _confirmed_entry(ctx,ergebnis)
                if on_confirmed:on_confirmed(eintrag,proxy)
                ctx.progress('Browser schließen')
            except Exception as error:
                if failure_window and not cancelled(stop):
                    if on_review:on_review()
                    if report:report.publish_failure(cfg.db_path,error,melden,secrets=(ctx.passwort,ctx.adresse),
                        next_step='Der Browser bleibt begrenzt zur Diagnose offen. Sichtbaren Stand prüfen; '
                                  'keinen neuen Registrierungsauftrag parallel starten.')
                    from ..registration_review import hold
                    try:hold(page,ctx,failure_window)
                    except FinalError:raise
                    except Exception:
                        error.add_note('Das Diagnosefenster konnte nicht offengehalten werden; ursprünglicher Fehler bleibt erhalten.')
                raise
    finally:
        ctx.handle = None
        if ctx.account_confirmed is True and on_account_seen:on_account_seen(ctx.passwort)
    ctx.check_cancelled()
    return _verify_mailbox(cfg,eintrag,proxy,melden,stop,report)


def _confirmed_entry(ctx, result, *, android=False):
    ctx.check_cancelled()
    returned=result.get('adresse',ctx.adresse) if isinstance(result,dict) else None
    if (not isinstance(returned,str) or ctx.account_confirmed is not True or
            returned.strip().casefold()!=ctx.adresse.casefold()):
        raise FinalError('Der Abschluss des gewünschten Postfachs wurde nicht bestätigt; kein Pool-Eintrag gespeichert.')
    if android:
        if getattr(ctx, 'android_credentials_uncertain', False):
            raise FinalError('Das tatsächlich verwendete Android-Kennwort ist noch nicht bestätigt.')
        if not (ctx.password_filled is True or ctx.password_confirmed_by_user is True):
            raise FinalError('Das verwendete Postfachkennwort ist noch nicht bestätigt.')
    else:
        ctx.require_known_password()
    hint=result.get('hinweis')
    if isinstance(hint,str) and hint and hint not in ctx.notizen:ctx.hinweis(hint)
    from ..registration_progress import update
    update(ctx,'account_confirmed')
    entry = {'adresse':ctx.adresse,'passwort':ctx.passwort,'anbieter':ctx.anbieter,
             'imap':result.get('imap') or {},'notizen':list(ctx.notizen),'erstellt_am':repo.iso()}
    if android and result.get('avd'):
        from ..android.avd import name_for
        if result['avd'] != name_for(ctx.identity):
            raise FinalError('Das bestätigte Android-Gerät gehört nicht zu diesem Profil.')
        entry.update(android_avd=result['avd'], profile_identity=ctx.identity)
    return entry


def _verify_mailbox(cfg, entry, proxy, melden, stop, report, *, already_saved=False):
    from .. import mailbox
    from ..operation import check_cancelled
    from ..registration_progress import update
    from types import SimpleNamespace
    ctx=SimpleNamespace(anbieter=entry['anbieter'],melden=melden)
    check_cancelled(stop)
    if proxy is None:raise FinalError('Die Abschlussprüfung benötigt den gespeicherten Proxy; kein Direktabruf.')
    if report is not None:report.phase('Posteingang tatsächlich abrufen')
    update(ctx,'checking_mail')
    try:
        box=mailbox.build(accountvault.als_postfach(entry),proxy=proxy)
        count=box.check_access()
        if type(count) is not int or count<0:
            raise mailbox.MailboxError('Keine gültige Postfachprüfung.')
    except (mailbox.MailboxError,OSError):
        check_cancelled(stop)
        update(ctx,'pending_mail')
        raise FinalError('Der Kontoabschluss wurde bestätigt, der Mailabruf noch nicht. '
                         'Postfachzugang und IMAP-Freigabe lokal prüfen; kein fertiges Postfach im Pool gespeichert. '
                         'Beim Fortsetzen wird nur der Mailabruf geprüft.') from None
    check_cancelled(stop)
    if report is not None:report.phase('Geprüftes Postfach lokal speichern')
    update(ctx,'saving')
    if not already_saved:accountvault.add_postfach(cfg.db_path,entry)
    # Saving is the commit point; a later cancel cannot undo a verified pool entry.
    update(ctx,'complete')
    melden('Kontoabschluss und Mailabruf bestätigt. Das Postfach liegt im Pool.')
    return {'adresse':entry['adresse'],'anbieter':entry['anbieter'],'notizen':entry['notizen']}


def _identity_manager(cfg):
    from ..net.factory import build_identity_manager
    return build_identity_manager(cfg)
