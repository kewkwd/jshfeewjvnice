import argparse

from .. import fyp, repo
from ..fyp import konsum, seed, monitor
from ..store import Store
NAME, HELP = 'fyp', 'For-You-Konsum, Seeding und Video-Monitoring'


def _positive(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('Wert muss eine positive ganze Zahl sein') from None
    if number < 1:
        raise argparse.ArgumentTypeError('Wert muss eine positive ganze Zahl sein')
    return number


def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)
    s = was.add_parser('seed'); s.add_argument('--konto', type=_positive, required=True)
    k = was.add_parser('konsum'); k.add_argument('--konto', type=_positive); k.add_argument('--sekunden', type=_positive, default=360)
    m = was.add_parser('monitor'); m.add_argument('--kanaele', required=True, help='mit Komma')


def run(args, cfg):
    if args.was == 'monitor':
        print(f"{len(monitor.neue_videos(cfg, [k.strip() for k in args.kanaele.split(',') if k.strip()]))} neue Videos.")
        return 0
    with Store(cfg.db_path) as store:
        fyp.ensure_schema(store.db)
        if args.was == 'seed' and args.konto is None:
            raise ValueError('Für Seeding muss ein TikTok-Konto mit --konto gewählt werden.')
        if args.konto is not None:
            konto = repo.get_account(store.db, args.konto)
            if konto is None:
                raise ValueError(f'Konto {args.konto} nicht gefunden.')
            if konto['platform'] != 'tiktok':
                raise ValueError(f'Konto {args.konto} gehört nicht zu TikTok.')
            konten = [konto]
        else:
            konten = repo.list_accounts(store.db, 'tiktok')
    for konto in konten:
        if args.was == 'seed':
            seed.sitzung(cfg, konto)
        elif args.was == 'konsum':
            konsum.sitzung(cfg, konto, ziel_sekunden=args.sekunden)
    return 0
