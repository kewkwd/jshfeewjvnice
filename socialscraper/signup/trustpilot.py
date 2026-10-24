import time
from ..net.form_guard import required
REGISTER_URL = 'https://www.trustpilot.com/signup'
SEL = {'email': ['input[name="email"]', 'input[type="email"]', 'input[autocomplete="email"]'],
       'pass': ['input[name="password"]', 'input[type="password"]'],
       'name': ['input[name="name"]', 'input[name="displayName"]', 'input[autocomplete="name"]'],
       'senden': ['button[type="submit"]', 'button:has-text("Konto erstellen")',
                  'button:has-text("Registrieren")', 'button:has-text("Sign up")']}

def run(page, ctx):
    start = time.time()
    ctx.warte(2500); ctx.dismiss()
    ctx.click(['button:has-text("Mit E-Mail")', 'button:has-text("Continue with email")',
               'button:has-text("Registrieren")', 'a:has-text("Registrieren")'], navigate=True)
    ctx.warte(1500)
    if not ctx.fill(SEL['email'], ctx.email):
        raise RuntimeError('Trustpilot: E-Mail-Feld nicht gefunden')
    from ..dom import first_selector
    if first_selector(page, SEL['pass'], visible=True):
        required(ctx.fill(SEL['pass'], ctx.password), 'Das Kennwort')
    from ..profile_settings import full_name
    if first_selector(page, SEL['name'], visible=True):
        required(ctx.fill(SEL['name'], full_name(ctx.person)), 'Der vollständige Name')
    ctx.warte(400)
    if not ctx.click(SEL['senden']):
        raise RuntimeError('Trustpilot: Absende-Schaltfläche nicht gefunden')
    ctx.warte(3000)
    try:
        link = ctx.link_holen(timeout=300, ab=start - 30)
        ctx.melden('Bestätigungslink gefunden – wird geöffnet.')
        page.goto(link, wait_until='domcontentloaded')
        ctx.warte(2500); ctx.dismiss()
    except Exception as fehler:
        ctx.melden(f'Kein Bestätigungslink gefunden ({fehler}) – bitte im Fenster prüfen.')
    return ctx.warte_auf_cookies(still=False)
