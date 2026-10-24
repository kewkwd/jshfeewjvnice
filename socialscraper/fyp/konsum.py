"""Eine FYP-Sitzung: scrollen, schauen, und bei Persona-Kanälen handeln."""
import logging, random, re, time

from . import ensure_schema, verbuche

log = logging.getLogger(__name__)
FORYOU = 'https://www.tiktok.com/foryou'
VIDEO_LINK = 'a[href*="/video/"]'
LIKE = ('[data-e2e="like-icon"]', '[data-e2e="browse-like-icon"]', 'button[aria-label*="Gefällt" i]')
SAVE = ('[data-e2e="save-icon"]', '[data-e2e="undefined-icon"]', 'button[aria-label*="Speichern" i]')
FOLGEN = ('button[data-e2e="follow-button"]', 'button:has-text("Folgen")')


def _autor(href):
    treffer = re.search(r'tiktok\.com/@([^/]+)/video/', href or '')
    return treffer.group(1).casefold() if treffer else None


def _klick(page, auswahl):
    for sel in auswahl:
        try:
            knopf = page.query_selector(sel)
            if knopf and knopf.is_visible():
                knopf.click()
                return True
        except Exception:
            continue
    return False


def sitzung(cfg, konto, *, ziel_sekunden=None, rng=None, melden=log.info, stop=None):
    from .. import cli, personas
    from ..net import humanize
    from ..net.cookies import CookieJar
    rng = rng or random.Random()
    persona = personas.persona_von(cfg.db_path, konto['identity'])
    if persona is None:
        return 0, 0
    watchlist = {k.casefold().lstrip('@') for k in persona['kanaele']}
    verhalten = persona['verhalten']
    ziel_sekunden = ziel_sekunden or rng.uniform(*verhalten['sitzung_sekunden'])
    im, backend, sessions = cli.make_runtime(cfg, True)
    country, mobile = im.stored(konto['identity'])
    ident = im.get(konto['identity'], country or konto.get('country') or 'de', bool(mobile))
    schritte = treffer = 0
    ende = time.monotonic() + ziel_sekunden
    try:
        with backend.session(ident, im.proxy_for(ident), headless=cfg.headless, platform='tiktok') as handle:
            page = backend.page(handle)
            page.goto(FORYOU, wait_until='domcontentloaded')
            page.wait_for_timeout(rng.randint(2500, 5000))
            while time.monotonic() < ende and (stop is None or not stop()):
                if rng.random() < 0.45:
                    humanize.scroll(page, rng.randint(400, 1200), rng=rng, stop=stop)
                    schritte += 1
                    continue
                link = _naechstes_video(page, rng)
                if link is None:
                    humanize.scroll(page, 800, rng=rng, stop=stop)
                    continue
                autor = _autor(link.get_attribute('href'))
                try:
                    link.click()
                except Exception:
                    continue
                page.wait_for_timeout(rng.randint(1500, 3500))
                if autor and autor in watchlist:
                    treffer += 1
                    _konsumiere(page, verhalten, rng)
                    _treffer(cfg.db_path, konto, persona, autor)
                else:
                    page.wait_for_timeout(int(verhalten['kurz_sekunden'] * 1000))
                if rng.random() < 0.5:
                    page.keyboard.press('Escape')
                page.wait_for_timeout(rng.randint(600, 1800))
                schritte += 1
            _cookies(backend, handle, konto['identity'])
        verbuche(cfg.db_path, konto['identity'], schritte=schritte, treffer=treffer)
        melden(f'FYP-Sitzung {konto["label"]}: {schritte} Schritte, {treffer} Treffer.')
        return schritte, treffer
    finally:
        cli.shutdown_runtime(backend, sessions)


def _naechstes_video(page, rng):
    try:
        links = [l for l in page.query_selector_all(VIDEO_LINK) if l.is_visible()]
    except Exception:
        return None
    return rng.choice(links[:6]) if links else None


def _konsumiere(page, verhalten, rng):
    page.wait_for_timeout(int(rng.uniform(*verhalten['schauen_sekunden']) * 1000))
    if rng.random() < verhalten['like_p']:
        _klick(page, LIKE)
        page.wait_for_timeout(rng.randint(400, 1200))
    if rng.random() < verhalten['save_p']:
        _klick(page, SAVE)
    if rng.random() < verhalten['follow_p']:
        _klick(page, FOLGEN)


def _treffer(db_path, konto, persona, autor):
    from ..store import Store
    from .. import repo
    organisch = 1 if konto.get('fyp_status') != 'gesaet' else 0
    with Store(db_path) as store:
        ensure_schema(store.db)
        store.db.execute('INSERT OR IGNORE INTO fyp_treffer (identity, kanal, video_uid, persona_id, art, '
                         'organisch, erstellt_am) VALUES (?,?,?,?,?,?,?)',
                         (konto['identity'], autor, None, persona['id'], 'konsum', organisch, repo.iso(repo.now())))
        if organisch:
            n = store.db.execute('SELECT COUNT(*) FROM fyp_treffer WHERE identity=? AND organisch=1',
                                 (konto['identity'],)).fetchone()[0]
            if n >= 3 and konto.get('fyp_status') != 'im_fyp':
                store.db.execute("UPDATE accounts SET fyp_status='im_fyp' WHERE identity=?", (konto['identity'],))
        store.db.commit()


def _cookies(backend, handle, identity, platform='tiktok'):
    from ..net.cookies import CookieJar
    try:
        cookies = backend.read_cookies(handle)
        if CookieJar.authenticated(cookies, platform):
            CookieJar.save(identity, platform, cookies)
    except Exception:
        log.debug('Cookies nicht erneuert', exc_info=True)