import random, time
from ..profile_settings import birthdate, full_name
from ..net.form_guard import required
REGISTER_URL = 'https://x.com/i/flow/signup'
SEL = {'name': ['input[name="name"]', 'input[autocomplete="name"]'],
       'email': ['input[name="email"]', 'input[type="email"]'],
       'otp': ['input[inputmode="numeric"]', 'input[autocomplete="one-time-code"]'],
       'pass': ['input[type="password"]', 'input[name="password"]'],
       'user': ['input[name="username"]', 'input[autocomplete="username"]'],
       'weiter': ['div[role="button"]:has-text("Weiter")', 'div[role="button"]:has-text("Next")',
                  'button:has-text("Weiter")', 'button:has-text("Next")'],
       'ueberspringen': ['div[role="button"]:has-text("Überspringen")', 'div[role="button"]:has-text("Skip")']}
ARKOSE = ['iframe[title*="arkose" i]', 'iframe[src*="arkose" i]', '#arkose', 'iframe[src*="funcaptcha" i]']

def run(page, ctx):
    start = time.time()
    ctx.warte(2500)
    ctx.click(['div[role="button"]:has-text("Konto erstellen")', 'div[role="button"]:has-text("Create account")'], navigate=True)
    ctx.warte(2000)
    if not ctx.fill(SEL['name'], full_name(ctx.person)):
        raise RuntimeError('X: Namensfeld nicht gefunden')
    ctx.click(SEL['weiter']); ctx.warte(1200)
    required(ctx.fill(SEL['email'], ctx.email), 'Die E-Mail-Adresse'); ctx.click(SEL['weiter']); ctx.warte(2000)
    if _erstes(page, ARKOSE):
        ctx.melden('X zeigt die Arkose-Abfrage – bitte im Fenster lösen.')
    ctx.warte(1500)
    if _erstes(page, SEL['otp']):
        required(ctx.fill(SEL['otp'], ctx.code_holen(timeout=240, ab=start - 30)), 'Der Bestätigungscode')
        ctx.click(SEL['weiter']); ctx.warte(2000)
    born = birthdate(ctx.person)
    for auswahl, wert in (('select[id*="YEAR"]', born.year), ('select[id*="MONTH"]', born.month),
                          ('select[id*="DAY"]', born.day)):
        from . import _select
        required(_select(page, [auswahl], wert, check=getattr(ctx, 'check_cancelled', None)), 'Die Datumsangabe')
    ctx.click(SEL['weiter']); ctx.warte(1500)
    if _erstes(page, SEL['pass']):
        required(ctx.fill(SEL['pass'], ctx.password), 'Das Kennwort'); ctx.click(SEL['weiter']); ctx.warte(1800)
    if _erstes(page, SEL['user']):
        required(ctx.fill(SEL['user'], ctx.username[:15]), 'Der Benutzername'); ctx.click(SEL['weiter']); ctx.warte(1800)
    ctx.click(SEL['ueberspringen'])
    return ctx.warte_auf_cookies(still=False)

def _erstes(page, auswahl):
    from ..dom import first_selector
    return first_selector(page, auswahl, visible=True)
