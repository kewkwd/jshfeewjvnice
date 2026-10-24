"""Kommandozeile für den Datengenerator: Profile erzeugen, prüfen, anzeigen."""
from .. import accountvault, profile_settings, profilgenerator
from ..store import Store

NAME, HELP = 'profil', 'Bot-Profile erzeugen (Person, Adresse, E-Mail, Passwort, Tempo)'
STANDARD_HTML = 'datengenerator/datengenerator.html'


def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)

    an = was.add_parser('anlegen')
    an.add_argument('identity')
    an.add_argument('--html', default=STANDARD_HTML)
    an.add_argument('--stadt', help='fester Ort; ohne Angabe zufällig aus dem Bestand')
    an.add_argument('--anbieter', choices=['web.de', 'gmx.net', 'gmail.com', 'outlook.com'])
    an.add_argument('--postfach', action='store_true', help='Adresse zusätzlich in den Postfach-Pool')
    an.add_argument('--trocken', action='store_true', help='nur rechnen, nichts schreiben')

    st = was.add_parser('stapel')
    st.add_argument('anzahl', type=int)
    st.add_argument('--ab', type=int, default=1)
    st.add_argument('--prefix', default='konto')
    st.add_argument('--html', default=STANDARD_HTML)
    st.add_argument('--stadt')
    st.add_argument('--anbieter', choices=['web.de', 'gmx.net', 'gmail.com', 'outlook.com'])
    st.add_argument('--postfach', action='store_true')

    zeig = was.add_parser('zeigen')
    zeig.add_argument('identity')
    was.add_parser('pruefen')


def _tempo_zeile(tempo, streuung):
    teile = []
    for name, einheit in (('mouse_px_s', 'px/s'), ('click_ms', 'ms'), ('typing_cps', 'Zeichen/s')):
        von, bis = streuung[name]
        teile.append(f"{name.replace('_', ' ')} {tempo[name]} (Band {von:g}-{bis:g}) {einheit}")
    print('  Tempo: ' + ' · '.join(teile), flush=True)


def _drucken(profil):
    a = profil['anschrift']
    print(f"{profil['identity']}  ({profil['archetyp']}, {profil['alter']} J., {profil['verhalten']})", flush=True)
    print(f"  {profil['vorname']} {profil['nachname']} · {profil['geschlecht']} · geb. {profil['geburt']}", flush=True)
    print(f"  {a['strasse']} {a['hausnummer']}, {a['plz']} {a['ort']}, {a['land']}", flush=True)
    print(f"  {profil['adresse']}", flush=True)
    _tempo_zeile(profil['tempo'], profil['streuung'])
    print(f"  Quelle {profil['quelle']} · {profil.get('schreibweise', '')}", flush=True)


def run(args, cfg):
    if args.was == 'pruefen':
        bericht = profilgenerator.pruefen(cfg)
        print(f"{bericht['mit_profil']} von {bericht['konten']} Konten haben ein Profil.", flush=True)
        for befund in bericht['befunde'][:40]:
            print(f"  [{befund['art']}] {befund['identity']}: {befund['text']}", flush=True)
        print(f"\n{len(bericht['befunde'])} Befunde.", flush=True)
        return 0

    if args.was == 'zeigen':
        from .. import repo
        with Store(cfg.db_path) as store:
            konto = next((k for k in repo.list_accounts(store.db, 'tiktok')
                          if k['identity'] == args.identity), None)
        if konto is None:
            print(f'Kein Konto mit der Kennung {args.identity}.', flush=True)
            return 1
        daten = profile_settings.load(cfg.profiles_dir, args.identity)
        geheim = accountvault.get(cfg.db_path, args.identity)
        person = daten['person']
        print(f"{args.identity}  (Alter {konto.get('alter')}, {konto.get('verhalten_typ')})", flush=True)
        print(f"  {person['vorname']} {person['nachname']} · {person['geschlecht']} · "
              f"geb. {person['geburtsdatum']} · {person['wohnort']}", flush=True)
        print(f"  {geheim.get('email') or '(keine E-Mail)'} · "
              f"Passwort {'gesetzt' if geheim.get('passwort') else 'fehlt'}", flush=True)
        _tempo_zeile(daten['tempo'], daten['streuung'])
        return 0

    try:
        quelle = profilgenerator.Quelle(args.html)
    except profilgenerator.ProfilError as fehler:
        print(f'{fehler}', flush=True)
        return 1

    if args.was == 'anlegen':
        try:
            profil = profilgenerator.anlegen(cfg, args.identity, quelle=quelle, stadt=args.stadt,
                                             anbieter=args.anbieter, mit_postfach=args.postfach,
                                             trocken=args.trocken)
        except profilgenerator.ProfilError as fehler:
            print(f'{fehler}', flush=True)
            return 1
        _drucken(profil)
        return 0

    bericht = profilgenerator.stapel(cfg, args.anzahl, quelle=quelle, prefix=args.prefix, ab=args.ab,
                                     stadt=args.stadt, anbieter=args.anbieter, mit_postfach=args.postfach)
    print(f"{bericht['angelegt']} Profile geschrieben, {bericht['uebersprungen']} übersprungen "
          f"(kein Konto mit dieser Nummer).", flush=True)
    for profil in bericht['profile'][:5]:
        _drucken(profil)
    return 0