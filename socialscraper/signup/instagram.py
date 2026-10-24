import random, time
from ..profile_settings import birthdate, full_name
from ..net.form_guard import required
REGISTER_URL = 'https://www.instagram.com/accounts/emailsignup/'
SEL = {'email': ['input[name="email"]', 'input[type="email"]', 'label:has-text("Handynummer oder E-Mail-Adresse") input', 'label:has-text("Mobile number or email") input', 'input[autocomplete="email"]', 'input[placeholder*="email" i]', 'input[aria-label*="email" i]', 'input[aria-label*="E-Mail" i]'],
       'name': ['input[name="fullName"]', 'label:has-text("Vollständiger Name") input', 'label:has-text("Full name") input', 'input[autocomplete="name"]', 'input[aria-label*="full name" i]', 'input[aria-label*="vollständiger name" i]'],
       'user': ['input[name="username"]', 'input[aria-label*="Benutzername" i]'],
       'pass': ['input[name="password"]', 'input[type="password"]'],
       'submit': ['button[type="submit"]', 'button:has-text("Registrieren")', 'button:has-text("Sign up")', '[role="button"]:has-text("Senden")', '[role="button"]:has-text("Submit")', 'text="Senden"', 'text="Submit"'],
       'monat': ['select[title="Monat"]', 'select[title="Month"]'], 'tag': ['select[title="Tag"]'],
       'jahr': ['select[title="Jahr"]', 'select[title="Year"]'],
       'weiter': ['button[type="button"]:has-text("Weiter")', 'button:has-text("Next")']}
CHECKPOINT = ['input[name="confirmationCode"]', 'input[aria-label*="Bestätigungscode" i]']

def run(page, ctx):
    start = time.time()
    ctx.warte(3000); ctx.dismiss()
    if not ctx.fill(SEL['email'], ctx.email):
        raise RuntimeError('Instagram: E-Mail-Feld nicht gefunden')
    required(ctx.fill(SEL['name'], full_name(ctx.person)), 'Der vollständige Name')
    required(ctx.fill(SEL['user'], ctx.username.replace('_', '.')[:20]), 'Der Benutzername')
    required(ctx.fill(SEL['pass'], ctx.password), 'Das Kennwort')
    # Current Meta forms request the date on the first page using custom controls.
    # Assisted mode leaves that personal input and final confirmation to the user.
    if _erstes(page, ['[role="combobox"][aria-label*="Tag" i]',
                     '[role="combobox"][aria-label*="Day" i]',
                     '[role="button"]:has-text("Tag")', '[role="button"]:has-text("Day")']):
        if ctx.mode != 'assisted':
            raise RuntimeError('Instagram verlangt ein Geburtsdatum im neuen Formular. Bitte im Modus assisted abschließen.')
        ctx.melden('Bitte das Geburtsdatum im Instagram-Fenster eintragen und die Registrierung abschließen.')
        return ctx.warte_auf_cookies(still=False)
    ctx.warte(400)
    if not ctx.click(SEL['submit']):
        raise RuntimeError('Instagram: Registrieren-Schaltfläche nicht gefunden')
    ctx.warte(3000)
    born = birthdate(ctx.person)
    for auswahl, wert in ((SEL['jahr'], born.year), (SEL['monat'], born.month), (SEL['tag'], born.day)):
        from . import _select
        required(_select(page, auswahl, wert, check=getattr(ctx, 'check_cancelled', None)), 'Die Datumsangabe')
    ctx.click(SEL['weiter']); ctx.warte(2500)
    if _erstes(page, CHECKPOINT):
        ctx.melden('Instagram verlangt einen Bestätigungscode – bitte im Fenster prüfen.')
        required(ctx.fill(CHECKPOINT, ctx.code_holen(timeout=240, ab=start - 30)), 'Der Bestätigungscode')
        ctx.click(SEL['weiter']); ctx.warte(2500)
    else:
        ctx.melden('Instagram zeigt evtl. einen Checkpoint – im Zweifel im Fenster bestätigen.')
    return ctx.warte_auf_cookies(still=False)

def _erstes(page, auswahl):
    from ..dom import first_selector
    return first_selector(page, auswahl, visible=True)
