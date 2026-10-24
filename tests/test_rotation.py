"""Konten-Rotation: Auswahl, Abkühlung, Tageslimit, Meldungen, Migration, Anzeige. Alles in Wegwerf-Ordnern."""
import datetime
import json
import pathlib
import sqlite3
import tempfile
import unittest

from socialscraper import repo, rotation
from socialscraper.net.session_guard import SessionExpired
from socialscraper.store import Store

UTC = datetime.timezone.utc
T0 = datetime.datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC)
COOKIE_NAME = {'tiktok': 'sessionid', 'instagram': 'sessionid', 'reddit': 'reddit_session', 'x': 'auth_token'}


def make_login(root, platform, identity):
    """Legt eine (gefälschte) gespeicherte Anmeldung an: nur eine Datei im Wegwerf-Ordner."""
    path = pathlib.Path(root) / 'data' / 'cookies' / f'{identity}.{platform}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    cookies = [{'name': COOKIE_NAME[platform], 'value': 'wert', 'domain': f'.{platform}.com', 'path': '/'}]
    if platform == 'x':
        cookies.append({'name': 'ct0', 'value': 'wert', 'domain': '.x.com', 'path': '/'})
    path.write_text(json.dumps(cookies), encoding='utf-8')


class RotationBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.store = Store(str(self.root / 'data' / 'scrape.db')); self.addCleanup(self.store.close)
        self.db = self.store.db
        repo.ensure_default_accounts(self.db)
        self.a = repo.default_account(self.db, 'tiktok')
        self.b = repo.add_account(self.db, 'tiktok', 'Zweit')
        self.c = repo.add_account(self.db, 'tiktok', 'Dritt')
        for acc in (self.a, self.b, self.c):
            make_login(self.root, 'tiktok', acc['identity'])

    def pick(self, now=T0, **kw):
        kw.setdefault('mode', 'rotate')
        return rotation.pick_account(self.db, 'tiktok', now=now, root=self.root, **kw)

    def row(self, account):
        return repo.get_account(self.db, account['id'])


class PickTests(RotationBase):
    def test_least_recently_used_first_and_wraps_around(self):
        order = []
        for step in range(6):
            now = T0 + datetime.timedelta(minutes=step)
            acc = self.pick(now)
            order.append(acc['label'])
            rotation.mark_used(self.db, acc['id'], now)
        self.assertEqual(order, ['Hauptkonto', 'Zweit', 'Dritt', 'Hauptkonto', 'Zweit', 'Dritt'])

    def test_never_used_before_used_and_tie_goes_to_lower_use_count_then_id(self):
        rotation.mark_used(self.db, self.a['id'], T0)
        self.assertEqual(self.pick()['label'], 'Zweit')
        same = repo.iso(T0)
        self.db.execute('UPDATE accounts SET last_used_at=?, use_count=5', (same,))
        self.db.execute('UPDATE accounts SET use_count=2 WHERE id=?', (self.c['id'],))
        self.db.commit()
        self.assertEqual(self.pick()['label'], 'Dritt')
        self.db.execute('UPDATE accounts SET use_count=2')
        self.db.commit()
        self.assertEqual(self.pick()['label'], 'Hauptkonto')

    def test_exclude_and_allowed(self):
        self.assertEqual(self.pick(exclude=(self.a['id'],))['label'], 'Zweit')
        self.assertEqual(self.pick(exclude=(self.a['id'], self.b['id']))['label'], 'Dritt')
        self.assertIsNone(self.pick(exclude=(self.a['id'], self.b['id'], self.c['id'])))
        self.assertEqual(self.pick(allowed={self.c['id']})['label'], 'Dritt')

    def test_rotation_switch_login_and_cooldown_exclude(self):
        repo.set_account_rotation(self.db, self.a['id'], False)
        self.assertEqual(self.pick()['label'], 'Zweit')
        (self.root / 'data' / 'cookies' / f"{self.b['identity']}.tiktok.json").unlink()   # keine Anmeldung mehr
        self.assertEqual(self.pick()['label'], 'Dritt')
        rotation.mark_failure(self.db, self.c['id'], 'x', 'captcha', T0)
        self.assertIsNone(self.pick())
        # nach Ablauf der Abkühlung ist das Konto wieder dran
        self.assertIsNone(self.pick(T0 + datetime.timedelta(hours=5, minutes=59)))
        self.assertEqual(self.pick(T0 + datetime.timedelta(hours=6, seconds=1))['label'], 'Dritt')

    def test_default_and_fixed_modes_ignore_cooldown_like_before(self):
        rotation.mark_failure(self.db, self.a['id'], 'x', 'block', T0)
        self.assertEqual(self.pick(mode='default')['label'], 'Hauptkonto')
        self.assertEqual(self.pick(mode='fixed', account_id=self.c['id'])['label'], 'Dritt')
        self.assertEqual(self.pick(mode='fixed', account_id=9999)['label'], 'Hauptkonto')   # Konto gelöscht -> Standard
        with self.assertRaises(ValueError):
            self.pick(mode='quatsch')

    def test_reddit_is_allowed_without_login_other_platforms_are_not(self):
        reddit = repo.default_account(self.db, 'reddit')
        self.assertEqual(rotation.pick_account(self.db, 'reddit', mode='rotate', root=self.root, now=T0)['id'], reddit['id'])
        ig = repo.default_account(self.db, 'instagram')
        self.assertIsNone(rotation.pick_account(self.db, 'instagram', mode='rotate', root=self.root, now=T0))
        make_login(self.root, 'instagram', ig['identity'])
        self.assertEqual(rotation.pick_account(self.db, 'instagram', mode='rotate', root=self.root, now=T0)['id'], ig['id'])


class CooldownTests(RotationBase):
    def until(self, account):
        return repo.parse(self.row(account)['cooldown_until'])

    def test_captcha_block_login_rise_6_12_24_48_and_cap(self):
        hours = []
        for _ in range(6):
            rotation.mark_failure(self.db, self.a['id'], 'x', 'captcha', T0)
            hours.append((self.until(self.a) - T0).total_seconds() / 3600)
        self.assertEqual(hours, [6, 12, 24, 48, 48, 48])
        self.assertEqual(self.row(self.a)['cooldown_reason'], 'CAPTCHA')
        self.assertEqual(self.row(self.a)['fail_streak'], 6)

    def test_ratelimit_1_2_4_and_proxy_30_minutes(self):
        hours = []
        for _ in range(5):
            rotation.mark_failure(self.db, self.b['id'], 'x', 'ratelimit', T0)
            hours.append((self.until(self.b) - T0).total_seconds() / 3600)
        self.assertEqual(hours, [1, 2, 4, 4, 4])
        rotation.mark_failure(self.db, self.c['id'], 'x', 'proxy', T0)
        self.assertEqual((self.until(self.c) - T0).total_seconds(), 30 * 60)
        self.assertEqual(self.row(self.c)['cooldown_reason'], 'Proxy nicht erreichbar')

    def test_success_resets_streak_and_clears_cooldown(self):
        rotation.mark_failure(self.db, self.a['id'], 'x', 'login', T0)
        rotation.mark_failure(self.db, self.a['id'], 'x', 'login', T0)
        rotation.mark_success(self.db, self.a['id'])
        row = self.row(self.a)
        self.assertEqual((row['fail_streak'], row['cooldown_until'], row['cooldown_reason']), (0, None, None))
        rotation.mark_failure(self.db, self.a['id'], 'x', 'login', T0)
        self.assertEqual((self.until(self.a) - T0).total_seconds() / 3600, 6)

    def test_clear_cooldown_and_login_cooldown_after_new_login(self):
        rotation.mark_failure(self.db, self.a['id'], 'x', 'captcha', T0)
        rotation.clear_login_cooldown(self.db, self.a['identity'])      # CAPTCHA bleibt
        self.assertIsNotNone(self.row(self.a)['cooldown_until'])
        rotation.clear_cooldown(self.db, self.a['id'])
        self.assertIsNone(self.row(self.a)['cooldown_until'])
        rotation.mark_failure(self.db, self.b['id'], 'x', 'login', T0)
        rotation.clear_login_cooldown(self.db, self.b['identity'])      # neue Anmeldung hebt auf
        self.assertIsNone(self.row(self.b)['cooldown_until'])

    def test_longer_cooldown_is_kept_and_unknown_kind_is_refused(self):
        rotation.mark_failure(self.db, self.a['id'], 'x', 'captcha', T0)
        rotation.mark_failure(self.db, self.a['id'], 'x', 'proxy', T0)   # kürzer als die laufende Abkühlung
        self.assertEqual(self.row(self.a)['cooldown_reason'], 'CAPTCHA')
        with self.assertRaises(ValueError):
            rotation.mark_failure(self.db, self.a['id'], 'x', 'quatsch', T0)

    def test_classify_kinds(self):
        c = rotation.classify
        self.assertEqual(c(SessionExpired('weg')), 'login')
        self.assertEqual(c(RuntimeError('Die Plattform zeigt eine Sicherheitsabfrage (CAPTCHA).')), 'captcha')
        self.assertEqual(c(RuntimeError('HTTP Error 429: Too Many Requests')), 'ratelimit')
        self.assertEqual(c(RuntimeError('Zu viele Anfragen (HTTP 429): die Plattform bremst uns aus.')), 'ratelimit')
        self.assertEqual(c(RuntimeError('blocked by network security')), 'block')
        # ein einzelnes 403 ist ein Videoproblem, kein Kontofehler
        self.assertIsNone(c(RuntimeError('Zugriff verweigert (HTTP 403/401): die Plattform sperrt diese Verbindung.')))
        self.assertIsNone(c(RuntimeError('HTTP Error 403: Forbidden')))
        self.assertIsNone(c(RuntimeError('This video is not available in your country')))
        self.assertIsNone(c(RuntimeError('Video unavailable. This video is private')))
        self.assertIsNone(c(RuntimeError('Das Video wurde gelöscht')))
        # CAPTCHA, Menschentest, Kontosperre, Anmeldeseite, zu viele Anfragen: Kontofehler
        self.assertEqual(c(RuntimeError('HTTP Error 403: Prove your humanity')), 'captcha')
        self.assertEqual(c(RuntimeError('Bitte den Menschentest bestehen')), 'captcha')
        self.assertEqual(c(RuntimeError('HTTP Error 403: Your account has been suspended')), 'block')
        self.assertEqual(c(RuntimeError('Konto gesperrt')), 'block')
        self.assertEqual(c(RuntimeError('HTTP 403: login required')), 'login')
        self.assertEqual(c(RuntimeError('Zu viele Anfragen, bitte warten')), 'ratelimit')
        self.assertEqual(c(RuntimeError('Reddit bremst uns aus (zu viele Anfragen). Warte einige Minuten.')), 'ratelimit')
        self.assertIsNone(c(RuntimeError('Das Video ist privat, gelöscht oder in diesem Land nicht verfügbar.')))
        self.assertIsNone(c(RuntimeError('irgendein Fehler')))
        self.assertIsNone(c(None))
        # Proxy-Fehler zählt nur bei festem Proxy des Kontos
        self.assertIsNone(c(RuntimeError('ERR_PROXY_CONNECTION_FAILED')))
        self.assertEqual(c(RuntimeError('ERR_PROXY_CONNECTION_FAILED'), has_proxy=True), 'proxy')
        from socialscraper.net.accountproxy import AccountProxyError
        self.assertEqual(c(AccountProxyError('Der feste Proxy dieses Kontos (1.2.3.4:80) ist nicht erreichbar.')), 'proxy')


class ForbiddenTests(RotationBase):
    ERR = RuntimeError('HTTP Error 403: Forbidden')

    def rot(self):
        rot = rotation.Rotator(self.db, 'tiktok', 'rotate', root=self.root, clock=lambda: T0)
        rot.begin()
        return rot

    def test_single_and_repeated_same_video_403_is_a_video_problem(self):
        rot = self.rot()
        self.assertIsNone(rot.classify(self.ERR, False, 'v1'))
        self.assertIsNone(rot.classify(self.ERR, False, 'v1'))
        self.assertIsNone(rot.classify(self.ERR, False, 'v1'))      # dasselbe Video zählt nur einmal
        self.assertEqual(rot.forbidden, ['v1'])

    def test_three_different_videos_in_a_row_count_as_account_block(self):
        rot = self.rot()
        self.assertIsNone(rot.classify(self.ERR, False, 'v1'))
        self.assertIsNone(rot.classify(self.ERR, False, 'v2'))
        self.assertEqual(rot.classify(self.ERR, False, 'v3'), 'block')
        self.assertEqual(rot.forbidden, [])                           # danach beginnt das Zählen neu
        self.assertIsNone(rot.classify(self.ERR, False, 'v4'))

    def test_switching_to_another_account_starts_a_fresh_count(self):
        rot = self.rot()
        first = rot.current['id']
        rot.classify(self.ERR, False, 'v1')
        rot.classify(self.ERR, False, 'v2')
        nxt = rot.fault('captcha', 'v2')
        self.assertIsNotNone(nxt)
        self.assertNotEqual(nxt['id'], first)
        self.assertIsNone(rot.classify(self.ERR, False, 'v3'))      # das neue Konto hat erst ein 403 gesehen
        self.assertEqual(rot.forbidden, ['v3'])

    def test_a_success_in_between_resets_the_count(self):
        rot = self.rot()
        rot.classify(self.ERR, False, 'v1')
        rot.classify(self.ERR, False, 'v2')
        rot.success()
        self.assertIsNone(rot.classify(self.ERR, False, 'v3'))
        self.assertIsNone(rot.classify(self.ERR, False, 'v4'))

    def test_other_errors_are_unchanged_and_classify_for_without_rotator(self):
        rot = self.rot()
        self.assertEqual(rot.classify(RuntimeError('CAPTCHA!'), False, 'v1'), 'captcha')
        self.assertIsNone(rot.classify(RuntimeError('irgendein Fehler'), False, 'v1'))
        self.assertEqual(rotation.classify_for(None, RuntimeError('CAPTCHA!')), 'captcha')
        self.assertIsNone(rotation.classify_for(None, self.ERR, False, 'v1'))
        self.assertEqual(rotation.classify_for(rot, RuntimeError('CAPTCHA!'), False, 'v2'), 'captcha')


class DailyLimitTests(RotationBase):
    def test_limit_reached_excludes_until_next_day(self):
        repo.set_account_daily_limit(self.db, self.a['id'], 2)
        rotation.mark_used(self.db, self.a['id'], T0)
        self.assertEqual(self.row(self.a)['used_today'], 1)
        self.assertEqual(rotation.account_state(self.row(self.a), T0, self.root)['state'], 'bereit')
        rotation.mark_used(self.db, self.a['id'], T0)
        state = rotation.account_state(self.row(self.a), T0, self.root)
        self.assertEqual((state['state'], state['text']), ('tageslimit', 'Tageslimit erreicht'))
        self.assertNotEqual(self.pick()['label'], 'Hauptkonto')
        later = T0 + datetime.timedelta(hours=26)
        self.assertEqual(rotation.account_state(self.row(self.a), later, self.root)['state'], 'bereit')
        rotation.mark_used(self.db, self.a['id'], later)
        self.assertEqual(self.row(self.a)['used_today'], 1)           # neuer Tag: Zähler beginnt neu
        self.assertEqual(self.row(self.a)['use_count'], 3)

    def test_limit_validation_and_unlimited(self):
        for bad in (0, -1, 'x', 1.5, True, 100001):
            with self.assertRaises(ValueError):
                repo.set_account_daily_limit(self.db, self.a['id'], bad)
        repo.set_account_daily_limit(self.db, self.a['id'], 5)
        repo.set_account_daily_limit(self.db, self.a['id'], None)
        self.assertIsNone(self.row(self.a)['daily_limit'])


class NoAccountTests(RotationBase):
    def test_all_cooling_names_earliest_time_and_account(self):
        rotation.mark_failure(self.db, self.a['id'], 'x', 'captcha', T0)                 # 6 h
        rotation.mark_failure(self.db, self.b['id'], 'x', 'ratelimit', T0)               # 1 h  <- früheste
        rotation.mark_failure(self.db, self.c['id'], 'x', 'block', T0)
        message, until = rotation.no_account_message(self.db, 'tiktok', T0, self.root)
        self.assertEqual(until, T0 + datetime.timedelta(hours=1))
        self.assertIn('Alle Konten für TikTok sind gerade nicht verfügbar', message)
        self.assertIn(rotation.fmt_when(until, T0), message)
        self.assertIn('„Zweit“', message)
        self.assertIn('zu viele Anfragen', message)

    def test_begin_raises_with_message_and_until(self):
        for acc in (self.a, self.b, self.c):
            rotation.mark_failure(self.db, acc['id'], 'x', 'login', T0)
        rot = rotation.Rotator(self.db, 'tiktok', 'rotate', root=self.root, clock=lambda: T0)
        with self.assertRaises(rotation.NoAccount) as caught:
            rot.begin()
        self.assertEqual(caught.exception.until, T0 + datetime.timedelta(hours=6))
        self.assertIn('wieder frei ab', str(caught.exception))

    def test_other_reasons(self):
        for acc in (self.a, self.b, self.c):
            repo.set_account_rotation(self.db, acc['id'], False)
        self.assertIn('kein Konto in der Rotation', rotation.no_account_message(self.db, 'tiktok', T0, self.root)[0])
        for acc in (self.a, self.b, self.c):
            repo.set_account_rotation(self.db, acc['id'], True)
            (self.root / 'data' / 'cookies' / f"{acc['identity']}.tiktok.json").unlink()
        self.assertIn('kein Konto angemeldet', rotation.no_account_message(self.db, 'tiktok', T0, self.root)[0])

    def test_tageslimit_names_midnight(self):
        for acc in (self.a, self.b, self.c):
            repo.set_account_daily_limit(self.db, acc['id'], 1)
            rotation.mark_used(self.db, acc['id'], T0)
        message, until = rotation.no_account_message(self.db, 'tiktok', T0, self.root)
        self.assertEqual(until, rotation.next_midnight(T0))
        self.assertIn('Tageslimit erreicht', message)

    def test_all_tried_for_this_video(self):
        ids = (self.a['id'], self.b['id'], self.c['id'])
        message, _ = rotation.no_account_message(self.db, 'tiktok', T0, self.root, exclude=ids)
        self.assertIn('schon versucht', message)


class RotatorTests(RotationBase):
    def rotator(self, **kw):
        t = [T0]
        rot = rotation.Rotator(self.db, 'tiktok', 'rotate', every=kw.pop('every', 2), root=self.root,
                               clock=lambda: t[0], **kw)
        rot._t = t
        return rot

    def test_block_switch_after_n_videos_and_used_names(self):
        rot = self.rotator(every=2)
        self.assertEqual(rot.begin()['label'], 'Hauptkonto')
        flags = []
        for step in range(4):
            rot._t[0] = T0 + datetime.timedelta(minutes=step)
            rot.attempt()
            flags.append(rot.video_done())
            if flags[-1]:
                rot.next_block()
        self.assertEqual(flags, [False, True, False, True])
        self.assertEqual(rot.used_labels(), ['Hauptkonto', 'Zweit', 'Dritt'])

    def test_fault_moves_on_and_each_account_once_per_video(self):
        rot = self.rotator(every=5)
        first = rot.begin()
        second = rot.fault('captcha', 'v1')
        third = rot.fault('captcha', 'v1')
        self.assertEqual([first['label'], second['label'], third['label']], ['Hauptkonto', 'Zweit', 'Dritt'])
        self.assertIsNone(rot.fault('captcha', 'v1'))                  # alle probiert
        self.assertIn('Alle Konten', rot.message)
        # nur einmal pro Lauf vermerkt (keine wachsende Abkühlung durch Wiederholungen)
        self.assertEqual(self.row(self.a)['fail_streak'], 1)

    def test_default_and_fixed_do_not_switch(self):
        rot = rotation.Rotator(self.db, 'tiktok', 'fixed', self.c['id'], root=self.root)
        self.assertEqual(rot.begin()['label'], 'Dritt')
        self.assertFalse(rot.video_done())
        self.assertIsNone(rot.fault('block', 'v'))
        self.assertEqual(self.row(self.c)['cooldown_reason'], 'Sperre')
        dflt = rotation.Rotator(self.db, 'tiktok', 'default', root=self.root)
        self.assertEqual(dflt.begin()['label'], 'Hauptkonto')


class StatusTests(RotationBase):
    def test_status_rows_and_summary(self):
        rotation.mark_failure(self.db, self.b['id'], 'x', 'captcha', T0)
        repo.set_account_rotation(self.db, self.c['id'], False)
        rows = rotation.rotation_status(self.db, 'tiktok', T0, self.root)
        by = {r['label']: r for r in rows}
        self.assertEqual(by['Hauptkonto']['state'], 'bereit')
        self.assertEqual(by['Zweit']['state'], 'abkuehlung')
        self.assertIn('wegen CAPTCHA', by['Zweit']['text'])
        self.assertFalse(by['Dritt']['in_rotation'])
        text = rotation.summary(rows, T0)
        self.assertEqual(text, f"3 Konten · 1 bereit · 1 in Abkühlung bis {rotation.fmt_when(T0 + datetime.timedelta(hours=6), T0)}"
                               " · 1 nicht in Rotation")
        self.assertEqual(rotation.summary([], T0), '0 Konten')

    def test_no_login_state(self):
        (self.root / 'data' / 'cookies' / f"{self.a['identity']}.tiktok.json").unlink()
        row = [r for r in rotation.rotation_status(self.db, 'tiktok', T0, self.root) if r['label'] == 'Hauptkonto'][0]
        self.assertEqual((row['state'], row['text']), ('keine_anmeldung', 'keine Anmeldung'))

    def test_ip_hints(self):
        rows = rotation.rotation_status(self.db, 'tiktok', T0, self.root)
        self.assertEqual(rotation.ip_hints(rows), [])
        repo.set_account_proxy_ref(self.db, self.a['id'], 'http://u:*****@1.2.3.4:8080')
        repo.set_account_proxy_ref(self.db, self.b['id'], 'http://u:*****@1.2.3.4:8080')
        hints = rotation.ip_hints(rotation.rotation_status(self.db, 'tiktok', T0, self.root))
        self.assertEqual(len(hints), 2)
        self.assertIn('denselben Proxy', hints[0])
        self.assertIn('Dritt', hints[1])
        self.assertIn('keinen eigenen Proxy', hints[1])
        repo.set_account_rotation(self.db, self.b['id'], False)
        repo.set_account_rotation(self.db, self.c['id'], False)
        self.assertEqual(rotation.ip_hints(rotation.rotation_status(self.db, 'tiktok', T0, self.root)), [])


class MigrationTests(unittest.TestCase):
    OLD = '''
    CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, label TEXT NOT NULL,
        identity TEXT NOT NULL UNIQUE, country TEXT NOT NULL DEFAULT 'de', is_default INTEGER NOT NULL DEFAULT 0,
        note TEXT, created_at TEXT NOT NULL);
    CREATE TABLE groups (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, color TEXT NOT NULL DEFAULT '#2563eb',
        note TEXT, created_at TEXT NOT NULL);
    CREATE TABLE targets (id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL CHECK (kind IN ('video','hashtag','search','profile')), platform TEXT NOT NULL, value TEXT NOT NULL,
        account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL, group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL,
        interval_min INTEGER NOT NULL DEFAULT 360, max_comments INTEGER NOT NULL DEFAULT 20, max_videos INTEGER NOT NULL DEFAULT 20,
        enabled INTEGER NOT NULL DEFAULT 1, last_run_at TEXT, next_run_at TEXT, last_status TEXT, last_error TEXT,
        created_at TEXT NOT NULL, UNIQUE (kind, platform, value));
    CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER, label TEXT, source TEXT NOT NULL DEFAULT 'manual',
        started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL DEFAULT 'running', videos_seen INTEGER DEFAULT 0,
        videos_new INTEGER DEFAULT 0, comments_new INTEGER DEFAULT 0, error TEXT);
    INSERT INTO accounts (platform,label,identity,is_default,created_at) VALUES ('tiktok','Hauptkonto','tiktok_main',1,'2026-01-01T00:00:00Z');
    INSERT INTO accounts (platform,label,identity,is_default,created_at) VALUES ('tiktok','Zweit','tiktok_zweit',0,'2026-01-01T00:00:00Z');
    INSERT INTO targets (kind,platform,value,account_id,interval_min,created_at) VALUES ('hashtag','tiktok','a',2,60,'2026-01-01T00:00:00Z');
    INSERT INTO targets (kind,platform,value,account_id,interval_min,created_at) VALUES ('hashtag','tiktok','b',NULL,60,'2026-01-01T00:00:00Z');
    INSERT INTO runs (started_at) VALUES ('2026-01-01T00:00:00Z');
    '''

    def test_old_database_is_migrated_without_losing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'alt.db'
            con = sqlite3.connect(path); con.executescript(self.OLD); con.commit(); con.close()
            for _ in range(2):                         # zweimal öffnen: die Migration ist wiederholbar
                with Store(str(path)) as store:
                    db = store.db
                    modes = dict(db.execute('SELECT value, account_mode FROM targets'))
                    self.assertEqual(modes, {'a': 'fixed', 'b': 'default'})
                    acc = repo.get_account(db, 1)
                    self.assertEqual((acc['rotation_enabled'], acc['proxy'], acc['cooldown_until'], acc['daily_limit'],
                                      acc['fail_streak'], acc['use_count']), (1, None, None, None, 0, 0))
                    self.assertEqual(db.execute('SELECT accounts_used FROM runs').fetchone()[0], None)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0], 2)

    def test_target_modes_in_repo(self):
        with tempfile.TemporaryDirectory() as tmp, Store(str(pathlib.Path(tmp) / 'n.db')) as store:
            db = store.db
            repo.ensure_default_accounts(db)
            acc = repo.add_account(db, 'tiktok', 'Zweit')
            t1 = repo.add_target(db, 'hashtag', 'tiktok', 'a')
            t2 = repo.add_target(db, 'hashtag', 'tiktok', 'b', account_id=acc['id'])
            t3 = repo.add_target(db, 'hashtag', 'tiktok', 'c', account_mode='rotate', account_id=acc['id'])
            self.assertEqual([t['account_mode'] for t in (t1, t2, t3)], ['default', 'fixed', 'rotate'])
            self.assertIsNone(t3['account_id'])                                 # Rotation hat kein festes Konto
            with self.assertRaises(ValueError):
                repo.add_target(db, 'hashtag', 'tiktok', 'd', account_mode='fixed')
            with self.assertRaises(ValueError):
                repo.add_target(db, 'hashtag', 'tiktok', 'd', account_mode='quatsch')
            repo.update_target(db, t1['id'], account_mode='rotate')
            self.assertEqual(repo.get_target(db, t1['id'])['account_mode'], 'rotate')
            repo.update_target(db, t1['id'], account_id=acc['id'])
            self.assertEqual(repo.get_target(db, t1['id'])['account_mode'], 'fixed')
            repo.update_target(db, t1['id'], account_mode='default')
            self.assertEqual((repo.get_target(db, t1['id'])['account_mode'], repo.get_target(db, t1['id'])['account_id']), ('default', None))
            self.assertEqual(repo.list_targets(db)[0]['account_mode'] in repo.ACCOUNT_MODES, True)

    def test_runs_accounts_used_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp, Store(str(pathlib.Path(tmp) / 'n.db')) as store:
            run = repo.start_run(store.db, None, 'x')
            repo.finish_run(store.db, run, 'ok', accounts_used=['A', 'B „C“'])
            row = repo.list_runs(store.db)[0]
            self.assertEqual(repo.accounts_used_list(row['accounts_used']), ['A', 'B „C“'])
            self.assertEqual(repo.accounts_used_list(None), [])


class SettingTests(unittest.TestCase):
    def test_rotation_every_setting_validated(self):
        from socialscraper.config import Settings, validate
        self.assertEqual(Settings().rotation_every, 5)
        for bad in (0, 101, -1, 2.5, True, '5'):
            with self.assertRaises(ValueError):
                validate(Settings(rotation_every=bad))
        self.assertEqual(validate(Settings(rotation_every=100)).rotation_every, 100)


class ExtrasTests(RotationBase):
    def test_deleting_a_fixed_account_resets_its_targets_to_default(self):
        t = repo.add_target(self.db, 'hashtag', 'tiktok', 'a', account_id=self.b['id'])
        self.assertEqual(t['account_mode'], 'fixed')
        repo.delete_account(self.db, self.b['id'])
        t = repo.get_target(self.db, t['id'])
        self.assertEqual((t['account_mode'], t['account_id']), ('default', None))

    def test_cli_commands_for_the_window(self):
        from socialscraper import guicore as core
        cmd = core.scrape_command('tiktok', ['https://www.tiktok.com/@a/video/1'], 5, rotate=True, rotation_every=7)
        self.assertEqual(cmd[-3:], ['--rotate', '--rotation-every', '7'])
        self.assertNotIn('--rotate', core.scrape_command('tiktok', ['https://www.tiktok.com/@a/video/1'], 5))
        self.assertIn('--rotate', core.profile_command('anna', 3, 5, rotate=True))
        for bad in (0, 101, 'x'):
            with self.assertRaises((ValueError, TypeError)):
                core.scrape_command('tiktok', ['https://www.tiktok.com/@a/video/1'], 5, rotate=True, rotation_every=bad)
        self.assertEqual(core.parse_used_accounts(['x', 'Benutzte Konten: Haupt, Zweit', 'Benutzte Konten: Zweit, Dritt']),
                         ['Haupt', 'Zweit', 'Dritt'])
        self.assertEqual(core.parse_used_accounts(['fertig: 1 ok, 0 fehler']), [])

    def test_plain_log_lines_about_accounts(self):
        from socialscraper.ui.plain_log import PlainLog
        log = PlainLog()
        text, _ = log.format('12:00:00 INFO    socialscraper.rotation | Konto: Zweit')
        self.assertEqual(text, 'Konto: Zweit')
        text, tag = log.format('12:00:00 WARNING socialscraper.rotation | Konto „Zweit“ pausiert bis 2026-10-12T16:00:00Z (CAPTCHA)')
        self.assertIn('macht eine Pause bis', text)
        self.assertIn('(CAPTCHA)', text)
        self.assertNotIn('2026-10-12T16', text)
        text, _ = log.format('Benutzte Konten: Hauptkonto, Zweit')
        self.assertEqual(text, 'Benutzte Konten: Hauptkonto, Zweit')
        message = 'Der feste Proxy dieses Kontos (1.2.3.4:80) ist nicht erreichbar. Der Abruf wurde NICHT ohne Proxy ausgeführt.'
        self.assertEqual(log.format(message)[0], message)

    def test_notify_scrubs_proxy_passwords(self):
        from socialscraper.net import accountproxy, notify
        from socialscraper.net.proxy import Proxy
        accountproxy._remember(Proxy.parse('http://u:Geheim-Passwort-1@1.2.3.4:80'))
        with self.assertLogs('socialscraper.net.notify', 'WARNING') as logs:
            notify.notify('x', 'Proxy Geheim-Passwort-1 kaputt')
        self.assertNotIn('Geheim-Passwort-1', ' '.join(logs.output))


if __name__ == '__main__':
    unittest.main()
