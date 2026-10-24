import contextlib
import datetime
from email.message import EmailMessage
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from socialscraper import accountvault, aktionen, mailbox, repo, signup, streuung, totp, warmup
from socialscraper.config import Settings
from socialscraper.store import Store


class BugscanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / 'data/test.sqlite'
        self.cfg = Settings(db_path=str(self.db), profiles_dir=str(self.root/'data/profiles'))
        from profile_fixture import prepare
        prepare(self.cfg, 'test-app', 'test-pool')
        with Store(self.db) as store:
            self.account = repo.add_account(store.db, 'reddit', 'Test')

    def tearDown(self):
        self.temp.cleanup()

    def test_html_confirmation_href_is_retained(self):
        mail = EmailMessage()
        mail.set_content('<p>Bestätige dein Konto:</p><a href="https://accounts.google.com/verify?x=1&amp;y=2">Weiter</a>', subtype='html')
        self.assertIn('https://accounts.google.com/verify?x=1&y=2', mailbox._text_of(mail))

    def test_mailbox_cancel_before_fetch(self):
        box = mailbox.Mailbox()
        with patch.object(box, 'fetch') as fetch:
            with self.assertRaises(mailbox.MailboxError):
                box.wait_for_code(stop=lambda: True, timeout=0)
        fetch.assert_not_called()

    def test_corrupt_vault_structure_is_not_overwritten(self):
        accountvault.path_for(self.db).write_text('{"konten": ["lost-account"]}', encoding='utf-8')
        before = accountvault.path_for(self.db).read_bytes()
        with self.assertRaises(ValueError):
            accountvault.put(self.db, 'new', passwort='test-only')
        self.assertEqual(accountvault.path_for(self.db).read_bytes(), before)

    def test_future_vault_version_is_preserved(self):
        accountvault.path_for(self.db).write_text('{"schema_version": 99}', encoding='utf-8')
        with self.assertRaises(ValueError):
            accountvault.put(self.db, 'new', passwort='test-only')

    def test_totp_unicode_input_returns_false(self):
        self.assertFalse(totp.verify('JBSWY3DPEHPK3PXP', 'ä12345', at=60))

    def test_totp_seconds_rejects_invalid_time(self):
        for at in (-1, float('nan'), float('inf')):
            with self.subTest(at=at), self.assertRaises(totp.TotpError):
                totp.seconds_left(at=at)

    def test_app_account_without_browser_cookies_is_due(self):
        with Store(self.db) as store:
            app = repo.add_account(store.db, 'tiktok', 'App')
        accountvault.put(self.db, app['identity'], email='test@gmx.net', passwort='test-only')
        now = repo.now()
        warmup.setzen(self.db, app['id'], phase='app', app_avd='test-avd', app_tage=3, now=now)
        with patch.object(streuung, 'im_fenster', return_value=True):
            self.assertIn(app['id'], [a['id'] for a in warmup.faellig(self.db, now=now)])
        with Store(self.db) as store:
            repo.set_account_daily_limit(store.db, app['id'], 1)
            store.db.execute('UPDATE accounts SET used_today=1, used_day=? WHERE id=?', (now.astimezone().strftime('%Y-%m-%d'), app['id']))
            store.db.commit()
        with patch.object(streuung, 'im_fenster', return_value=True):
            self.assertNotIn(app['id'], [a['id'] for a in warmup.faellig(self.db, now=now)])

    def test_warmup_cancelled_session_is_not_counted(self):
        warmup.setzen(self.db, self.account['id'])
        cancelled = [False]
        def session(*args, **kwargs):
            cancelled[0] = True
            return 0
        with patch('socialscraper.warmup.sitzung.laufen', side_effect=session):
            result = warmup.run(self.db, cfg=self.cfg, stop=lambda: cancelled[0])
        self.assertTrue(result['abgebrochen'])
        self.assertEqual(result['sitzungen'], 0)
        self.assertEqual(warmup.zustand(self.db)[0]['warmup_sitzungen'], 0)

    def test_warmup_counts_towards_account_daily_usage(self):
        warmup.setzen(self.db, self.account['id'])
        with patch('socialscraper.warmup.sitzung.laufen', return_value=2):
            warmup.run(self.db, cfg=self.cfg)
        with Store(self.db) as store:
            account = repo.get_account(store.db, self.account['id'])
        self.assertEqual(account['use_count'], 1)
        self.assertEqual(account['used_today'], 1)

    def test_emulator_is_stopped_if_connection_fails(self):
        from socialscraper.android import avd, avd_session
        sdk = self.root / 'owned-sdk'
        home = self.root / 'owned-avd'
        sdk.mkdir(); home.mkdir()
        closed = []
        bridge, dns, job, lock = [MagicMock() for _ in range(4)]
        bridge.playwright = {'server': 'http://127.0.0.1:17778'}
        dns.start.return_value = dns
        lock.try_acquire.return_value = True
        job.start.side_effect = RuntimeError('owned ADB server refused startup')
        job.run.return_value = MagicMock(returncode=0)
        for name, resource in (('job', job), ('dns', dns), ('bridge', bridge)):
            resource.close.side_effect = lambda name=name: closed.append(name)
        lock.release.side_effect = lambda: closed.append('lock')
        reservation = MagicMock()
        reservation.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 17777)
        with patch.object(avd, 'sdk_info', return_value=(sdk, {}, [])), \
                patch.object(avd, 'verify_profile'), patch.object(avd, 'verify_guard'), \
                patch.object(avd_session, 'OsLock', return_value=lock), \
                patch.object(avd_session, 'emulator_port', return_value=5560), \
                patch.object(avd_session.socket, 'socket', return_value=reservation), \
                patch.object(avd_session.socket, 'create_connection') as connect, \
                patch('socialscraper.net.browserproxy.BrowserProxy', return_value=bridge), \
                patch('socialscraper.android.avd_dns.ProxyDns', return_value=dns), \
                patch('socialscraper.android.avd_process.WindowsOwnedJob', return_value=job), \
                patch('subprocess.Popen', side_effect=AssertionError('No native process in fixture')):
            with self.assertRaisesRegex(RuntimeError, 'owned ADB server refused'):
                with avd_session.Session(avd.TEST_NAME, 'http://proxy.example.test:80', sdk=sdk, home=home):
                    self.fail('Failed ADB startup must not enter an emulator session')
        job.start.assert_called_once()
        command = job.start.call_args.args[0]
        self.assertEqual(Path(command[0]), sdk / 'platform-tools/adb.exe')
        self.assertEqual(command[-2:], ['nodaemon', 'server'])
        self.assertEqual(job.start.call_args.kwargs['env']['ANDROID_AVD_HOME'], str(home))
        self.assertEqual(closed, ['job', 'dns', 'bridge', 'lock'])
        connect.assert_not_called()

    def test_mail_registration_needs_positive_confirmation(self):
        from socialscraper.mailanbieter import unitedinternet
        page = MagicMock()
        page.query_selector.return_value = None
        ctx = SimpleNamespace(stop=None)
        with patch.object(unitedinternet.time, 'monotonic', side_effect=[0, 1, 200]):
            with self.assertRaises(RuntimeError):
                unitedinternet._warte_bis_fertig(page, ctx)

    def test_maps_url_is_accepted_by_review_command(self):
        from socialscraper.commands import bewerten
        with Store(self.db) as store:
            repo.add_account(store.db, 'google', 'Review')
        args = SimpleNamespace(ziel='https://www.google.com/maps/place/test', plattform='google', anzahl=1, text='test', sterne=4, spanne=20, start=False)
        with contextlib.redirect_stdout(__import__('io').StringIO()):
            self.assertEqual(bewerten.run(args, self.cfg), 0)
        with Store(self.db) as store:
            target = store.db.execute('SELECT ziel_uid FROM aktionen').fetchone()[0]
        self.assertEqual(target, 'google:'+args.ziel)

    def test_deleting_fixed_account_does_not_rotate_its_action(self):
        with Store(self.db) as store:
            aktionen.enqueue(store.db, 'reddit', 'like', 'reddit:comment', account_id=self.account['id'])
            repo.delete_account(store.db, self.account['id'])
            self.assertFalse(aktionen.pending(store.db))
            self.assertEqual(store.db.execute('SELECT status FROM aktionen').fetchone()[0], 'uebersprungen')

    def test_queue_marks_in_progress_before_external_call(self):
        with Store(self.db) as store:
            aktionen.enqueue(store.db, 'reddit', 'like', 'reddit:comment', account_id=self.account['id'])
        def execute(*args):
            with Store(self.db) as store:
                self.assertEqual(store.db.execute('SELECT status FROM aktionen').fetchone()[0], 'laeuft')
            return aktionen.Ergebnis(True)
        with patch.object(aktionen, '_Runtime'), patch.object(aktionen, '_ausfuehren', side_effect=execute), patch.object(aktionen.time, 'sleep'):
            aktionen.run(self.db, cfg=self.cfg, takt_pro_minute=0)

    def test_bad_cookie_file_is_a_per_action_error(self):
        runtime = MagicMock()
        runtime.proxy_url.return_value = None
        runtime.cookies.side_effect = ValueError('bad cookie data')
        result = aktionen._ausfuehren(runtime, self.account, {'platform':'reddit', 'art':'like', 'ziel_uid':'reddit:x'})
        self.assertFalse(result.ok)
        self.assertEqual(result.art, aktionen.ART_ANMELDUNG)

    def test_unknown_http_json_is_not_success(self):
        from socialscraper.aktionen import endpunkte
        answer = MagicMock(status_code=200)
        for data in ({}, [], {'message':'unknown'}):
            answer.json.return_value = data
            self.assertFalse(endpunkte._urteil(answer).ok)

    def test_comment_targets_the_stored_comment_not_a_post(self):
        with Store(self.db) as store:
            aktionen.enqueue(store.db, 'reddit', 'kommentar', 'reddit:comment', video_uid='reddit:post', text='test')
            action = aktionen.pending(store.db)[0]
        self.assertEqual(action['eltern_id'], 'comment')

    def test_registration_abort_does_not_fall_back_to_browser(self):
        cancelled = [False]
        def aborted(*args, **kwargs):
            cancelled[0] = True
            raise RuntimeError('Abgebrochen.')
        module = SimpleNamespace(REGISTER_URL='https://example.test', WEG='app', registrieren_app=aborted)
        im = MagicMock(); im.proxy_for.return_value = None
        with patch.object(signup, 'MODULE', {'tiktok': module}), patch.object(streuung, 'wartezeit_bis_registrierung', return_value=0), patch.object(signup, '_browser_lauf') as browser:
            with self.assertRaises(Exception):
                signup.create(self.cfg, 'tiktok', email='test@example.test', name='test-app', backend=MagicMock(), im=im, stop=lambda: cancelled[0], warmup='aus')
        browser.assert_not_called()

    def test_explicit_pool_address_uses_its_mailbox(self):
        accountvault.add_postfach(self.db, {'adresse':'pool@gmx.net', 'passwort':'test-only'})
        def registered(mod, ctx, *args):
            self.assertIsInstance(ctx.box, mailbox.ImapBox)
            self.assertEqual(ctx.box.address(), 'pool@gmx.net')
            self.assertIs(access.call_args.args[0], ctx.box)
            ctx.cookies=[{'name':'reddit_session', 'value':'test', 'domain':'.reddit.com'}]
        im=MagicMock(); im.proxy_for.return_value=None
        module=SimpleNamespace(REGISTER_URL='https://example.test')
        # This unit test selects a saved fixture; real IMAP access is covered
        # separately and must never be attempted with the test-only password.
        with patch.object(mailbox.ImapBox, 'check_access', autospec=True, return_value=0) as access, patch.object(signup, 'MODULE', {'reddit':module}), patch.object(signup, '_browser_lauf', side_effect=registered), patch.object(streuung, 'wartezeit_bis_registrierung', return_value=0), patch('socialscraper.net.cookies.BASE', self.root/'data/cookies'):
            signup.create(self.cfg, 'reddit', email='pool@gmx.net', name='test-pool', backend=MagicMock(), im=im, warmup='aus')
        access.assert_called_once()

    def test_registration_spacing_ignores_other_platforms(self):
        self.assertEqual(streuung.wartezeit_bis_registrierung(self.db, platform='google'), 0)

    def test_activity_window_uses_local_time(self):
        moment=datetime.datetime(2026, 1, 1, 6, tzinfo=datetime.timezone(datetime.timedelta(hours=-2)))
        # The test supplies its local conversion explicitly, independent of host timezone.
        with patch.object(streuung, 'repo') as clock:
            clock.now.return_value=moment
            expected=8 <= moment.astimezone().hour < 23
            self.assertEqual(streuung.im_fenster({'fenster_von':8, 'fenster_bis':23}), expected)

    def test_invalid_activity_hours_rejected(self):
        with self.assertRaises(ValueError):
            streuung.setze_fenster(self.db, self.account['id'], von=-1, bis=30)

    def test_legacy_action_migration_rolls_back_on_invalid_data(self):
        import sqlite3
        with Store(self.db) as store:
            store.db.execute('DROP TABLE aktionen')
            store.db.execute("CREATE TABLE aktionen (id INTEGER PRIMARY KEY, platform TEXT, art TEXT CHECK(art IN ('like','report')), ziel_uid TEXT, idempotenz TEXT, erstellt_am TEXT)")
            store.db.executemany('INSERT INTO aktionen VALUES (?,?,?,?,?,?)', [(1,'reddit','like','reddit:a','duplicate','2026-01-01'),(2,'reddit','like','reddit:b','duplicate','2026-01-01')])
            store.db.commit()
            before=store.db.execute('SELECT * FROM aktionen').fetchall()
            with self.assertRaises(sqlite3.IntegrityError):
                aktionen.ensure_schema(store.db)
            self.assertEqual(store.db.execute('SELECT * FROM aktionen').fetchall(),before)
            self.assertFalse(store.db.execute("SELECT 1 FROM sqlite_master WHERE name='aktionen_neu'").fetchone())

    def test_unconfirmed_action_is_not_automatically_retried(self):
        with Store(self.db) as store:
            aktionen.enqueue(store.db,'reddit','like','reddit:test',account_id=self.account['id'])
        with patch.object(aktionen,'_Runtime'), patch.object(aktionen,'_ausfuehren',return_value=aktionen.Ergebnis(False,aktionen.ART_UNKLAR,'unconfirmed')), patch.object(aktionen.time,'sleep'):
            result=aktionen.run(self.db,cfg=self.cfg,takt_pro_minute=0)
        self.assertEqual(result['fehler'],1)
        with Store(self.db) as store:
            self.assertFalse(aktionen.pending(store.db,now=repo.now()+datetime.timedelta(days=1)))
            self.assertEqual(store.db.execute('SELECT status,versuche FROM aktionen').fetchone(),('laeuft',1))

    def test_manual_warmup_respects_previous_session(self):
        warmup.setzen(self.db,self.account['id'])
        with Store(self.db) as store:
            store.db.execute('UPDATE accounts SET warmup_last=? WHERE id=?',(repo.iso(),self.account['id']));store.db.commit()
        with patch('socialscraper.warmup.sitzung.laufen') as session:
            result=warmup.run(self.db,konto_id=self.account['id'],cfg=self.cfg)
        self.assertEqual(result['sitzungen'],0);session.assert_not_called()

    def test_mailbox_pool_rejects_case_duplicate_address(self):
        accountvault.add_postfach(self.db,{'adresse':'Test@GMX.net'})
        with self.assertRaises(ValueError):accountvault.add_postfach(self.db,{'adresse':'test@gmx.net'})

    def test_mail_audit_covers_accounts_without_proxy(self):
        with Store(self.db) as store:
            accounts=[repo.add_account(store.db,'reddit',str(i)) for i in range(4)]
        for i,account in enumerate(accounts):accountvault.put(self.db,account['identity'],email=f'test{i}@example.test')
        findings=[f for f in streuung.audit(self.db) if f['art']=='mail_domaene']
        self.assertEqual(len(findings),1);self.assertEqual(len(findings[0]['konten']),4)

    def test_imap_folder_failure_closes_connection(self):
        box=mailbox.ImapBox('example.test','user','password')
        connection=MagicMock();connection.select.return_value=('NO',[b'no folder'])
        with patch.object(mailbox.imaplib,'IMAP4_SSL',return_value=connection) as constructor:
            with self.assertRaises(mailbox.MailboxError):box.fetch()
        self.assertEqual(constructor.call_args.kwargs['timeout'],20)
        connection.search.assert_not_called();connection.logout.assert_called_once()

    def test_comment_payload_uses_video_and_reply_identifiers(self):
        from socialscraper.aktionen import endpunkte
        client=MagicMock();client.post.return_value.status_code=200;client.post.return_value.json.return_value={'success':True}
        for platform in ('tiktok','youtube','instagram'):
            with self.subTest(platform=platform), patch.object(endpunkte,'_'+platform,return_value=client):
                result=getattr(endpunkte,platform+'_kommentar')(cookies=[],proxy=None,ziel=platform+':comment',video=platform+':video',text='test',eltern='comment')
                self.assertTrue(result.ok)
                args,kwargs=client.post.call_args
                payload=kwargs['data']
                if platform=='tiktok':self.assertEqual(payload['aweme_id'],'video');self.assertEqual(payload['comment_id'],'comment')
                if platform=='youtube':self.assertEqual(json.loads(payload)['videoId'],'video');self.assertEqual(json.loads(payload)['target']['commentId'],'comment')
                if platform=='instagram':self.assertIn('/video/add/',args[0]);self.assertEqual(payload['replied_to_comment_id'],'comment')

    def test_cancel_during_action_delay_stops_before_second_action(self):
        with Store(self.db) as store:
            for uid in ('a','b'):aktionen.enqueue(store.db,'reddit','like','reddit:'+uid,account_id=self.account['id'])
        cancelled=[False]
        def execute(*args):cancelled[0]=True;return aktionen.Ergebnis(True)
        with patch.object(aktionen,'_Runtime'),patch.object(aktionen,'_ausfuehren',side_effect=execute) as action:
            result=aktionen.run(self.db,cfg=self.cfg,stop=lambda:cancelled[0])
        self.assertTrue(result['abgebrochen']);self.assertEqual(action.call_count,1)

    def test_callback_error_does_not_leave_background_task_running(self):
        from socialscraper.ui.hintergrund import Auftrag
        widget=MagicMock();widget.app.job=None;widget.app.tabs={}
        task=Auftrag(widget,lambda *_:None,bei_zeile=MagicMock(side_effect=RuntimeError('failed')))
        task.laeuft=True;task._schlange.put(('zeile','test'));task._schlange.put(('ende',{},None))
        task._abholen()
        self.assertFalse(task.laeuft);widget.app._busy.assert_called_once_with(False)

    def test_review_failure_categories_match_account_cooldown(self):
        from socialscraper.aktionen import browser
        self.assertEqual(browser._urteil_text('Account suspended'),aktionen.ART_SPERRE)
        self.assertEqual(browser._urteil_text('Please sign in'),aktionen.ART_ANMELDUNG)

    def test_generic_thank_you_is_not_review_confirmation(self):
        from socialscraper.aktionen import browser
        page=MagicMock();page.inner_text.return_value='Andere Bewertung: Thank you for the great service.'
        self.assertFalse(browser._bewerte_seite(page).ok)
        page.inner_text.return_value='Your review has been published.'
        self.assertTrue(browser._bewerte_seite(page).ok)

    def test_response_timeout_is_not_automatically_retried(self):
        runtime=MagicMock();runtime.cookies.return_value=[{'name':'test','value':'test'}]
        with patch('socialscraper.aktionen.endpunkte.reddit_like',side_effect=TimeoutError('timeout')):
            result=aktionen._ausfuehren(runtime,self.account,{'platform':'reddit','art':'like','ziel_uid':'reddit:test'})
        self.assertEqual(result.art,aktionen.ART_UNKLAR)

    def test_maximum_attempts_have_terminal_status(self):
        with Store(self.db) as store:
            aktionen.enqueue(store.db,'reddit','like','reddit:retry',account_id=self.account['id'])
            action=aktionen.pending(store.db)[0];action['versuche']=4
            aktionen._fehler(store.db,action,self.account,aktionen.Ergebnis(False,aktionen.ART_PROXY,'not reachable'))
            self.assertEqual(store.db.execute('SELECT status,versuche FROM aktionen').fetchone(),('uebersprungen',5))

    def test_full_trustpilot_url_is_normalized(self):
        from socialscraper.commands import bewerten
        with Store(self.db) as store:repo.add_account(store.db,'trustpilot','Review')
        args=SimpleNamespace(ziel='https://de.trustpilot.com/review/example.test',plattform='trustpilot',anzahl=1,text='test',sterne=4,spanne=20,start=False)
        with contextlib.redirect_stdout(__import__('io').StringIO()):bewerten.run(args,self.cfg)
        with Store(self.db) as store:self.assertEqual(store.db.execute('SELECT ziel_uid FROM aktionen').fetchone()[0],'trustpilot:example.test')


class BugscanWindowTests(unittest.TestCase):
    from test_workflow_ux import WorkflowWindowTests as _Setup
    setUp = _Setup.setUp

    def test_registration_keeps_inputs_when_another_task_runs(self):
        from socialscraper.ui.register_dialog import KontoDialog
        dialog=KontoDialog(self.app,self.app);dialog.withdraw();self.addCleanup(lambda:dialog.destroy() if dialog.winfo_exists() else None)
        dialog.email.set('test@example.test');self.app.job=object()
        try:
            with patch('tkinter.messagebox.showinfo'),patch.object(self.app,'run_steps') as run:
                dialog.start()
            self.assertTrue(dialog.winfo_exists());self.assertEqual(dialog.email.get(),'test@example.test');run.assert_not_called()
        finally:self.app.job=None

    def test_busy_restores_disabled_and_readonly_controls(self):
        from tkinter import ttk
        button=ttk.Button(self.app,state='disabled');combo=ttk.Combobox(self.app,state='readonly')
        self.app.register_busy(button);self.app.register_busy(combo)
        self.app._busy(True);self.app._busy(False)
        self.assertEqual(str(button.cget('state')),'disabled');self.assertEqual(str(combo.cget('state')),'readonly')

    def test_broken_vault_does_not_crash_new_pages(self):
        accountvault.path_for(self.db).write_text('broken JSON',encoding='utf-8')
        self.app.tabs['Streuung'].refresh()
        self.assertIn('nicht möglich',self.app.tabs['Streuung'].status.cget('text'))

    def test_bad_warmup_date_is_visible_and_does_not_crash(self):
        with Store(self.db) as store:
            account=repo.list_accounts(store.db)[0]
            store.db.execute("UPDATE accounts SET warmup_modus='normal',warmup_until='bad-date' WHERE id=?",(account['id'],));store.db.commit()
        page=self.app.tabs['Aufwärmen'];page.refresh()
        self.assertEqual(page.liste.item(str(account['id']),'values')[-1],'Zeitplan prüfen')

    def test_account_controls_remain_disabled_during_background_task(self):
        from socialscraper.ui.hintergrund import Auftrag
        page=self.app.tabs['Konten'];page.refresh()
        page.tree.selection_set(page.tree.get_children()[0]);page._update_buttons()
        task=Auftrag(self.app.tabs['Aktionen'],lambda *_:None);task.laeuft=True
        self.app.tabs['Aktionen']._auftrag=task
        self.app._busy(True)
        page._update_buttons()
        self.assertEqual(str(page.btn['remove'].cget('state')),'disabled')
        task.laeuft=False;self.app._busy(False)
