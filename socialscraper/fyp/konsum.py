"""Eine FYP-Sitzung: scrollen, schauen, und bei Persona-Kanälen handeln."""
import logging, random, re, time
from urllib.parse import urlsplit

from . import ensure_schema, _verbuche_in_db

log = logging.getLogger(__name__)
FORYOU = 'https://www.tiktok.com/foryou'
VIDEO_LINK = 'a[href*="/video/"]'
LIKE = ('[data-e2e="like-icon"]', '[data-e2e="browse-like-icon"]', 'button[aria-label*="Gefällt" i]')
SAVE = ('[data-e2e="save-icon"]', '[data-e2e="undefined-icon"]', 'button[aria-label*="Speichern" i]')
FOLGEN = ('button[data-e2e="follow-button"]', 'button:has-text("Folgen")')


def _video_meta(href):
    """Autor und Video-ID aus absoluten oder relativen TikTok-Links lesen."""
    try:
        parsed = urlsplit(href or '')
        host = parsed.hostname
    except ValueError:
        return None, None
    if host and host != 'tiktok.com' and not host.endswith('.tiktok.com'):
        return None, None
    treffer = re.fullmatch(r'/@([^/]+)/video/(\d+)/?', parsed.path)
    return (treffer.group(1).casefold(), treffer.group(2)) if treffer else (None, None)


def _autor(href):
    return _video_meta(href)[0]


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
    rng = rng or random.Random()
    persona = personas.persona_von(cfg.db_path, konto['identity'])
    if persona is None:
        return 0, 0
    watchlist = {k.casefold().lstrip('@') for k in persona['kanaele']}
    verhalten = persona['verhalten']
    ziel_sekunden = ziel_sekunden or rng.uniform(*verhalten['sitzung_sekunden'])
    im, backend, sessions = cli.make_runtime(cfg, True)
    try:
        country, mobile = im.stored(konto['identity'])
        ident = im.get(konto['identity'], country or konto.get('country') or 'de', bool(mobile))
        schritte = treffer = 0
        video_treffer = []
        ende = time.monotonic() + ziel_sekunden
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
                autor, video_uid = _video_meta(link.get_attribute('href'))
                try:
                    link.click()
                except Exception:
                    continue
                page.wait_for_timeout(rng.randint(1500, 3500))
                if autor and video_uid and autor in watchlist:
                    treffer += 1
                    _konsumiere(page, verhalten, rng)
                    video_treffer.append((autor, video_uid))
                else:
                    page.wait_for_timeout(int(verhalten['kurz_sekunden'] * 1000))
                if rng.random() < 0.5:
                    page.keyboard.press('Escape')
                page.wait_for_timeout(rng.randint(600, 1800))
                schritte += 1
            _cookies(backend, handle, konto['identity'])
        if schritte:
            _verbuche_sitzung(cfg.db_path, konto, persona, video_treffer, schritte, treffer)
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


def _verbuche_sitzung(db_path, konto, persona, video_treffer, schritte, treffer):
    from ..store import Store, _atomic_schema
    from .. import repo
    with Store(db_path) as store:
        ensure_schema(store.db)
        with _atomic_schema(store.db, 'fyp_konsum'):
            row = store.db.execute('SELECT fyp_status FROM accounts WHERE identity=?',
                                   (konto['identity'],)).fetchone()
            status = row[0] if row else konto.get('fyp_status')
            organisch = 0 if status == 'gesaet' else 1
            jetzt = repo.iso(repo.now())
            for autor, video_uid in video_treffer:
                store.db.execute('INSERT OR IGNORE INTO fyp_treffer (identity, kanal, video_uid, persona_id, art, '
                                 'organisch, erstellt_am) VALUES (?,?,?,?,?,?,?)',
                                 (konto['identity'], autor, video_uid, persona['id'], 'konsum', organisch, jetzt))
            if organisch and status != 'im_fyp':
                n = store.db.execute('SELECT COUNT(*) FROM fyp_treffer WHERE identity=? AND organisch=1 '
                                     'AND video_uid IS NOT NULL',
                                     (konto['identity'],)).fetchone()[0]
                if n >= 3:
                    store.db.execute("UPDATE accounts SET fyp_status='im_fyp' WHERE identity=?", (konto['identity'],))
            _verbuche_in_db(store.db, konto['identity'], schritte=schritte, treffer=treffer)


def _cookies(backend, handle, identity, platform='tiktok'):
    from ..net.cookies import CookieJar
    try:
        cookies = backend.read_cookies(handle)
        if CookieJar.authenticated(cookies, platform):
            CookieJar.save(identity, platform, cookies)
    except Exception:
        log.debug('Cookies nicht erneuert', exc_info=True)
