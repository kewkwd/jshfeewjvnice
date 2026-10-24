from ..profile_settings import birthdate, select_gender
from ..signup import _select, _passwords, _google_start
from ..net.form_guard import required
SIGNUP_URL = 'https://accounts.google.com/signup'
SEL = {'vorname': ['input[name="firstName"]'], 'nachname': ['input[name="lastName"]'],
       'weiter': ['button:has-text("Weiter")', 'button:has-text("Next")', '#accountDetailsNext'],
       'monat': ['select[id="month"]', '#month[role="combobox"]', '[role="combobox"]:has-text("Monat")', '[role="combobox"]:has-text("Month")'], 'tag': ['input[id="day"]', 'select[id="day"]'], 'jahr': ['input[id="year"]', 'select[id="year"]'],
       'geschlecht': ['select[id="gender"]', '#gender[role="combobox"]', '[role="combobox"]:has-text("Geschlecht")', '[role="combobox"]:has-text("Gender")'], 'user': ['input[name="Username"]', 'input[name="username"]'],
       'passwort': ['input[name="Passwd"]', 'input[name="PasswdAgain"]'],
       'handy': ['input[id="phoneNumberId"]', 'input[type="tel"]']}

def run(page, ctx):
    ctx.warte(2500)
    _google_start(page, ctx, SEL['vorname'], ctx.vorname)
    required(ctx.fill(SEL['nachname'], ctx.nachname), 'Der Nachname')
    ctx.click(SEL['weiter']); ctx.warte(2200)
    ctx.hinweis('Geburtsangaben im Fenster prüfen. Bei nicht bedienbaren Auswahlfeldern „Im eigenen Browserfenster anzeigen“ verwenden; Proxy und Sitzung bleiben erhalten.')
    born = birthdate(ctx.person)
    for auswahl, wert in ((SEL['jahr'], born.year), (SEL['monat'], born.month), (SEL['tag'], born.day)):
        if not ctx.fill(auswahl, str(wert)):
            required(_select(page, auswahl, wert, check=getattr(ctx, 'check_cancelled', None)), 'Die Datumsangabe')
    select_gender(page, SEL['geschlecht'], ctx.person, check=getattr(ctx, 'check_cancelled', None))
    ctx.click(SEL['weiter']); ctx.warte(2200)
    state = {'username_submitted': False, 'choice_selected': False}
    _credentials(page, ctx, state)
    if _erstes(page, SEL['handy']):
        ctx.hinweis('Google verlangt eine Telefonnummer – bitte im Fenster eintragen.')
    ctx.warte(3000)
    def pending_credentials():
        _credentials(page, ctx, state)
    ctx.bestaetigen('google',tick=pending_credentials)
    return {'adresse': ctx.adresse,
            'imap': {'host': 'imap.gmail.com', 'port': 993},
            'hinweis': 'Für den Abruf IMAP aktivieren und ein App-Passwort setzen.'}

def _credentials(page, ctx, state):
    """Continue credentials after a human completes an earlier assisted stage."""
    from urllib.parse import urlsplit
    if urlsplit(str(page.url)).path.endswith('/collectemailphone'):
        # This field verifies an existing address; our desired Gmail address
        # does not exist yet. Choose creation rather than submitting it here.
        choices = ['button:has-text("Sie haben keine"):has-text("Adresse")',
                   'button:has-text("have an email address or phone number")',
                   'text="Sie haben keine E-Mail-Adresse oder Telefonnummer?"',
                   'text="Don’t have an email address or phone number?"',
                   'text="Don\'t have an email address or phone number?"']
        if not state.get('email_route_selected') and _erstes(page, choices):
            state['email_route_selected'] = True
            required(ctx.click(choices, navigate=True), 'Der Weg zur neuen E-Mail-Adresse')
        return
    if not state['username_submitted'] and _erstes(page, SEL['user']):
        if ctx.fill(SEL['user'], ctx.adresse.partition('@')[0]):
            state['username_submitted'] = True
            required(ctx.click(SEL['weiter']), 'Die Bestätigung der Wunschadresse')
            ctx.warte(1500)
    elif not state['username_submitted'] and not state['choice_selected']:
        choices = ['text="Gmail-Adresse erstellen"', 'text="Create a Gmail address"',
                   'text="Eigene Gmail-Adresse erstellen"', 'text="Create your own Gmail address"']
        if _erstes(page, choices):
            state['choice_selected'] = True
            required(ctx.click(choices, navigate=True), 'Die Auswahl einer eigenen E-Mail-Adresse')
    if not ctx.password_filled and _erstes(page, SEL['passwort']):
        if _passwords(ctx, SEL['passwort'], ctx.passwort):
            ctx.click(SEL['weiter'])

def _erstes(page, auswahl):
    from ..dom import first_selector
    return first_selector(page, auswahl, visible=True)

def wunsch_adresse(vorname, nachname, kennung):
    return f'{vorname.lower()}.{nachname.lower()}{kennung[:2]}@gmail.com'
