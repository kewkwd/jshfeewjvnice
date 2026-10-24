import json, logging,os
from pathlib import Path
from .. import accountvault, mailanbieter
log = logging.getLogger(__name__)
NAME, HELP = 'mailkonto', 'Postfach anlegen (web.de, GMX, Gmail, Outlook) oder Pool zeigen'

def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)
    neu = was.add_parser('neu', help='Postfach anlegen')
    neu.add_argument('anbieter', choices=['web.de', 'gmx.net', 'gmail.com', 'outlook.com'])
    neu.add_argument('--adresse'); neu.add_argument('--vorname'); neu.add_argument('--nachname')
    neu.add_argument('--passwort'); neu.add_argument('--modus', choices=['auto', 'assisted'], default='assisted')
    neu.add_argument('--handy', action='store_true')
    neu.add_argument('--profil', help='Vorbereitetes Profil mit festem Proxy und Personendaten')
    neu.add_argument('--weg',choices=['browser','android'],default='browser',
                     help='Android: WEB.DE-App im AVD oder begleiteter Gmail-Weg')
    neu.add_argument('--fehlerfenster',type=int,default=0,metavar='SEKUNDEN',
                     help='Bei Browserfehlern 0–600 Sekunden zur Diagnose offen lassen')
    was.add_parser('pool', help='Pool zeigen')
    frei = was.add_parser('freigeben', help='Postfach wieder freigeben'); frei.add_argument('konto')

def run(args, cfg):
    if args.was == 'pool':
        for e in accountvault.postfaecher(cfg.db_path):
            print(f"{e['adresse']:<40} {e.get('anbieter', '?'):<12} "
                  f"{'belegt von ' + e['belegt_von'] if e.get('belegt_von') else 'frei'}")
        return 0
    if args.was == 'freigeben':
        accountvault.freigeben(cfg.db_path, args.konto)
        print(f'Postfach von {args.konto} freigegeben.')
        return 0
    cancel_file=os.environ.get('SS_CANCEL_FILE')
    stop=(lambda:Path(cancel_file).exists()) if cancel_file else None
    bericht = mailanbieter.create(cfg, args.anbieter, adresse=args.adresse, vorname=args.vorname,
                                  nachname=args.nachname, passwort=args.passwort, mode=args.modus,
                                  mobile=args.handy, melden=print,stop=stop,route=getattr(args,'weg','browser'),
                                  failure_window=getattr(args,'fehlerfenster',0),
                                  profile_identity=getattr(args,'profil',None))
    print('fertig: ' + json.dumps(bericht, ensure_ascii=False))
    return 0
