# socialscraper/profile_settings.py
"""Tempo je Profil: eine Mitte plus Streuungsband.

Der Mensch ist nie gleich schnell. Deshalb:
  * Jeder Tempo-Wert hat eine Mitte (das typische Tempo dieser Person).
  * Um die Mitte liegt ein Band, aus dem tatsaechlich gezogen wird.
  * Die Bandgrenzen werden praktisch nie erreicht (Dreiecksverteilung mit
    Modus = Mitte).
  * Auch das Band selbst ist nicht fest: Breite und Lage streuen je Profil.

Werkzeugspanne (LIMITS) = was die Technik ueberhaupt kann.
Band = was dieser eine Mensch tut.

Wichtig: der Randabstand wird ZWEIMAL gedeckelt - gegen die Werkzeugspanne
UND gegen den Abstand zur Mitte. Sonst schiebt er bei kleinen Werten die
Untergrenze ueber die Mitte und das Band wird zusammengequetscht.
"""
from __future__ import annotations

import random

LIMITS = {
    "mouse_px_s": (260.0, 4600.0),
    "click_ms": (35.0, 950.0),
    "typing_cps": (1.0, 10.0),
}

TENDENZ = {
    "mouse_px_s": (900.0, 2600.0),
    "click_ms": (80.0, 300.0),
    "typing_cps": (2.2, 6.8),
}

MINDESTBREITE = 0.05
RANDANTEIL = 0.05
RANDHOECHST = 0.25
BREITEN_FAKTOR = (0.45, 2.40)
LAGE = (0.25, 0.75)
MITTE_STREUUNG = 0.06


def _mitte_ziehen(rng: random.Random, wert: str) -> float:
    unten, oben = TENDENZ[wert]
    return rng.triangular(unten, oben, (unten + oben) / 2.0)


def band_bauen(rng: random.Random, mitte: float,
               unten_werkzeug: float, oben_werkzeug: float) -> dict:
    """Baut ein Band um die Mitte, ohne die Werkzeuggrenzen zu verletzen."""
    mitte = float(mitte) * (1.0 + rng.uniform(-MITTE_STREUUNG, MITTE_STREUUNG))

    breite = max(mitte * MINDESTBREITE, mitte * rng.uniform(*BREITEN_FAKTOR))
    lage = rng.uniform(*LAGE)
    unten = mitte - breite * lage
    oben = mitte + breite * (1.0 - lage)

    # Randabstand: gedeckelt gegen die Spanne UND gegen den Abstand zur Mitte.
    rand_unten = min((oben_werkzeug - unten_werkzeug) * RANDANTEIL,
                     (mitte - unten_werkzeug) * 0.5,
                     mitte * RANDHOECHST)
    rand_oben = min((oben_werkzeug - unten_werkzeug) * RANDANTEIL,
                    (oben_werkzeug - mitte) * 0.5,
                    mitte * RANDHOECHST)

    if unten < unten_werkzeug + rand_unten:
        unten = unten_werkzeug + rand_unten
    if oben > oben_werkzeug - rand_oben:
        oben = oben_werkzeug - rand_oben
    if oben - unten < mitte * MINDESTBREITE:
        oben = unten + mitte * MINDESTBREITE

    return {"mitte": round(mitte, 4), "unten": round(unten, 4),
            "oben": round(oben, 4)}


def tempo_profil(rng: random.Random | None = None) -> dict:
    """Ein komplettes Tempo-Set fuer ein Profil: drei Baender."""
    rng = rng or random.Random()
    return {wert: band_bauen(rng, _mitte_ziehen(rng, wert), *LIMITS[wert])
            for wert in LIMITS}


def tempo_ziehen(rng: random.Random, band: dict) -> float:
    """Zieht ein konkretes Tempo aus dem Band. Grenzen fast nie erreicht."""
    return rng.triangular(band["unten"], band["oben"], band["mitte"])


def tempo_ziehen_profil(rng: random.Random, baender: dict) -> dict:
    return {wert: round(tempo_ziehen(rng, band), 4)
            for wert, band in baender.items()}


def pruefen(baender: dict) -> list:
    """Prueft ein Tempo-Set. Gemessen wird gegen die Mitte, nicht gegen die
    Werkzeugspanne."""
    meldungen = []
    for wert, band in (baender or {}).items():
        if wert not in LIMITS:
            meldungen.append(f"[tempo] unbekannter Wert: {wert}")
            continue
        unten_werkzeug, oben_werkzeug = LIMITS[wert]
        mitte, unten, oben = band.get("mitte"), band.get("unten"), band.get("oben")
        if None in (mitte, unten, oben):
            meldungen.append(f"[tempo] {wert}: Band unvollstaendig")
            continue
        if not (unten_werkzeug <= unten < oben <= oben_werkzeug):
            meldungen.append(f"[tempo] {wert}: Band ausserhalb der Werkzeugspanne")
        if not (unten <= mitte <= oben):
            meldungen.append(f"[tempo] {wert}: Mitte liegt nicht im Band")
        if oben - unten < mitte * MINDESTBREITE * 0.99:
            meldungen.append(f"[band] Band fuer {wert} zu schmal")
    return meldungen


def als_flach(baender: dict) -> dict:
    """Baender in einzelne Spalten flachklopfen, fuer die Datenbank."""
    flach = {}
    for wert, band in (baender or {}).items():
        for teil in ("mitte", "unten", "oben"):
            flach[f"{wert}_{teil}"] = band.get(teil)
    return flach