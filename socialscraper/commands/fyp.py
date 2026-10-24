from .. import fyp, repo
from ..fyp import konsum, seed, monitor
from ..store import Store
NAME, HELP = 'fyp', 'For-You-Konsum, Seeding und Video-Monitoring'


def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)
    s = was.add_parser('seed'); s.add_argument('--konto', type=int)
    k = was.add_parser('konsum'); k.add_argument('--konto', type=int); k.add_argument('--sekunden', type=int, default=360)
    m = was.add_parser('monitor'); m.add_argument('--kanaele', required=True, help='mit Komma')


def run(args, cfg):
    if args.was == 'monitor':
        print(f"{len(monitor.neue_videos(cfg, [k.strip() for k in args.kanaele.split(',') if k.strip()]))} neue Videos.")
        return 0
    with Store(cfg.db_path) as store:
        fyp.ensure_schema(store.db)
        konten = [repo.get_account(store.db, args.konto)] if args.konto else repo.list_accounts(store.db, 'tiktok')
    for konto in konten:
        if args.was == 'seed':
            seed.sitzung(cfg, konto)
        elif args.was == 'konsum':
            konsum.sitzung(cfg, konto, ziel_sekunden=args.sekunden)
    return 0