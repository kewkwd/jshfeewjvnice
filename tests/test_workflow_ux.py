import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from socialscraper import workflowlogic, repo, warmup
from socialscraper.store import Store
from socialscraper.ui.workflow_components import summary


class WorkflowLogicTests(unittest.TestCase):
    def test_tor_is_first_action_for_account_setup(self):
        items=workflowlogic.steps('account',{'anonymous':True,'connected':2})
        self.assertEqual(items[workflowlogic.next_step(items)]['target'],'Einstellungen')
    def test_registration_requires_mailbox_before_creating_account(self):
        items=workflowlogic.steps('register',{'anonymous':False,'mailboxes':0})
        self.assertEqual(items[workflowlogic.next_step(items)]['target'],'Postfächer')
        items=workflowlogic.steps('register',{'anonymous':False,'mailboxes':1})
        self.assertEqual(items[workflowlogic.next_step(items)]['target'],'Konten')
    def test_collecting_public_data_has_no_account_step(self):
        self.assertEqual([i['target'] for i in workflowlogic.steps('collect',{})],['Abrufen','Gespeicherte Daten'])
    def test_actions_without_comments_start_with_data_collection(self):
        items=workflowlogic.steps('act',{'connected':2,'comments':0})
        self.assertEqual(items[workflowlogic.next_step(items)]['target'],'Abrufen')
    def test_probe_explains_blockers_and_no_due_sessions(self):
        self.assertIn('Kontenanmeldung',summary({'gruende':{'kein Konto frei':1}},probe=True))
        self.assertIn('keine Sitzung fällig',summary({'konten':0},probe=True))


class WorkflowWindowTests(unittest.TestCase):
    def setUp(self):
        from socialscraper import guicore
        from socialscraper.ui import common
        from socialscraper.ui.app import App
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=self.root/'data/test.db'
        (self.root/'settings.toml').write_text('proxy_mode="aus"\ndb_path="'+str(self.db).replace('\\','/')+'"\nprofiles_dir="'+str(self.root/'data/profiles').replace('\\','/')+'"\n',encoding='utf-8')
        for replacement in (patch.object(guicore,'ROOT',self.root),patch.object(common,'ROOT',self.root),patch.object(common,'GUI_LOG',self.root/'data/gui.log'),patch.dict(os.environ,{'SS_DB_PATH':str(self.db)}),patch.object(App,'_startup_check'),patch.object(App,'_problem_tick')):
            replacement.start();self.addCleanup(replacement.stop)
        self.app=App();self.app.withdraw();self.addCleanup(self.app.destroy);self.app.update()

    def test_sidebar_reaches_every_feature_and_updates_header(self):
        from socialscraper.ui.workflow_design import LABELS
        self.assertEqual(set(self.app.nav_buttons),set(self.app.tabs))
        for title,button in self.app.nav_buttons.items():
            button.invoke();self.app.update()
            self.assertEqual(self.app._current_tab_title(),title)
            self.assertEqual(self.app.page_title.cget('text'),LABELS[title])

    def test_start_focuses_one_workflow_and_can_return_to_task_choices(self):
        hub=self.app.tabs['Übersicht']
        self.assertFalse(hub.focused)
        hub.choose('register');self.app.update()
        self.assertTrue(hub.focused);self.assertFalse(hub.cards_container.winfo_manager())
        self.app.navigate('Übersicht');self.app.update()
        self.assertFalse(hub.focused);self.assertTrue(hub.cards_container.winfo_manager())

    def test_warmup_lists_accounts_without_a_plan(self):
        with Store(self.db) as store:account=repo.add_account(store.db,'reddit','Neues Testkonto')
        page=self.app.tabs['Aufwärmen'];page.refresh()
        self.assertIn(str(account['id']),page.liste.get_children())
        self.assertEqual(page.liste.item(str(account['id']),'values')[-1],'Noch kein Plan')
        self.assertEqual(str(page.start_knopf.cget('state')),'disabled')
        page.liste.selection_set(str(account['id']));self.app.update()
        self.assertEqual(str(page.plan_button.cget('state')),'normal')

    def test_empty_action_queue_disables_execution(self):
        page=self.app.tabs['Aktionen'];page.refresh()
        self.assertEqual(str(page.start_knopf.cget('state')),'disabled')
        self.assertEqual(str(page.probe_knopf.cget('state')),'disabled')

    def test_details_stay_closed_when_window_is_resized(self):
        with patch.object(self.app,'winfo_height',return_value=1000):self.app._auto_log()
        self.assertFalse(self.app._log_open)
        self.app.toggle_log();self.assertTrue(self.app._log_open)

    def test_comment_plan_keeps_user_text_and_target(self):
        from socialscraper.models import Video,Comment
        with Store(self.db) as store:
            store.save_result(Video('reddit','v','https://www.reddit.com/comments/v'),[Comment('reddit','v','c','Testtext')],repo.iso())
        page=self.app.tabs['Aktionen']
        with patch('socialscraper.ui.widgets.ask_form',side_effect=[{'platform':'reddit','art':'kommentar','count':'1','filter':'all'},{'text':'Meine Anmerkung'}]):page.planen()
        with Store(self.db) as store:
            self.assertEqual(store.db.execute('SELECT ziel_uid,text FROM aktionen').fetchone(),('reddit:c','Meine Anmerkung'))

    def test_editing_plan_preserves_existing_app_transition_date(self):
        with Store(self.db) as store:account=repo.add_account(store.db,'tiktok','App Testkonto')
        warmup.setzen(self.db,account['id'],phase='app',app_tage=2)
        with Store(self.db) as store:before=repo.get_account(store.db,account['id'])['app_bis']
        page=self.app.tabs['Aufwärmen'];page.refresh();page.liste.selection_set(str(account['id']))
        with patch('socialscraper.ui.warmup_page.ask_form',return_value={'mode':'normal','scope':'nur_schauen','days':'5'}):page.uebernehmen()
        with Store(self.db) as store:self.assertEqual(repo.get_account(store.db,account['id'])['app_bis'],before)

    def test_tor_block_is_visible_before_starting_account_operations(self):
        (self.root/'settings.toml').write_text('proxy_mode="tor"\ndb_path="'+str(self.db).replace('\\','/')+'"\n',encoding='utf-8')
        for title in ('Aktionen','Aufwärmen'):
            page=self.app.tabs[title];page.refresh()
            self.assertEqual(str(page.start_knopf.cget('state')),'disabled')
            self.assertIn('Tor',page.status.cget('text'))

    def test_registration_dialog_prepares_visible_registration_without_auto_warmup(self):
        from socialscraper.ui.register_dialog import KontoDialog
        dialog=KontoDialog(self.app,self.app);dialog.withdraw()
        dialog.email.set('test@example.test')
        dialog.name.set('workflow-fixture')
        from profile_fixture import prepare
        from socialscraper.ui.hintergrund import cfg_of
        prepare(cfg_of(self.app),'workflow-fixture')
        with patch.object(self.app,'run_steps') as run:
            dialog.start()
            command=run.call_args.args[0][0][1]
        self.assertEqual(command[:6],['konto','reddit','--modus','assisted','--aufwaermen','aus'])
        self.assertEqual(command[-4:],['--mail','test@example.test','--name','workflow-fixture'])

    def test_new_controls_stay_disabled_while_another_job_runs(self):
        page=self.app.tabs['Aufwärmen'];page.refresh()
        page.liste.selection_set(page.liste.get_children()[0])
        self.app.job=object()
        try:
            page.selection_changed()
            self.assertEqual(str(page.plan_button.cget('state')),'disabled')
            self.app.tabs['Aktionen'].refresh()
            self.assertEqual(str(self.app.tabs['Aktionen'].start_knopf.cget('state')),'disabled')
        finally:self.app.job=None
