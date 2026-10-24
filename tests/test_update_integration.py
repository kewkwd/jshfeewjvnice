import contextlib
import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from socialscraper import aktionen, accountvault, repo, signup, streuung, warmup, mailanbieter
from socialscraper.config import Settings
from socialscraper.store import Store
from socialscraper.net import accountproxy, proxymode
from socialscraper.net.cookies import CookieJar


class IntegrationTests(unittest.TestCase):
    def test_http_success_does_not_hide_platform_rejection(self):
        from socialscraper.aktionen import endpunkte
        response=MagicMock(status_code=200)
        response.json.return_value={'status_code': 1001, 'message': 'rejected'}
        self.assertFalse(endpunkte._urteil(response).ok)
        response.json.return_value={'errors': [{'message':'failed'}]}
        self.assertFalse(endpunkte._urteil(response).ok)
        response.json.return_value={'success': True}
        self.assertTrue(endpunkte._urteil(response).ok)

    def test_review_needs_confirmation(self):
        from socialscraper.aktionen import browser
        page=MagicMock()
        page.inner_text.return_value='Bitte versuche es später noch einmal.'
        self.assertFalse(browser._bewerte_seite(page).ok)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root/'data/test.sqlite'
        self.cfg = Settings(db_path=str(self.db), profiles_dir=str(self.root/'data/profiles'), proxy_mode='aus')
        from profile_fixture import prepare
        prepare(self.cfg, 'reddit_new', 'unconfirmed')
        with Store(self.db) as store:
            self.account = repo.add_account(store.db,'reddit','Test')

    def tearDown(self):
        self.temp.cleanup()

    def test_all_registration_modules_load(self):
        signup._lade_module()
        self.assertEqual(set(signup.MODULE),set(repo.ACCOUNT_PLATFORMS))
        self.assertTrue(all(hasattr(m,'REGISTER_URL') for m in signup.MODULE.values()))
        mailanbieter._lade()
        self.assertEqual(set(mailanbieter.ANBIETER),{'gmx.net','web.de','gmail.com','outlook.com'})

    def test_review_accounts_are_separate_from_scraping(self):
        with Store(self.db) as store:
            account=repo.add_account(store.db,'google','Review')
            self.assertEqual(account['platform'],'google')
        self.assertNotIn('google',repo.PLATFORMS)

    def test_legacy_actions_migrate_without_losing_rows(self):
        with Store(self.db) as store:
            store.db.execute('DROP TABLE aktionen')
            store.db.execute("CREATE TABLE aktionen (id INTEGER PRIMARY KEY, platform TEXT, account_id INTEGER, art TEXT CHECK(art IN ('like','report','kommentar')), ziel_uid TEXT, status TEXT, faellig_am TEXT, idempotenz TEXT, erstellt_am TEXT)")
            store.db.execute("INSERT INTO aktionen VALUES (1,'reddit',NULL,'like','reddit:x','offen',NULL,'old','2026-01-01T00:00:00Z')")
            store.db.commit()
        with Store(self.db) as store:
            self.assertEqual(store.db.execute('SELECT ziel_uid FROM aktionen WHERE id=1').fetchone()[0],'reddit:x')
            self.assertTrue(aktionen.enqueue(store.db,'google','bewertung','google:place',sterne=5))

    def test_comment_keys_are_deterministic(self):
        with Store(self.db) as store:
            self.assertTrue(aktionen.enqueue(store.db,'reddit','kommentar','reddit:x',text='Grüße'))
            self.assertFalse(aktionen.enqueue(store.db,'reddit','kommentar','reddit:x',text='Grüße'))
            self.assertTrue(aktionen.enqueue(store.db,'reddit','kommentar','reddit:x',text='Anderer Text'))
            with self.assertRaises(ValueError): aktionen.enqueue(store.db,'reddit','like','x:wrong')

    def test_actions_probe_leaves_queue_unchanged_and_no_runtime(self):
        with Store(self.db) as store:
            aktionen.enqueue(store.db,'reddit','like','reddit:x',account_id=self.account['id'])
            before=store.db.execute('SELECT * FROM aktionen').fetchall()
        with patch.object(aktionen,'_Runtime',side_effect=AssertionError('No runtime in probe')):
            result=aktionen.run(self.db,cfg=self.cfg,dry=True)
        self.assertEqual(result['erledigt'],0)
        with Store(self.db) as store:
            self.assertEqual(store.db.execute('SELECT * FROM aktionen').fetchall(),before)

    def test_action_success_closes_runtime_and_records_usage(self):
        with Store(self.db) as store:
            aktionen.enqueue(store.db,'reddit','like','reddit:x',account_id=self.account['id'])
        runtime=MagicMock()
        with patch.object(aktionen,'_Runtime',return_value=runtime), patch.object(aktionen,'_ausfuehren',return_value=aktionen.Ergebnis(True)), patch.object(aktionen.time,'sleep'):
            result=aktionen.run(self.db,cfg=self.cfg,pause=(0,0),takt_pro_minute=0)
        self.assertEqual(result['erledigt'],1)
        runtime.close.assert_called_once()
        with Store(self.db) as store:
            self.assertEqual(aktionen.counts(store.db)['erledigt'],1)

    def test_warmup_state_and_app_probe_never_start_emulator(self):
        now=repo.now()
        with Store(self.db) as store: account=repo.add_account(store.db,'tiktok','App probe')
        accountvault.put(self.db,account['identity'],email='probe@gmx.net',passwort='test-only')
        warmup.setzen(self.db,account['id'],phase='app',app_tage=3,now=now)
        self.assertEqual(warmup.zustand(self.db)[0]['phase'],'app')
        with patch('socialscraper.android.geraet.mit_geraet',side_effect=AssertionError('No SDK in probe')):
            result=warmup.run(self.db,cfg=self.cfg,trocken=True,now=now)
        self.assertEqual(result['konten'],1)
        self.assertEqual(warmup.zustand(self.db)[0]['warmup_sitzungen'],0)

    def test_tor_prevents_network_actions(self):
        self.cfg.proxy_mode='tor'
        with self.assertRaises(proxymode.AccountsForbidden):
            aktionen.run(self.db,cfg=self.cfg)
        with self.assertRaises(proxymode.AccountsForbidden):
            warmup.run(self.db,cfg=self.cfg)
        with self.assertRaises(proxymode.AccountsForbidden):
            signup.create(self.cfg,'reddit')

    def test_proxy_format_and_redaction(self):
        accountproxy.set_entry(self.db,'one','http://user:secret-test@127.0.0.1:9001')
        accountproxy.set_entry(self.db,'two','http://user:secret-test@127.0.0.1:9001')
        self.assertEqual(set(streuung.proxy_je_konto(self.db)),{'one','two'})
        self.assertNotIn('secret-test',str(streuung.audit(self.db)))
        self.assertEqual(streuung.streuen(self.db),0)

    def test_signup_injected_browser_and_explicit_email_preserve_pool(self):
        accountvault.add_postfach(self.db,{'adresse':'pool@gmx.net','passwort':'test-password'})
        module=SimpleNamespace(REGISTER_URL='https://example.test/signup')
        def registered(mod,ctx,*args):
            ctx.cookies=[{'name':'reddit_session','value':'fake','domain':'.reddit.com'}]
            return True
        im=MagicMock()
        im.get.return_value=SimpleNamespace(name='reddit_new',country='de',mobile=False)
        im.proxy_for.return_value=None
        with patch.object(signup,'MODULE',{'reddit':module}), patch.object(signup,'_browser_lauf',side_effect=registered), patch('socialscraper.net.cookies.BASE',self.root/'data/cookies'), patch.object(streuung,'wartezeit_bis_registrierung',return_value=0):
            result=signup.create(self.cfg,'reddit',name='reddit_new',email='explicit@example.test',backend=MagicMock(),im=im,warmup='aus')
        self.assertGreater(result['konto_id'],0)
        self.assertIsNone(accountvault.postfaecher(self.db)[0]['belegt_von'])
        self.assertEqual(accountvault.get(self.db,'reddit_new')['email'],'explicit@example.test')

    def test_missing_registration_confirmation_does_not_create_account(self):
        module=SimpleNamespace(REGISTER_URL='https://example.test/signup')
        im=MagicMock(); im.proxy_for.return_value=None
        with patch.object(signup,'MODULE',{'reddit':module}), patch.object(signup,'_browser_lauf',return_value=True), patch.object(streuung,'wartezeit_bis_registrierung',return_value=0), self.assertRaises(Exception):
            signup.create(self.cfg,'reddit',name='unconfirmed',email='test@example.test',backend=MagicMock(),im=im,warmup='aus')
        with Store(self.db) as store:
            self.assertFalse(store.db.execute("SELECT 1 FROM accounts WHERE identity='unconfirmed'").fetchone())
