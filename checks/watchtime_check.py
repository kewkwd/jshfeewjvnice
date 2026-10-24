"""Prüft Video-Fortschritt und begleitende Telemetrie im TikTok-Webbrowser.

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
    treffer, fortschritt = [], -1.0
    try:
        identity = konto['identity'] if konto else 'watchtime_check'
        country, mobile = im.stored(identity)
        ident = im.get(identity, country or (konto or {}).get('country') or 'de', bool(mobile))
        with backend.session(ident, im.proxy_for(ident), headless=headless, platform='tiktok') as handle:
            page = backend.page(handle)
            wiedergabe = False
            def request(r):
                if wiedergabe and any(m in r.url for m in TELEMETRIE):
                    treffer.append(r.url)
            page.on('request', request)
            page.goto(url, wait_until='domcontentloaded')
            page.wait_for_timeout(3000)
            try:
                page.evaluate("() => { const v=document.querySelector('video'); if(v){v.muted=true;"
                              "v.play && v.play().catch(()=>{});} }")
            except Exception:
                pass
            t0 = page.evaluate("() => { const v=document.querySelector('video'); return v ? v.currentTime : -1; }")
            wiedergabe = True
            page.wait_for_timeout(int(sekunden * 1000))
            wiedergabe = False
            t1 = page.evaluate("() => { const v=document.querySelector('video'); return v ? v.currentTime : -1; }")
            if t0 is not None and t1 is not None and t0 >= 0 and t1 >= 0:
                fortschritt = t1 - t0
    finally:
        cli.shutdown_runtime(backend, sessions)
    return fortschritt, treffer


def _urteil(fortschritt, treffer):
    if fortschritt > 1.0 and treffer:
        return 'BESTANDEN', (f'Videozeit steigt ({fortschritt:.1f}s) und {len(treffer)} Telemetrie-Requests '
                            'während der Wiedergabe; serverseitige Watch-Time bleibt ungeprüft.')
    if fortschritt > 1.0:
        return 'TEILWEISE', f'Videozeit steigt ({fortschritt:.1f}s), aber keine Wiedergabe-Telemetrie erkannt.'
    return 'FEHLGESCHLAGEN', f'Kein ausreichender Video-Fortschritt (currentTime-Δ {fortschritt:.2f}s).'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='https://www.tiktok.com/foryou')
    p.add_argument('--konto', type=int)
    p.add_argument('--sekunden', type=int, default=6)
    args = p.parse_args()
    if args.sekunden < 1:
        p.error('--sekunden muss positiv sein')
    if args.konto is not None and args.konto < 1:
        p.error('--konto muss positiv sein')
    cfg = load()
    konto = None
    if args.konto is not None:
        with Store(cfg.db_path) as store:
            konto = repo.get_account(store.db, args.konto)
        if konto is None:
            print(f'Konto {args.konto} nicht gefunden.', file=sys.stderr)
            return 1
        if konto['platform'] != 'tiktok':
            print(f'Konto {args.konto} gehört nicht zu TikTok.', file=sys.stderr)
            return 1
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
        print('Headless zeigt Video-Fortschritt und begleitende Telemetrie; serverseitige Watch-Time ungeprüft.'); return 0
    if verdikte.get('headful') == 'BESTANDEN':
        print('Nur headful zeigt Video-Fortschritt und begleitende Telemetrie; serverseitige Watch-Time ungeprüft.'); return 0
    print('Kein Modus zeigt Video-Fortschritt mit begleitender Telemetrie; Player-Flags prüfen.'); return 1


if __name__ == '__main__':
    sys.exit(main())
