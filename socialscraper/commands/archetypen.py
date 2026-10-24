"""Kommandozeile für die Archetypen-Ebene: zeigen, zuweisen, Mischung prüfen."""
from .. import archetypen
from ..store import Store

NAME, HELP = 'archetypen', 'Archetypen zeigen, zuweisen und die Mischung prüfen'


def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)
    zu = was.add_parser('zuweisen')
    zu.add_argument('--alle', action='store_true', help='auch bereits zugewiesene Konten neu ziehen')
    zeig = was.add_parser('zeigen')
    zeig.add_argument('identity')
    pruf = was.add_parser('pruefen')
    pruf.add_argument('--ziel', type=float, default=archetypen.ZIELWERT)
    kreuz = was.add_parser('kreuz')
    kreuz.add_argument('links', choices=archetypen.FELDER)
    kreuz.add_argument('rechts', choices=archetypen.FELDER)
    was.add_parser('liste')


def _balken(wert):
    gefuellt = max(0, min(10, int(round(wert * 10))))
    return '#' * gefuellt + '.' * (10 - gefuellt)


def _tabelle(links, rechts, tabelle):
    spalten = sorted({s for zeile in tabelle.values() for s in zeile})
    print('    ' + links.ljust(12) + ''.join(f'{s:>14}' for s in spalten), flush=True)
    for links_wert in sorted(tabelle):
        print(f'    {links_wert:<12}' + ''.join(f"{tabelle[links_wert].get(s, 0):>14}" for s in spalten),
              flush=True)


def run(args, cfg):
    with Store(cfg.db_path) as store:
        archetypen.ensure_schema(store.db)

        if args.was == 'zuweisen':
            print(f'{archetypen.zuweisen(cfg.db_path, nur_ohne=not args.alle)} Konten zugewiesen.', flush=True)
            return 0

        if args.was == 'zeigen':
            daten = archetypen.archetyp_von(cfg.db_path, args.identity)
            if daten is None:
                print(f'Kein Konto mit der Kennung {args.identity}.', flush=True)
                return 1
            print(f"{daten['identity']} · {daten.get('alter')} J. · {daten.get('geschlecht')} · "
                  f"{daten.get('geraet')} · {daten.get('verhalten_typ')} · Stil {daten.get('stil')}", flush=True)
            for eintrag in daten['archetypen']:
                print(f"  {eintrag['gewicht']:>5}  {archetypen.ARCHETYPEN[eintrag['schluessel']]['name']}", flush=True)
            return 0

        if args.was == 'kreuz':
            try:
                tabelle = archetypen.kreuztabelle(cfg.db_path, args.links, args.rechts)
            except Exception as fehler:
                print(f'Kreuztabelle abgebrochen: {type(fehler).__name__}: {fehler}', flush=True)
                return 1
            _tabelle(args.links, args.rechts, tabelle)
            return 0

        if args.was == 'pruefen':
            print(f'Misch-Prüfer · Zielwert {args.ziel:.2f}', flush=True)
            try:
                befunde = archetypen.pruefen(cfg.db_path, zielwert=args.ziel)
            except Exception as fehler:
                print(f'Prüfer abgebrochen: {type(fehler).__name__}: {fehler}', flush=True)
                return 1
            if not befunde:
                print('Keine Paarungen geprüft.', flush=True)
                return 0
            for befund in befunde:
                print(f"  {befund['wert']:>6.3f}  {befund['links']:<11} -> {befund['rechts']:<11} "
                      f"{_balken(befund['wert'])}  {befund['urteil']}", flush=True)
            schlecht = [b for b in befunde if b['urteil'] != 'nur ein Wert' and b['wert'] > args.ziel]
            print(f"\n{len(befunde) - len(schlecht)} von {len(befunde)} Paarungen unter {args.ziel:.2f}.", flush=True)
            for befund in schlecht[:2]:
                print(f"\n{befund['links']} -> {befund['rechts']} ist zu gleichförmig:", flush=True)
                _tabelle(befund['links'], befund['rechts'],
                         archetypen.kreuztabelle(cfg.db_path, befund['links'], befund['rechts']))
            return 0

        for zeile in store.db.execute('SELECT schluessel, name, anteil, aktiv FROM archetypen ORDER BY anteil DESC'):
            zaehler = store.db.execute(
                'SELECT COUNT(*) FROM account_archetypen WHERE archetyp_id='
                "(SELECT id FROM archetypen WHERE schluessel=?) AND rolle='haupt'", (zeile[0],)).fetchone()[0]
            print(f"{zeile[3] and ' ' or 'x'} {zeile[1]:<24} {zeile[2] * 100:>5.1f} %  {zaehler:>5} Konten", flush=True)
        return 0