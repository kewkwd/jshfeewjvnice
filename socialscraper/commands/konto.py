import logging
from .. import signup, warmup
log = logging.getLogger(__name__)
NAME, HELP = 'konto', 'Konto auf einer Plattform anlegen'

def add_arguments(p):
    p.add_argument('platform', choices=['reddit', 'tiktok', 'x', 'instagram', 'youtube', 'google', 'trustpilot'])
    p.add_argument('--name'); p.add_argument('--mail'); p.add_argument('--passwort')
    p.add_argument('--modus', choices=['auto', 'assisted']); p.add_argument('--land', default='de')
    p.add_argument('--handy', action='store_true'); p.add_argument('--zeitlimit', type=int, default=600)
    p.add_argument('--aufwaermen', choices=list(warmup.MODI), default='normal')
    p.add_argument('--umfang', default='liken')

def run(args, cfg):
    bericht = signup.create(cfg, args.platform, name=args.name, email=args.mail, password=args.passwort,
                            mode=args.modus, country=args.land, mobile=args.handy, timeout=args.zeitlimit,
                            warmup=args.aufwaermen, warmup_umfang=args.umfang, melden=print)
    print(f"Konto {bericht['identity']} angelegt (ID {bericht['konto_id']}, {bericht['cookies']} Cookies).")
    return 0
