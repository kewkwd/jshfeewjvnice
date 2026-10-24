from ..profile_settings import birthdate
from ..signup import _select
from ..net.form_guard import required
SIGNUP_URL = 'https://signup.live.com/'
SEL = {'adresse': ['input[name="MemberName"]', '#MemberName', 'input[name="email"]', 'input[type="email"]'],
       'passwort': ['input[name="Password"]', '#Password', 'input[type="password"]'],
       'weiter': ['input[type="submit"]', 'button:has-text("Weiter")', 'button:has-text("Next")'],
       'vorname': ['input[name="FirstName"]', '#FirstName'], 'nachname': ['input[name="LastName"]', '#LastName'],
       'monat': ['select[id="BirthMonth"]', 'select[name="BirthMonth"]'],
       'tag': ['select[id="BirthDay"]', 'select[name="BirthDay"]'],
       'jahr': ['input[id="BirthYear"]', 'select[name="BirthYear"]']}
ABFRAGE = ['iframe[src*="arkose" i]', 'div#hip', 'img[src*="hip" i]']

def run(page, ctx):
    ctx.warte(2500)
    # New forms accept a full address; legacy MemberName accepts the local part.
    ctx.click(['a:has-text("Neue E-Mail-Adresse anfordern")', 'a:has-text("Get a new email address")'], navigate=True)
    ctx.warte(500)
    if not (ctx.fill(SEL['adresse'][:2], ctx.adresse.partition('@')[0]) or
            ctx.fill(SEL['adresse'][2:], ctx.adresse)):
        raise RuntimeError('Outlook: Adressfeld nicht gefunden')
    ctx.click(SEL['weiter']); ctx.warte(2000)
    required(ctx.fill(SEL['passwort'], ctx.passwort), 'Das Kennwort')
    ctx.click(SEL['weiter']); ctx.warte(2000)
    required(ctx.fill(SEL['vorname'], ctx.vorname), 'Der Vorname')
    required(ctx.fill(SEL['nachname'], ctx.nachname), 'Der Nachname')
    ctx.click(SEL['weiter']); ctx.warte(1800)
    born = birthdate(ctx.person)
    if not ctx.fill(SEL['jahr'], str(born.year)):
        required(_select(page, SEL['jahr'], born.year, check=getattr(ctx, 'check_cancelled', None)), 'Das Geburtsjahr')
    for auswahl, wert in ((SEL['monat'], born.month), (SEL['tag'], born.day)):
        required(_select(page, auswahl, wert, check=getattr(ctx, 'check_cancelled', None)), 'Die Datumsangabe')
    ctx.click(SEL['weiter']); ctx.warte(2000)
    if _erstes(page, ABFRAGE):
        ctx.hinweis('Outlook zeigt eine Abfrage – bitte im Fenster lösen.')
    ctx.warte(3000)
    # These stages were already dispatched above. Do not resubmit them on a
    # timer when the provider keeps a validation page visible.
    ctx.bestaetigen('outlook')
    return {'adresse': ctx.adresse,
            'imap': {'host': 'outlook.office365.com', 'port': 993},
            'hinweis': 'Für den Abruf IMAP aktivieren; bei Zwei-Faktor ein App-Passwort setzen.'}

def _erstes(page, auswahl):
    from ..dom import first_selector
    return first_selector(page, auswahl, visible=True)

def wunsch_adresse(vorname, nachname, kennung):
    return f'{vorname.lower()}{nachname.lower()}{kennung[:3]}@outlook.com'
