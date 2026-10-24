"""Tests der KI-Stimmung ohne Fenster: Speicher, Einstellungsdatei, Parser, Lauf gegen einen lokalen Fake-Server der
Anthropic-API (nie die echte API!), Abbrechen/Fortsetzen, Kostenschätzung, Schlüssel nie im Protokoll, Befehlszeile."""
import contextlib
import io
import json
import logging
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from fake_anthropic import KEY, FakeAnthropic

from socialscraper import aiconfig, daemon, guicore, repo, sentiment, sentiment_repo as srepo
from socialscraper.models import Comment, Video
from socialscraper.store import Store

COMMENTS = [('c1', 'Super Video, ich liebe es!', 'Anna'), ('c2', 'Das ist Mist', 'Bert'), ('c3', 'Was ist das für ein Lied?', 'Cleo'),
            ('c4', 'Super Video, ich liebe es!', 'Dora'), ('c5', 'Ganz schlecht gemacht 🙄', 'Emil'), ('c6', 'great stuff', 'Fritz'),
            ('c7', '@anna schau mal https://example.com/konto/anna', 'Gert')]


def make_db(path, comments=COMMENTS):
    with Store(path) as store:
        video = Video('tiktok', 'v1', 'https://www.tiktok.com/@u/video/v1', author='Creator', author_id='u-1')
        store.save_result(video, [Comment('tiktok', 'v1', cid, text, author=author, author_id=f'id-{author}',
                                          created_at='2026-10-0%dT10:00:00+00:00' % (1 + i % 3))
                                  for i, (cid, text, author) in enumerate(comments)], None)
        return video


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = self.root / 'data' / 'scrape.db'
        self.fake = FakeAnthropic()
        self.addCleanup(self.fake.close)
        env = patch.dict(os.environ, {'SS_DB_PATH': str(self.db), 'SS_ANTHROPIC_BASE_URL': self.fake.url})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('ANTHROPIC_API_KEY', None)
        for p in (patch.object(guicore, 'ROOT', self.root),):
            p.start()
            self.addCleanup(p.stop)
        make_db(self.db)
        self.cfg = dict(aiconfig.default_config(), api_key=KEY)
        self.slept = []

    def run_sentiment(self, scope=None, **kw):
        kw.setdefault('pause', 0)
        kw.setdefault('sleep', self.slept.append)
        kw.setdefault('backoff_base', 0.01)
        kw.setdefault('cfg', self.cfg)
        return sentiment.run(self.db, scope, **kw)

    def rows(self):
        with Store(self.db) as s:
            return {r[0]: r[1:] for r in s.db.execute('SELECT comment_uid, label, score, model, prompt_version, text_hash FROM comment_sentiment')}


class RepoTests(Base):
    def test_open_count_save_idempotent_and_distribution(self):
        with Store(self.db) as s:
            db = s.db
            self.assertEqual(srepo.counts(db), {'total': 7, 'analyzable': 7, 'analyzed': 0, 'open': 7})
            items = srepo.open_comments(db)
            self.assertEqual(len(items), 7)
            rows = [(items[0]['uid'], items[0]['hash'], 'positiv', 0.9), (items[1]['uid'], items[1]['hash'], 'negativ', -0.5)]
            self.assertEqual(srepo.save_results(db, rows, 'm1', 1), 2)
            self.assertEqual(srepo.save_results(db, rows, 'm1', 1), 2)          # idempotent
            self.assertEqual(db.execute('SELECT COUNT(*) FROM comment_sentiment').fetchone()[0], 2)
            self.assertEqual(srepo.counts(db)['open'], 5)
            d = srepo.distribution(db)
            self.assertEqual((d['positive'], d['neutral'], d['negative'], d['analyzed'], d['total_comments']), (1, 0, 1, 2, 7))
            self.assertEqual(d['kind'], 'ki_einschaetzung')
            self.assertAlmostEqual(d['coverage'], 2 / 7, places=3)
            self.assertAlmostEqual(d['score_avg'], 0.2)
            self.assertEqual(d['model'], 'm1')
            with self.assertRaises(ValueError):
                srepo.save_results(db, [(items[2]['uid'], items[2]['hash'], 'toll', 0.1)], 'm1', 1)
            with self.assertRaises(sqlite3.IntegrityError):   # die Tabelle prüft selbst
                db.execute("INSERT INTO comment_sentiment (comment_uid, label, score) VALUES ('x','positiv',2.5)")

    def test_changed_text_makes_it_open_again_and_scores_are_clamped(self):
        with Store(self.db) as s:
            db = s.db
            items = srepo.open_comments(db)
            srepo.save_results(db, [(items[0]['uid'], items[0]['hash'], 'positiv', 5.0)], 'm', 1)
            self.assertEqual(db.execute('SELECT score FROM comment_sentiment').fetchone()[0], 1.0)
            self.assertEqual(srepo.counts(db)['analyzed'], 1)
            db.execute("UPDATE comments SET text='ganz anders' WHERE uid=?", (items[0]['uid'],))
            db.commit()
            self.assertEqual(srepo.counts(db)['analyzed'], 0)
            self.assertEqual(srepo.distribution(db)['analyzed'], 0)
            self.assertIn(items[0]['uid'], [i['uid'] for i in srepo.open_comments(db)])
            db.execute("UPDATE comments SET text='  Super   Video,\n ich liebe es! ' WHERE uid=?", (items[0]['uid'],))
            db.commit()   # nur anderer Leerraum -> derselbe Text
            self.assertEqual(srepo.text_hash('Super Video, ich liebe es!'), srepo.text_hash('  Super   Video,\n ich liebe es! '))

    def test_scopes_video_group_platform_and_time(self):
        with Store(self.db) as s:
            db = s.db
            other = Video('youtube', 'y1', 'https://youtu.be/y1', author='Zed')
            s.save_result(other, [Comment('youtube', 'y1', 'z1', 'Hallo', author='Q', created_at='2026-09-30T08:00:00Z')], None)
            g = repo.create_group(db, 'Eins')
            repo.add_videos_to_group(db, g['id'], ['youtube:y1'])
            self.assertEqual(srepo.counts(db, {'videos': ['youtube:y1']})['total'], 1)
            self.assertEqual(srepo.counts(db, {'videos': []})['total'], 0)
            self.assertEqual(srepo.counts(db, {'group': g['id']})['total'], 1)
            self.assertEqual(srepo.counts(db, {'platform': 'tiktok'})['total'], 7)
            self.assertEqual(srepo.counts(db, {'until': '2026-09-30'})['total'], 1)
            self.assertEqual(srepo.counts(db, {'since': '2026-10-01'})['total'], 7)
            self.assertEqual(srepo.counts(db, {'since': '2026-10-02', 'until': '2026-10-02'})['total'], 2)
            with self.assertRaises(ValueError):
                srepo.counts(db, {'since': 'gestern'})
            self.assertEqual(srepo.reset(db, {'videos': ['youtube:y1']}), 0)

    def test_series_by_day_and_week_and_reset(self):
        self.run_sentiment()
        with Store(self.db) as s:
            db = s.db
            days = srepo.series(db)
            self.assertEqual([d['date'] for d in days], ['2026-10-01', '2026-10-02', '2026-10-03'])
            self.assertEqual(sum(d['analyzed'] for d in days), 7)
            self.assertTrue(all(abs(sum(d['share'].values()) - 1) < 0.01 for d in days))
            weeks = srepo.series(db, interval='week')
            self.assertEqual([w['date'] for w in weeks], ['2026-09-28'])
            self.assertEqual(weeks[0]['analyzed'], 7)
            with self.assertRaises(ValueError):
                srepo.series(db, interval='jahr')
            self.assertEqual(srepo.reset(db, {'videos': ['tiktok:v1']}), 7)
            self.assertEqual(srepo.counts(db)['open'], 7)

    def test_old_database_is_migrated_without_loss(self):
        old = self.root / 'alt.db'
        con = sqlite3.connect(old)
        con.executescript('''CREATE TABLE videos (uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, url TEXT, author TEXT, author_id TEXT,
            caption TEXT, created_at TEXT, duration_s REAL, view_count INTEGER, like_count INTEGER, comment_count INTEGER, share_count INTEGER,
            save_count INTEGER, hashtags TEXT, mentions TEXT, music TEXT, language TEXT, thumbnail TEXT, raw TEXT, fetched_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE comments (uid TEXT PRIMARY KEY, platform TEXT, video_id TEXT, comment_id TEXT, text TEXT, author TEXT, author_id TEXT,
            created_at TEXT, like_count INTEGER, reply_count INTEGER, parent_id TEXT, is_author_reply INTEGER, language TEXT, raw TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO comments (uid, platform, video_id, comment_id, text) VALUES ('tiktok:1','tiktok','v','1','Hallo');''')
        con.commit()
        con.close()
        with Store(old) as s:
            tables = {r[0] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'comment_sentiment', 'group_people'} <= tables)
            self.assertEqual(s.db.execute('SELECT text FROM comments').fetchone()[0], 'Hallo')
            self.assertEqual(srepo.counts(s.db)['open'], 1)
        # eine Datenbank ganz ohne die neue Tabelle (nur gelesen) bricht die Lesefunktionen nicht
        bare = sqlite3.connect(':memory:')
        self.addCleanup(bare.close)
        bare.executescript('CREATE TABLE comments (uid TEXT, platform TEXT, video_id TEXT, text TEXT, created_at TEXT, first_seen_at TEXT, rowid2 INT);'
                           "INSERT INTO comments VALUES ('a','tiktok','v','Hallo',NULL,'2026-10-01',1);")
        self.assertEqual(srepo.counts(bare)['open'], 1)
        self.assertEqual(srepo.distribution(bare)['analyzed'], 0)
        self.assertEqual(srepo.series(bare), [])


class TextTests(unittest.TestCase):
    def test_prepare_text_sends_only_the_text(self):
        t = sentiment.prepare_text('  Hallo @max.mustermann, schau   https://www.tiktok.com/@max/video/1?x=1 und www.beispiel.de/p\nok ')
        self.assertEqual(t, 'Hallo @nutzer, schau [Link] und [Link] ok')
        self.assertNotIn('max', t)
        long = sentiment.prepare_text('a' * 2000)
        self.assertEqual(len(long), sentiment.MAX_CHARS + 1)
        self.assertTrue(long.endswith('…'))
        self.assertEqual(sentiment.prepare_text(None), '')

    def test_parse_entries_is_robust(self):
        ok = '```json\n[{"n":1,"label":"positiv","score":0.8},{"n":2,"label":"Negative","score":-0.4},{"n":3,"label":"neutral","score":0}]\n```'
        self.assertEqual(sentiment.parse_entries(ok), {1: ('positiv', 0.8), 2: ('negativ', -0.4), 3: ('neutral', 0.0)})
        self.assertEqual(sentiment.parse_entries('Hier: [{"n":1,"label":"positiv","score":0.5}] fertig'), {1: ('positiv', 0.5)})
        # abgeschnitten: die vollständigen Einträge bleiben
        self.assertEqual(sentiment.parse_entries('[{"n":1,"label":"positiv","score":0.5},{"n":2,"label":"neg'), {1: ('positiv', 0.5)})
        # eingewickelt
        self.assertEqual(sentiment.parse_entries('{"ergebnisse":[{"n":4,"label":"neutral","score":0.1}]}'), {4: ('neutral', 0.1)})
        # unlesbare Einträge werden übersprungen, der Rest bleibt
        mixed = '[{"n":1,"label":"super","score":1},{"n":2,"label":"positiv"},{"n":"x","label":"positiv","score":1},{"n":3,"label":"negativ","score":-1}]'
        self.assertEqual(sentiment.parse_entries(mixed), {3: ('negativ', -1.0)})
        self.assertEqual(sentiment.parse_entries(''), {})
        self.assertEqual(sentiment.parse_entries('kein json {'), {})
        # das erste Auftreten zählt; Vorzeichen wird zum Label passend gemacht
        self.assertEqual(sentiment.parse_entries('[{"n":1,"label":"negativ","score":0.7},{"n":1,"label":"positiv","score":1}]'), {1: ('negativ', -0.7)})
        self.assertEqual(sentiment.parse_entries('[{"n":1,"label":"neutral","score":0.9}]'), {1: ('neutral', 0.3)})
        self.assertEqual(sentiment.parse_entries('[{"n":1,"label":"positiv","score":NaN}]'), {})

    def test_user_message_numbers_the_inputs_and_keeps_text_as_data(self):
        msg = json.loads(sentiment.user_message(['a "b"', 'zeile\nzwei']))
        self.assertEqual([k['n'] for k in msg['kommentare']], [1, 2])
        self.assertEqual(msg['kommentare'][1]['text'], 'zeile\nzwei')

    def test_base_url_only_accepts_the_own_computer(self):
        self.assertEqual(sentiment.base_url(), sentiment.API_URL)
        self.assertEqual(sentiment.base_url('http://127.0.0.1:9/'), 'http://127.0.0.1:9/v1/messages')
        self.assertEqual(sentiment.base_url('https://evil.example'), sentiment.API_URL)   # Schlüssel nie an fremde Adressen
        with patch.dict(os.environ, {sentiment.ENV_BASE_URL: 'http://localhost:1234'}):
            self.assertEqual(sentiment.base_url(), 'http://localhost:1234/v1/messages')
        with patch.dict(os.environ, {sentiment.ENV_BASE_URL: 'http://evil.example:80'}):
            self.assertEqual(sentiment.base_url(), sentiment.API_URL)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('ANTHROPIC_API_KEY', None)

    def test_defaults_roundtrip_redaction_and_atomic_file(self):
        cfg = aiconfig.load(self.root)
        self.assertEqual(cfg, aiconfig.DEFAULTS)
        self.assertEqual((cfg['model'], cfg['max_per_run'], cfg['auto_after_runs']), ('claude-haiku-5-5', 5000, False))
        saved = aiconfig.save(dict(cfg, api_key='  sk-test-1234567890  ', price_in_per_mtok=1.5, auto_after_runs=True), self.root)
        self.assertEqual(saved['api_key'], 'sk-test-1234567890')
        again = aiconfig.load(self.root)
        self.assertEqual((again['price_in_per_mtok'], again['price_out_per_mtok'], again['auto_after_runs']), (1.5, None, True))
        self.assertEqual(aiconfig.redacted(again)['api_key'], aiconfig.MASK)
        self.assertNotIn('sk-test', json.dumps(aiconfig.redacted(again)))
        self.assertEqual([p.name for p in (self.root / 'data').iterdir()], ['ai.json'])   # keine .tmp-Reste
        if os.name != 'nt':
            self.assertEqual(oct((self.root / 'data' / 'ai.json').stat().st_mode & 0o777), '0o600')

    def test_validation_names_problems_without_echoing_the_key(self):
        with self.assertRaises(aiconfig.AiConfigError) as ctx:
            aiconfig.save(dict(aiconfig.DEFAULTS, api_key='geheim mit leerzeichen', max_per_run=0, price_out_per_mtok=-1), self.root)
        text = str(ctx.exception)
        self.assertNotIn('geheim', text)
        self.assertIn('Schlüssel', text)
        self.assertIn('pro Lauf', text)
        self.assertIn('Preis', text)
        self.assertFalse((self.root / 'data' / 'ai.json').exists())    # es wurde nichts geschrieben

    def test_key_source_file_before_environment_and_scrub(self):
        self.assertEqual(aiconfig.api_key(aiconfig.default_config()), ('', 'fehlt'))
        os.environ['ANTHROPIC_API_KEY'] = 'sk-from-env-0123456789'
        self.assertEqual(aiconfig.api_key(aiconfig.default_config()), ('sk-from-env-0123456789', 'umgebung'))
        cfg = dict(aiconfig.default_config(), api_key='sk-from-file-0123456789')
        self.assertEqual(aiconfig.api_key(cfg), ('sk-from-file-0123456789', 'datei'))
        cleaned = aiconfig.scrub('Fehler mit sk-from-file-0123456789 und sk-from-env-0123456789', cfg)
        self.assertNotIn('sk-from', cleaned)

    def test_damaged_file_is_ignored_and_replaced_with_copy(self):
        p = aiconfig.path(self.root)
        p.parent.mkdir(parents=True)
        p.write_text('{kaputt', encoding='utf-8')
        cfg, problem = aiconfig.load_with_status(self.root)
        self.assertEqual(cfg, aiconfig.DEFAULTS)
        self.assertIn('beschädigt', problem)
        aiconfig.save(dict(cfg, api_key='sk-new-key-0123456789'), self.root)
        self.assertTrue((p.parent / 'ai.json.defekt').exists())
        self.assertEqual(aiconfig.load(self.root)['api_key'], 'sk-new-key-0123456789')

    def test_update_changes_only_the_named_field(self):
        aiconfig.save(dict(aiconfig.DEFAULTS, api_key='sk-keep-0123456789', max_per_run=77), self.root)
        aiconfig.update(self.root, auto_after_runs=True)
        cfg = aiconfig.load(self.root)
        self.assertEqual((cfg['api_key'], cfg['max_per_run'], cfg['auto_after_runs']), ('sk-keep-0123456789', 77, True))
        with self.assertRaises(ValueError):
            aiconfig.update(self.root, unbekannt=1)


class EstimateTests(Base):
    def test_estimate_counts_dedups_and_has_no_invented_prices(self):
        est = sentiment.estimate(self.db, None, self.cfg)
        self.assertEqual((est['open_total'], est['to_send'], est['unique_texts'], est['requests']), (7, 7, 6, 1))
        self.assertGreater(est['input_tokens'], 0)
        self.assertEqual(est['output_tokens'], 6 * sentiment.OUT_TOKENS_PER_ENTRY)
        self.assertIsNone(est['cost'])
        text = '\n'.join(sentiment.estimate_lines(est))
        self.assertIn('Anfragen an die API: 1', text)
        self.assertIn('Tokens', text)
        self.assertNotIn('€', text)
        self.assertNotIn('$', text)
        self.assertIn('Preise einträgst', text)

    def test_estimate_with_prices_and_run_limit(self):
        cfg = dict(self.cfg, price_in_per_mtok=3.0, price_out_per_mtok=15.0, max_per_run=3)
        est = sentiment.estimate(self.db, None, cfg)
        self.assertEqual((est['to_send'], est['open_total'], est['limited']), (3, 7, True))
        expected = (est['input_tokens'] * 3.0 + est['output_tokens'] * 15.0) / 1_000_000
        self.assertAlmostEqual(est['cost'], expected)
        self.assertIn('Betrag', '\n'.join(sentiment.estimate_lines(est)))
        self.assertIn('Obergrenze', '\n'.join(sentiment.estimate_lines(est)))
        self.assertEqual(sentiment.estimate(self.db, None, self.cfg, max_comments=2)['to_send'], 2)

    def test_batches_and_nothing_to_do(self):
        make_db(self.root / 'viele.db', [(f'k{i}', f'Text Nummer {i}', f'u{i}') for i in range(95)])
        est = sentiment.estimate(self.root / 'viele.db', None, self.cfg)
        self.assertEqual(est['requests'], 3)    # 40 + 40 + 15
        self.run_sentiment()
        self.assertIn('Nichts zu tun', sentiment.estimate_lines(sentiment.estimate(self.db, None, self.cfg))[0])
        self.assertEqual(sentiment.estimate(self.root / 'gibt-es-nicht.db', None, self.cfg)['to_send'], 0)


class RunTests(Base):
    def test_happy_path_saves_everything_sends_unique_texts_and_no_author_data(self):
        progress = []
        result = self.run_sentiment(progress=lambda d, t, i: progress.append((d, t)))
        self.assertEqual((result['status'], result['analyzed'], result['skipped'], result['remaining']), ('ok', 7, 0, 0))
        self.assertEqual(self.fake.sent_texts.count('Super Video, ich liebe es!'), 1)     # gleiche Texte nur einmal
        self.assertEqual(len(self.fake.sent_texts), 6)
        self.assertEqual(progress[0], (0, 7))
        self.assertEqual(progress[-1], (7, 7))
        rows = self.rows()
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows['tiktok:c1'][0], 'positiv')
        self.assertEqual(rows['tiktok:c4'][0], 'positiv')                              # Ergebnis gilt für alle gleichen Texte
        self.assertEqual(rows['tiktok:c2'][0], 'negativ')
        self.assertEqual(rows['tiktok:c3'][0], 'neutral')
        self.assertEqual(rows['tiktok:c1'][2:4], ('claude-haiku-5-5', sentiment.PROMPT_VERSION))
        req = self.fake.requests[0]
        self.assertEqual(req['headers']['x-api-key'], KEY)
        self.assertEqual(req['headers']['anthropic-version'], '2023-06-01')
        self.assertEqual(req['body']['model'], 'claude-haiku-5-5')
        wire = json.dumps(req['body'], ensure_ascii=False)
        for secret in ('Anna', 'Bert', 'Creator', 'id-Anna', 'u-1', 'tiktok.com', 'example.com', 'anna'):
            self.assertNotIn(secret, wire)
        self.assertIn('[Link]', wire)
        self.assertIn('@nutzer', wire)
        self.assertEqual(result['requests'], 1)
        self.assertEqual((result['input_tokens'], result['output_tokens']), (100, 20))
        # zweiter Lauf: nichts mehr offen, keine Anfrage
        again = self.run_sentiment()
        self.assertEqual((again['status'], again['analyzed'], again['requests']), ('ok', 0, 0))
        self.assertIn('Nichts zu tun', again['message'])
        self.assertEqual(len(self.fake.requests), 1)

    def test_prompt_names_reddit_and_x_without_authors_and_version_is_2(self):
        with Store(self.db) as s:
            s.save_result(Video('reddit', 'r1', 'https://www.reddit.com/r/test/comments/r1/', author='Poster', author_id='t2_poster'),
                          [Comment('reddit', 'r1', 'rc1', 'Na klar, genau so. /s', author='RedUser', author_id='t2_red')], None)
            s.save_result(Video('x', 'x1', 'https://x.com/Kanal/status/x1', author='Kanal', author_id='77'),
                          [Comment('x', 'x1', 'xc1', '@Kanal super #Tag', author='XUser', author_id='99')], None)
        result = self.run_sentiment()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(sentiment.PROMPT_VERSION, 2)
        self.assertTrue(all(r[3] == 2 for r in self.rows().values()))
        body = self.fake.requests[0]['body']
        system = body['system'] if isinstance(body['system'], str) else json.dumps(body['system'], ensure_ascii=False)
        self.assertIn('Reddit', system)
        self.assertIn(', X)', system)
        self.assertIn('/s', system)
        self.assertIn('Hashtags', system)
        self.assertNotIn('Reddit', json.dumps(body['messages'], ensure_ascii=False))
        wire = json.dumps(body, ensure_ascii=False)
        for secret in ('Poster', 't2_poster', 'RedUser', 't2_red', 'XUser', 'reddit.com', 'x.com', 'Kanal', 'id-Anna', 'Creator'):
            self.assertNotIn(secret, wire)
        self.assertIn('@nutzer', wire)
        self.assertIn('Na klar, genau so. /s', self.fake.sent_texts)

    def test_only_new_comments_are_sent_afterwards(self):
        self.run_sentiment()
        with Store(self.db) as s:
            s.save_result(Video('tiktok', 'v1', 'https://www.tiktok.com/@u/video/v1'),
                          [Comment('tiktok', 'v1', 'neu1', 'Ganz neu, super', author='X')], None)
        self.fake.sent_texts.clear()
        result = self.run_sentiment()
        self.assertEqual((result['analyzed'], self.fake.sent_texts), (1, ['Ganz neu, super']))

    def test_batches_of_the_given_size_and_run_limit(self):
        result = self.run_sentiment(batch_size=2)
        self.assertEqual((result['status'], result['requests']), ('ok', 3))               # 6 verschiedene Texte / 2
        self.assertEqual(max(len(json.loads(r['body']['messages'][0]['content'])['kommentare']) for r in self.fake.requests), 2)

    def test_run_limit_leaves_the_rest_open_and_next_run_continues(self):
        result = self.run_sentiment(max_comments=3)
        self.assertEqual((result['status'], result['analyzed'], result['remaining']), ('ok', 3, 4))
        self.assertIn('Obergrenze', result['message'])
        self.assertEqual(len(self.rows()), 3)
        result = self.run_sentiment()
        self.assertEqual((result['analyzed'], result['remaining']), (4, 0))

    def test_auth_error_aborts_with_clear_message_and_saves_nothing(self):
        cfg = dict(self.cfg, api_key='sk-ant-WRONG-0123456789')
        result = self.run_sentiment(cfg=cfg)
        self.assertEqual((result['status'], result['error_kind']), ('failed', 'auth'))
        self.assertIn('Der API-Schlüssel wird nicht akzeptiert', result['message'])
        self.assertEqual(len(self.fake.requests), 1)       # keine Wiederholungen bei 401
        self.assertEqual(self.rows(), {})
        self.assertNotIn('WRONG', json.dumps(result))
        self.fake.faults.append(403)
        self.assertEqual(self.run_sentiment()['error_kind'], 'auth')

    def test_missing_key_and_missing_data(self):
        result = self.run_sentiment(cfg=aiconfig.default_config())
        self.assertEqual((result['status'], result['error_kind']), ('failed', 'nokey'))
        self.assertIn('API-Schlüssel', result['message'])
        self.assertEqual(self.fake.requests, [])
        result = sentiment.run(self.root / 'nichts.db', None, cfg=self.cfg)
        self.assertEqual(result['status'], 'failed')

    def test_rate_limit_waits_for_retry_after_then_succeeds(self):
        self.fake.faults.append(('retry', 429, 3))
        result = self.run_sentiment()
        self.assertEqual((result['status'], result['analyzed']), ('ok', 7))
        self.assertEqual(self.slept, [3.0])                                    # Retry-After wurde beachtet
        self.assertEqual(len(self.fake.requests), 2)

    def test_server_errors_use_backoff_and_give_up_cleanly(self):
        self.fake.faults += [500, 502]
        result = self.run_sentiment()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(self.slept, [0.01, 0.02])
        self.slept.clear()
        self.fake.faults += [500] * sentiment.MAX_ATTEMPTS
        result = self.run_sentiment(cfg=dict(self.cfg, model='claude-sonnet-5-5'))
        self.assertEqual(result['status'], 'ok')       # alles war schon eingestuft: es wurde gar nicht gefragt
        make_db(self.root / 'zwei.db', [('a', 'erster Text', 'u'), ('b', 'zweiter Text', 'v'), ('c', 'dritter Text', 'w')])
        self.fake.faults.clear()
        self.fake.faults += [('retry', 529, 0)] * sentiment.MAX_ATTEMPTS
        result = sentiment.run(self.root / 'zwei.db', None, cfg=self.cfg, pause=0, sleep=self.slept.append, backoff_base=0.01)
        self.assertEqual((result['status'], result['error_kind'], result['analyzed']), ('failed', 'rate', 0))
        self.assertIn('später', result['message'])

    def test_partial_success_keeps_saved_batches_when_a_later_one_fails(self):
        # erster Stapel gelingt, danach fällt der Server dauerhaft aus
        calls = {'n': 0}

        class Faulty(list):
            def pop(inner, index=0):
                calls['n'] += 1
                return 500 if calls['n'] >= 2 else None

            def __bool__(inner):
                return True
        self.fake.faults = Faulty()
        result = self.run_sentiment(batch_size=2)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['error_kind'], 'rate')
        self.assertGreater(result['analyzed'], 0)
        self.assertEqual(len(self.rows()), result['analyzed'])
        self.assertGreater(result['remaining'], 0)
        self.assertIn('bleibt offen', result['message'])
        self.fake.faults = []
        resumed = self.run_sentiment()
        self.assertEqual(resumed['status'], 'ok')
        self.assertEqual(len(self.rows()), 7)

    def test_broken_json_is_retried_and_unreadable_entries_do_not_lose_the_batch(self):
        self.fake.faults += [('raw', 'Tut mir leid, hier ist kein JSON'), ('raw', '[{"n":1,')]
        result = self.run_sentiment()
        self.assertEqual((result['status'], result['analyzed']), ('ok', 7))
        self.assertEqual(len(self.fake.requests), 3)
        # dauerhaft kaputt: nichts gespeichert, Kommentare bleiben offen, keine Endlosschleife
        make_db(self.root / 'kaputt.db', [('a', 'erster Text', 'u')])
        self.fake.faults += [('raw', 'nein')] * (sentiment.PARSE_ATTEMPTS * 2)
        before = len(self.fake.requests)
        result = sentiment.run(self.root / 'kaputt.db', None, cfg=self.cfg, pause=0, sleep=self.slept.append)
        self.assertEqual((result['analyzed'], result['skipped'], result['status']), (0, 1, 'partial'))
        self.assertEqual(len(self.fake.requests) - before, sentiment.PARSE_ATTEMPTS)
        self.assertIn('nicht lesbar', result['message'])

    def test_missing_entries_are_asked_for_once_and_then_stay_open(self):
        self.fake.faults += [('drop_last', 2)]
        result = self.run_sentiment()
        self.assertEqual((result['status'], result['analyzed']), ('ok', 7))   # die zwei fehlenden kamen im zweiten Anlauf
        self.assertEqual(len(self.fake.requests), 2)
        second = json.loads(self.fake.requests[1]['body']['messages'][0]['content'])['kommentare']
        self.assertEqual(len(second), 2)
        self.assertEqual([k['n'] for k in second], [1, 2])      # neu durchnummeriert

    def test_cancel_keeps_saved_batches_and_resume_sends_only_open_ones(self):
        cancel = threading.Event()
        result = self.run_sentiment(batch_size=2, cancel=cancel, progress=lambda d, t, i: cancel.set() if d else None)
        self.assertEqual(result['status'], 'cancelled')
        self.assertIn('Abgebrochen', result['message'])
        done = len(self.rows())
        self.assertGreater(done, 0)
        self.assertLess(done, 7)
        self.fake.sent_texts.clear()
        resumed = self.run_sentiment()
        self.assertEqual(resumed['status'], 'ok')
        self.assertEqual(len(self.rows()), 7)
        self.assertEqual(resumed['analyzed'], 7 - done)
        self.assertLessEqual(len(self.fake.sent_texts), 6)
        self.assertTrue(set(self.fake.sent_texts).isdisjoint({'Super Video, ich liebe es!'}) or done == 0)

    def test_cancel_during_wait_and_before_start(self):
        cancel = threading.Event()
        self.fake.faults.append(('retry', 429, 5))
        result = self.run_sentiment(cancel=cancel, sleep=lambda s: cancel.set())
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(self.rows(), {})
        done = threading.Event()
        done.set()
        self.assertEqual(self.run_sentiment(cancel=done)['status'], 'cancelled')
        self.assertEqual(self.run_sentiment(cancel=lambda: True)['status'], 'cancelled')

    def test_progress_callback_errors_do_not_stop_the_run(self):
        def broken(*a):
            raise RuntimeError('Anzeige kaputt')
        self.assertEqual(self.run_sentiment(progress=broken)['status'], 'ok')

    def test_second_run_at_the_same_time_is_refused(self):
        from socialscraper.runlock import OsLock
        lock = OsLock(self.db.with_name('sentiment.lock'))
        self.assertTrue(lock.try_acquire({'owner': 'test'}))
        try:
            result = self.run_sentiment()
            self.assertEqual((result['status'], result['error_kind']), ('busy', 'busy'))
            self.assertEqual(self.fake.requests, [])
        finally:
            lock.release()
        self.assertEqual(self.run_sentiment()['status'], 'ok')

    def test_scope_only_selected_video(self):
        with Store(self.db) as s:
            s.save_result(Video('youtube', 'y1', 'https://youtu.be/y1'), [Comment('youtube', 'y1', 'z1', 'Mist hier', author='Q')], None)
        result = self.run_sentiment({'videos': ['youtube:y1']})
        self.assertEqual(result['analyzed'], 1)
        self.assertEqual(list(self.rows()), ['youtube:z1'])

    def test_key_never_appears_in_logs_messages_or_files_other_than_ai_json(self):
        records = []

        class Grab(logging.Handler):
            def emit(inner, record):
                records.append(record.getMessage())
        handler = Grab(level=logging.DEBUG)
        root_logger = logging.getLogger()
        old_level = root_logger.level
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        self.addCleanup(lambda: (root_logger.removeHandler(handler), root_logger.setLevel(old_level)))
        outputs = []
        self.fake.faults += [('retry', 429, 0), 500, ('raw', 'kaputt')]
        outputs.append(self.run_sentiment())
        self.fake.key = 'anderer-schluessel-0123456789'
        outputs.append(self.run_sentiment(cfg=dict(self.cfg, model='claude-opus-5-5')))     # nichts offen -> kein Aufruf
        make_db(self.root / 'x.db', [('a', 'Text', 'u')])
        outputs.append(sentiment.run(self.root / 'x.db', None, cfg=self.cfg, pause=0, sleep=self.slept.append))   # 401
        outputs.append(sentiment.test_connection(self.cfg, base_url_=self.fake.url, sleep=self.slept.append))
        blob = json.dumps(outputs, ensure_ascii=False, default=str) + '\n'.join(records)
        self.assertNotIn(KEY, blob)
        self.assertNotIn(KEY.split('-')[-1], blob)
        self.assertTrue(records)             # es wurde überhaupt etwas protokolliert
        for path in self.root.rglob('*'):
            if path.is_file() and path.suffix in ('.log', '.json', '.toml', '.txt'):
                self.assertNotIn(KEY, path.read_text(encoding='utf-8', errors='replace'))

    def test_connection_test_sends_one_tiny_request(self):
        ok, text = sentiment.test_connection(self.cfg, base_url_=self.fake.url)
        self.assertTrue(ok)
        self.assertIn('in Ordnung', text)
        body = self.fake.requests[0]['body']
        self.assertEqual(body['max_tokens'], 16)
        self.assertNotIn('kommentare', json.dumps(body))
        self.fake.key = 'andere-0123456789'
        ok, text = sentiment.test_connection(self.cfg, base_url_=self.fake.url)
        self.assertFalse(ok)
        self.assertIn('wird nicht akzeptiert', text)
        self.assertFalse(sentiment.test_connection(aiconfig.default_config())[0])


class AutoAndDaemonTests(Base):
    def _run_row(self, started):
        with Store(self.db) as s:
            run_id = repo.start_run(s.db, None, 'test', 'daemon')
            s.db.execute('UPDATE runs SET started_at=? WHERE id=?', (started, run_id))
            s.db.commit()
            return run_id

    def test_auto_classifies_only_comments_seen_since_the_run_and_is_off_by_default(self):
        with Store(self.db) as s:
            s.db.execute("UPDATE comments SET first_seen_at='2026-01-01T00:00:00Z'")
            s.save_result(Video('tiktok', 'v1', 'https://www.tiktok.com/@u/video/v1'),
                          [Comment('tiktok', 'v1', 'frisch', 'Frisch und super', author='N')], None)
            s.db.commit()
        run_id = self._run_row('2026-06-01T00:00:00Z')
        daemon._sentiment(self.root, self.db, {'run_id': run_id, 'comments_new': 1}, lambda: False)
        self.assertEqual(self.fake.requests, [])                                  # Schalter aus (Standard): nichts passiert
        aiconfig.save(dict(self.cfg, auto_after_runs=True), self.root)
        daemon._sentiment(self.root, self.db, {'run_id': run_id, 'comments_new': 0}, lambda: False)
        self.assertEqual(self.fake.requests, [])                                  # nichts Neues im Lauf: keine Kosten
        daemon._sentiment(self.root, self.db, {'run_id': run_id, 'comments_new': 1}, lambda: False)
        self.assertEqual(list(self.rows()), ['tiktok:frisch'])
        self.assertEqual(self.fake.sent_texts, ['Frisch und super'])

    def test_auto_never_raises_even_when_the_api_fails(self):
        aiconfig.save(dict(self.cfg, auto_after_runs=True, api_key='sk-ant-WRONG-0123456789'), self.root)
        run_id = self._run_row('2026-01-01T00:00:00Z')
        self.assertIsNone(daemon._sentiment(self.root, self.db, {'run_id': run_id, 'comments_new': 3}, lambda: False))
        self.assertEqual(self.rows(), {})
        with patch('socialscraper.sentiment.run', side_effect=RuntimeError('kaputt')):
            self.assertIsNone(daemon._sentiment(self.root, self.db, {'run_id': run_id, 'comments_new': 3}, lambda: False))
        failed = sentiment.auto_for_run(self.root, self.db, '2026-01-01T00:00:00Z')   # Schalter an, aber falscher Schlüssel: Ergebnis, kein Fehler
        self.assertEqual(failed['status'], 'failed')


class CliTests(Base):
    def call(self, *argv):
        from socialscraper.commands import sentiment as cmd
        import argparse
        parser = argparse.ArgumentParser()
        cmd.add_arguments(parser)
        args = parser.parse_args(list(argv) + ['--db', str(self.db)])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cmd.run(args, types.SimpleNamespace(db_path=str(self.db)))
        return code, out.getvalue()

    def test_status_estimate_and_run_needs_yes(self):
        aiconfig.save(self.cfg, self.root)
        code, out = self.call('status')
        self.assertEqual(code, 0)
        self.assertIn('KI-Einschätzung', out)
        self.assertIn('offen: 7', out)
        self.assertIn('vorhanden', out)
        self.assertNotIn(KEY, out)
        code, out = self.call('estimate')
        self.assertEqual(code, 0)
        self.assertIn('Anfragen an die API: 1', out)
        code, out = self.call('run')
        self.assertEqual(code, 0)
        self.assertIn('nichts gesendet', out)
        self.assertEqual(self.fake.requests, [])
        code, out = self.call('run', '--yes', '--max', '2')
        self.assertEqual(code, 0)
        self.assertIn('2 Kommentare als KI-Einschätzung eingestuft', out)
        self.assertEqual(len(self.rows()), 2)
        code, out = self.call('status')
        self.assertIn('eingestuft: 2 von 7', out)
        code, out = self.call('estimate', '--video', 'tiktok:gibt-es-nicht')
        self.assertIn('Nichts zu tun', out)

    def test_run_without_key_fails_with_message(self):
        code, out = self.call('run', '--yes')
        self.assertEqual(code, 1)
        self.assertIn('API-Schlüssel', out)

    def test_command_is_registered(self):
        from socialscraper import cli
        self.assertIn('sentiment', cli.parser().format_help())


if __name__ == '__main__':
    unittest.main()
