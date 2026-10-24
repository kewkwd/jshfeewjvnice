"""Logik der Kanal-Oberfläche ohne Tk: Lesen (schreibgeschützt), Anlegen, Aktionen, Vorschau.

Lesen geht schreibgeschützt (die Datenbank wird dabei nicht angelegt oder verändert); Schreiben über `Store` + `repo`.
Datenbankfehler werden zu verständlichen ValueError-Meldungen.
"""
import contextlib
import logging
import pathlib
import sqlite3

from . import autologic as al
from . import channel_list as cl
from . import channels, repo

log = logging.getLogger(__name__)

PROFILE_ORDER = ('careful', 'normal', 'fast')
PROFILE_LABELS = {key: channels.PROFILES[key]['name'] for key in PROFILE_ORDER}
SCOPE_LABELS = {'all': 'alle Videos', 'latest': 'nur die neuesten …', 'since': 'ab Datum …'}
ACTIONS = ('pause', 'resume', 'now', 'requeue', 'pace', 'remove')


def profile_text(key):
    p = channels.PROFILES[key]
    return f'{p["name"]}: {p["text"]}.'


def load(db_path, strict=False):
    """Kanäle mit Fortschritt; ohne Datenbank oder ohne Kanal-Tabellen eine leere Liste.
    Mit strict=True wird eine belegte oder nicht lesbare Datenbank als ValueError (deutsche Meldung) gemeldet,
    statt wie eine leere Liste auszusehen – sonst hieße es fälschlich „diesen Kanal gibt es nicht"."""
    try:
        with al._read(db_path) as db:
            if db is None:
                return []
            try:
                return repo.list_channels(db)
            except sqlite3.OperationalError as error:
                if 'no such' in str(error).lower():
                    return []
                raise
    except sqlite3.OperationalError as error:
        if strict:
            raise ValueError(al._db_text(error)) from None
        return []
    except sqlite3.DatabaseError:
        if strict:
            raise ValueError('Die Datenbank lässt sich nicht lesen. Bitte gleich noch einmal versuchen.') from None
        return []


def items(db_path, target_id, status=None, limit=500):
    try:
        with al._read(db_path) as db:
            if db is None:
                return []
            return repo.list_channel_items(db, target_id, status, limit)
    except sqlite3.Error:
        return []


def create(db_path, ref, **options):
    """Legt den Kanal an. `ref` kommt aus channels.parse_channel_link. Gibt das Ziel zurück (mit `notes`)."""
    with al._write(db_path) as db:
        return channels.create_channel(db, ref, **options)


def action(db_path, target_id, name, **kw):
    """Pausieren, Fortsetzen, Jetzt weiter, Fehlgeschlagene erneut, Tempo ändern, Entfernen. Gibt einen Satz zurück."""
    if name not in ACTIONS:
        raise ValueError('Unbekannte Aktion')
    with al._write(db_path) as db:
        target = repo.get_target(db, target_id)
        if target is None or target['kind'] != 'channel':
            raise ValueError('Diesen Kanal gibt es nicht mehr.')
        if name == 'pause':
            repo.update_channel(db, target_id, enabled=0)
            return 'Der Kanal ist angehalten. Mit „Fortsetzen“ geht es weiter.'
        if name == 'resume':
            channels.resume_channel(db, target_id, now=repo.now(), force=False)
            return 'Der Kanal macht beim nächsten Schritt weiter.'
        if name == 'now':
            channels.resume_channel(db, target_id, now=repo.now(), force=True)
            return ('Der nächste Schritt läuft in den nächsten Sekunden, sobald der Hintergrunddienst frei ist. '
                    'Die Tagesgrenze gilt weiter.')
        if name == 'requeue':
            n = repo.requeue_failed(db, target_id)
            return (f'{n} fehlgeschlagene Videos kommen noch einmal dran.' if n else 'Es gibt keine fehlgeschlagenen Videos.')
        if name == 'pace':
            fields = {'pace_profile': kw['profile']}
            if 'quiet_hours' in kw:
                fields['quiet_hours'] = kw['quiet_hours']
            repo.update_channel(db, target_id, **fields)
            return f'Tempo: {channels.PROFILES[kw["profile"]]["name"]}.'
        repo.delete_target(db, target_id)      # Warteschlange weg; gespeicherte Videos bleiben
        return 'Der Kanal wurde entfernt. Gespeicherte Videos bleiben erhalten.'


def estimate_for(ref, profile, *, count, accounts, comments, quiet_hours=None, already=0):
    return channels.estimate(count, ref.platform, profile, accounts=accounts, comments=comments, quiet_hours=quiet_hours,
                             already=already)


def preview(ref, root):
    """Name und, falls schnell ermittelbar, Zahl der Videos. YouTube: eine kleine Abfrage der Kanalseite (Netz!).
    Die anderen Plattformen brauchen ein angemeldetes Konto im Browser; dort gibt es keine Vorschau ohne Abruf.
    Läuft nie im Fenster-Thread. Gibt {'name', 'total', 'note'} zurück."""
    if ref.platform != 'youtube':
        return {'name': ref.display, 'total': None,
                'note': 'Name und Zahl der Videos stehen erst nach dem ersten Lauf fest (die Plattform zeigt sie nur mit Anmeldung).'}
    from . import cli, scheduler
    from .adapters.base import ScrapeContext
    from .adapters.youtube import YouTubeAdapter
    cfg = scheduler.load_config(root)
    im, backend, sessions = cli.make_runtime(cfg, False)
    try:
        ctx = ScrapeContext(cfg=cfg, store=None, im=im, sessions=None, identity=scheduler.YOUTUBE_IDENTITY, country='de')
        opts = cl.ListOptions(limit=1, include_shorts=False)
        result = YouTubeAdapter().channel_items(ref.value, ctx, opts)
    finally:
        cli.shutdown_runtime(backend, sessions)
    total = result.platform_total
    note = (f'{total:,} Videos laut YouTube.'.replace(',', '.') if total else
            'Die Zahl der Videos lässt sich vorab nicht ermitteln; sie steht nach dem ersten Lauf fest.')
    return {'name': result.name or ref.display, 'total': total, 'note': note}
