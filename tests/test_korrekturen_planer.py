"""Korrekturen am Planer und Hintergrunddienst (Fundliste vom 13.10.2026).

Jede Korrektur hat hier einen Test, der den alten Fehler zeigt und mit der Korrektur besteht. Nur Wegwerf-Ordner und
Attrappen: kein Browser, kein Netz, keine echten Konten, keine Datei unter <Projekt>/data."""
import contextlib
import datetime
import io
import logging
import os
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from socialscraper import autologic as al
from socialscraper import channels, cli, daemon, discovery, repo, rotation, runlock, scheduler
from socialscraper.config import Settings, load
from socialscraper.fetch_result import FetchResult
from socialscraper.models import Video
from socialscraper.store import Store

os.environ.setdefault('SS_PACE', 'off')   # keine Zufallspausen zwischen Abrufen in Tests
UTC = datetime.timezone.utc
YT = 'https://www.youtube.com/watch?v=aaaaaaaaaaa'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        # Unabhängig von der Umgebung: ein gesetztes SS_DB_PATH darf keinen Test auf eine fremde Datenbank lenken
        env = patch.dict(os.environ); env.start(); self.addCleanup(env.stop)
        os.environ.pop('SS_DB_PATH', None)
        cwd = os.getcwd(); self.addCleanup(os.chdir, cwd)
        # cli.main richtet die Protokollierung ein; danach den Zustand von vorher wiederherstellen
        root_logger = logging.getLogger()
        before, level = list(root_logger.handlers), root_logger.level

        def restore():
            for h in list(root_logger.handlers):
                if h not in before:
                    root_logger.removeHandler(h); h.close()
            root_logger.setLevel(level)
        self.addCleanup(restore)

    def main(self, argv, cfg):
        out = io.StringIO()
        with patch.object(cli, 'load', return_value=cfg), contextlib.redirect_stdout(out):
            return cli.main(argv), out.getvalue()


# ---------- Fund 1/6: eine Sperrdatei für Dienst, run-target und Befehle ----------

class LockPlaceTests(Base):
    """Liegt die Datenbank außerhalb von <root>/data (SS_DB_PATH oder db_path in settings.toml), sperren die Befehle
    (cli.py: scrape, discover) unter runlock.root_for_db(db_path). Dienst und run-target müssen dieselbe Datei nehmen,
    sonst laufen zwei Browser auf demselben Profil."""

    def setUp(self):
        super().setUp()
        self.cfg = Settings(db_path=str(self.root / 'anders' / 'scrape.db'), notify_file='',
                            profiles_dir=str(self.root / 'data' / 'profiles'))
        self.lock_root = runlock.root_for_db(self.cfg.db_path)   # so sperrt cli.py
        self.assertNotEqual(self.lock_root, self.root.resolve())

    def test_daemon_takes_the_same_lock_as_the_commands(self):
        with Store(self.cfg.db_path) as s:
            repo.ensure_default_accounts(s.db)
            repo.add_target(s.db, 'video', 'youtube', YT)
        seen = {}

        def fake_run(root, tid, source, cancel=None, **kw):
            seen['commands'] = runlock.holder(self.lock_root)
            seen['project'] = runlock.holder(self.root)
            return {'run_id': 1, 'target_id': tid, 'status': 'ok', 'error': None,
                    'videos_seen': 0, 'videos_new': 0, 'comments_new': 0}
        with patch.object(scheduler, 'run_target', side_effect=fake_run):
            ran = daemon.run(self.root, once=True, cfg=self.cfg, install_signals=False)
        self.assertEqual(ran, 1)
        self.assertEqual((seen['commands'] or {}).get('owner'), 'daemon')   # vorher: None – der Dienst sperrte <root>/data/run.lock
        self.assertIsNone(seen['project'])
        self.assertIsNone(runlock.holder(self.lock_root))                    # nach dem Lauf wieder frei

    def test_run_target_respects_the_lock_of_the_commands(self):
        with runlock.acquire(self.lock_root, 'daemon'), patch.object(scheduler, 'run_target') as run, \
             self.assertLogs('cli', 'ERROR') as logs:
            code, _ = self.main(['run-target', '5', '--root', str(self.root)], self.cfg)
        self.assertEqual(code, 4); run.assert_not_called()                   # vorher: 0, der Lauf startete trotz Sperre
        self.assertIn('Es läuft gerade schon', logs.output[0])


# ---------- Fund 10: gleichzeitiges Nachsehen ist kein Besitzer ----------

class ReadInfoTests(Base):
    """Zwei gleichzeitige Prüfungen (Übersicht, Automatik, Aktionen) störten sich: die zweite traf die
    Millisekunden-Sperre der ersten und meldete „gehalten“ ohne Besitzer – mit falsch gesperrten Knöpfen und
    einem „Dienst starten“, das nichts startete."""

    def test_short_lock_of_another_check_is_not_reported_as_held(self):
        real = runlock.OsLock._try_lock
        calls = []

        def flaky(fd):
            calls.append(1)
            if len(calls) <= 2:            # zwei Versuche lang hält ein anderer Prüfer Byte 0, ohne Besitzerinfo
                raise OSError(13, 'kurz belegt')
            real(fd)
        with patch.object(runlock.OsLock, '_try_lock', staticmethod(flaky)):
            self.assertEqual(runlock.OsLock(runlock.lock_path(self.root)).read_info(), (False, {}))   # vorher: (True, {})
            calls.clear()
            self.assertIsNone(runlock.holder(self.root))
            calls.clear()
            self.assertFalse(daemon.status(self.root)['running'])

    def test_real_holder_is_reported_at_once_with_its_info(self):
        with runlock.acquire(self.root, 'daemon'):
            real = runlock.OsLock._try_lock
            calls = []

            def counting(fd):
                calls.append(1); real(fd)
            with patch.object(runlock.OsLock, '_try_lock', staticmethod(counting)):
                held, info = runlock.OsLock(runlock.lock_path(self.root)).read_info()
            self.assertTrue(held)
            self.assertEqual(info.get('owner'), 'daemon')
            self.assertEqual(len(calls), 1)    # keine Wiederholung, kein Warten bei einem echten Besitzer


# ---------- Fund 11: nach „Dienst stoppen“ mit Gewalt bleibt kein Lauf als „Läuft …“ stehen ----------

class StopRecoveryTests(Base):
    def test_forced_stop_marks_the_interrupted_run_and_items(self):
        cfg = Settings(db_path=str(self.root / 'data' / 'scrape.db'), notify_file='')   # wie settings.toml ohne Eintrag
        with Store(cfg.db_path) as s:
            repo.ensure_default_accounts(s.db)
            t = repo.add_target(s.db, 'video', 'youtube', YT)
            run_id = repo.start_run(s.db, t['id'], 'x', 'daemon')
            s.db.execute("INSERT INTO channel_items (target_id, platform, video_url, status, discovered_at) "
                         "VALUES (?,?,?,'running',?)", (t['id'], 'youtube', YT, repo.iso()))
            s.db.commit()
        killed = []

        def fake_status(root, stale_after=daemon.STALE_AFTER):   # läuft, bis _kill gerufen wurde
            return {'running': not killed, 'pid': 4242, 'started_at': None, 'last_beat': None,
                    'current': None, 'stale': False, 'age_s': None}
        with patch.object(daemon, 'status', side_effect=fake_status), \
             patch.object(daemon, '_kill', side_effect=lambda pid: killed.append(pid)):
            self.assertTrue(daemon.stop(self.root, timeout=0))
        self.assertEqual(killed, [4242])
        with Store(cfg.db_path) as s:
            row = s.db.execute('SELECT status, error FROM runs WHERE id=?', (run_id,)).fetchone()
            item = s.db.execute('SELECT status FROM channel_items').fetchone()[0]
        self.assertEqual(tuple(row), ('failed', 'Unterbrochen (Programm beendet)'))   # vorher: 'running' bis zum nächsten Dienststart
        self.assertEqual(item, 'pending')
        self.assertFalse(daemon.stop_flag(self.root).exists())


# ---------- Fund 3: resolve_db liest settings.toml wie der Rest des Programms ----------

class ResolveDbTests(Base):
    def test_db_path_behind_sphere_presets_is_recovered_like_config_load(self):
        (self.root / 'settings.toml').write_text('[sphere_presets]\ntiktok = ""\ndb_path = "data/anders.db"\n',
                                                  encoding='utf-8')
        expected = self.root / load(str(self.root / 'settings.toml')).db_path   # Befehle, Dienst und Daten-Tab
        self.assertEqual(expected, self.root / 'data' / 'anders.db')
        self.assertEqual(al.resolve_db(self.root), expected)                   # vorher: <root>/data/scrape.db

    def test_env_settings_default_order_is_kept(self):
        self.assertEqual(al.resolve_db(self.root), self.root / 'data' / 'scrape.db')
        (self.root / 'settings.toml').write_text('db_path = "x/y.db"\n', encoding='utf-8')
        self.assertEqual(al.resolve_db(self.root), self.root / 'x' / 'y.db')
        os.environ['SS_DB_PATH'] = str(self.root / 'env.db')
        self.assertEqual(al.resolve_db(self.root), self.root / 'env.db')
        os.environ.pop('SS_DB_PATH')
        (self.root / 'settings.toml').write_text('kaputt [[', encoding='utf-8')
        self.assertEqual(al.resolve_db(self.root), self.root / 'data' / 'scrape.db')


# ---------- Fund 5/14: Ruhezeit-Ende, Tagesgrenze und Mitternacht am Tag der Zeitumstellung ----------

def _day_before_switch(year):
    """Ein Tag, in dessen folgender Nacht die Ortszeit des Rechners umgestellt wird; None ohne Zeitumstellung."""
    day = datetime.date(year, 1, 1)
    while day.year == year:
        nxt = day + datetime.timedelta(days=1)
        if (datetime.datetime.combine(day, datetime.time(12)).astimezone().utcoffset()
                != datetime.datetime.combine(nxt, datetime.time(12)).astimezone().utcoffset()):
            return day
        day = nxt
    return None


class DaylightSavingTests(unittest.TestCase):
    """`now.astimezone()` liefert eine Zone mit festem Versatz; `replace(hour=…)` und `+ 1 Tag` behielten ihn bei –
    am Tag der Umstellung lagen Ruhezeit-Ende, Tagesgrenze und nächste Mitternacht eine Stunde daneben.
    Gerechnet wird mit der Ortszeit des Rechners; ohne Zeitumstellung wird der Test übersprungen."""

    def setUp(self):
        before = _day_before_switch(datetime.date.today().year)
        if before is None:
            self.skipTest('Die Zeitzone dieses Rechners kennt keine Zeitumstellung.')
        self.switch_day = before + datetime.timedelta(days=1)
        # 1:00 Uhr Ortszeit am Umstellungstag: vor der Umstellung (2 bzw. 3 Uhr), die Ziele liegen danach
        self.early = datetime.datetime.combine(self.switch_day, datetime.time(1, 0)).astimezone(UTC)
        # Mittag des Umstellungstags: die Tagesgrenze 0:00 Uhr liegt vor der Umstellung
        self.noon = datetime.datetime.combine(self.switch_day, datetime.time(12, 0)).astimezone(UTC)

    def local(self, moment):
        here = moment.astimezone()
        return here.date(), here.hour, here.minute

    def test_next_midnight_is_local_midnight(self):
        for func in (channels.next_midnight, rotation.next_midnight):
            self.assertEqual(self.local(func(self.early)), (self.switch_day + datetime.timedelta(days=1), 0, 0), func.__module__)

    def test_quiet_end_is_the_full_local_hour(self):
        self.assertEqual(self.local(channels.quiet_end('23-7', self.early)), (self.switch_day, 7, 0))

    def test_local_day_start_is_local_midnight(self):
        self.assertEqual(self.local(repo.parse(channels.local_day_start(self.noon))), (self.switch_day, 0, 0))

    def test_ordinary_day_is_unchanged(self):
        plain = datetime.datetime.combine(self.switch_day + datetime.timedelta(days=30), datetime.time(23, 30)).astimezone(UTC)
        self.assertEqual(self.local(channels.quiet_end('23-7', plain)), (plain.astimezone().date() + datetime.timedelta(days=1), 7, 0))
        self.assertEqual(self.local(channels.next_midnight(plain)), (plain.astimezone().date() + datetime.timedelta(days=1), 0, 0))
        self.assertEqual(self.local(repo.parse(channels.local_day_start(plain))), (plain.astimezone().date(), 0, 0))


# ---------- Fund 7: der Befehl scrape speichert den Verlaufspunkt im Format des Planers ----------

class SnapshotFormatTests(Base):
    def test_scrape_stores_captured_at_like_the_scheduler(self):
        cfg = Settings(db_path=str(self.root / 'scrape.db'), profiles_dir=str(self.root / 'p'), notify_file='')
        adapter = Mock(platform='youtube', needs_browser=False, needs_auth=False)
        adapter.fetch = lambda url, ctx: FetchResult(Video('youtube', 'aaaaaaaaaaa', YT), [], 'received')
        with patch.object(cli, 'resolve', return_value=adapter), \
             patch.object(cli, '_ctx_and_backend', return_value=(Mock(), Mock(), Mock())), \
             patch.object(cli, '_auth', return_value=False), patch.object(cli, 'shutdown_runtime'):
            code, _ = self.main(['scrape', YT], cfg)
        self.assertEqual(code, 0)
        with Store(cfg.db_path) as s:
            stamps = [r[0] for r in s.db.execute('SELECT captured_at FROM video_snapshots').fetchall()]
        self.assertEqual(len(stamps), 1)
        self.assertRegex(stamps[0], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')   # vorher: …:56.123456+00:00
        self.assertEqual(repo.iso(repo.parse(stamps[0])), stamps[0])           # strenge Leser (repo.parse) kommen damit zurecht


# ---------- Fund 8: Strg+C endet mit einer Zeile und Code 130 statt Stacktrace ----------

class InterruptTests(Base):
    def test_keyboard_interrupt_gives_short_message_and_130(self):
        cfg = Settings(db_path=str(self.root / 'scrape.db'), profiles_dir=str(self.root / 'p'), notify_file='')
        with patch.object(cli, '_execute', side_effect=KeyboardInterrupt), self.assertLogs('cli', 'ERROR') as logs:
            code, _ = self.main(['check'], cfg)        # vorher: KeyboardInterrupt lief bis ganz nach oben durch
        self.assertEqual(code, 130)
        self.assertIn('Abgebrochen (Strg+C)', logs.output[0])


# ---------- Fund 9: der Hinweis „Tabellen fehlen in der Cloud“ erscheint in der Ausgabe ----------

class SyncWarningTests(Base):
    def test_warning_is_printed_as_indented_line(self):
        cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='')
        result = {'ok': True, 'direction': 'push', 'counts': {'videos': 2}, 'total': 2, 'error': None, 'partial': False,
                  'warning': 'Tabellen fehlen in der Cloud: comment_sentiment. Bitte deploy/supabase_schema.sql erneut ausführen.'}
        with patch('socialscraper.sync.push', return_value=result):
            code, out = self.main(['sync', 'push'], cfg)
        self.assertEqual(code, 0)
        self.assertIn('\n  Hinweis: Tabellen fehlen in der Cloud', out)   # vorher: nur die Zähler und „Fertig“; zwei Leerzeichen, damit das Fenster die Zeile zeigt
        self.assertIn('Fertig: 2 Zeilen gesendet.', out)

    def test_no_warning_no_line(self):
        cfg = Settings(db_path=str(self.root / 'scrape.db'), notify_file='')
        result = {'ok': True, 'direction': 'pull', 'counts': {'videos': 0}, 'total': 0, 'error': None, 'partial': False}
        with patch('socialscraper.sync.pull', return_value=result):
            code, out = self.main(['sync', 'pull'], cfg)
        self.assertEqual(code, 0)
        self.assertNotIn('Hinweis', out)


# ---------- Fund 13: normalize liefert bei limit 0 keinen Link ----------

class NormalizeTests(unittest.TestCase):
    def test_limit_zero_gives_nothing(self):
        urls = ['https://www.tiktok.com/@a/video/1', 'https://www.tiktok.com/@b/video/2']
        self.assertEqual(discovery.normalize(urls, 0), [])            # vorher: der erste Link kam durch
        self.assertEqual(discovery.normalize(urls, 1), urls[:1])
        self.assertEqual(discovery.normalize(urls, 5), urls)


if __name__ == '__main__':
    unittest.main()
