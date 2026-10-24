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
    influencer = persona['influencer']
    influencer_key = influencer.casefold().lstrip('@')
    weitere = list({kanal.casefold().lstrip('@'): kanal for kanal in persona['kanaele']
                    if kanal.casefold().lstrip('@') != influencer_key}.values())
    kanaele = [influencer] + rng.sample(weitere, min(len(weitere), 4))
    im, backend, sessions = cli.make_runtime(cfg, True)
    try:
        country, mobile = im.stored(konto['identity'])
        ident = im.get(konto['identity'], country or konto.get('country') or 'de', bool(mobile))
        schritte = 0
        abgebrochen = False
        with backend.session(ident, im.proxy_for(ident), headless=cfg.headless, platform='tiktok') as handle:
            page = backend.page(handle)
            for kanal in kanaele:
                if stop is not None and stop():
                    abgebrochen = True
                    break
                page.goto(SUCHE.format(kanal.lstrip('@')), wait_until='domcontentloaded')
                page.wait_for_timeout(rng.randint(2500, 4500))
                links = [l for l in page.query_selector_all(VIDEO_LINK) if l.is_visible()][:kanaele_je]
                for link in links:
                    if stop is not None and stop():
                        abgebrochen = True
                        break
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
                if abgebrochen:
                    break
                if rng.random() < 0.5:
                    _klick(page, FOLGEN)
                page.wait_for_timeout(rng.randint(800, 2000))
            _cookies(backend, handle, konto['identity'])
        if schritte and not abgebrochen and (stop is None or not stop()):
            with Store(cfg.db_path) as store:
                ensure_schema(store.db)
                store.db.execute("UPDATE accounts SET fyp_status='gesaet' WHERE identity=?", (konto['identity'],))
                store.db.commit()
        melden(f'Seeding {konto["label"]}: {schritte} Videos über {len(kanaele)} Kanäle.')
        return schritte
    finally:
        cli.shutdown_runtime(backend, sessions)
