from .. import personas
from ..store import Store
NAME, HELP = 'personas', 'Personas verwalten und Konten zuweisen'


def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)
    neu = was.add_parser('anlegen'); neu.add_argument('name'); neu.add_argument('--influencer', required=True)
    neu.add_argument('--kanaele', required=True, help='mit Komma'); neu.add_argument('--gewicht', type=float, default=1.0)
    vor = was.add_parser('vorschlag'); vor.add_argument('--kanaele', required=True, help='Pool, mit Komma')
    vor.add_argument('--influencer', required=True); vor.add_argument('--anzahl', type=int, default=24)
    vor.add_argument('--je', type=int, default=8)
    was.add_parser('zuweisen'); was.add_parser('liste')


def run(args, cfg):
    with Store(cfg.db_path) as store:
        db = store.db
        personas.ensure_schema(db)
        if args.was == 'anlegen':
            pid = personas.anlegen(db, args.name, influencer=args.influencer, influencer_gewicht=args.gewicht,
                                   kanaele=[k.strip() for k in args.kanaele.split(',') if k.strip()])
            print(f'Persona {pid} angelegt.'); return 0
        if args.was == 'vorschlag':
            if not args.influencer:
                raise ValueError('Persona braucht einen Influencer.')
            vorschlaege = personas.vorschlag([k.strip() for k in args.kanaele.split(',') if k.strip()],
                                             anzahl=args.anzahl, kanaele_je=args.je, influencer=args.influencer)
            db.execute('BEGIN IMMEDIATE')
            try:
                namen = {zeile[0] for zeile in db.execute('SELECT name FROM personas')}
                nummer = 1
                for vorschlag in vorschlaege:
                    while (name := f'persona_{nummer:02d}') in namen:
                        nummer += 1
                    namen.add(name)
                    nummer += 1
                    personas.anlegen(db, name, influencer=vorschlag['influencer'],
                                    kanaele=sorted(vorschlag['kanaele']))
                db.commit()
            except BaseException:
                db.rollback()
                raise
            print(f'{len(vorschlaege)} Personas angelegt.'); return 0
        if args.was == 'zuweisen':
            print(f'{personas.zuweisen(cfg.db_path)} Konten zugewiesen.'); return 0
        for p in personas.aktive(db):
            print(f"[{p['id']:>3}] {p['name']:<14} {len(p['kanaele'])} Kanäle · Influencer {p['influencer']}")
        return 0
