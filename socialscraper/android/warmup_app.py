"""Aufwärmen in der TikTok-App über ADB."""
import logging, random, time

log = logging.getLogger(__name__)
PAKET = 'com.zhiliaoapp.musically'
SCHLIESSEN = ['Schließen', 'Close', 'Später', 'Not now', 'Überspringen', 'Skip']


def sitzung(adb, konto, *, plan, erlaubt, melden=log.info, stop=None):
    adb.app_starten(PAKET)
    time.sleep(random.uniform(6, 10))
    for _ in range(4):
        if not _wegklicken(adb):
            break
    ende, erledigt = time.monotonic() + random.uniform(*plan['minuten']) * 60, 0
    for _ in range(plan['schritte']):
        if stop is not None and stop():
            break
        if time.monotonic() > ende:
            break
        try:
            handlung = _waehle(erlaubt)
            if handlung == 'scroll':
                _scroll(adb)
            elif handlung == 'video':
                _schauen(adb)
            elif handlung == 'like':
                _like(adb)
            elif handlung == 'folgen':
                _folgen(adb)
            elif handlung == 'kommentar':
                _kommentar(adb)
            erledigt += 1
        except Exception:
            log.debug('App-Aufwärm-Handlung %s fehlgeschlagen', handlung, exc_info=True)
        time.sleep(random.uniform(1.5, 4.5))
    adb.app_stoppen(PAKET)
    melden(f'App-Sitzung {konto["label"]}: {erledigt} Handlungen.')
    return erledigt


def _waehle(erlaubt):
    gewicht = {'scroll': 6, 'video': 4, 'like': 3, 'folgen': 1, 'kommentar': 1}
    return random.choice([h for h in erlaubt for _ in range(gewicht.get(h, 1))])


def _wegklicken(adb):
    for text in SCHLIESSEN:
        if adb.tippe_auf(teil=text, timeout=3):
            time.sleep(1)
            return True
    return False


def _scroll(adb):
    breite, hoehe = adb.groesse()
    x = int(breite * random.uniform(0.45, 0.6))
    adb.swipe(x, int(hoehe * 0.72), x, int(hoehe * 0.22), ms=random.randint(180, 380))
    time.sleep(random.uniform(1.2, 3.0))


def _schauen(adb):
    time.sleep(random.uniform(4, 11))


def _like(adb):
    ziel = adb.finde(teil='Like') or adb.finde(teil='Gefällt')
    if ziel:
        adb.tap(*ziel)
        return True
    breite, hoehe = adb.groesse()
    adb.tap(int(breite * 0.88), int(hoehe * 0.52))
    return True


def _folgen(adb):
    for text in ('Folgen', 'Follow'):
        if adb.tippe_auf(teil=text, timeout=3):
            return True
    return False


def _kommentar(adb):
    if not adb.tippe_auf(teil='Kommentar', timeout=4):
        return False
    time.sleep(1.5)
    breite, hoehe = adb.groesse()
    adb.tap(int(breite * 0.5), int(hoehe * 0.9))
    time.sleep(1)
    adb.tippen(random.choice(['Sehr schön', 'Stark', 'Interessant', 'Guter Punkt']))
    time.sleep(0.8)
    adb.taste('66')
    time.sleep(1.5)
    adb.taste('4')
    return True
