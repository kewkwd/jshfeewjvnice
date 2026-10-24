"""Bewertungen über den Browser: Google Maps und Trustpilot."""
import logging, random, re

from . import ART_ANMELDUNG, ART_CAPTCHA, ART_FEHLER, ART_RATELIMIT, ART_SPERRE, ART_ZIEL, ART_UNKLAR, Ergebnis

log = logging.getLogger(__name__)
UA_TEXT = {'sperre': ('suspended', 'gesperrt', 'blocked', 'eingeschränkt', 'flagged'),
           'anmeldung': ('anmelden', 'sign in', 'log in', 'login'),
           'captcha': ('captcha', 'sicherheits', 'verify you', 'arkose'),
           'ratelimit': ('zu viele', 'too many', 'später', 'try again')}
STERNE_TEXTE = ['Sehr gut', 'Gut', 'In Ordnung', 'Brauchbar', 'Geht so', 'Passt schon',
                'Empfehlenswert', 'Solide Sache', 'Gerne wieder', 'Alles bestens']


def _urteil_text(text):
    klein = (text or '').lower()
    for art, worte in UA_TEXT.items():
        if any(w in klein for w in worte):
            return {'sperre':ART_SPERRE,'anmeldung':ART_ANMELDUNG,'captcha':ART_CAPTCHA,'ratelimit':ART_RATELIMIT}[art]
    return ART_FEHLER


def ausfuehren(sitzung, konto, aktion):
    from ..net.cookies import CookieJar
    cfg = sitzung.cfg
    backend, im = sitzung.browser(), sitzung.im_browser()
    person = im.get(konto['identity'], konto.get('country') or 'de', bool(im.stored(konto['identity'])[1]))
    plattform = aktion['platform']
    ort = str(aktion['ziel_uid']).partition(':')[2]
    sterne = int(aktion.get('sterne') or 5)
    text = aktion.get('text') or random.choice(STERNE_TEXTE)
    with backend.session(person, im.proxy_for(person), headless=cfg.headless, platform=plattform) as handle:
        page = backend.page(handle)
        try:
            if plattform == 'google':
                ergebnis = _google(page, ort, sterne, text)
            elif plattform == 'trustpilot':
                ergebnis = _trustpilot(page, ort, sterne, text)
            else:
                return Ergebnis(False, ART_FEHLER, f'Kein Bewertungsweg für {plattform}.')
        except Exception as fehler:
            log.debug('Bewertung fehlgeschlagen', exc_info=True)
            return Ergebnis(False, ART_UNKLAR, f'Der Bewertungsablauf wurde unterbrochen ({type(fehler).__name__}); Ergebnis prüfen.')
        try:
            cookies = backend.read_cookies(handle)
            if CookieJar.authenticated(cookies, plattform):
                CookieJar.save(konto['identity'], plattform, CookieJar.for_platform(cookies, plattform))
        except Exception:
            pass
    return ergebnis


def _google(page, ort, sterne, text):
    page.goto(ort if ort.startswith('http') else f'https://www.google.com/maps/place/?q=place_id:{ort}',
              wait_until='domcontentloaded')
    page.wait_for_timeout(random.randint(3000, 5000))
    _kekse(page)
    if not _klick(page, ['button[aria-label*="Bewertung schreiben" i]', 'button[aria-label*="Write a review" i]',
                         'button:has-text("Bewertung schreiben")', 'button:has-text("Write a review")']):
        _klick(page, ['button[role="tab"]:has-text("Bewertungen")', 'button[role="tab"]:has-text("Reviews")'])
        page.wait_for_timeout(1500)
        if not _klick(page, ['button[aria-label*="Bewertung schreiben" i]',
                             'button:has-text("Bewertung schreiben")']):
            return Ergebnis(False, ART_ZIEL, 'Ort hat keinen Bewertungsknopf (evtl. place_id falsch).')
    page.wait_for_timeout(2500)
    if not _sterne_setzen(page, sterne):
        return Ergebnis(False, ART_ZIEL, 'Sterne konnten nicht ausgewählt werden.')
    if not _tippen(page, ['textarea[aria-label*="Text" i]', 'textarea[aria-label*="Erfahrung" i]',
                          'div[contenteditable="true"]'], text):
        return Ergebnis(False, ART_ZIEL, 'Textfeld im Bewertungsdialog nicht gefunden.')
    page.wait_for_timeout(600)
    if not _klick(page, ['button:has-text("Posten")', 'button:has-text("Post")',
                  'button[aria-label*="Posten" i]', 'button[aria-label*="Post" i]']):
        return Ergebnis(False, ART_ZIEL, 'Absende-Schaltfläche nicht gefunden.')
    page.wait_for_timeout(random.randint(2500, 5000))
    return _bewerte_seite(page)


def _trustpilot(page, ort, sterne, text):
    page.goto(f'https://www.trustpilot.com/review/{ort}', wait_until='domcontentloaded')
    page.wait_for_timeout(random.randint(3000, 5000))
    _kekse(page)
    if not _klick(page, ['a:has-text("Bewertung abgeben")', 'button:has-text("Bewertung abgeben")',
                         'a:has-text("Write a review")', 'button:has-text("Write a review")']):
        return Ergebnis(False, ART_ZIEL, 'Firma nicht gefunden oder kein Bewertungsknopf.')
    page.wait_for_timeout(3000)
    if not _sterne_setzen(page, sterne):
        return Ergebnis(False, ART_ZIEL, 'Sterne konnten nicht ausgewählt werden.')
    page.wait_for_timeout(800)
    _klick(page, ['button:has-text("Weiter")', 'button:has-text("Next")'])
    page.wait_for_timeout(1500)
    if not _tippen(page, ['textarea[name="review[text]"]', 'textarea[id*="review"]',
                   'textarea[placeholder*="Erfahrung" i]', 'textarea'], text):
        return Ergebnis(False, ART_ZIEL, 'Bewertungstext konnte nicht eingegeben werden.')
    page.wait_for_timeout(500)
    if not _klick(page, ['button:has-text("Bewertung absenden")', 'button:has-text("Submit review")',
                  'button[type="submit"]']):
        return Ergebnis(False, ART_ZIEL, 'Absende-Schaltfläche nicht gefunden.')
    page.wait_for_timeout(random.randint(2500, 5000))
    return _bewerte_seite(page)


def _klick(page, auswahl):
    for sel in auswahl:
        try:
            knopf = page.query_selector(sel)
            if knopf and knopf.is_visible():
                knopf.click()
                return sel
        except Exception:
            continue
    return None


def _tippen(page, auswahl, text):
    for sel in auswahl:
        try:
            feld = page.query_selector(sel)
            if feld and feld.is_visible():
                feld.click()
                feld.fill('')
                page.wait_for_timeout(300)
                page.keyboard.type(text, delay=random.randint(35, 95))
                return sel
        except Exception:
            continue
    return None


def _sterne_setzen(page, sterne):
    if _klick(page, [f'button[aria-label="{sterne} Stern" i]', f'button[aria-label="{sterne} Sterne" i]',
                     f'button[aria-label="{sterne} star" i]', f'button[aria-label="{sterne} stars" i]',
                     f'button[aria-label*="{sterne} von 5"]']):
        return True
    try:
        knoepfe = page.query_selector_all('button[role="radio"], div[role="radio"], button[aria-label*="Stern"]')
        if len(knoepfe) >= sterne:
            knoepfe[sterne - 1].click()
            return True
    except Exception:
        pass
    return False


def _kekse(page):
    for text in ['Alle akzeptieren', 'Accept all', 'Alle ablehnen', 'Accept']:
        try:
            knopf = page.query_selector(f'button:has-text("{text}")')
            if knopf and knopf.is_visible():
                knopf.click()
                page.wait_for_timeout(800)
                return
        except Exception:
            continue


def _bewerte_seite(page):
    try:
        text = page.inner_text('body')[:4000]
    except Exception:
        text = ''
    if re.search(r'(?:deine|ihre) bewertung (?:wurde |ist )?(?:veröffentlicht|gespeichert)|'
                 r'your review (?:has been |was |is )?(?:published|submitted)|'
                 r'(?:danke|thank you) (?:für (?:deine|ihre) bewertung|for your review)', text.lower()):
        return Ergebnis(True, 'ok', 'Bewertung abgegeben')
    art = _urteil_text(text)
    if art != ART_FEHLER:
        if art == ART_ANMELDUNG:
            return Ergebnis(False, ART_UNKLAR, 'Bewertung nicht eindeutig bestätigt; Anmeldung und Ergebnis prüfen.')
        return Ergebnis(False, art, 'Bewertung nicht bestätigt')
    return Ergebnis(False, ART_UNKLAR, 'Bewertung nicht eindeutig bestätigt; bitte auf der Plattform prüfen.')
