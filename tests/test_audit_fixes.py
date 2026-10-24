"""Regression checks for the confirmed audit defects; all endpoints are local/fake."""
import datetime, json, socket, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from socialscraper import repo, accountvault, sentiment, streuung, signup, aktionen
from socialscraper.config import Settings
from socialscraper.store import Store
from socialscraper.net import accountproxy

class AuditFixes(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.db=self.root/'test.db'
        self.cfg=Settings(db_path=str(self.db),profiles_dir=str(self.root/'profiles'))
        from profile_fixture import prepare
        prepare(self.cfg, 'app_audit')
        with Store(self.db) as store:
            self.account=repo.add_account(store.db,'reddit','Audit')

    def test_rate_counts_every_attempt_including_same_account_failures(self):
        now=repo.now()
        with Store(self.db) as store:
            for i in range(4):
                store.db.execute('INSERT INTO action_attempts(action_id,account_id,started_at) VALUES (?,?,?)',
                    (i,self.account['id'],repo.iso(now-datetime.timedelta(seconds=10))))
            store.db.commit()
        self.assertAlmostEqual(streuung.takt_wartezeit(self.db,takt_pro_minute=4,now=now),50,delta=1)
        self.assertEqual(streuung.takt_wartezeit(self.db,takt_pro_minute=5,now=now),0)

    def test_negative_action_filter_ignores_stale_analysis(self):
        from socialscraper import sentiment_repo
        from socialscraper.models import Comment,Video
        from socialscraper.ui.aktionen import AktionenTab
        with Store(self.db) as store:
            video=Video('reddit','v','https://example.test/v')
            stale=Comment('reddit','v','stale','Schlecht')
            current=Comment('reddit','v','current','Noch immer schlecht')
            store.save_result(video,[stale,current])
            sentiment_repo.save_results(store.db,[(c.uid,sentiment_repo.text_hash(c.text),'negativ',-1)
                for c in (stale,current)],'fake',2)
            stale.text='Jetzt gut';store.save_result(video,[stale])
        def variable(value):return SimpleNamespace(get=lambda:value)
        tab=SimpleNamespace(app=MagicMock(),_cfg=lambda:self.cfg,nur_negativ=variable(True),
            plattform=variable('reddit'),anzahl=variable(20),art=variable('like'),
            status=MagicMock(),protokoll=MagicMock(),aktualisieren=MagicMock())
        with patch('socialscraper.ui.workflow_components.busy',return_value=False),patch('tkinter.messagebox.showerror') as error:
            AktionenTab.einreihen(tab);error.assert_not_called()
        with Store(self.db) as store:
            self.assertEqual(store.db.execute('SELECT ziel_uid FROM aktionen').fetchall(),[(current.uid,)])

    def test_app_signup_preserves_phase_when_warmup_disabled(self):
        def completed(cfg, ctx, **kwargs):
            ctx.tiktok_username = 'provider.fixture'
            return True, 'fake_avd'
        module=SimpleNamespace(REGISTER_URL='https://example.test/signup',WEG='app',
            registrieren_app=completed)
        im=MagicMock();im.proxy_for.return_value=None
        im.get.return_value=SimpleNamespace(name='app_audit',country='de',mobile=True)
        with patch.object(signup,'MODULE',{'tiktok':module}),patch.object(streuung,'wartezeit_bis_registrierung',return_value=0),patch.object(accountvault,'put'),patch('socialscraper.net.cookies.BASE',self.root/'cookies'):
            result=signup.create(self.cfg,'tiktok',name='app_audit',email='audit@example.test',backend=MagicMock(),im=im,warmup='aus')
        with Store(self.db) as store:
            account=repo.get_account(store.db,result['konto_id'])
            self.assertEqual(account['phase'],'app')
            self.assertEqual(account['app_avd'],'fake_avd')
            self.assertEqual(account['warmup_modus'],'aus')
            self.assertIsNotNone(account['app_bis'])

    def test_rotation_failure_keeps_requested_account_empty(self):
        with Store(self.db) as store:
            other=repo.add_account(store.db,'reddit','Other')
            aktionen.enqueue(store.db,'reddit','like','reddit:c')
        with patch.object(aktionen,'_Runtime',return_value=MagicMock()), patch.object(aktionen,'_ausfuehren',return_value=aktionen.Ergebnis(False,'captcha','fake')), patch('socialscraper.operation.wait',return_value=True):
            aktionen.run(self.db,cfg=self.cfg,pause=(0,0),takt_pro_minute=0)
        with Store(self.db) as store:
            cur=store.db.execute('SELECT * FROM aktionen')
            action=dict(zip([x[0] for x in cur.description],cur.fetchone()))
            self.assertIsNone(action['account_id'])
            self.assertEqual(action['last_account_id'],self.account['id'])
            self.assertEqual(aktionen._konto_waehlen(store.db,action)['id'],other['id'])

    def test_migration_does_not_confuse_target_rot_with_fixed_account(self):
        with Store(self.db) as store:
            aktionen.enqueue(store.db,'reddit','kommentar','reddit:rot',account_id=self.account['id'],text='Test')
            aktionen.enqueue(store.db,'reddit','kommentar','reddit:other',text='Test')
            store.db.execute('UPDATE aktionen SET account_id=? WHERE ziel_uid=?',(self.account['id'],'reddit:other'))
            store.db.execute('ALTER TABLE aktionen DROP COLUMN last_account_id')
            aktionen.ensure_schema(store.db)
            fixed=store.db.execute('SELECT account_id,last_account_id FROM aktionen WHERE ziel_uid=?',('reddit:rot',)).fetchone()
            rotating=store.db.execute('SELECT account_id,last_account_id FROM aktionen WHERE ziel_uid=?',('reddit:other',)).fetchone()
            self.assertEqual(fixed,(self.account['id'],None))
            self.assertEqual(rotating,(None,self.account['id']))

    def test_delete_removes_secret_and_releases_mailbox(self):
        def write(path,data):
            path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(data),encoding='utf8')
        identity=self.account['identity']
        with patch.object(accountvault,'_write',side_effect=write):
            accountvault.put(self.db,identity,passwort='fake')
            accountvault.add_postfach(self.db,{'adresse':'audit@example.test','passwort':'fake'})
            accountvault.belege(self.db,'audit@example.test',identity)
            with Store(self.db) as store: repo.delete_account(store.db,self.account['id'])
        self.assertFalse(accountvault.get(self.db,identity))
        self.assertEqual(accountvault.nimm_freies(self.db)['adresse'],'audit@example.test')

    def test_cli_blocked_and_cancel_are_not_success(self):
        from socialscraper.commands import aktionen as command
        args=SimpleNamespace(was='start',anzahl=50,art=None,plattform=None,probe=False)
        result={'erledigt':0,'fehler':0,'gruende':{'kein Konto frei':1},'abgebrochen':False}
        with patch.object(aktionen,'run',return_value=result): self.assertEqual(command.run(args,self.cfg),3)
        result['abgebrochen']=True
        with patch.object(aktionen,'run',return_value=result): self.assertEqual(command.run(args,self.cfg),130)

    def test_corrupt_proxy_shape_is_safe(self):
        accountproxy.path_for(self.db).write_text('{"schema_version":1,"proxies":["broken"]}',encoding='utf8')
        self.assertEqual(accountproxy.load(self.db),{})

    def test_late_problem_callback_cannot_restore_acknowledged_count(self):
        from socialscraper.ui.app import App
        now=repo.now()
        state=MagicMock();state.get.return_value=None
        app=SimpleNamespace(_problem_loading=False,_destroyed=False,root=self.root,ui_state=state,
            set_problem_count=MagicMock())
        with patch('socialscraper.ui.app.run_async') as async_read:
            App.update_problem_count(app)
        state.get.return_value=repo.iso(now+datetime.timedelta(seconds=1))
        callback=async_read.call_args.args[2]
        callback([{'kind':'run','key':'run:1','time':now}],None)
        app.set_problem_count.assert_called_once_with(0)

    def test_db_read_failure_fails_closed(self):
        resolver=accountproxy.Resolver(self.db,reach_check=False)
        import sqlite3
        with patch.object(sqlite3,'connect',side_effect=sqlite3.OperationalError('locked')):
            with self.assertRaises(accountproxy.AccountProxyError): resolver.proxy_for('Audit')

    def test_confirmation_wins_over_navigation_login(self):
        from socialscraper.aktionen.browser import _bewerte_seite
        page=MagicMock();page.inner_text.return_value='Sign in\nThank you for your review'
        self.assertTrue(_bewerte_seite(page).ok)

    def test_channel_failure_rolls_back_group(self):
        from socialscraper.commands import channel
        from socialscraper.cli import parser
        args=parser().parse_args(['channel','add','https://www.youtube.com/@Audit','--group','Orphan','--interval','1'])
        with self.assertRaises(ValueError): channel.run(args,self.cfg)
        with Store(self.db) as store: self.assertEqual(repo.list_groups(store.db),[])

    def test_secrets_scrubbed_and_infinite_sentiment_skipped(self):
        from socialscraper.problemlogic import scrub
        for value in ('Authorization: Bearer FAKE_SECRET','{"password": "FAKE_SECRET"}'):
            self.assertNotIn('FAKE_SECRET',scrub(value))
        for value in ('{"password": "FAKE\\"SECRET_TAIL"}',
                      'email_passwort="FAKE SECRET_TAIL"',
                      '{"api_key": "FAKE\\nSECRET_TAIL"}',
                      '{"password": "FAKE SECRET_TAIL'):
            self.assertNotIn('SECRET_TAIL',scrub(value))
        self.assertEqual(sentiment.parse_entries('[{"n":1e309,"label":"positiv","score":0.8}]'),{})

    def test_password_confirmation_and_select_values(self):
        ctx=MagicMock();signup._passwords(ctx,['#password','#confirmation'],'FAKE')
        self.assertEqual(ctx.fill.call_args_list[1].args,(['#confirmation'],'FAKE'))
        page=MagicMock();self.assertTrue(signup._select(page,['#year'],1995))
        page.select_option.assert_called_once_with('#year',value='1995',timeout=1500)

    def test_mail_does_not_load_social_cookie_platform(self):
        from socialscraper.backends.base import BrowserBackend
        backend=MagicMock(spec=BrowserBackend)
        self.assertEqual(BrowserBackend._apply_cookies(backend,MagicMock(),'mail'),0)

    def test_current_urls_and_fields(self):
        from socialscraper.mailanbieter import gmx,webde,outlook,gmail
        from socialscraper.signup import instagram,youtube
        self.assertIn('register.gmx.net',gmx.SIGNUP_URL)
        self.assertIn('register.web.de',webde.SIGNUP_URL)
        self.assertEqual(gmail.SIGNUP_URL,youtube.REGISTER_URL)
        self.assertIn('input[type="email"]',instagram.SEL['email'])
        self.assertIn('input[name="email"]',outlook.SEL['adresse'])

    def test_mobile_tiktok_does_not_require_password_on_first_step(self):
        from socialscraper.signup import tiktok
        ctx=MagicMock(); calls=[]
        def fill(selectors,value):
            calls.append(selectors)
            return 'field' if selectors==tiktok.SEL['email'] else None
        ctx.fill.side_effect=fill;page=MagicMock();page.query_selector.return_value=None
        tiktok.run(page,ctx)
        ctx.warte_auf_cookies.assert_called_once()

    def test_proxy_missing_rejected_before_browser_launch(self):
        from socialscraper.backends.local import LocalBackend
        backend=LocalBackend(profiles_dir=str(self.root/'profiles'))
        with patch.dict('os.environ',{'SS_REQUIRE_PROXY':'1'}):
            with self.assertRaises(ValueError):backend.open(MagicMock(),None)

    def test_socks_bridge_forwards_hostname_and_never_falls_back(self):
        from socialscraper.net.browserproxy import BrowserProxy
        from socialscraper.net.proxy import Proxy
        bridge=BrowserProxy(Proxy.parse('socks5://fake:fake@127.0.0.1:9'))
        self.addCleanup(bridge.close)
        with patch('socialscraper.net.browserproxy.open_tunnel',side_effect=OSError('offline')) as upstream:
            with socket.create_connection(bridge.server.server_address,timeout=3) as client:
                client.sendall(b'CONNECT example.test:443 HTTP/1.1\r\nHost: example.test\r\n\r\n')
                self.assertIn(b'502',client.recv(4096))
            self.assertEqual(upstream.call_args.args[1:],('example.test',443))
