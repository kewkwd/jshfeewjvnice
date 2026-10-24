# socialscraper/mail_pruefen.py
"""Prueft, ob eine E-Mail-Adresse frei bzw. ueberhaupt erreichbar ist.

Drei Stufen, aufsteigend streng:

    "keine"  : nichts tun, alles gilt als frei
    "formal" : Syntax + Wegwerf-Domains, kein Netz, sofort
    "mx"     : zusaetzlich DNS - hat die Domain ueberhaupt einen Mailserver?
    "smtp"   : zusaetzlich Handshake mit dem Mailserver (Port 25).
               Nimmt er die Adresse an (250 = vergeben) oder lehnt er ab
               (550 = frei)?

Ampel je Adresse:

    "frei"      - nach bestem Wissen nicht belegt
    "belegt"    - Adresse existiert schon
    "unklar"    - Server antwortet unbestimmt (catch-all, Greylisting, Timeout)
    "ungueltig" - Syntax kaputt oder Domain nimmt gar keine Post an

Wichtig und ehrlich:
  * "frei" heisst nicht "gehoert uns", sondern "der fremde Server hat sie
    zum Pruefzeitpunkt nicht als belegt angenommen".
  * Ist Port 25 gesperrt, bleibt die Pruefung auf "mx" stehen und meldet
    "unklar" mit Vermerk. Nichts wird stillschweigend als frei verbucht.
  * Bei catch-all-Domains ist eine Unterscheidung unmoeglich -> "unklar".
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import random
import re
import smtplib
import socket
import sqlite3
import string
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

STUFEN = ("keine", "formal", "mx", "smtp")
STUFEN_RANG = {"keine": 0, "formal": 1, "mx": 2, "smtp": 3}

AMPEL_FREI = "frei"
AMPEL_BELEGT = "belegt"
AMPEL_UNKLAR = "unklar"
AMPEL_UNGUELTIG = "ungueltig"

ABSENDER = "pruefer@example.com"

SYNTAX = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._%+\-]{0,63}"
    r"@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+$"
)

WEGWERF = frozenset({
    "mailinator.com", "trashmail.com", "trashmail.de", "guerrillamail.com",
    "10minutemail.com", "temp-mail.org", "yopmail.com", "wegwerfemail.de",
    "throwawaymail.com", "sharklasers.com", "getnada.com", "dispostable.com",
})


@dataclass
class Befund:
    """Ergebnis einer Pruefung. Immer mit Grund, nie nur ein Ja/Nein."""
    adresse: str
    stufe: str
    ampel: str
    grund: str = ""
    dauer_ms: int = 0
    aus_cache: bool = False

    @property
    def brauchbar(self) -> bool:
        return self.ampel in (AMPEL_FREI, AMPEL_UNKLAR)

    @property
    def belegt(self) -> bool:
        return self.ampel == AMPEL_BELEGT

    def als_dict(self) -> dict:
        return {"adresse": self.adresse, "stufe": self.stufe, "ampel": self.ampel,
                "grund": self.grund, "dauer_ms": self.dauer_ms,
                "aus_cache": self.aus_cache}

    def kurz(self) -> str:
        return f"{self.adresse} -> {self.ampel}" + (f" ({self.grund})" if self.grund else "")


def domain_von(adresse: str) -> str:
    return adresse.rsplit("@", 1)[-1].strip().lower() if "@" in adresse else ""


def syntax_ok(adresse: str) -> bool:
    if not adresse or len(adresse) > 254 or not SYNTAX.match(adresse):
        return False
    name, _ = adresse.rsplit("@", 1)
    if name.startswith(".") or name.endswith(".") or ".." in name:
        return False
    return True


def _mx_dnspython(dom: str, timeout: float):
    """MX ueber dnspython. [] = sicher nichts, None = konnten nicht fragen."""
    try:
        import dns.resolver  # type: ignore
    except Exception:
        return None
    try:
        antwort = dns.resolver.resolve(dom, "MX", lifetime=timeout)
    except Exception as fehler:
        if type(fehler).__name__ in ("NXDOMAIN", "NoAnswer", "NoNameservers"):
            return []
        return None
    return [str(r.exchange).rstrip(".") for r in sorted(antwort, key=lambda r: r.preference)]


def _mx_nslookup(dom: str, timeout: float) -> list:
    try:
        roh = subprocess.run(["nslookup", "-type=MX", dom],
                             capture_output=True, text=True, timeout=timeout + 5)
    except Exception:
        return []
    hosts = []
    for zeile in roh.stdout.splitlines():
        if "mail exchanger" in zeile.lower():
            host = zeile.split("=", 1)[-1].strip().split()[-1].rstrip(".")
            if host and host not in hosts:
                hosts.append(host)
    return hosts


def mx_holen(dom: str, timeout: float = 5.0) -> list:
    """Mailserver einer Domain. Leer = die Domain nimmt keine Post an."""
    hosts = _mx_dnspython(dom, timeout)
    if hosts is None:
        hosts = _mx_nslookup(dom, timeout)
    return hosts


def host_hat_a(dom: str, timeout: float = 5.0) -> bool:
    """Ohne MX gilt laut RFC der A-Record als Mailserver."""
    try:
        socket.gethostbyname(dom)
        return True
    except Exception:
        return False


def smtp_befragen(host: str, adresse: str, timeout: float = 10.0,
                  absender: str = ABSENDER):
    """RCPT ohne Versand. Gibt (ampel, meldung) zurueck."""
    server = None
    try:
        server = smtplib.SMTP(host, 25, timeout=timeout)
        server.ehlo_or_helo_if_needed()
        try:
            server.mail(absender)
        except smtplib.SMTPException:
            pass
        try:
            code, meldung = server.rcpt(adresse)
            if code in (250, 251):
                return AMPEL_BELEGT, f"{code} {meldung}"
            return AMPEL_UNKLAR, f"{code} {meldung}"
        except smtplib.SMTPRecipientsRefused as fehler:
            code, meldung = list(fehler.recipients.values())[0]
            if code == 550:
                return AMPEL_FREI, f"{code} {meldung}"
            return AMPEL_UNKLAR, f"{code} {meldung}"
    except (socket.timeout, TimeoutError):
        return AMPEL_UNKLAR, "SMTP Zeitueberschreitung"
    except (ConnectionRefusedError, OSError) as fehler:
        return AMPEL_UNKLAR, f"SMTP nicht erreichbar ({type(fehler).__name__})"
    except smtplib.SMTPException as fehler:
        return AMPEL_UNKLAR, f"SMTP Fehler ({type(fehler).__name__})"
    finally:
        try:
            if server:
                server.quit()
        except Exception:
            pass


def ist_catch_all(host: str, dom: str, timeout: float = 10.0, rng=None) -> bool:
    """Fragt eine frei erfundene Adresse. Sagt der Server auch ja, ist alles ja."""
    rng = rng or random.Random()
    zufall = "".join(rng.choice(string.ascii_lowercase) for _ in range(16))
    ampel, _ = smtp_befragen(host, f"{zufall}@{dom}", timeout)
    return ampel == AMPEL_BELEGT


def _fertig(adresse, stufe, ampel, grund, start) -> Befund:
    return Befund(adresse=adresse, stufe=stufe, ampel=ampel, grund=grund,
                  dauer_ms=int((time.monotonic() - start) * 1000))


def pruefen(adresse: str, stufe: str = "mx", timeout: float = 10.0,
            merker: dict | None = None, mx_fn=None, smtp_fn=None,
            ca_fn=None, a_fn=None) -> Befund:
    """Prueft eine Adresse. Die *_fn-Parameter erlauben Fakes in Tests."""
    start = time.monotonic()
    adresse = (adresse or "").strip()
    if stufe not in STUFEN:
        raise ValueError(f"unbekannte Stufe: {stufe}")
    if stufe == "keine":
        return _fertig(adresse, stufe, AMPEL_FREI, "nicht geprueft", start)

    mx_fn = mx_fn or mx_holen
    smtp_fn = smtp_fn or smtp_befragen
    ca_fn = ca_fn or ist_catch_all
    a_fn = a_fn or host_hat_a

    if not syntax_ok(adresse):
        return _fertig(adresse, stufe, AMPEL_UNGUELTIG, "Syntax", start)

    dom = domain_von(adresse)
    if dom in WEGWERF:
        return _fertig(adresse, stufe, AMPEL_UNKLAR, "Wegwerf-Domain", start)

    hosts = mx_fn(dom, timeout)
    nur_a = False
    if not hosts:
        if a_fn(dom, timeout):
            hosts = [dom]
            nur_a = True
        else:
            return _fertig(adresse, stufe, AMPEL_UNGUELTIG,
                           "Domain nimmt keine Post an", start)

    if stufe == "mx":
        grund = "nur A-Record, kein MX" if nur_a else "MX vorhanden"
        return _fertig(adresse, stufe, AMPEL_FREI, grund, start)

    letzter = (AMPEL_UNKLAR, "kein Mailserver hat geantwortet")
    for host in hosts[:2]:
        ampel, meldung = smtp_fn(host, adresse, timeout)
        if ampel == AMPEL_BELEGT and merker is not None:
            if host not in merker:
                merker[host] = ca_fn(host, dom, timeout)
            if merker[host]:
                return _fertig(adresse, stufe, AMPEL_UNKLAR,
                               f"catch-all ({meldung})", start)
        if ampel in (AMPEL_BELEGT, AMPEL_FREI):
            return _fertig(adresse, stufe, ampel, meldung, start)
        letzter = (ampel, meldung)
    return _fertig(adresse, stufe, letzter[0], letzter[1], start)


class Cache:
    """Kurzes Gedaechtnis, damit wir nicht 500-mal dieselbe Domain fragen."""

    def __init__(self, db_pfad: str, ttl_tage: int = 21):
        self.db_pfad = db_pfad
        self.ttl = timedelta(days=ttl_tage)
        self.ttl_belegt = timedelta(days=90)
        self._anlegen()

    def _verbinden(self):
        conn = sqlite3.connect(self.db_pfad, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _anlegen(self):
        with self._verbinden() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS adresspruefung ("
                " adresse TEXT PRIMARY KEY, stufe TEXT NOT NULL,"
                " ampel TEXT NOT NULL, grund TEXT, zeitpunkt TEXT NOT NULL)")

    def holen(self, adresse: str, stufe: str):
        with self._verbinden() as conn:
            zeile = conn.execute(
                "SELECT stufe, ampel, grund, zeitpunkt FROM adresspruefung"
                " WHERE adresse = ?", (adresse,)).fetchone()
        if not zeile:
            return None
        g_stufe, ampel, grund, zeitpunkt = zeile
        if STUFEN_RANG.get(stufe, 0) > STUFEN_RANG.get(g_stufe, 0):
            return None
        try:
            wann = datetime.fromisoformat(zeitpunkt)
        except Exception:
            return None
        if datetime.now(timezone.utc) - wann > (self.ttl_belegt if ampel == AMPEL_BELEGT else self.ttl):
            return None
        return Befund(adresse=adresse, stufe=g_stufe, ampel=ampel, grund=grund,
                      aus_cache=True)

    def merken(self, befund: Befund):
        if befund.stufe == "keine":
            return
        with self._verbinden() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO adresspruefung"
                " (adresse, stufe, ampel, grund, zeitpunkt) VALUES (?,?,?,?,?)",
                (befund.adresse, befund.stufe, befund.ampel, befund.grund,
                 datetime.now(timezone.utc).isoformat()))


class Adresspruefer:
    """Buendelt Stufe, Cache und Gleichzeitigkeit fuer den Generator."""

    def __init__(self, stufe: str = "mx", db_pfad: str | None = None,
                 threads: int = 8, ttl_tage: int = 21, timeout: float = 10.0):
        if stufe not in STUFEN:
            raise ValueError(f"unbekannte Stufe: {stufe}")
        self.stufe = stufe
        self.threads = max(1, int(threads))
        self.timeout = timeout
        self.merker: dict = {}
        self.cache = Cache(db_pfad, ttl_tage) if (db_pfad and stufe != "keine") else None
        self.zaehlerstand = {AMPEL_FREI: 0, AMPEL_BELEGT: 0,
                             AMPEL_UNKLAR: 0, AMPEL_UNGUELTIG: 0}

    def befund(self, adresse: str) -> Befund:
        if self.cache:
            alt = self.cache.holen(adresse, self.stufe)
            if alt:
                self.zaehlerstand[alt.ampel] = self.zaehlerstand.get(alt.ampel, 0) + 1
                return alt
        ergebnis = pruefen(adresse, self.stufe, self.timeout, self.merker)
        if self.cache:
            self.cache.merken(ergebnis)
        self.zaehlerstand[ergebnis.ampel] = self.zaehlerstand.get(ergebnis.ampel, 0) + 1
        return ergebnis

    def befund_viele(self, adressen) -> list:
        adressen = list(adressen)
        if self.threads == 1 or len(adressen) < 2:
            return [self.befund(a) for a in adressen]
        with futures.ThreadPoolExecutor(max_workers=self.threads) as pool:
            return list(pool.map(self.befund, adressen))

    def bericht(self) -> str:
        teile = [f"{k}={v}" for k, v in self.zaehlerstand.items() if v]
        return f"Pruefung ({self.stufe}): " + (", ".join(teile) or "nichts geprueft")


def adressen_aus_db(pfad: str) -> list:
    """Alle bekannten Adressen aus der Datenbank, fuer den Nachlauf."""
    conn = sqlite3.connect(pfad, timeout=30)
    try:
        for tabelle in ("postfaecher", "account_secrets"):
            try:
                zeilen = conn.execute(
                    f'SELECT adresse FROM "{tabelle}" WHERE adresse IS NOT NULL'
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            adressen = [z[0] for z in zeilen if z[0]]
            if adressen:
                return adressen
        return []
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="mail_pruefen", description="E-Mail-Adressen wirklich pruefen.")
    parser.add_argument("adressen", nargs="*", help="zu pruefende Adressen")
    parser.add_argument("--datei", help="Datei mit einer Adresse je Zeile")
    parser.add_argument("--db", help="Datenbank mit Adressen und fuer den Cache")
    parser.add_argument("--stufe", default="mx", choices=STUFEN)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--ttl", type=int, default=21, help="Cache in Tagen")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    adressen = list(args.adressen)
    if args.datei:
        with open(args.datei, encoding="utf-8") as datei:
            adressen += [z.strip() for z in datei if z.strip()]
    if not adressen and args.db:
        adressen = adressen_aus_db(args.db)
    if not adressen:
        print("Keine Adressen angegeben.", flush=True)
        return 2

    pruefer = Adresspruefer(args.stufe, args.db, args.threads, args.ttl, args.timeout)
    befunde = pruefer.befund_viele(adressen)
    if args.json:
        print(json.dumps([b.als_dict() for b in befunde],
                         ensure_ascii=False, indent=2), flush=True)
    else:
        for befund in befunde:
            print(befund.kurz(), flush=True)
        print(pruefer.bericht(), flush=True)

    if pruefer.zaehlerstand.get(AMPEL_BELEGT) or pruefer.zaehlerstand.get(AMPEL_UNGUELTIG):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())