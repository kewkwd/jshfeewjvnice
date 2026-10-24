# socialscraper/profilgenerator.py
"""Erzeugt fertige Profildaten fuer neue Konten - aber nur als ENTWURF.

Jeder Datensatz bekommt eine AUSWEICHKETTE: die Hauptadresse zuerst, dahinter
Ausweichadressen fuer dieselbe Person. Alle Glieder sind gegen den ganzen
Vorrat eindeutig, also fuer diesen einen Datensatz reserviert.

Der Anbieter folgt einer TENDENZ ueber das Alter, nicht einer Grenze:
jeder Anbieter hat eine Glocke, jeder Mensch weicht davon ab. Nie 0, nie 1.

anlegen() ist ein vollstaendiger Reset fuer diese Kennung: Profil, Postfach,
Zugang und Kette werden neu geschrieben, eine alte Freigabe wird geloescht.
Damit ist ein zweiter Lauf immer wiederholbar.

Dieser Baustein schreibt NICHT in die Wartschlange. Das macht freigabe.py.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

from . import profile_settings
from .mail_pruefen import AMPEL_FREI, AMPEL_UNKLAR, Adresspruefer, Befund

QUELLE_NAMEN = "daten-namen"
QUELLE_STRASSEN = "daten-strassen"

ZUSTAND_ENTWURF = "entwurf"
AMPEL_VORLAEUFIG = "vorlaeufig"
GRUND_VORLAEUFIG = "Pruefung folgt bei Freigabe"

KANDIDATEN = 8

# Anbieter als Tendenz: (Mitte, Breite, Grundgewicht).
# Die Glocke liegt ueber dem Alter. Breit = der Anbieter kommt in jedem Alter
# vor. Nie genau null, also gibt es immer eine Restchance.
ANBIETER_KURVE = {
    "gmail.com": (21, 16, 1.60),
    "web.de": (33, 17, 0.75),
    "gmx.de": (41, 19, 0.80),
    "gmx.net": (41, 19, 0.30),
    "t-online.de": (48, 16, 0.42),
    "outlook.de": (26, 22, 0.35),
    "yahoo.de": (38, 22, 0.18),
    "posteo.de": (27, 16, 0.12),
}
ANBIETER = tuple(ANBIETER_KURVE)
ANBIETER_JUNG = ANBIETER
ANBIETER_ALT = ANBIETER

ALTER_RAUSCHEN = 6.0
ANBIETER_RAUSCHEN = 0.15
EIN_ANBIETER = 0.35
ZWEITANBIETER = 0.78

ALTER_KERN = 24
ALTER_AUSLAUF = 16
ALTER_SPANNE = (16, 52)

GERAETE = (("android", 0.46), ("beide", 0.11), ("browser", 0.43))
HEIMATWEGE = {"android": ("avd", "apk"), "beide": ("avd", "browser"),
              "browser": ("browser", "http")}

FALLBACK_VORNAMEN = (
    "Anna;w", "Lena;w", "Marie;w", "Sophie;w", "Emilia;w", "Mia;w",
    "Hannah;w", "Lea;w", "Johanna;w", "Nele;w", "Lina;w", "Clara;w",
    "Lukas;m", "Finn;m", "Jonas;m", "Leon;m", "Paul;m", "Max;m",
    "Felix;m", "Noah;m", "Elias;m", "Tim;m", "Jan;m", "Nico;m",
)
FALLBACK_NACHNAMEN = (
    "Mueller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer",
    "Wagner", "Becker", "Schulz", "Hoffmann", "Koch", "Bauer", "Richter",
    "Klein", "Wolf", "Neumann", "Schwarz", "Zimmermann", "Braun", "Krueger",
)
FALLBACK_STRASSEN = (
    "Hauptstrasse;10115;Berlin", "Gartenweg;50667;Koeln", "Bahnhofstrasse;80331;Muenchen",
    "Schulstrasse;20095;Hamburg", "Ringstrasse;60311;Frankfurt", "Am Anger;04109;Leipzig",
    "Lindenallee;01067;Dresden", "Feldstrasse;30159;Hannover", "Kirchgasse;70173;Stuttgart",
    "Muehlweg;28195;Bremen", "Bergstrasse;40213;Duesseldorf", "Talstrasse;55116;Mainz",
)

TRENNER = re.compile(r"[;|\t]+")
PASSWORT_ZEICHEN = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

UMLAUTE = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"),
           ("á", "a"), ("à", "a"), ("â", "a"), ("é", "e"), ("è", "e"),
           ("ê", "e"), ("í", "i"), ("ó", "o"), ("ô", "o"), ("ú", "u"),
           ("ç", "c"), ("ñ", "n"))


class ProfilError(Exception):
    """Etwas am Datensatz passt nicht."""


# --- Text putzen -------------------------------------------------------------

def saeubern(text) -> str:
    text = str(text or "").lower()
    for a, b in UMLAUTE:
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", text)


def saeubern_weich(text) -> str:
    """Wie saeubern, aber der Bindestrich bleibt (Hans-Dieter)."""
    text = str(text or "").lower()
    for a, b in UMLAUTE:
        text = text.replace(a, b)
    return re.sub(r"[^a-z0-9\-]", "", text).strip("-")


# --- Quellen lesen -----------------------------------------------------------

def verbinden(db_pfad: str):
    conn = sqlite3.connect(db_pfad, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _tabelle_der_bloecke(conn):
    for name in ("datenblock", "datenbloecke", "bloecke"):
        try:
            conn.execute(f'SELECT 1 FROM "{name}" LIMIT 1')
            return name
        except sqlite3.OperationalError:
            continue
    return None


def block_lesen(conn, block: str) -> list:
    tabelle = _tabelle_der_bloecke(conn)
    if not tabelle:
        return []
    spalten = {z[1] for z in conn.execute(f'PRAGMA table_info("{tabelle}")')}
    spalte_block = "block" if "block" in spalten else ("name" if "name" in spalten else None)
    spalte_wert = next((s for s in ("wert", "value", "inhalt", "text") if s in spalten), None)
    if not spalte_block or not spalte_wert:
        return []
    zeilen = conn.execute(
        f'SELECT "{spalte_wert}" FROM "{tabelle}" WHERE "{spalte_block}" = ?',
        (block,)).fetchall()
    return [z[0] for z in zeilen if z[0]]


def _zerlegen(text: str) -> list:
    return [t.strip() for t in TRENNER.split(str(text).strip()) if t.strip()]


def namen_lesen(quellen: dict):
    vornamen, nachnamen = [], []
    for roh in quellen.get("namen", []):
        teile = _zerlegen(roh)
        if not teile:
            continue
        if len(teile) >= 3:
            vorname, nachname, geschlecht = teile[0], teile[1], teile[2].lower()
            nachnamen.append(nachname)
        elif len(teile) == 2:
            if teile[1].lower() in ("m", "w", "maennlich", "weiblich", "f", "d"):
                vorname, geschlecht = teile[0], teile[1].lower()
            else:
                vorname, geschlecht = teile[0], ""
                nachnamen.append(teile[1])
        else:
            vorname, geschlecht = teile[0], ""
        geschlecht = {"maennlich": "m", "weiblich": "w", "f": "w", "d": ""}.get(geschlecht, geschlecht)
        vornamen.append((vorname, geschlecht))

    if not vornamen:
        vornamen = [tuple(t.split(";")) for t in FALLBACK_VORNAMEN]
    if not nachnamen:
        nachnamen = list(FALLBACK_NACHNAMEN)
    return vornamen, nachnamen


def strassen_lesen(quellen: dict) -> list:
    strassen = []
    for roh in quellen.get("strassen", []):
        teile = _zerlegen(roh)
        if not teile:
            continue
        strassen.append((teile[0],
                         teile[1] if len(teile) > 1 else "",
                         teile[2] if len(teile) > 2 else ""))
    if not strassen:
        strassen = [tuple(t.split(";")) for t in FALLBACK_STRASSEN]
    return strassen


def quellen_laden(db_pfad):
    if not db_pfad:
        return {}
    try:
        with verbinden(db_pfad) as conn:
            return {"namen": block_lesen(conn, QUELLE_NAMEN),
                    "strassen": block_lesen(conn, QUELLE_STRASSEN)}
    except sqlite3.Error:
        return {}


# --- Werte ziehen ------------------------------------------------------------

def geschlecht_ziehen(rng: random.Random) -> str:
    return "w" if rng.random() < 0.52 else "m"


def alter_ziehen(rng: random.Random) -> int:
    unten, oben = ALTER_SPANNE
    if rng.random() < 0.62:
        kern_unten = max(unten, ALTER_KERN - ALTER_AUSLAUF // 2)
        kern_oben = min(oben, ALTER_KERN + ALTER_AUSLAUF // 2)
        return int(rng.triangular(kern_unten, kern_oben, ALTER_KERN))
    seite = rng.choice((-1, 1))
    rand = ALTER_KERN + seite * rng.uniform(ALTER_AUSLAUF // 2, oben - ALTER_KERN)
    return int(max(unten, min(oben, rand)))


def geburtsdatum_ziehen(rng: random.Random, heute: date | None = None) -> date:
    heute = heute or date.today()
    alter = alter_ziehen(rng)
    tag = heute.replace(year=heute.year - alter)
    return tag - timedelta(days=rng.randint(0, 364))


def vorname_ziehen(rng: random.Random, vornamen: list, geschlecht: str):
    passend = [v for v, g in vornamen if not g or g == geschlecht]
    if not passend:
        passend = [v for v, _ in vornamen]
    return rng.choice(passend)


def passwort_bauen(rng: random.Random, laenge: int | None = None) -> str:
    laenge = laenge or rng.randint(11, 15)
    kern = "".join(rng.choice(PASSWORT_ZEICHEN) for _ in range(laenge - 3))
    zeichen = list(kern + rng.choice("23456789")
                   + rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")
                   + rng.choice("abcdefghijkmnopqrstuvwxyz"))
    rng.shuffle(zeichen)
    return "".join(zeichen)


def _gewichtet(rng: random.Random, paare) -> str:
    marke = rng.random()
    summe = 0.0
    for wert, gewicht in paare:
        summe += gewicht
        if marke <= summe:
            return wert
    return paare[-1][0]


def anbieter_gewichte(alter: int, rng: random.Random | None = None) -> dict:
    """Gewichte je Anbieter fuer dieses Alter. Nie null, immer Tendenz."""
    rng = rng or random.Random()
    verschoben = max(14.0, alter + rng.gauss(0.0, ALTER_RAUSCHEN))
    gewichte = {}
    for anbieter, (mitte, breite, grund) in ANBIETER_KURVE.items():
        abstand = (verschoben - mitte) / breite
        gewicht = grund * math.exp(-0.5 * abstand * abstand)
        gewicht *= 1.0 + rng.uniform(-ANBIETER_RAUSCHEN, ANBIETER_RAUSCHEN)
        gewichte[anbieter] = max(gewicht, 1e-4)
    return gewichte


def anbieter_ziehen(rng: random.Random, alter: int) -> str:
    """Zieht einen Anbieter passend zum Alter - als Tendenz, nicht als Grenze."""
    return _gewichtet(rng, list(anbieter_gewichte(alter, rng).items()))


def _jahr(person: dict) -> int:
    roh = str(person.get("geburtsdatum") or "")
    if len(roh) >= 4 and roh[:4].isdigit():
        return int(roh[:4])
    return date.today().year - int(person.get("alter") or ALTER_KERN)


def _alter(person: dict) -> int:
    return int(person.get("alter") or ALTER_KERN)


# --- Ausweichkette -----------------------------------------------------------

def kandidaten_bauen(rng: random.Random, vorname: str, nachname: str,
                     jahr: int, alter: int, vorhanden: set,
                     anzahl: int = KANDIDATEN, stufe: int = 1) -> list:
    """Baut die Kette. Reihenfolge = Reihenfolge des Versuchens.

    stufe 1: die naheliegenden Schreibweisen
    stufe 2: dieselben mit Jahr
    stufe 3: mit mehr Ziffern, wenn alles andere weg ist
    """
    v_mit = saeubern_weich(vorname)
    v = v_mit.replace("-", "")
    n = saeubern(nachname)
    if not v:
        v, v_mit = "max", "max"
    if not n:
        n = "mustermann"
    v_kurz = v[:1]
    jahr2 = str(jahr)[-2:]

    if stufe <= 1:
        formen = [f"{v_mit}-{n}", f"{v_mit}.{n}", f"{v}.{n}", f"{n}.{v}",
                  f"{v}_{n}", f"{v}{n}"]
    elif stufe == 2:
        formen = [f"{v}.{n}{jahr2}", f"{v}{n}{jahr2}", f"{n}{v}{jahr2}",
                  f"{v_mit}-{n}{jahr2}", f"{v_kurz}{n}{jahr2}", f"{n}.{v}{jahr2}"]
    else:
        formen = [f"{v}.{n}{rng.randint(10, 99)}", f"{v}{n}{rng.randint(100, 9999)}",
                  f"{n}{v_kurz}{rng.randint(100, 999)}", f"{v}-{n}{rng.randint(100, 999)}",
                  f"{v}.{n}{rng.randint(1000, 9999)}"]

    anbieter_a = anbieter_ziehen(rng, alter)
    anbieter_b = anbieter_ziehen(rng, alter)
    for _ in range(8):
        if anbieter_b != anbieter_a:
            break
        anbieter_b = anbieter_ziehen(rng, alter)
    if rng.random() < EIN_ANBIETER:
        anbieter_b = anbieter_a

    adressen = []
    for i, form in enumerate(formen):
        anbieter = anbieter_a if (i == 0 or rng.random() < ZWEITANBIETER) else anbieter_b
        adresse = f"{form}@{anbieter}"
        if adresse in vorhanden or adresse in adressen:
            continue
        adressen.append(adresse)

    sicherung = 0
    while len(adressen) < anzahl and sicherung < anzahl * 30:
        sicherung += 1
        form = f"{v}.{n}{rng.randint(100, 99999)}"
        adresse = f"{form}@{rng.choice((anbieter_a, anbieter_b))}"
        if adresse in vorhanden or adresse in adressen:
            continue
        adressen.append(adresse)
    return adressen[:anzahl]


def kandidaten_tabelle(conn):
    conn.execute(
        'CREATE TABLE IF NOT EXISTS "adresskandidaten" ('
        ' identity TEXT NOT NULL, rang INTEGER NOT NULL, adresse TEXT NOT NULL,'
        ' ampel TEXT, grund TEXT, geprueft_am TEXT, in_benutzung INTEGER DEFAULT 0,'
        ' PRIMARY KEY (identity, rang))')


def kandidaten_lesen(conn, identity) -> list:
    try:
        zeilen = conn.execute(
            'SELECT rang, adresse, ampel, grund, geprueft_am, in_benutzung'
            ' FROM "adresskandidaten" WHERE identity = ? ORDER BY rang',
            (identity,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(z) for z in zeilen]


def kandidaten_schreiben(conn, identity, adressen):
    """Setzt die Kette neu (ersetzt eine alte)."""
    kandidaten_tabelle(conn)
    conn.execute('DELETE FROM "adresskandidaten" WHERE identity = ?', (identity,))
    for rang, adresse in enumerate(adressen):
        _schreiben(conn, "adresskandidaten", {
            "identity": identity, "rang": rang, "adresse": adresse,
            "ampel": None, "grund": None, "geprueft_am": None, "in_benutzung": 0})


def kandidaten_anlegen(conn, identity, person: dict, rng: random.Random,
                       vorhanden=None, anzahl: int = KANDIDATEN,
                       stufe: int = 1) -> list:
    """Haengt Glieder an, wenn noch keine da sind (fuer alte Profile)."""
    kandidaten_tabelle(conn)
    bereits = kandidaten_lesen(conn, identity)
    pool = set(vorhanden or ()) | {z["adresse"] for z in bereits}
    adressen = kandidaten_bauen(rng, person.get("vorname", ""),
                                person.get("nachname", ""), _jahr(person),
                                _alter(person), pool, anzahl, stufe)
    rang_start = max((z["rang"] for z in bereits), default=-1) + 1
    for i, adresse in enumerate(adressen):
        _schreiben(conn, "adresskandidaten", {
            "identity": identity, "rang": rang_start + i, "adresse": adresse,
            "ampel": None, "grund": None, "geprueft_am": None, "in_benutzung": 0})
    return adressen


def kandidaten_erweitern(db_pfad: str, identity: str, person: dict,
                         rng: random.Random, anzahl: int = 4) -> list:
    """Haengt neue Glieder an, wenn die Kette leer gelaufen ist."""
    with verbinden(db_pfad) as conn:
        kandidaten_tabelle(conn)
        alt = kandidaten_lesen(conn, identity)
        pool = vorhandene_adressen_conn(conn, ohne=identity)
        pool |= {z["adresse"] for z in alt}
        rang_start = max((z["rang"] for z in alt), default=-1) + 1
        adressen = kandidaten_bauen(rng, person.get("vorname", ""),
                                    person.get("nachname", ""), _jahr(person),
                                    _alter(person), pool, anzahl, stufe=3)
        for i, adresse in enumerate(adressen):
            _schreiben(conn, "adresskandidaten", {
                "identity": identity, "rang": rang_start + i, "adresse": adresse,
                "ampel": None, "grund": None, "geprueft_am": None, "in_benutzung": 0})
        return adressen


# --- Profil bauen ------------------------------------------------------------

def profil_bauen(rng: random.Random | None = None, vorhandene=frozenset(),
                 pruefer=None, quellen: dict | None = None,
                 heute: date | None = None, kandidaten: int = KANDIDATEN) -> dict:
    rng = rng or random.Random()
    quellen = quellen if quellen is not None else {}
    vornamen, nachnamen = namen_lesen(quellen)
    strassen = strassen_lesen(quellen)

    geschlecht = geschlecht_ziehen(rng)
    vorname = vorname_ziehen(rng, vornamen, geschlecht)
    nachname = rng.choice(nachnamen)
    geburt = geburtsdatum_ziehen(rng, heute)
    strasse, plz, ort = rng.choice(strassen)

    pool = set(vorhandene)
    alter = _alter_am(geburt, heute or date.today())
    kette = kandidaten_bauen(rng, vorname, nachname, geburt.year, alter,
                             pool, kandidaten)
    if not kette:
        raise ProfilError("keine Adresse gefunden")
    adresse = kette[0]

    stufe = getattr(pruefer, "stufe", "keine") if pruefer is not None else "keine"
    scharf = stufe not in ("keine", "formal")
    ampel, grund = AMPEL_VORLAEUFIG, GRUND_VORLAEUFIG
    if scharf:
        befund = pruefer.befund(adresse)
        ampel, grund = befund.ampel, befund.grund

    geraet = _gewichtet(rng, GERAETE)

    return {
        "vorname": vorname,
        "nachname": nachname,
        "geschlecht": geschlecht,
        "geburtsdatum": geburt.isoformat(),
        "alter": alter,
        "strasse": strasse,
        "hausnummer": str(rng.randint(1, 189)),
        "plz": plz,
        "ort": ort,
        "land": "DE",
        "adresse": adresse,
        "kandidaten": kette,
        "adresse_ampel": ampel,
        "adresse_grund": grund,
        "passwort": passwort_bauen(rng),
        "benutzer": f"{vorname.lower()}.{nachname.lower()}{rng.randint(10, 9999)}",
        "geraet": geraet,
        "heimatweg": rng.choice(HEIMATWEGE[geraet]),
        "tempo": profile_settings.tempo_profil(rng),
    }


def _alter_am(geburt: date, stichtag: date) -> int:
    return stichtag.year - geburt.year - ((stichtag.month, stichtag.day) < (geburt.month, geburt.day))


# --- Vorrat ------------------------------------------------------------------

def vorhandene_adressen_conn(conn, ohne: str | None = None) -> set:
    adressen = set()
    for tabelle in ("postfaecher", "account_secrets", "adresskandidaten"):
        try:
            zeilen = conn.execute(
                f'SELECT identity, adresse FROM "{tabelle}"'
                f' WHERE adresse IS NOT NULL').fetchall()
        except sqlite3.OperationalError:
            continue
        for zeile in zeilen:
            if ohne and zeile["identity"] == ohne:
                continue
            adressen.add(zeile["adresse"])
    return adressen


def vorhandene_adressen(db_pfad, ohne: str | None = None) -> set:
    """Alle vergebenen und reservierten Adressen. Die eigene Kennung wird
    ausgeschlossen, damit ein zweiter Lauf nicht gegen sich selbst laeuft."""
    if not db_pfad:
        return set()
    try:
        with verbinden(db_pfad) as conn:
            return vorhandene_adressen_conn(conn, ohne)
    except sqlite3.Error:
        return set()


# --- Schreiben ---------------------------------------------------------------

def _spalten(conn, tabelle: str) -> set:
    try:
        return {z[1] for z in conn.execute(f'PRAGMA table_info("{tabelle}")')}
    except sqlite3.OperationalError:
        return set()


def _schreiben(conn, tabelle: str, werte: dict):
    """Schreibt nur die Spalten, die es wirklich gibt."""
    spalten = _spalten(conn, tabelle)
    if not spalten:
        liste = ", ".join(f'"{k}"' for k in werte)
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{tabelle}" ({liste})')
        spalten = set(werte)
    nutz = {k: v for k, v in werte.items() if k in spalten}
    if not nutz:
        return
    namen = ", ".join(f'"{k}"' for k in nutz)
    platz = ", ".join("?" for _ in nutz)
    conn.execute(f'INSERT OR REPLACE INTO "{tabelle}" ({namen}) VALUES ({platz})',
                 list(nutz.values()))


def _leeren(conn, identity):
    """Alte Freigabe und alte Schlange weg - der Datensatz ist wieder Entwurf."""
    for tabelle in ("adressfreigabe", "wartschlange"):
        try:
            conn.execute(f'DELETE FROM "{tabelle}" WHERE identity = ?', (identity,))
        except sqlite3.OperationalError:
            pass


def anlegen(db_pfad: str, identity: str, rng: random.Random | None = None,
            pruefer=None, quellen: dict | None = None,
            schreiben: bool = True, heute: date | None = None,
            kandidaten: int = KANDIDATEN) -> dict:
    """Legt ein Profil als ENTWURF an, samt Ausweichkette.

    Vollstaendiger Reset fuer diese Kennung: zweimal aufrufen ist erlaubt.
    """
    rng = rng or random.Random()
    quellen = quellen if quellen is not None else quellen_laden(db_pfad)
    with verbinden(db_pfad) as conn:
        vorhanden = vorhandene_adressen_conn(conn, ohne=identity)
    daten = profil_bauen(rng, vorhanden, pruefer, quellen, heute, kandidaten)
    daten["identity"] = identity
    daten["zeitpunkt"] = datetime.now(timezone.utc).isoformat()
    daten["zustand"] = ZUSTAND_ENTWURF

    if not schreiben:
        return daten

    person = {k: daten[k] for k in (
        "identity", "vorname", "nachname", "geschlecht", "geburtsdatum", "alter",
        "strasse", "hausnummer", "plz", "ort", "land", "geraet", "heimatweg",
        "zeitpunkt")}
    person.update(profile_settings.als_flach(daten["tempo"]))

    with verbinden(db_pfad) as conn:
        _schreiben(conn, "profile_settings", person)
        _schreiben(conn, "postfaecher", {
            "identity": identity, "adresse": daten["adresse"],
            "passwort": daten["passwort"], "zustand": ZUSTAND_ENTWURF,
            "ampel": daten["adresse_ampel"], "grund": daten["adresse_grund"],
            "kandidaten_rest": len(daten["kandidaten"]) - 1,
            "zeitpunkt": daten["zeitpunkt"]})
        _schreiben(conn, "account_secrets", {
            "identity": identity, "benutzer": daten["benutzer"],
            "passwort": daten["passwort"], "zeitpunkt": daten["zeitpunkt"]})
        kandidaten_schreiben(conn, identity, daten["kandidaten"])
        _leeren(conn, identity)
    return daten


def naechste_nummer(db_pfad: str, praefix: str) -> int:
    hoechste = 0
    try:
        with verbinden(db_pfad) as conn:
            for zeile in conn.execute("SELECT identity FROM profile_settings").fetchall():
                kennung = zeile["identity"] or ""
                if kennung.startswith(praefix):
                    rest = kennung[len(praefix):]
                    if rest.isdigit():
                        hoechste = max(hoechste, int(rest))
    except sqlite3.Error:
        pass
    return hoechste + 1


def stapel(db_pfad: str, anzahl: int, praefix: str = "tiktok_konto_",
           stellen: int = 4, pruefer=None, saat: int | None = None,
           schreiben: bool = True, kandidaten: int = KANDIDATEN) -> list:
    """Legt NEUE Kennungen an, direkt hinter der hoechsten vorhandenen."""
    rng = random.Random(saat)
    quellen = quellen_laden(db_pfad)
    start = naechste_nummer(db_pfad, praefix)
    ergebnisse = []
    for nummer in range(start, start + anzahl):
        identity = f"{praefix}{nummer:0{stellen}d}"
        daten = anlegen(db_pfad, identity, rng, pruefer, quellen, schreiben,
                        kandidaten=kandidaten)
        ergebnisse.append(daten)
        print(f"[entwurf] {identity}: {daten['vorname']} {daten['nachname']}, "
              f"{daten['alter']} J., {daten['geraet']}/{daten['heimatweg']}, "
              f"{daten['adresse']} (+{len(daten['kandidaten']) - 1} Ausweich)", flush=True)
    return ergebnisse


def erneuern(db_pfad: str, praefix: str = "tiktok_konto_",
             von: int | None = None, bis: int | None = None,
             pruefer=None, saat: int | None = None,
             kandidaten: int = KANDIDATEN, schreiben: bool = True) -> list:
    """Schreibt VORHANDENE Profile neu - gleiche Kennungen, frische Daten."""
    rng = random.Random(saat)
    quellen = quellen_laden(db_pfad)
    kennungen = []
    with verbinden(db_pfad) as conn:
        for zeile in conn.execute("SELECT identity FROM profile_settings ORDER BY identity"):
            kennung = zeile["identity"] or ""
            if praefix and not kennung.startswith(praefix):
                continue
            rest = kennung[len(praefix):]
            if rest.isdigit():
                nummer = int(rest)
                if von is not None and nummer < von:
                    continue
                if bis is not None and nummer > bis:
                    continue
            kennungen.append(kennung)

    ergebnisse = []
    for kennung in kennungen:
        daten = anlegen(db_pfad, kennung, rng, pruefer, quellen, schreiben,
                        kandidaten=kandidaten)
        ergebnisse.append(daten)
        print(f"[neu] {kennung}: {daten['alter']} J., {daten['adresse']}", flush=True)
    return ergebnisse


def bericht(ergebnisse: list) -> str:
    from collections import Counter, defaultdict
    alter = Counter(e["alter"] // 5 * 5 for e in ergebnisse)
    geschlecht = Counter(e["geschlecht"] for e in ergebnisse)
    geraet = Counter(e["geraet"] for e in ergebnisse)
    anbieter = Counter(e["adresse"].split("@")[-1] for e in ergebnisse)
    ketten = Counter(len(e["kandidaten"]) for e in ergebnisse)

    baender = ((16, 24), (25, 32), (33, 41), (42, 52))
    zellen = defaultdict(Counter)
    for e in ergebnisse:
        for lo, hi in baender:
            if lo <= e["alter"] <= hi:
                zellen[(lo, hi)][e["adresse"].split("@")[-1]] += 1
                break

    zeilen = [
        f"Profile: {len(ergebnisse)}",
        "Alter (5er): " + ", ".join(f"{k}-{k+4}:{v}" for k, v in sorted(alter.items())),
        "Geschlecht: " + ", ".join(f"{k}:{v}" for k, v in geschlecht.items()),
        "Geraet: " + ", ".join(f"{k}:{v}" for k, v in geraet.items()),
        "Anbieter erste Wahl: " + ", ".join(f"{k}:{v}" for k, v in anbieter.most_common()),
        "Kettenlaenge: " + ", ".join(f"{k}:{v}" for k, v in sorted(ketten.items())),
        "Alter x Anbieter (Anteil, zeigt die Tendenz):",
    ]
    for lo, hi in baender:
        zaehler = zellen.get((lo, hi))
        if not zaehler:
            continue
        gesamt = sum(zaehler.values())
        teile = ", ".join(f"{a}:{100 * v / gesamt:.0f}%" for a, v in zaehler.most_common(4))
        zeilen.append(f"  {lo}-{hi} (n={gesamt}): {teile}")
    return "\n".join(zeilen)


# --- CLI ---------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="profilgenerator")
    unter = parser.add_subparsers(dest="befehl", required=True)

    p_stapel = unter.add_parser("stapel", help="neue Entwuerfe anlegen")
    p_stapel.add_argument("anzahl", type=int)
    p_stapel.add_argument("--db", required=True)
    p_stapel.add_argument("--praefix", default="tiktok_konto_")
    p_stapel.add_argument("--stellen", type=int, default=4)
    p_stapel.add_argument("--pruefen", default="formal",
                          choices=("keine", "formal", "mx", "smtp"))
    p_stapel.add_argument("--threads", type=int, default=8)
    p_stapel.add_argument("--kandidaten", type=int, default=KANDIDATEN)
    p_stapel.add_argument("--saat", type=int)

    p_ern = unter.add_parser("erneuern", help="vorhandene Profile neu schreiben")
    p_ern.add_argument("--db", required=True)
    p_ern.add_argument("--praefix", default="tiktok_konto_")
    p_ern.add_argument("--von", type=int)
    p_ern.add_argument("--bis", type=int)
    p_ern.add_argument("--pruefen", default="formal",
                       choices=("keine", "formal", "mx", "smtp"))
    p_ern.add_argument("--kandidaten", type=int, default=KANDIDATEN)
    p_ern.add_argument("--saat", type=int)

    p_eins = unter.add_parser("anlegen", help="ein einzelner Entwurf")
    p_eins.add_argument("identity")
    p_eins.add_argument("--db", required=True)
    p_eins.add_argument("--pruefen", default="formal",
                        choices=("keine", "formal", "mx", "smtp"))
    p_eins.add_argument("--kandidaten", type=int, default=KANDIDATEN)
    p_eins.add_argument("--trocken", action="store_true")
    p_eins.add_argument("--json", action="store_true")

    p_anb = unter.add_parser("anbieter", help="Anbieterverteilung zeigen")
    p_anb.add_argument("--alter", type=int)
    p_anb.add_argument("--male", type=int, default=5000)

    args = parser.parse_args(argv)

    if args.befehl == "anbieter":
        rng = random.Random(1)
        for alter in ([args.alter] if args.alter else range(16, 53, 4)):
            zaehler = {}
            for _ in range(args.male):
                a = anbieter_ziehen(rng, alter)
                zaehler[a] = zaehler.get(a, 0) + 1
            teile = ", ".join(f"{a}:{100 * v / args.male:.1f}%"
                              for a, v in sorted(zaehler.items(), key=lambda x: -x[1]))
            print(f"Alter {alter:2d}: {teile}", flush=True)
        return 0

    if args.befehl == "stapel":
        pruefer = Adresspruefer(args.pruefen, args.db, args.threads) \
            if args.pruefen != "keine" else None
        ergebnisse = stapel(args.db, args.anzahl, args.praefix, args.stellen,
                            pruefer, args.saat, kandidaten=args.kandidaten)
        print(bericht(ergebnisse), flush=True)
        return 0

    if args.befehl == "erneuern":
        ergebnisse = erneuern(args.db, args.praefix, args.von, args.bis,
                              saat=args.saat, kandidaten=args.kandidaten)
        print(bericht(ergebnisse), flush=True)
        return 0

    pruefer = Adresspruefer(args.pruefen, args.db) if args.pruefen != "keine" else None
    daten = anlegen(args.db, args.identity, pruefer=pruefer,
                    schreiben=not args.trocken, kandidaten=args.kandidaten)
    if args.json:
        print(json.dumps({k: v for k, v in daten.items() if k != "tempo"},
                         ensure_ascii=False, indent=2, default=str), flush=True)
    else:
        print(f"{daten['identity']}: {daten['vorname']} {daten['nachname']}, "
              f"{daten['alter']} J.", flush=True)
        for rang, adresse in enumerate(daten["kandidaten"]):
            print(f"  {rang}: {adresse}", flush=True)
        for meldung in profile_settings.pruefen(daten["tempo"]):
            print(meldung, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())