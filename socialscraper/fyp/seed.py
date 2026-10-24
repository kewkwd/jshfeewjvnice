"""FYP-Korridor legen: Persona-Kanäle suchen, wenige Videos schauen, 1-2 liken, teils folgen."""
import logging, random

from .. import personas
from . import ensure_schema
from .konsum import FOLGEN, LIKE, VIDEO_LINK, _cookies, _klick

log = logging.getLogger(__name__)
SUCHE = 'https://www.tiktok.com/search?q={}'


def sitzung(cfg, konto, *, kanaele_je=3, rng=None, melden=log.info, stop=None):
    from .. import cli
    from ..store import Store
    rng = rng or random.Random()
    persona = personas.persona_von(cfg.db_path, konto['identity'])
    if persona is None:
        return 0
    kanaele = [persona['influencer']] + rng.sample(persona['kanaele'], min(len(persona['kanaele']), 4))
    im, backend, sessions = cli.make_runtime(cfg, True)
    country, mobile = im.stored(konto['identity'])
    ident = im.get(konto['identity'], country or konto.get('country') or 'de', bool(mobile))
    schritte = 0
    try:
        with backend.session(ident, im.proxy_for(ident), headless=cfg.headless, platform='tiktok') as handle:
            page = backend.page(handle)
            for kanal in kanaele:
                if stop is not None and stop():
                    break
                page.goto(SUCHE.format(kanal.lstrip('@')), wait_until='domcontentloaded')
                page.wait_for_timeout(rng.randint(2500, 4500))
                links = [l for l in page.query_selector_all(VIDEO_LINK) if l.is_visible()][:kanaele_je]
                for link in links:
                    try:
                        link.click()
                    except Exception:
                        continue
                    page.wait_for_timeout(rng.randint(3000, 8000))
                    if rng.random() < 0.4:
                        _klick(page, LIKE)
                    schritte += 1
                    if rng.random() < 0.5:
                        page.keyboard.press('Escape')
                    page.wait_for_timeout(rng.randint(800, 2000))
                if rng.random() < 0.5:
                    _klick(page, FOLGEN)
                page.wait_for_timeout(rng.randint(800, 2000))
            _cookies(backend, handle, konto['identity'])
        with Store(cfg.db_path) as store:
            ensure_schema(store.db)
            store.db.execute("UPDATE accounts SET fyp_status='gesaet' WHERE identity=?", (konto['identity'],))
            store.db.commit()
        melden(f'Seeding {konto["label"]}: {schritte} Videos über {len(kanaele)} Kanäle.')
        return schritte
    finally:
        cli.shutdown_runtime(backend, sessions)