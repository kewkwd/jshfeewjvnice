import json, logging
from .. import streuung
log = logging.getLogger(__name__)
NAME, HELP = 'streuung', 'Abstand zwischen Konten prüfen und herstellen'

def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)
    was.add_parser('pruefen', help='Cluster-Befunde zeigen')
    setzen = was.add_parser('fenster', help='Aktivitätsfenster eines Kontos setzen')
    setzen.add_argument('--konto', type=int, required=True)
    setzen.add_argument('--von', type=int); setzen.add_argument('--bis', type=int)
    str_ = was.add_parser('streuen', help='offene Aktionen zeitlich verteilen')
    str_.add_argument('--spanne', type=int, default=240); str_.add_argument('--plattform')
    was.add_parser('name', help='nächsten freien Kontonamen vorschlagen')
    bann = was.add_parser('bann', help='nach einer Sperre die Nachbarn prüfen'); bann.add_argument('konto')

def run(args, cfg):
    if args.was == 'pruefen':
        befunde = streuung.audit(cfg.db_path)
        if not befunde:
            print('Alles sauber – keine Cluster.')
            return 0
        for b in befunde:
            print(f"[{b['schwere']:>7}] {b['art']:<14} {b['wert']:<28} {b['hinweis']}")
        return 1 if any(b['schwere'] == 'hoch' for b in befunde) else 0
    if args.was == 'fenster':
        print(json.dumps(streuung.setze_fenster(cfg.db_path, args.konto, von=args.von, bis=args.bis),
                         ensure_ascii=False))
        return 0
    if args.was == 'streuen':
        print(f'{streuung.streuen(cfg.db_path, spanne_minuten=args.spanne, platform=args.plattform)} '
              f'Aktion(en) verteilt.')
        return 0
    if args.was == 'name':
        print(streuung.naechster_name(cfg.db_path, 'reddit'))
        return 0
    print(json.dumps(streuung.bann_verdacht(cfg.db_path, args.konto), ensure_ascii=False))
    return 0
