import logging, random, time
from ..net.cookies import CookieJar
from ..net.errors import FinalError
log = logging.getLogger(__name__)

PLATTFORMEN = {
    'tiktok': {'home': 'https://www.tiktok.com/foryou',
               'video_link': ['a[href*="/video/"]', 'div[data-e2e="recommend-list-item-container"] a'],
               'like': ['[data-e2e="like-icon"]', 'button[data-e2e="like-icon"]'],
               'folgen': ['button[data-e2e="follow-button"]', 'button:has-text("Folgen")'],
               'kommentar_feld': ['div[data-e2e="comment-input"]', 'div[contenteditable="true"]'],
               'kommentar_texte': ['Sehr schön!', 'Stark', 'Interessant', 'Guter Punkt']},
    'instagram': {'home': 'https://www.instagram.com/',
                  'video_link': ['article a[href*="/p/"]', 'article a[href*="/reel/"]'],
                  'like': ['svg[aria-label="Gefällt mir"]', 'button[aria-label*="Gefällt mir"]'],
                  'folgen': ['button:has-text("Folgen")', 'button:has-text("Follow")'],
                  'kommentar_feld': ['textarea[aria-label*="Kommentar" i]'],
                  'kommentar_texte': ['Toll!', 'Sehr schön', 'Gefällt mir', 'Interessant']},
    'youtube': {'home': 'https://www.youtube.com/',
                'video_link': ['a#video-title-link', 'ytd-rich-item-renderer a#thumbnail'],
                'like': ['button[aria-label*="Gefällt mir" i]'],
                'folgen': ['button[aria-label*="abonnieren" i]', '#subscribe-button button'],
                'kommentar_feld': ['ytd-commentbox #contenteditable-root', '#simple-box #contenteditable-root'],
                'kommentar_texte': ['Sehr informativ!', 'Danke für das Video', 'Guter Beitrag']},
    'reddit': {'home': 'https://www.reddit.com/',
               'video_link': ['a[data-click-id="body"]', 'shreddit-post a[slot="full-post-link"]'],
               'like': ['button[aria-label*="upvote" i]'],
               'folgen': ['button:has-text("Beitreten")', 'button:has-text("Join")'],
               'kommentar_feld': ['div[contenteditable="true"]', 'textarea'],
               'kommentar_texte': ['Guter Punkt', 'Sehe ich genauso', 'Interessant']},
    'x': {'home': 'https://x.com/home',
          'video_link': ['article a[href*="/status/"]'],
          'like': ['button[data-testid="like"]'],
          'folgen': ['div[data-testid$="-follow"]', 'button:has-text("Folgen")'],
          'kommentar_feld': ['div[data-testid="tweetTextarea_0"]', 'div[role="textbox"]'],
          'kommentar_texte': ['Guter Punkt', 'Sehe ich ähnlich', 'Interessant']},
}

def laufen(cfg, konto, *, plan, erlaubt, melden=log.info, stop=None):
    from ..net import proxymode
    from ..net.accountproxy import Resolver
    from ..net.factory import build as build_backend
    from ..net.identity import IdentityManager
    from ..net.proxy import ProxyPool
    regeln = PLATTFORMEN.get(konto['platform'])
    if regeln is None:
        raise FinalError(f'Für {konto["platform"]} ist das Aufwärmen noch nicht hinterlegt.')
    pool = proxymode.make_pool(cfg) if hasattr(proxymode, 'make_pool') else ProxyPool([])
    im = IdentityManager(pool, cfg.profiles_dir)
    if not proxymode.is_anonymous(cfg):
        im.account_proxies = Resolver(cfg.db_path)              # FIX A4
    ident = im.get(konto['identity'], konto.get('country') or 'de', bool(im.stored(konto['identity'])[1]))
    backend = build_backend(cfg, im)
    ende, erledigt = time.monotonic() + random.uniform(*plan['minuten']) * 60, 0
    try:
        with backend.session(ident, im.proxy_for(ident), headless=cfg.headless, platform=konto['platform']) as handle:
            page = backend.page(handle)
            page.goto(regeln['home'], wait_until='domcontentloaded')
            page.wait_for_timeout(random.randint(2500, 5000))
            for _ in range(plan['schritte']):
                if stop is not None and stop():
                    break
                if time.monotonic() > ende:
                    break
                try:
                    handlung = _waehle(erlaubt)
                    performed = {'scroll': lambda: _scroll(page), 'video': lambda: _video(page, regeln),
                     'like': lambda: _klick(page, regeln['like']),
                     'folgen': lambda: _klick(page, regeln['folgen']),
                     'kommentar': lambda: _kommentar(page, regeln)}[handlung]()
                    if performed is not False:
                        erledigt += 1
                except Exception:
                    log.debug('Aufwärm-Handlung fehlgeschlagen', exc_info=True)
                page.wait_for_timeout(random.randint(800, 2600))
            _cookies_erneuern(backend, handle, konto)
        melden(f'Aufwärm-Sitzung {konto["label"]}: {erledigt} Handlungen.')
        return erledigt
    finally:
        from ..cli import shutdown_runtime
        shutdown_runtime(backend, None)


def _waehle(erlaubt):
    gewicht = {'scroll': 5, 'video': 3, 'like': 3, 'kommentar': 1, 'folgen': 1}
    return random.choice([h for h in erlaubt for _ in range(gewicht.get(h, 1))])

def _scroll(page):
    for _ in range(random.randint(2, 5)):
        page.mouse.wheel(0, random.randint(400, 1200))
        page.wait_for_timeout(random.randint(600, 1800))

def _video(page, regeln):
    link = _erstes(page, regeln['video_link'])
    if not link:
        return False
    page.click(link)
    page.wait_for_timeout(random.randint(3000, 7000))
    if random.random() < 0.5:
        page.keyboard.press('Escape')
        page.wait_for_timeout(1000)
    return True

def _klick(page, auswahl):
    ziel = _erstes(page, auswahl)
    if not ziel:
        return False
    page.click(ziel)
    return True

def _kommentar(page, regeln):
    feld = _erstes(page, regeln['kommentar_feld'])
    if not feld:
        return False
    page.click(feld)
    page.wait_for_timeout(500)
    page.keyboard.type(random.choice(regeln['kommentar_texte']), delay=random.randint(40, 110))
    page.wait_for_timeout(400)
    page.keyboard.press('Enter')
    return True

def _erstes(page, auswahl):
    from ..dom import first_selector
    return first_selector(page, auswahl, visible=False)

def _cookies_erneuern(backend, handle, konto):
    try:
        cookies = backend.read_cookies(handle)
        if CookieJar.authenticated(cookies, konto['platform']):
            CookieJar.save(konto['identity'], konto['platform'],
                           CookieJar.for_platform(cookies, konto['platform']))
    except Exception:
        log.debug('Cookies konnten nicht erneuert werden', exc_info=True)
