"""Konten in Stapeln anlegen — nur Datenbankzeilen, ohne Browser und ohne Anmeldung.

Gedacht für den Aufbau eines großen Bestands (z. B. 500 Konten) und für Testbestände.
Die Kennung entsteht wie überall aus dem Kontonamen (repo.add_account), also
„konto 0001“ → „tiktok_konto_0001“.
"""
from .. import repo
from ..store import Store

NAME, HELP = 'konten', 'Konten in Stapeln anlegen, auflisten und entfernen'


def add_arguments(p):
    was = p.add_subparsers(dest='was', required=True)

    an = was.add_parser('anlegen', help='Konten als Datenbankzeilen anlegen')
    an.add_argument('anzahl', type=int)
    an.add_argument('--plattform', default='tiktok', choices=sorted(repo.ACCOUNT_PLATFORMS))
    an.add_argument('--prefix', default='konto')
    an.add_argument('--land', default='de')
    an.add_argument('--ab', dest='start', type=int, default=1, help='erste Nummer (Standard 1)')

    li = was.add_parser('liste', help='Konten zählen und die ersten zeigen')
    li.add_argument('--plattform', default='tiktok', choices=sorted(repo.ACCOUNT_PLATFORMS))

    weg = was.add_parser('entfernen', help='Konten wieder löschen (Anmeldungen bleiben liegen)')
    weg.add_argument('--plattform', default='tiktok', choices=sorted(repo.ACCOUNT_PLATFORMS))
    weg.add_argument('--prefix', default='konto')
    weg.add_argument('--alle', action='store_true', help='alle Konten der Plattform, nicht nur das Muster')


def _kennung(plattform, prefix, nummer):
    return f'{plattform}_{repo.slug(f"{prefix} {nummer:04d}")}'


def run(args, cfg):
    if args.was == 'anlegen':
        if args.anzahl < 1:
            raise ValueError('Die Anzahl muss mindestens 1 sein.')
        with Store(cfg.db_path) as store:
            angelegt, nummer, versuche = 0, args.start, 0
            while angelegt < args.anzahl and versuche < args.anzahl * 4 + 10:
                versuche += 1
                name = f'{args.prefix} {nummer:04d}'
                kennung = _kennung(args.plattform, args.prefix, nummer)
                nummer += 1
                if store.db.execute('SELECT 1 FROM accounts WHERE identity=?', (kennung,)).fetchone():
                    continue
                repo.add_account(store.db, args.plattform, name, country=args.land)
                angelegt += 1
            print(f'{angelegt} Konten angelegt ({args.plattform}, ab Nummer {args.start}).')
            print('Nur Datenbankzeilen: keine Anmeldung, keine Cookies, kein Proxy.')
        return 0

    with Store(cfg.db_path) as store:
        if args.was == 'liste':
            zeilen = repo.list_accounts(store.db, args.plattform)
            print(f'{len(zeilen)} Konten auf {args.plattform}.')
            for konto in zeilen[:20]:
                print(f"  [{konto['id']:>4}] {konto['label']:<20} {konto['identity']}")
            if len(zeilen) > 20:
                print(f'  ... und {len(zeilen) - 20} weitere')
            return 0

        muster = f'{args.plattform}_{repo.slug(args.prefix)}_%'
        if args.alle:
            treffer = repo.list_accounts(store.db, args.plattform)
        else:
            treffer = [k for k in repo.list_accounts(store.db, args.plattform)
                       if k['identity'].startswith(f'{args.plattform}_{repo.slug(args.prefix)}_')]
        for konto in treffer:
            store.db.execute('DELETE FROM accounts WHERE id=?', (konto['id'],))
        store.db.commit()
        print(f'{len(treffer)} Konten entfernt (Muster {muster if not args.alle else "alle"}).')
        print('Gespeicherte Anmeldungen und Proxys bleiben liegen.')
        return 0