from .. import archetypen
from ..store import Store
NAME, HELP = 'archetypen', 'Archetypen zeigen, zuweisen und die Mischung prüfen'


def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)
    zu = was.add_parser('zuweisen'); zu.add_argument('--alle', action='store_true',
                                                     help='auch bereits zugewiesene Konten neu ziehen')
    zeig = was.add_parser('zeigen'); zeig.add_argument('identity')
    pruf = was.add_parser('pruefen'); pruf.add_argument('--ziel', type=float, default=archetypen.ZIELWERT)
    kreuz = was.add_parser('kreuz'); kreuz.add_argument('links', choices=archetypen.FELDER)
    kreuz.add_argument('rechts', choices=archetypen.FELDER)
    was.add_parser('liste')


def run(args, cfg):
    with Store(cfg.db_path) as store:
        archetypen.ensure_schema(store.db)
        if args.was == 'zuweisen':
            print(f'{archetypen.zuweisen(cfg.db_path, nur_ohne=not args.alle)} Konten zugewiesen.')
            return 0
        if args.was == 'zeigen':
            daten = archetypen.archetyp_von(cfg.db_path, args.identity)
            if daten is None:
                print(f'Kein Konto mit der Kennung {args.identity}.')
                return 1
            print(f"{daten['identity']} · {daten.get('alter')} J. · {daten.get('geschlecht')} · "
                  f"{daten.get('geraet')} · {daten.get('verhalten_typ')} · Stil {daten.get('stil')}")
            for eintrag in daten['archetypen']:
                print(f"  {eintrag['gewicht']:>5}  {archetypen.ARCHETYPEN[eintrag['schluessel']]['name']}")
            return 0
        if args.was == 'kreuz':
            tabelle = archetypen.kreuztabelle(cfg.db_path, args.links, args.rechts)
            spalten = sorted({s for zeile in tabelle.values() for s in zeile})
            print(f'{args.links} \\ {args.rechts}'.ljust(16) + ''.join(f'{s:>14}' for s in spalten))
            for links_wert in sorted(tabelle):
                print(f'{links_wert:<16}' + ''.join(f"{tabelle[links_wert].get(s, 0):>14}" for s in spalten))
            return 0
        if args.was == 'pruefen':
            befunde = archetypen.pruefen(cfg.db_path, zielwert=args.ziel)
            for b in befunde:
                print(f"{b['wert']:>6.3f}  {b['links']:<12} → {b['rechts']:<12} {b['urteil']}")
            schlecht = [b for b in befunde if b['wert'] > args.ziel]
            print(f"\n{len(befunde) - len(schlecht)} von {len(befunde)} Paarungen unter {args.ziel:.2f}.")
            return 0
        for zeile in store.db.execute('SELECT schluessel, name, anteil, aktiv FROM archetypen ORDER BY anteil DESC'):
            zaehler = store.db.execute(
                'SELECT COUNT(*) FROM account_archetypen WHERE archetyp_id=(SELECT id FROM archetypen WHERE schluessel=?) '
                "AND rolle='haupt'", (zeile[0],)).fetchone()[0]
            print(f"{zeile[3] and ' ' or 'x'} {zeile[1]:<24} {zeile[2] * 100:>5.1f} %  {zaehler:>5} Konten")
        return 0