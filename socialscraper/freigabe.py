# socialscraper/freigabe.py
"""Das Tor zur Wartschlange.

Harte Regel:
    Ein Datensatz darf erst in die Wartschlange, wenn eine Adresse aus seiner
    Ausweichkette frisch als frei geprueft wurde. Nicht vorher.

Die Kette:
    Jeder Datensatz hat eine geordnete Kette von Adressen fuer dieselbe Person.
    Ist die erste belegt, greift die naechste - der Name bleibt, nur die
    Schreibweise aendert sich. Alle Glieder sind reserviert.

Haltbarkeit:
    Die Wartschlange kann stehen. Deshalb wird beim Einreihen frisch geprueft,
    die Freigabe bekommt ein Haltbarkeitsdatum, und beim Entnehmen wird ein
    letztes Mal geprueft. Wer durchfaellt, bekommt das naechste Kettenglied.

Zustaende:
    entwurf    - angelegt, noch nicht geprueft
    nacharbeit - Kette durch, nichts frei (belegt/unklar)
    bereit     - geprueft frei, liegt in der Wartschlange
    entnommen  - von der Registrierung abgeholt
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from . import profilgenerator as pg
from .mail_pruefen import (AMPEL_BELEGT, AMPEL_FREI, AMPEL_UNKLAR,
                           AMPEL_UNGUELTIG, Adresspruefer, Befund, mx_holen,
                           smtp_befragen)

MINDEST_STUFE = "smtp"
NOTFALL_STUFE = "mx"
GUELTIG_STUNDEN = 24
RUNDEN = 3

ZUSTAND_ENTWURF = "entwurf"
ZUSTAND_NACHARBEIT = "nacharbeit"
ZUSTAND_BEREIT = "bereit"
ZUSTAND_ENTNOMMEN = "entnommen"

SCHAFLOSE_GRUENDE = ("SMTP nicht erreichbar", "Zeitueberschreitung")


class FreigabeError(Exception):
    """Freigabe nicht moeglich."""


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(zeitpunkt: datetime | None = None) -> str:
    return (zeitpunkt or _jetzt()).isoformat()


# --- Netzlage ----------------------------------------------------------------

def netz_probe(timeout: float = 8.0):
    hosts = mx_holen("gmail.com", timeout)
    if not hosts:
        return NOTFALL_STUFE, "kein DNS - nur formale Pruefung moeglich"
    zufall = "".join(random.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(18))
    ampel, grund = smtp_befragen(hosts[0], f"{zufall}@gmail.com", timeout)
    if ampel == AMPEL_UNKLAR and any(g in grund for g in SCHAFLOSE_GRUENDE):
        return NOTFALL_STUFE, f"Port 25 zu ({grund}) - nur DNS pruefbar"
    return MINDEST_STUFE, "SMTP erreichbar - scharfe Pruefung moeglich"


# --- Tabellen ----------------------------------------------------------------

def _tabellen_sichern(conn):
    conn.execute(
        'CREATE TABLE IF NOT EXISTS "adressfreigabe" ('
        ' identity TEXT PRIMARY KEY, zustand TEXT NOT NULL, ampel TEXT,'
        ' stufe TEXT, grund TEXT, geprueft_am TEXT, gueltig_bis TEXT,'
        ' versuche INTEGER, kandidat_rang INTEGER)')
    conn.execute(
        'CREATE TABLE IF NOT EXISTS "wartschlange" ('
        ' identity TEXT PRIMARY KEY, adresse TEXT, passwort TEXT, zustand TEXT,'
        ' ampel TEXT, stufe TEXT, grund TEXT, bereit_am TEXT, gueltig_bis TEXT,'
        ' entnommen_am TEXT)')
    pg.kandidaten_tabelle(conn)


def _person(conn, identity):
    zeile = conn.execute('SELECT * FROM "profile_settings" WHERE identity = ?',
                         (identity,)).fetchone()
    return dict(zeile) if zeile else None


def _postfach(conn, identity):
    zeile = conn.execute('SELECT * FROM "postfaecher" WHERE identity = ?',
                         (identity,)).fetchone()
    return dict(zeile) if zeile else None


def _freigabe_zeile(conn, identity):
    zeile = conn.execute('SELECT * FROM "adressfreigabe" WHERE identity = ?',
                         (identity,)).fetchone()
    return dict(zeile) if zeile else None


def _merken(conn, identity, zustand, befund=None, gueltig_bis=None,
            rang=None, verworfen=0):
    pg._schreiben(conn, "adressfreigabe", {
        "identity": identity, "zustand": zustand,
        "ampel": befund.ampel if befund else None,
        "stufe": befund.stufe if befund else None,
        "grund": befund.grund if befund else None,
        "geprueft_am": _stamp() if befund else None,
        "gueltig_bis": gueltig_bis, "versuche": verworfen,
        "kandidat_rang": rang})


def _aus_schlange(conn, identity):
    try:
        conn.execute('DELETE FROM "wartschlange" WHERE identity = ?', (identity,))
    except sqlite3.OperationalError:
        pass


# --- Kette ablaufen ----------------------------------------------------------

def kette_gehen(db_pfad, identity, person, pruefer, rng, conn, trocken=False):
    """Geht die Ausweichkette von oben nach unten.

    Erste freie gewinnt. Ist die Kette leer, wird nachgelegt.
    Rueckgabe: (adresse, befund, rang, wieviele_belegte).
    """
    if not trocken:
        vorhanden = pg.vorhandene_adressen_conn(conn, ohne=identity)
        if not pg.kandidaten_lesen(conn, identity):
            pg.kandidaten_anlegen(conn, identity, person, rng, vorhanden)

    erster_unklar = None
    befund = None
    verworfen = 0

    for _ in range(RUNDEN):
        kandidaten = pg.kandidaten_lesen(conn, identity)
        if not kandidaten:
            break
        for kandidat in kandidaten:
            adresse = kandidat["adresse"]
            befund = pruefer.befund(adresse)
            if not trocken:
                pg._schreiben(conn, "adresskandidaten", {
                    "identity": identity, "rang": kandidat["rang"], "adresse": adresse,
                    "ampel": befund.ampel, "grund": befund.grund,
                    "geprueft_am": _stamp()})
            if befund.ampel == AMPEL_FREI:
                return adresse, befund, kandidat["rang"], verworfen
            if befund.ampel == AMPEL_UNKLAR and erster_unklar is None:
                erster_unklar = (adresse, befund, kandidat["rang"], verworfen)
            if befund.ampel in (AMPEL_BELEGT, AMPEL_UNGUELTIG):
                verworfen += 1
        if trocken:
            break
        if not pg.kandidaten_erweitern(db_pfad, identity, person, rng):
            break

    if erster_unklar:
        return erster_unklar
    kandidaten = pg.kandidaten_lesen(conn, identity)
    if kandidaten and befund is not None:
        letzter = kandidaten[-1]
        return letzter["adresse"], befund, letzter["rang"], verworfen
    raise FreigabeError(f"keine Adresse fuer {identity}")


def _darf_rein(befund, auch_unklar, notfall_mx):
    if befund.ampel == AMPEL_FREI:
        return True, "nur_mx" if befund.stufe == "mx" else ""
    if befund.ampel == AMPEL_UNKLAR:
        if any(g in (befund.grund or "") for g in SCHAFLOSE_GRUENDE) and notfall_mx:
            return True, "nur_mx"
        if auch_unklar:
            return True, "unklar"
    return False, ""


def _benutzen(conn, identity, adresse, rang, postfach=None):
    pg._schreiben(conn, "postfaecher", {"identity": identity, "adresse": adresse})
    try:
        conn.execute('UPDATE "adresskandidaten" SET in_benutzung = 0'
                     ' WHERE identity = ?', (identity,))
        conn.execute('UPDATE "adresskandidaten" SET in_benutzung = 1'
                     ' WHERE identity = ? AND rang = ?', (identity, rang))
    except sqlite3.OperationalError:
        pass


# --- Der Kern ----------------------------------------------------------------

def freigeben(db_pfad: str, identity: str, pruefer=None, stufe: str = MINDEST_STUFE,
              rng: random.Random | None = None, gueltig_stunden: int = GUELTIG_STUNDEN,
              auch_unklar: bool = False, notfall_mx: bool = True,
              trocken: bool = False) -> dict:
    """Prueft die Kette und reiht nur bei Erfolg in die Wartschlange."""
    rng = rng or random.Random()
    pruefer = pruefer or Adresspruefer(stufe, db_pfad)

    with pg.verbinden(db_pfad) as conn:
        _tabellen_sichern(conn)
        person = _person(conn, identity)
        if not person:
            raise FreigabeError(f"kein Profil zu {identity}")
        postfach = _postfach(conn, identity)
        if not postfach:
            raise FreigabeError(f"kein Postfach zu {identity}")

        alt = _freigabe_zeile(conn, identity) or {}
        if alt.get("zustand") == ZUSTAND_BEREIT and (alt.get("gueltig_bis") or "") > _stamp():
            return {"identity": identity, "zustand": ZUSTAND_BEREIT,
                    "adresse": postfach.get("adresse"), "ampel": alt.get("ampel"),
                    "grund": alt.get("grund"), "rang": alt.get("kandidat_rang"),
                    "verworfen": alt.get("versuche") or 0,
                    "hinweis": "schon freigegeben"}

        adresse, befund, rang, verworfen = kette_gehen(
            db_pfad, identity, person, pruefer, rng, conn, trocken)

        rein, vermerk = _darf_rein(befund, auch_unklar, notfall_mx)
        grund = (befund.grund or "") + (f" [{vermerk}]" if vermerk else "")

        ergebnis = {"identity": identity, "adresse": adresse, "ampel": befund.ampel,
                    "grund": grund, "rang": rang, "verworfen": verworfen,
                    "zustand": ZUSTAND_BEREIT if rein else ZUSTAND_NACHARBEIT}
        if trocken:
            return ergebnis

        _benutzen(conn, identity, adresse, rang, postfach)
        pg._schreiben(conn, "postfaecher", {
            "identity": identity, "adresse": adresse, "ampel": befund.ampel,
            "grund": grund, "zeitpunkt": _stamp()})

        if not rein:
            _aus_schlange(conn, identity)
            _merken(conn, identity, ZUSTAND_NACHARBEIT, befund, rang=rang,
                    verworfen=verworfen)
            pg._schreiben(conn, "postfaecher", {"identity": identity,
                                                "zustand": ZUSTAND_NACHARBEIT})
            return ergebnis

        gueltig_bis = _stamp(_jetzt() + timedelta(hours=gueltig_stunden))
        _merken(conn, identity, ZUSTAND_BEREIT, befund, gueltig_bis, rang, verworfen)
        pg._schreiben(conn, "wartschlange", {
            "identity": identity, "adresse": adresse,
            "passwort": postfach.get("passwort") or "", "zustand": ZUSTAND_BEREIT,
            "ampel": befund.ampel, "stufe": befund.stufe, "grund": grund,
            "bereit_am": _stamp(), "gueltig_bis": gueltig_bis})
        pg._schreiben(conn, "postfaecher", {"identity": identity,
                                            "zustand": ZUSTAND_BEREIT})
        ergebnis["gueltig_bis"] = gueltig_bis
        return ergebnis


def offene(db_pfad: str) -> list:
    with pg.verbinden(db_pfad) as conn:
        _tabellen_sichern(conn)
        zeilen = conn.execute(
            'SELECT p.identity FROM "profile_settings" p'
            ' LEFT JOIN "adressfreigabe" f ON f.identity = p.identity'
            ' WHERE f.zustand IS NULL OR f.zustand != ?'
            ' ORDER BY p.identity', (ZUSTAND_BEREIT,)).fetchall()
        return [z["identity"] for z in zeilen]


def lauf(db_pfad: str, anzahl: int | None = None, stufe: str = MINDEST_STUFE,
         threads: int = 8, gueltig_stunden: int = GUELTIG_STUNDEN,
         auch_unklar: bool = False, notfall_mx: bool = True,
         saat: int | None = None, still: bool = False) -> dict:
    rng = random.Random(saat)
    lage, meldung = netz_probe()
    if lage == NOTFALL_STUFE and stufe == MINDEST_STUFE:
        print(f"[netz] {meldung}", flush=True)

    pruefer = Adresspruefer(stufe, db_pfad, threads)
    kennungen = offene(db_pfad)
    if anzahl:
        kennungen = kennungen[:anzahl]

    zaehler = {ZUSTAND_BEREIT: 0, ZUSTAND_NACHARBEIT: 0}
    for identity in kennungen:
        ergebnis = freigeben(db_pfad, identity, pruefer, stufe, rng,
                             gueltig_stunden, auch_unklar, notfall_mx)
        zustand = ergebnis.get("zustand")
        zaehler[zustand] = zaehler.get(zustand, 0) + 1
        if not still:
            print(f"[{zustand}] {identity}: {ergebnis.get('adresse')} "
                  f"(Glied {ergebnis.get('rang')}, verworfen {ergebnis.get('verworfen')}, "
                  f"{ergebnis.get('ampel')})", flush=True)

    print(f"[freigabe] bereit={zaehler.get(ZUSTAND_BEREIT, 0)} "
          f"nacharbeit={zaehler.get(ZUSTAND_NACHARBEIT, 0)}", flush=True)
    return zaehler


def nachpruefen(db_pfad: str, stufe: str = MINDEST_STUFE,
                gueltig_stunden: int = GUELTIG_STUNDEN,
                auch_unklar: bool = False, notfall_mx: bool = True,
                still: bool = False) -> dict:
    """Abgelaufene Freigaben neu pruefen. Wer nicht mehr frei ist, fliegt raus."""
    pruefer = Adresspruefer(stufe, db_pfad)
    zaehler = {"verlaengert": 0, "raus": 0}

    with pg.verbinden(db_pfad) as conn:
        _tabellen_sichern(conn)
        abgelaufen = conn.execute(
            'SELECT identity FROM "adressfreigabe"'
            ' WHERE zustand = ? AND gueltig_bis < ? ORDER BY gueltig_bis',
            (ZUSTAND_BEREIT, _stamp())).fetchall()
        kennungen = [z["identity"] for z in abgelaufen]

    for identity in kennungen:
        with pg.verbinden(db_pfad) as conn:
            _tabellen_sichern(conn)
            postfach = _postfach(conn, identity)
        if not postfach:
            continue
        befund = pruefer.befund(postfach["adresse"])
        rein, _ = _darf_rein(befund, auch_unklar, notfall_mx)
        if rein:
            gueltig_bis = _stamp(_jetzt() + timedelta(hours=gueltig_stunden))
            with pg.verbinden(db_pfad) as conn:
                _merken(conn, identity, ZUSTAND_BEREIT, befund, gueltig_bis)
                pg._schreiben(conn, "wartschlange", {
                    "identity": identity, "adresse": postfach["adresse"],
                    "zustand": ZUSTAND_BEREIT, "ampel": befund.ampel,
                    "stufe": befund.stufe, "grund": befund.grund,
                    "bereit_am": _stamp(), "gueltig_bis": gueltig_bis})
            zaehler["verlaengert"] += 1
            if not still:
                print(f"[verlaengert] {identity}", flush=True)
        else:
            with pg.verbinden(db_pfad) as conn:
                _aus_schlange(conn, identity)
                _merken(conn, identity, ZUSTAND_ENTWURF, befund)
            zaehler["raus"] += 1
            if not still:
                print(f"[raus] {identity}: {befund.ampel} ({befund.grund})", flush=True)
    return zaehler


def entnehmen(db_pfad: str, stufe: str = MINDEST_STUFE,
              gueltig_stunden: int = GUELTIG_STUNDEN,
              auch_unklar: bool = False, notfall_mx: bool = True,
              rng: random.Random | None = None) -> dict | None:
    """Nimmt den naechsten fertigen Datensatz. Prueft unmittelbar vorher.

    Faellt die Adresse durch, greift das naechste Kettenglied - die Person
    bleibt, nur die Schreibweise wandert.
    """
    rng = rng or random.Random()
    pruefer = Adresspruefer(stufe, db_pfad)

    while True:
        with pg.verbinden(db_pfad) as conn:
            _tabellen_sichern(conn)
            zeile = conn.execute(
                'SELECT identity, adresse, passwort FROM "wartschlange"'
                ' WHERE zustand = ? ORDER BY bereit_am LIMIT 1',
                (ZUSTAND_BEREIT,)).fetchone()
            if not zeile:
                return None
            person = _person(conn, zeile["identity"]) or {}

        identity = zeile["identity"]
        befund = pruefer.befund(zeile["adresse"])
        rein, _ = _darf_rein(befund, auch_unklar, notfall_mx)

        if rein:
            with pg.verbinden(db_pfad) as conn:
                conn.execute('UPDATE "wartschlange" SET zustand = ?, entnommen_am = ?'
                             ' WHERE identity = ?',
                             (ZUSTAND_ENTNOMMEN, _stamp(), identity))
                _merken(conn, identity, ZUSTAND_ENTNOMMEN, befund)
            return {"identity": identity, "adresse": zeile["adresse"],
                    "passwort": zeile["passwort"], "ampel": befund.ampel,
                    "stufe": befund.stufe, "grund": befund.grund}

        with pg.verbinden(db_pfad) as conn:
            _tabellen_sichern(conn)
            adresse, neuer, rang, verworfen = kette_gehen(
                db_pfad, identity, person, pruefer, rng, conn)
            rein2, _ = _darf_rein(neuer, auch_unklar, notfall_mx)
            if rein2:
                gueltig_bis = _stamp(_jetzt() + timedelta(hours=gueltig_stunden))
                _benutzen(conn, identity, adresse, rang)
                _merken(conn, identity, ZUSTAND_BEREIT, neuer, gueltig_bis, rang, verworfen)
                pg._schreiben(conn, "wartschlange", {
                    "identity": identity, "adresse": adresse,
                    "zustand": ZUSTAND_BEREIT, "ampel": neuer.ampel,
                    "stufe": neuer.stufe, "grund": neuer.grund,
                    "bereit_am": _stamp(), "gueltig_bis": gueltig_bis})
            else:
                _aus_schlange(conn, identity)
                _merken(conn, identity, ZUSTAND_NACHARBEIT, neuer, rang=rang,
                        verworfen=verworfen)


def stand(db_pfad: str) -> dict:
    with pg.verbinden(db_pfad) as conn:
        _tabellen_sichern(conn)
        profile = conn.execute('SELECT COUNT(*) FROM "profile_settings"').fetchone()[0]
        zustaende = dict(conn.execute(
            'SELECT zustand, COUNT(*) FROM "adressfreigabe" GROUP BY zustand').fetchall())
        schlange = conn.execute(
            'SELECT COUNT(*) FROM "wartschlange" WHERE zustand = ?',
            (ZUSTAND_BEREIT,)).fetchone()[0]
        kandidaten = conn.execute('SELECT COUNT(*) FROM "adresskandidaten"').fetchone()[0]
        verworfen = conn.execute(
            'SELECT COUNT(*) FROM "adresskandidaten" WHERE ampel IS NOT NULL'
            ' AND ampel != ?', (AMPEL_FREI,)).fetchone()[0]
    entwurf = profile - sum(zustaende.values())
    return {"profile": profile, "entwurf": max(0, entwurf),
            "nacharbeit": zustaende.get(ZUSTAND_NACHARBEIT, 0),
            "bereit": zustaende.get(ZUSTAND_BEREIT, 0),
            "in_schlange": schlange, "kettenglieder": kandidaten,
            "davon_verworfen": verworfen}


# --- CLI ---------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="freigabe")
    unter = parser.add_subparsers(dest="befehl", required=True)

    for name in ("lauf", "nachpruefen", "entnehmen", "stand"):
        p = unter.add_parser(name)
        p.add_argument("--db", required=True)
        p.add_argument("--verlangen", default=MINDEST_STUFE, choices=("mx", "smtp"))
        p.add_argument("--gueltig-stunden", type=int, default=GUELTIG_STUNDEN)
        p.add_argument("--auch-unklar", action="store_true")
        p.add_argument("--kein-notfall", dest="notfall_mx", action="store_false")
        p.add_argument("--json", action="store_true")
        if name == "lauf":
            p.add_argument("--anzahl", type=int)
            p.add_argument("--threads", type=int, default=8)
            p.add_argument("--saat", type=int)
            p.add_argument("--still", action="store_true")

    p_ein = unter.add_parser("einzel")
    p_ein.add_argument("identity")
    p_ein.add_argument("--db", required=True)
    p_ein.add_argument("--verlangen", default=MINDEST_STUFE, choices=("mx", "smtp"))
    p_ein.add_argument("--auch-unklar", action="store_true")
    p_ein.add_argument("--kein-notfall", dest="notfall_mx", action="store_false")
    p_ein.add_argument("--trocken", action="store_true")
    p_ein.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.befehl == "stand":
        ergebnis = stand(args.db)
        print(json.dumps(ergebnis, ensure_ascii=False, indent=2) if args.json
              else "\n".join(f"{k}: {v}" for k, v in ergebnis.items()), flush=True)
        return 0

    if args.befehl == "entnehmen":
        daten = entnehmen(args.db, args.verlangen, args.gueltig_stunden,
                          args.auch_unklar, args.notfall_mx)
        if not daten:
            print("Wartschlange ist leer.", flush=True)
            return 1
        print(json.dumps(daten, ensure_ascii=False, indent=2) if args.json
              else f"{daten['identity']} {daten['adresse']} [{daten['ampel']}]", flush=True)
        return 0

    if args.befehl == "nachpruefen":
        zaehler = nachpruefen(args.db, args.verlangen, args.gueltig_stunden,
                              args.auch_unklar, args.notfall_mx)
        print(json.dumps(zaehler, ensure_ascii=False) if args.json
              else f"verlaengert={zaehler['verlaengert']} raus={zaehler['raus']}", flush=True)
        return 0

    if args.befehl == "einzel":
        ergebnis = freigeben(args.db, args.identity, stufe=args.verlangen,
                             auch_unklar=args.auch_unklar,
                             notfall_mx=args.notfall_mx, trocken=args.trocken)
        print(json.dumps(ergebnis, ensure_ascii=False, indent=2) if args.json
              else f"{ergebnis['identity']}: {ergebnis['zustand']} "
                   f"Glied {ergebnis['rang']} ({ergebnis['ampel']}: {ergebnis['grund']})",
              flush=True)
        return 0 if ergebnis.get("zustand") == ZUSTAND_BEREIT else 1

    lauf(args.db, getattr(args, "anzahl", None), args.verlangen,
         getattr(args, "threads", 8), args.gueltig_stunden, args.auch_unklar,
         args.notfall_mx, getattr(args, "saat", None), getattr(args, "still", False))
    return 0


if __name__ == "__main__":
    sys.exit(main())