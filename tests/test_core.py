import datetime
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from socialscraper.config import Settings, load
from socialscraper.models import Video, Comment, iso
from socialscraper.store import Store
from socialscraper.net.proxy import Proxy, ProxyPool
from socialscraper.net.identity import IdentityManager
from socialscraper.net.retry import with_retry

class CoreTests(unittest.TestCase):
    def test_extra_fields_and_raw_are_merged(self):
        video = Video.from_dict({'platform':'youtube','video_id':'v','url':'https://youtu.be/v',
                                 'raw':None,'extra_field':7})
        self.assertEqual(video.raw, {'extra_field':7})

    def test_iso_rejects_infinite_and_normalizes_timezone(self):
        self.assertIsNone(iso(float('inf')))
        self.assertEqual(iso(datetime.datetime(2026,1,1,3,tzinfo=datetime.timezone(datetime.timedelta(hours=3)))),
                         '2026-01-01T00:00:00+00:00')

    def test_ids_update_existing_rows_and_link_snapshots(self):
        with Store(':memory:') as store:
            video=Video('youtube','video1','url',view_count=10)
            comment=Comment('youtube','video1','comment1','erst')
            store.save_result(video,[comment],'2026-01-01T00:00:00+00:00')
            video.view_count=20; comment.text='aktualisiert'
            store.save_result(video,[comment],'2026-01-02T00:00:00+00:00')
            self.assertEqual(store.stats(), {'videos':{'youtube':1},'comments':{'youtube':1}})
            self.assertEqual(store.db.execute('SELECT uid, view_count FROM videos').fetchone(),('youtube:video1',20))
            self.assertEqual(store.db.execute('SELECT uid, text FROM comments').fetchone(),('youtube:comment1','aktualisiert'))
            self.assertEqual(store.db.execute('SELECT DISTINCT video_uid FROM video_snapshots').fetchall(),[('youtube:video1',)])

    def test_failed_result_rolls_back_whole_video(self):
        with Store(':memory:') as store:
            with self.assertRaises(ValueError):
                store.save_result(Video('youtube','v','url'),[Comment('youtube','v','','text')],'now')
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM videos').fetchone()[0],0)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM video_snapshots').fetchone()[0],0)

    def test_failed_batch_rolls_back_previous_records(self):
        with Store(':memory:') as store:
            with self.assertRaises(ValueError):
                store.save_many([Video('youtube','v','url')],[Comment('youtube','v','','text')])
            self.assertEqual(store.stats(),{'videos':{},'comments':{}})

    def test_export_creates_directory_and_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as tmp, Store(':memory:') as store:
            store.save_video(Video('youtube','v','url',caption='Grüße'))
            target=pathlib.Path(tmp)/'nested'/'videos.jsonl';store.dump_jsonl('videos',target)
            row=json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(row['caption'],'Grüße')
            self.assertEqual((row['hashtags'],row['raw']),([],{}))
            with self.assertRaises(ValueError):store.dump_jsonl('videos; DROP TABLE videos',target)
            self.assertEqual(store.stats()['videos'],{'youtube':1})

    @patch.dict(os.environ, {}, clear=True)
    def test_old_toml_table_misplacement_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=pathlib.Path(tmp)/'settings.toml'
            p.write_text('[sphere_presets]\ntiktok="preset"\nproxies=["http://host:80"]\nig_session_user="user"\n',encoding='utf-8')
            cfg=load(p);self.assertEqual(cfg.proxies,['http://host:80'])
            self.assertEqual(cfg.ig_session_user,'user');self.assertEqual(cfg.sphere_presets,{'tiktok':'preset'})

    @patch.dict(os.environ, {}, clear=True)
    def test_current_settings_top_level_values_are_loaded(self):
        # Mitgelieferte Standard-Einstellungen aus einer Wegwerf-Datei – nie die echte settings.toml des Nutzers
        from socialscraper.formlogic import DEFAULT_SETTINGS_TEXT
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/'settings.toml'
            path.write_text(DEFAULT_SETTINGS_TEXT,encoding='utf-8',newline='')
            cfg=load(str(path))
        self.assertEqual(cfg.proxies,[])
        self.assertEqual(cfg.credentials_file,'data/credentials.json')
        self.assertEqual(cfg.session_max_age,24.0);self.assertEqual(cfg.sphere_presets,{'tiktok':'','instagram':''})

    @patch.dict(os.environ, {'SS_MAX_COMMENTS':'0','SS_HEADLESS':'false'}, clear=True)
    def test_zero_comments_and_false_environment_work(self):
        cfg=load('does-not-exist.toml');self.assertEqual(cfg.max_comments,0);self.assertFalse(cfg.headless)

    @patch.dict(os.environ, {'SS_HEADLESS':'maybe'}, clear=True)
    def test_invalid_environment_boolean_rejected(self):
        with self.assertRaises(ValueError):load('does-not-exist.toml')

    def test_proxy_credentials_not_silently_rewritten(self):
        pool=ProxyPool(['http://realuser:p%2Fa%40ss@host:8080'])
        proxy=pool.acquire('one','us');self.assertEqual(proxy.user,'realuser');self.assertEqual(proxy.password,'p/a@ss')
        self.assertEqual(Proxy.parse(proxy.url).password,'p/a@ss')

    def test_proxy_explicit_templates_are_isolated(self):
        pool=ProxyPool(['http://user-session-%7Bidentity%7D:pw@host:80?country=de'])
        self.assertNotEqual(pool.acquire('a','de').user,pool.acquire('b','de').user)
        with self.assertRaises(ValueError):pool.acquire('a','us')

    def test_ipv6_proxy_and_no_scheme_parse(self):
        proxy=Proxy.parse('http://[::1]:8080');self.assertEqual(proxy.url,'http://[::1]:8080')
        self.assertEqual(Proxy.parse('localhost:8080').host,'localhost')
        with self.assertRaises(ValueError):Proxy.parse('http:///')

    def test_identity_configured_location_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=IdentityManager(ProxyPool([]),pathlib.Path(tmp)/'profiles')
            identity=manager.get('test','de',False)
            self.assertTrue((pathlib.Path(tmp)/'identities'/'test.json').exists())
            self.assertEqual(manager.get('test','de',False),identity)
            with self.assertRaises(ValueError):manager.get('test','us',True)
            with self.assertRaises(ValueError):manager.get('../outside')

    def test_retry_transient_failures_and_nonretryable_failure(self):
        calls=[]
        @with_retry(times=3)
        def transient():
            calls.append(1)
            if len(calls)<3:raise TimeoutError('slow')
            return 'ok'
        with patch('socialscraper.net.retry.time.sleep') as sleep:
            self.assertEqual(transient(),'ok');self.assertEqual(sleep.call_count,2)
        calls.clear()
        class SessionFailure(RuntimeError):retryable=False
        @with_retry(times=3)
        def permanent():calls.append(1);raise SessionFailure('expired')
        with patch('socialscraper.net.retry.time.sleep') as sleep:
            with self.assertRaises(SessionFailure):permanent()
            self.assertEqual(len(calls),1);sleep.assert_not_called()
        with self.assertRaises(ValueError):with_retry(times=0)

if __name__=='__main__':unittest.main()

