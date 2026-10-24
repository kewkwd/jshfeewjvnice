"""Prüft, ob TikTok-Web im Browser Watch-Time registriert (kritische Annahme für FYP-Konsum).

Aufruf:  python -m checks.watchtime_check [--url URL] [--konto ID] [--sekunden 6]
Verdikt je Modus: BESTANDEN / TEILWEISE / FEHLGESCHLAGEN.
"""
import argparse, sys

from socialscraper import cli, repo
from socialscraper.config import load
from socialscraper.store import Store

TELEMETRIE = ('mon.tiktokv.com', 'mssdk.bytedance.com', 'log.tiktokv.com', 'slardar',
              'monitor_browser', '/api/item/stats', '/web/report')


def _lauf(cfg, konto, url, sekunden, headless):
    im, backend, sessions = cli.make_runtime(cfg, True)
    identity = konto['identity'] if konto else 'watchtime_check'
    country = (konto or {}).get('country') or 'de'
    ident = im.get(identity, country, False)
    treffer, fortschritt = [], -1.0
    try:
        with backend.session(ident, im.proxy_for(ident), headless=headless, platform='tiktok') as handle:
            page = backend.page(handle)
            page.on('request', lambda r: treffer.append(r.url) if any(m in r.url for m in TELEMETRIE) else None)
            page.goto(url, wait_until='domcontentloaded')
            page.wait_for_timeout(3000)
            try:
                page.evaluate("() => { const v=document.querySelector('video'); if(v){v.muted=true;"
                              "v.play && v.play().catch(()=>{});} }")
            except Exception:
                pass
            t0 = page.evaluate("() => { const v=document.querySelector('video'); return v ? v.currentTime : -1; }")
            page.wait_for_timeout(int(sekunden * 1000))
            t1 = page.evaluate("() => { const v=document.querySelector('video'); return v ? v.currentTime : -1; }")
            if t0 is not None and t1 is not None and t0 >= 0 and t1 >= 0:
                fortschritt = t1 - t0
    finally:
        cli.shutdown_runtime(backend, sessions)
    return fortschritt, treffer


def _urteil(fortschritt, treffer):
    if fortschritt > 1.0 and treffer:
        return 'BESTANDEN', f'Video läuft ({fortschritt:.1f}s) und {len(treffer)} Telemetrie-Requests.'
    if fortschritt > 1.0:
        return 'TEILWEISE', f'Video läuft ({fortschritt:.1f}s), aber keine Telemetrie erkannt.'
    return 'FEHLGESCHLAGEN', f'Video läuft nicht (currentTime-Δ {fortschritt:.2f}s).'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='https://www.tiktok.com/foryou')
    p.add_argument('--konto', type=int)
    p.add_argument('--sekunden', type=int, default=6)
    args = p.parse_args()
    cfg = load()
    konto = None
    if args.konto:
        with Store(cfg.db_path) as store:
            konto = repo.get_account(store.db, args.konto)
    verdikte = {}
    for headless in (True, False):
        modus = 'headless' if headless else 'headful'
        print(f'\n--- {modus} ---')
        try:
            fortschritt, treffer = _lauf(cfg, konto, args.url, args.sekunden, headless)
        except Exception as fehler:
            print(f'Fehler: {fehler!r}'); verdikte[modus] = 'FEHLER'; continue
        urteil, begruendung = _urteil(fortschritt, treffer)
        print(f'{urteil}: {begruendung}'); verdikte[modus] = urteil
    print('\n=== Verdikt ===')
    if verdikte.get('headless') == 'BESTANDEN':
        print('Headless trägt Watch-Time -> FYP-Konsum headless.'); return 0
    if verdikte.get('headful') == 'BESTANDEN':
        print('Nur headful trägt Watch-Time -> FYP-Sitzungen headful (headless=false).'); return 0
    print('Kein Modus registriert Watch-Time -> Player-Flags prüfen.'); return 1


if __name__ == '__main__':
    sys.exit(main())