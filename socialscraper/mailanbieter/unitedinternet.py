"""Gemeinsame Registrierung für GMX und web.de."""
import datetime as dt
import re, time
from urllib.parse import urlsplit
from ..net.errors import FinalError

SEL = {
    'anrede': ['select[name="salutation"]', 'select#salutation', 'select[name="gender"]'],
    'vorname': ['input[name="firstName"]', 'input#firstName'],
    'nachname': ['input[name="lastName"]', 'input#lastName'],
    'tag': ['input[name="birthDay"]', 'select[name="birthDay"]', 'select#birthDay'],
    'monat': ['input[name="birthMonth"]', 'select[name="birthMonth"]', 'select#birthMonth'],
    'jahr': ['input[name="birthYear"]', 'select[name="birthYear"]', 'select#birthYear'],
    'plz': ['input[name="zip"]', 'input[name="postalCode"]', 'input#zip'],
    'ort': ['input[name="city"]', 'input#city'],
    'strasse': ['input[name="street"]', 'input#street'],
    'hausnummer': ['input[name="houseNumber"]', 'input#houseNumber'],
    'land': ['select[name="country"]', 'select#country'],
    'adresse': ['input[name="alias"]', 'input#alias', 'input[name="email"]', 'input#email', 'input[name="wunschAdresse"]'],
    'passwort': ['input[name="password"]', 'input#password', 'input[type="password"]'],
    'agb': ['input[name="agb"]', 'input#agb', 'input[name="termsAccepted"]', 'label[for*="agb" i]'],
    'senden': ['button[type="submit"]', 'button:has-text("Kostenlos")', 'button:has-text("registrieren")',
               'input[type="submit"]'],
}
CAPTCHA = ['iframe[src*="captcha" i]', 'img[src*="captcha" i]', '.captcha', '#captcha', 'text="Ich bin ein Mensch"']
ALIAS_NEXT = ['form#alias-step button[data-testid="next-button"]']
ALIAS_AVAILABLE = ['form#alias-step .alias-step__form-element--available input#alias']
PERSONAL_DETAILS = sum((SEL[key] for key in ('plz', 'anrede', 'ort', 'strasse', 'hausnummer', 'land')), []) + [
    'input[type="radio"][name="gender"]']
SALUTATION_LABELS=re.compile(r'^\s*(?:Herr|Frau|männlich|weiblich|divers|Keine Angabe(?: machen)?)\s*$',re.I)


def _salutation_radios(page,labels=SALUTATION_LABELS):
    radios=page.get_by_role('radio',name=labels)
    count=radios.count()
    if type(count) is not int:return ()
    return tuple(radios.nth(i) for i in range(min(count,20)) if radios.nth(i).is_visible())


def _has_salutation(page):
    return bool(_erstes(page,SEL['anrede']+['input[type="radio"][name="gender"]']) or _salutation_radios(page))


def _salutation_state(page):
    return tuple(i for i,radio in enumerate(_salutation_radios(page)) if radio.is_enabled() and radio.is_checked())


def _value_matches(page, selector, value):
    """Read the actual control back before allowing the next step."""
    try:
        state = page.locator(selector).evaluate('''el => ({select:el.tagName === 'SELECT',
            options:el.options ? [...el.options].map(o => ({label:o.label.trim(),value:o.value})) : [],
            selected:el.selectedOptions ? [...el.selectedOptions].map(o => ({label:o.label.trim(),value:o.value})) : []})''', timeout=500)
        if state['select']:
            from ..net.form_choices import matches, month_options
            return len(state['selected']) == 1 and matches(state['selected'][0], value, months=month_options(state['options']))
        actual = page.input_value(selector, timeout=500)
        if actual == str(value):return True
        if isinstance(value, int) and isinstance(actual, str) and actual.isdecimal():
            return int(actual) == value
        return False
    except Exception:
        return False


def _salutation(page, value, check):
    labels = {'Herr': ('Herr', 'Männlich', 'männlich'), 'Frau': ('Frau', 'Weiblich', 'weiblich'),
              'Divers': ('Divers', 'divers'), 'Keine Angabe': ('Keine Angabe', 'Keine Angabe machen')}.get(value, ())
    for label in labels:
        if check:check()
        selector = _erstes(page, SEL['anrede'])
        if selector and _waehle(page, [selector], label, check=check) and _value_matches(page, selector, label):
            return True
        try:
            matches=_salutation_radios(page,re.compile(r'^\s*'+re.escape(label)+r'\s*$',re.I))
            if len(matches)==1 and (radio:=matches[0]).is_enabled():
                if check:check()
                if not radio.is_checked():
                    from ..net.input_behavior import click
                    click(page, radio, check=check, timeout=5000)
                if check:check()
                if radio.is_checked():return True
        except FinalError:raise
        except Exception:
            if check:check()
    return False


def _salutation_matches(page, value):
    labels = {'Herr': ('Herr', 'Männlich', 'männlich'), 'Frau': ('Frau', 'Weiblich', 'weiblich'),
              'Divers': ('Divers', 'divers'), 'Keine Angabe': ('Keine Angabe', 'Keine Angabe machen')}.get(value, ())
    selector = _erstes(page, SEL['anrede'])
    if selector and any(_value_matches(page, selector, label) for label in labels):
        return True
    checked = [radio for radio in _salutation_radios(page) if radio.is_checked()]
    expected = _salutation_radios(page, re.compile(r'^\s*(?:' + '|'.join(re.escape(label) for label in labels) + r')\s*$', re.I)) if labels else ()
    return len(checked) == 1 and sum(radio.is_checked() for radio in expected) == 1


def _alias_available(page,ctx):
    """Only the provider's positive check for our exact address permits selection."""
    if not _erstes(page,ALIAS_AVAILABLE):return False
    try:
        return (page.input_value('#alias',timeout=500)==ctx.adresse.partition('@')[0] and
                page.input_value('#domain',timeout=500)==ctx.domaene)
    except Exception:
        return False


def raise_if_rejected(page):
    location=urlsplit(str(page.url))
    if location.hostname in ('hilfe.gmx.net','hilfe.web.de') and 'flexreg' in location.path:
        raise FinalError('Der Anbieter meldet „Registrierung fehlgeschlagen“. '
                         'Es wurde kein Testpostfach angelegt; der Grund ist aus dieser Fehlerseite nicht ersichtlich.')


def run(page, ctx):
    submitted=set();notified=set();filled_steps={}
    manual_consent_steps=set()
    manual_captcha_steps=set()
    last_stage=None;alias_followup_attempted=False
    from ..registration_data import normalize, COUNTRIES
    details=normalize(getattr(ctx,'registrierungsdaten',{}))
    born=dt.date.fromisoformat(details['geburtsdatum']) if details['geburtsdatum'] else None
    birthday={'tag':born.day,'monat':born.month,'jahr':born.year} if born else {}
    ctx.hinweis(f'Zieladresse für dieses Testpostfach: {ctx.adresse}. Bestätigungen im Fenster selbst übernehmen.')
    def consent_required(stages):
        pending=_has_consent_controls(page)
        if pending:
            state=getattr(ctx,'state',None)
            if callable(state):state('waiting_confirmation')
            manual_consent_steps.add(stages)
        if pending and 'consent' not in notified:
            notified.add('consent')
            ctx.hinweis('Bitte die Bedingungen und Einwilligungen im Fenster selbst prüfen, entscheiden und weitergehen. '
                        'Das Programm setzt keine Zustimmung.')
        # A checkbox disappearing is not proof that the user submitted this
        # section. Keep its submit manual for the remainder of the flow.
        return stages in manual_consent_steps

    def captcha_required(stages):
        if _has_captcha_controls(page):
            state=getattr(ctx,'state',None)
            if callable(state):state('waiting_confirmation')
            manual_captcha_steps.add(stages)
            if 'captcha' not in notified:
                notified.add('captcha')
                ctx.hinweis(f'{ctx.anbieter}: Bitte ein angezeigtes CAPTCHA selbst lösen und '
                            'diesen Schritt im Fenster mit Jetzt starten bzw. Weiter bestätigen.')
        return stages in manual_captcha_steps

    def advance():
        nonlocal last_stage,alias_followup_attempted
        if ctx.stop is not None and ctx.stop():
            raise FinalError('Abgebrochen.')
        raise_if_rejected(page)
        # Only registration forms may receive generated credentials, never a
        # login page reached later in the provider's flow.
        location=urlsplit(str(page.url))
        if location.hostname not in ('register.web.de','register.gmx.net'):
            return
        if location.scheme!='https':
            raise FinalError('Die Registrierungsseite verwendet kein HTTPS; keine Testdaten eingetragen.')
        stages=tuple(key for key in ('vorname','adresse','passwort') if _erstes(page,SEL[key]))
        alias_stage=stages==('adresse',) and bool(_erstes(page,['input[name="alias"]','input#alias']))
        personal_details=bool(_erstes(page,PERSONAL_DETAILS)) or _has_salutation(page)
        current_stage=stages+(('anschrift',) if personal_details else ())
        if current_stage and current_stage!=last_stage:
            state=getattr(ctx,'state',None)
            if callable(state):state('form')
            labels={'vorname':'Name und Geburtsdatum','adresse':'E-Mail-Adresse',
                    'passwort':'Kennwort','anschrift':'Anschrift und Anrede'}
            message='Aktueller Registrierungsabschnitt: '+', '.join(labels[key] for key in current_stage)+'.'
            from ..entry_rules import progress
            progress(ctx, ', '.join(labels[key] for key in current_stage))
            if personal_details:
                message+=' Hinterlegte Registrierungsdaten werden eingetragen. Fehlende Angaben bitte lokal ergänzen oder im Fenster übernehmen.'
            ctx.hinweis(message)
            last_stage=current_stage
        pending_consent=consent_required(current_stage)
        pending_captcha=captcha_required(current_stage)
        if current_stage and current_stage not in submitted:
            completed=filled_steps.setdefault(current_stage,set())
            def fill_once(key,selectors,value,*,select=False,select_only=False):
                if value is None or value=='':return False
                if key in completed:
                    selector = _erstes(page, selectors)
                    return bool(selector and _value_matches(page, selector, value))
                changed=None if select_only else ctx.fill(selectors,str(value))
                if not changed and (select or select_only):
                    changed=_waehle(page,selectors,value,check=getattr(ctx,'check_cancelled',None))
                selector=changed if isinstance(changed,str) else _erstes(page,selectors)
                verified=bool(changed and selector and _value_matches(page,selector,value))
                if verified:completed.add(key)
                return verified
            ready=True
            if 'vorname' in stages:
                ready=fill_once('vorname',SEL['vorname'],ctx.vorname) and ready
                ready=fill_once('nachname',SEL['nachname'],ctx.nachname) and ready
                for key in ('jahr','monat','tag'):
                    if _erstes(page,SEL[key]):
                        ready=fill_once(key,SEL[key],birthday.get(key),select=True) and ready
            if 'adresse' in stages:ready=fill_once('adresse',SEL['adresse'],ctx.adresse.partition('@')[0]) and ready
            if alias_stage and _erstes(page, ['select#domain']):
                ready=fill_once('domaene', ['select#domain'], ctx.domaene, select_only=True) and ready
            if 'passwort' in stages:
                ready=fill_once('passwort',SEL['passwort'],ctx.passwort) and ready
                for selector in ('input[name="passwordConfirmation"]','input[name="confirmPassword"]','input[name="passwordRepeat"]'):
                    if _erstes(page,[selector]):ready=fill_once(selector,[selector],ctx.passwort) and ready
            if personal_details:
                for key in ('plz','ort','strasse','hausnummer','land'):
                    if not _erstes(page,SEL[key]):continue
                    value=details[key]
                    if key=='strasse' and value and details['hausnummer'] and not _erstes(page,SEL['hausnummer']):
                        value+=' '+details['hausnummer']
                    if key=='land':value=COUNTRIES.get(value,'')
                    ready=fill_once(key,SEL[key],value,select_only=key=='land') and ready
                if _has_salutation(page):
                    if 'anrede' not in completed:
                        if _salutation(page,details['anrede'],getattr(ctx,'check_cancelled',None)):
                            completed.add('anrede')
                    ready=('anrede' in completed and _salutation_matches(page,details['anrede'])) and ready
                    if not ready and 'anrede' not in completed and 'anrede_missing' not in notified:
                        notified.add('anrede_missing')
                        ctx.hinweis('Die hinterlegte Anrede/Geschlechtsauswahl fehlt oder wird hier nicht angeboten. '
                                    'Bitte eine angebotene Auswahl im Fenster bestätigen oder die Registrierungsdaten lokal ändern. '
                                    'Keine automatische Ersatzwahl.')
            if ready and not pending_consent and not pending_captcha:
                ctx.warte(400);ctx.dismiss()
                if consent_required(current_stage) or captcha_required(current_stage):
                    return
                # A validation/CAPTCHA page must stay available instead of receiving
                # another submit on every poll. The user can continue it manually.
                submitted.add(current_stage)
                if not ctx.click(ALIAS_NEXT if alias_stage else SEL['senden']):
                    ctx.hinweis('Im aktuellen Registrierungsformular selbst auf Weiter tippen.')
            elif not ready and not personal_details and ('input',stages) not in notified:
                notified.add(('input',stages))
                ctx.hinweis('Ein Registrierungsfeld konnte noch nicht ausgefüllt werden; dieser Schritt wurde nicht abgesendet.')
        elif alias_stage and not personal_details and not pending_consent and current_stage not in manual_captcha_steps and not alias_followup_attempted and _alias_available(page,ctx):
            if consent_required(current_stage) or captcha_required(current_stage):
                return
            # WEB.DE first checks a changed alias, then requires a second click
            # to select it. A pending/negative check must never trigger retries.
            alias_followup_attempted=True
            ctx.hinweis('Die gewünschte E-Mail-Adresse ist verfügbar; die Auswahl wird übernommen.')
            if not ctx.click(ALIAS_NEXT):
                ctx.hinweis('Die gewünschte E-Mail-Adresse ist verfügbar. Bitte im Fenster auf Weiter tippen.')
        if 'phone' not in notified and _erstes(page,['input[name="phone"]','input[type="tel"]','input[name="mobile"]']):
            notified.add('phone')
            ctx.hinweis(f'{ctx.anbieter} verlangt eine Telefonnummer. Bitte im Fenster selbst übernehmen.')
    advance()
    _warte_bis_fertig(page, ctx,tick=advance)
    return {'adresse': f'{ctx.adresse.partition("@")[0]}@{ctx.domaene}'}


def _confirmed_mailbox(page,ctx):
    address=getattr(ctx,'adresse','');domain=getattr(ctx,'domaene','') or getattr(ctx,'anbieter','')
    if not isinstance(address,str) or '@' not in address or domain not in ('web.de','gmx.net','gmx.de'):return False
    parts=urlsplit(str(page.url));host=parts.hostname or ''
    registration_host='register.'+domain
    inbox_hosts={prefix+'.'+domain for prefix in ('navigator','webmailer','mail','3c')}
    if parts.scheme!='https' or host not in {registration_host,*inbox_hosts}:return False
    try:body=page.inner_text('body',timeout=1500)
    except Exception as error:
        if type(error).__name__=='TimeoutError' or 'Execution context was destroyed' in str(error):return False
        raise
    if not isinstance(body,str):return False
    exact=r'(?<![\w.+-])'+re.escape(address.casefold())+r'(?![\w.+-])'
    if not re.search(exact,body.casefold()):return False
    # A positive declaration at a sentence/heading boundary is required.
    # Substrings such as "noch nicht bereit" or "sobald ... abgeschlossen"
    # are not evidence of an account, nor is quoted text on a help page.
    subject=r'(?:(?:dein(?:e)?|ihr(?:e)?|das|die)\s+)?'
    ready=re.search(r'(?:^|[\r\n.!?])\s*(?:'
        +subject+r'(?:postfach|e-mail[- ]adresse)\s+(?:ist\s+)?(?:(?:jetzt|nun)\s+)?bereit\b|'
        +subject+r'registrierung\s+(?:(?:ist|wurde)\s+)?(?:erfolgreich\s+)?abgeschlossen\b(?!\s+(?:ist|wird)\b)|'
        r'(?:du bist\s+|sie sind\s+|sie haben sich\s+)?erfolgreich\s+(?:registriert|angelegt)\b)',body,re.I)
    inbox=host in inbox_hosts and re.search(r'\b(?:Posteingang|Abmelden)\b',body,re.I)
    ready=host==registration_host and ready
    return bool(ready or inbox)

def _warte_bis_fertig(page, ctx, *, sekunden=None,tick=None):
    frist = time.monotonic() + (sekunden if sekunden is not None else getattr(ctx,'timeout',180))
    while time.monotonic() < frist:
        if ctx.stop is not None and ctx.stop():
            raise RuntimeError('Abgebrochen.')
        raise_if_rejected(page)
        if tick:tick()
        raise_if_rejected(page)
        if _confirmed_mailbox(page,ctx):
            ctx.account_confirmed = True
            verify=getattr(ctx,'require_known_password',None)
            if callable(verify):
                verify()
            elif not getattr(ctx,'password_filled',False):
                raise RuntimeError('Das tatsächlich verwendete Kennwort ist dem Programm nicht bekannt; kein Postfach im Pool gespeichert.')
            return
        page.wait_for_timeout(2000)
    raise RuntimeError('Die Postfachregistrierung wurde nicht bestätigt.')


def _waehle(page, auswahl, index, *, check=None):
    from ..signup import _select
    return _select(page, auswahl, index, check=check)


def _has_captcha_controls(page):
    """Inspect every match; an unreadable check cannot permit auto-submission."""
    for selector in CAPTCHA:
        try:
            if any(field.is_visible() for field in page.query_selector_all(selector)):
                return True
        except Exception:
            return True
    return False


def _has_consent_controls(page):
    """The whole consent step is manual, including preselected controls."""
    for sel in SEL['agb']:
        try:
            if any(field.is_visible() for field in page.query_selector_all(sel)):
                return True
        except Exception:
            # An unreadable or replaced control cannot establish consent.
            return True
    return False


def _erstes(page, auswahl):
    from ..dom import first_selector
    return first_selector(page, auswahl, visible=True)


def wunsch_adresse(vorname, nachname, kennung, *, domaene):
    rein = lambda t: re.sub(r'[^a-z0-9]', '', t.lower())
    return f'{rein(vorname)}{"" if domaene == "web.de" else "."}{rein(nachname)}{kennung[:2]}'
