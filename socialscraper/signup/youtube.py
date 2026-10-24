from ..profile_settings import birthdate, select_gender
from . import _select, _passwords, _google_start
from ..net.form_guard import required
REGISTER_URL = 'https://accounts.google.com/signup'
SEL = {'vorname': ['input[name="firstName"]'], 'nachname': ['input[name="lastName"]'],
       'weiter': ['button:has-text("Weiter")', 'button:has-text("Next")', '#accountDetailsNext'],
       'monat': ['select[id="month"]', '#month[role="combobox"]', '[role="combobox"]:has-text("Monat")', '[role="combobox"]:has-text("Month")'], 'tag': ['input[id="day"]', 'select[id="day"]'], 'jahr': ['input[id="year"]', 'select[id="year"]'],
       'geschlecht': ['select[id="gender"]', '#gender[role="combobox"]', '[role="combobox"]:has-text("Geschlecht")', '[role="combobox"]:has-text("Gender")'], 'user': ['input[name="Username"]', 'input[name="username"]'],
       'pass': ['input[name="Passwd"]', 'input[name="PasswdAgain"]'],
       'handy': ['input[id="phoneNumberId"]', 'input[type="tel"]']}

def run(page, ctx):
    ctx.warte(2500)
    _google_start(page, ctx, SEL['vorname'], ctx.person['vorname'])
    required(ctx.fill(SEL['nachname'], ctx.person['nachname']), 'Der Nachname')
    ctx.click(SEL['weiter']); ctx.warte(2200)
    born = birthdate(ctx.person)
    for auswahl, wert in ((SEL['jahr'], born.year), (SEL['monat'], born.month), (SEL['tag'], born.day)):
        if not ctx.fill(auswahl, str(wert)):
            required(_select(page, auswahl, wert, check=getattr(ctx, 'check_cancelled', None)), 'Die Datumsangabe')
    select_gender(page, SEL['geschlecht'], ctx.person, check=getattr(ctx, 'check_cancelled', None))
    ctx.click(SEL['weiter']); ctx.warte(2200)
    if _erstes(page, SEL['user']):
        required(ctx.fill(SEL['user'], ctx.username[:20]), 'Der Benutzername'); ctx.click(SEL['weiter']); ctx.warte(1800)
    if _erstes(page, SEL['pass']):
        required(_passwords(ctx, SEL['pass'], ctx.password), 'Das Kennwort'); ctx.click(SEL['weiter']); ctx.warte(1800)
    if _erstes(page, SEL['handy']):
        ctx.melden('Google verlangt eine Telefonnummer – bitte im Fenster eintragen.')
        ctx.notizen.append('Handynummer nötig')
    ctx.warte(2000)
    return ctx.warte_auf_cookies(still=False)

def _erstes(page, auswahl):
    from ..dom import first_selector
    return first_selector(page, auswahl, visible=True)
