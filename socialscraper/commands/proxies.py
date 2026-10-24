"""Befehl `proxies`: kostenlose Proxys aktualisieren/prüfen und Tor erkennen.

    proxies refresh   Liste kostenloser Proxys laden, parallel testen, die schnellsten merken
    proxies status    zeigt Proxy-Modus, Stand der Liste und Tor
    proxies tor       Tor erkennen und testen

Kostenlose Proxys und Tor werden nie zusammen mit einem Konto benutzt (siehe net/proxymode.py).
"""
import pathlib

NAME = 'proxies'
HELP = 'kostenlose Proxys aktualisieren und prüfen, Tor erkennen (nur für Abrufe ohne Konto)'


def add_arguments(p):
    p.add_argument('aktion', choices=['refresh', 'status', 'tor', 'tor-start', 'tor-stop'],
                   help='refresh = Liste aktualisieren und testen, status = Überblick, tor = Tor erkennen und testen, tor-start = mitgeliefertes Tor starten (falls keins läuft), tor-stop = dieses Tor beenden')
    p.add_argument('--limit', type=int, default=10, help='so viele schnelle Proxys behalten (Standard 10)')
    p.add_argument('--kandidaten', type=int, default=300, help='so viele Adressen höchstens testen (Standard 300)')


def run(args, cfg):
    from ..net import freeproxy, proxymode, tor
    root = proxymode.root_for(cfg)
    if args.aktion == 'tor':
        found = tor.find(verify=True)
        print(found['message'])
        if found['port'] is None:
            print('Mit „proxies tor-start“ lässt sich das mitgelieferte Tor starten.')
        return 0 if found['ok'] else 2
    if args.aktion == 'tor-start':
        print('Starte Tor (kann bis zu 90 Sekunden dauern) ...')
        found = tor.ensure_running(root)
        print(found['message'])
        return 0 if found['ok'] else 2
    if args.aktion == 'tor-stop':
        ok, message = tor.stop(root)
        print(message)
        return 0 if ok else 2
    if args.aktion == 'status':
        modus, text, ok = proxymode.describe(cfg, root)
        print(f'Proxy-Modus: {modus} ({proxymode.LABELS[modus]})')
        print(text)
        info = freeproxy.status(root)
        if info['alter_s'] is None:
            print('Kostenlose Proxys: noch nie aktualisiert (Befehl „proxies refresh“).')
        else:
            print(f'Kostenlose Proxys: {info["vorhanden"]} bereit, Liste {info["alter_s"] // 60} Min. alt'
                  f'{"" if info["gueltig"] else " (abgelaufen, wird beim nächsten Abruf erneuert)"}, '
                  f'{info["gesperrt"]} gesperrt.')
            for item in info['proxys']:
                print(f'  {item["proxy"]}  {item["ms"]} ms  (ok {item["ok"]}, Fehler {item["fail"]})')
        port = tor.detect(timeout=0.5)
        print('Tor: ' + (f'Anschluss {port} gefunden (mit „proxies tor“ prüfen).' if port else 'läuft nicht.'))
        return 0
    if args.limit < 1 or args.kandidaten < 1:
        raise ValueError('--limit und --kandidaten müssen mindestens 1 sein')
    print('Lade öffentliche Listen und teste Proxys (das dauert etwa eine halbe Minute) ...')
    stats = freeproxy.refresh(root, limit=args.limit, max_candidates=args.kandidaten)
    print(f'Listen geladen: {stats["quellen_ok"]}  (nicht erreichbar: {len(stats["quellen_fehler"])})')
    print(f'Gefunden/getestet: {stats["kandidaten"]} / {stats["getestet"]}')
    print(f'Funktionierend: {stats["funktionierend"]}')
    print(f'Zertifikatsfehler (verworfen): {stats["zertifikatsfehler"]}')
    if stats['verdaechtige_quellen']:
        print('Auffällige Listen: ' + ', '.join(stats['verdaechtige_quellen']))
    if stats['schnellste']:
        print('Schnellste: ' + ', '.join(f'{item["proxy"]} ({item["ms"]} ms)' for item in stats['schnellste'][:5]))
        print(f'Gespeichert in {pathlib.Path(freeproxy.cache_path(root))} (20 Minuten gültig).')
        return 0
    print('Keine funktionierenden Proxys gefunden. Später noch einmal versuchen. '
          'Ohne Proxy wird im Modus „frei“ nicht abgerufen.')
    return 2
