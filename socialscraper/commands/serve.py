"""CLI `serve`: lokale, nur lesende Schnittstelle für PULSE im Vordergrund starten (Strg+C beendet)."""
import threading

NAME = 'serve'
HELP = 'Lokale Schnittstelle für PULSE starten (nur lesend, mit Token)'


def add_arguments(parser):
    parser.add_argument('--host', help='Adresse (Standard: aus data/integration.json, sonst 127.0.0.1)')
    parser.add_argument('--port', type=int, help='Port (Standard: aus data/integration.json, sonst 8765)')
    parser.add_argument('--db', help='Pfad der Datenbank (Standard: wie im Programm)')
    parser.add_argument('--show-token', action='store_true', help='Token anzeigen (sonst nur sein Ort)')


def run(args, cfg):
    from .. import api, integration
    root = None
    token = integration.ensure_token(root)
    handle = api.start(root, db_path=args.db or cfg.db_path, host=args.host, port=args.port)
    print(f'Schnittstelle läuft: {handle.url}/v1/health')
    if args.show_token:
        print(f'Token: {token}')
    else:
        print(f'Token steht in {integration.path(root)} (anzeigen mit --show-token).')
    print('Beenden mit Strg+C.')
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        handle.stop()
    print('Schnittstelle beendet.')
    return 0
