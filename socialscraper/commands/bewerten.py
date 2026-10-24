import json, logging
from .. import aktionen, streuung, repo
from ..store import Store
log = logging.getLogger(__name__)
NAME, HELP = 'bewerten', 'Bewertungen auf Google oder Trustpilot einreihen'

def add_arguments(p):
    p.add_argument('--plattform', required=True, choices=['google', 'trustpilot'])
    p.add_argument('--ziel', required=True, help='bei google place_id oder Maps-Adresse, bei trustpilot die Domain')
    p.add_argument('--sterne', type=int, choices=range(1, 6), default=5)
    p.add_argument('--text'); p.add_argument('--anzahl', type=int, default=1)
    p.add_argument('--spanne', type=int, default=2880)
    p.add_argument('--start', action='store_true'); p.add_argument('--probe', action='store_true')

def run(args, cfg):
    raw = args.ziel.strip()
    if args.plattform == 'trustpilot' and raw.startswith(('https://', 'http://')):
        from urllib.parse import urlsplit
        parsed = urlsplit(raw)
        if parsed.hostname and (parsed.hostname == 'trustpilot.com' or parsed.hostname.endswith('.trustpilot.com')):
            parts = parsed.path.strip('/').split('/')
            if len(parts) != 2 or parts[0] != 'review':
                raise ValueError('Bitte eine Firmen-Domain oder einen Trustpilot-Link zu /review/ verwenden.')
            raw = parts[1]
        else:
            raw = parsed.hostname or ''
    ziel = raw if raw.startswith(args.plattform + ':') else f'{args.plattform}:{raw}'
    with Store(cfg.db_path) as store:
        db = store.db
        neu = 0
        accounts = repo.list_accounts(db, args.plattform)
        if args.anzahl < 1 or args.anzahl > len(accounts):
            raise ValueError('Bitte höchstens eine Bewertung je vorhandenem Konto einreihen.')
        for i in range(args.anzahl):
            text = args.text
            if aktionen.enqueue(db, args.plattform, 'bewertung', ziel, account_id=accounts[i]['id'], text=text, sterne=args.sterne):
                neu += 1
    print(f'{neu} Bewertung(en) eingereiht.')
    streuung.streuen(cfg.db_path, spanne_minuten=args.spanne, platform=args.plattform)
    if args.start:
        ergebnis = aktionen.run(cfg.db_path, art='bewertung', platform=args.plattform,
                                dry=args.probe, cfg=cfg)
        print('fertig: ' + json.dumps(ergebnis, ensure_ascii=False))
        return 0 if ergebnis['fehler'] == 0 else 1
    return 0
