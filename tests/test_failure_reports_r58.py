from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from socialscraper import failure_reports as reports, registration_attempts as drafts
from socialscraper.net.errors import FinalError
from socialscraper.ui.postfaecher import PostfaecherTab


class FailureReportsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'data/test.db'

    def test_concrete_last_step_survives_with_safe_message_and_unknown_cause(self):
        report = reports.Progress('web.de'); report.phase('E-Mail-Adresse')
        report.note('Prüfe fixture@web.de')
        report.failure(self.db, FinalError('Registrierung fehlgeschlagen: FIXTURE_PASSWORD'), secrets=('fixture@web.de','FIXTURE_PASSWORD'))
        item = reports.latest(self.db, 'web.de'); text = reports.render(item)
        self.assertIn('E-Mail-Adresse', text); self.assertIn('Registrierung fehlgeschlagen', text)
        self.assertNotIn('FIXTURE_PASSWORD', text); self.assertNotIn('fixture@web.de', text)
        self.assertEqual(item['kind'], 'registration_failure'); self.assertIn('nicht', item['next'])

    def test_unknown_browser_error_does_not_dump_payload_but_keeps_error_code(self):
        item = reports.Progress('web.de').failure(self.db, RuntimeError('net::ERR_PROXY_CONNECTION_FAILED private-body'))
        self.assertIn('ERR_PROXY_CONNECTION_FAILED', item['message']); self.assertNotIn('private-body', reports.render(item))

    def test_explicit_account_block_has_joint_review_and_no_automatic_retry(self):
        item = reports.Progress('web.de').failure(self.db, FinalError('Dein Konto wurde gesperrt'))
        self.assertEqual(item['kind'], 'reported_account_block')
        self.assertIn('Wiederherstellungsweg', item['next']); self.assertIn('Kein automatischer', item['next'])

    def test_negated_or_unknown_account_block_is_not_evidence(self):
        for message in ('Dein Konto ist nicht gesperrt', 'Unklar, ob dein Konto gesperrt ist',
                        'Unknown whether your account is locked'):
            item = reports.Progress('web.de').failure(self.db, FinalError(message))
            self.assertEqual(item['kind'], 'registration_failure')

    def test_report_save_and_ui_callback_failures_do_not_replace_registration_error(self):
        report = reports.Progress('web.de')
        with patch.object(reports.accountvault, '_write', side_effect=OSError('fixture-write')):
            report.publish_failure(self.db, FinalError('fixture-form'), Mock(side_effect=RuntimeError('fixture-ui')))

    def test_corrupt_report_fails_closed(self):
        self.db.parent.mkdir()
        (self.db.parent / reports.FILE_NAME).write_text('[{"provider":"web.de","stage":123}]')
        with self.assertRaises(ValueError):reports.latest(self.db, 'web.de')

    def test_report_button_reads_selected_provider_and_does_not_start_job(self):
        report = reports.Progress('web.de'); report.phase('Google-Einstieg')
        report.failure(self.db, FinalError('Google-Einstieg nicht abgeschlossen'))
        subject = SimpleNamespace(provider=SimpleNamespace(get=lambda:'web.de'), app=SimpleNamespace(run_steps=Mock()))
        with patch('socialscraper.ui.postfaecher.cfg_of', return_value=SimpleNamespace(db_path=self.db)), \
             patch('socialscraper.ui.postfaecher.messagebox.showinfo') as shown:
            PostfaecherTab.failure_report(subject)
        self.assertIn('Google-Einstieg', shown.call_args.args[1]); subject.app.run_steps.assert_not_called()

    def test_failed_drafts_rotate_only_identity_and_unclean_attempts_require_review(self):
        args = dict(address='fixture@web.de', password='FIXTURE', identity='abcdef', explicit_address=False,
                    explicit_password=False, details={}, route='browser', country='de', mobile=False)
        key, first, _ = drafts.get_or_create(self.db, 'web.de', **args)
        for state in ('failed',):
            drafts.status(self.db, key, state)
            _, next_draft, reused = drafts.get_or_create(self.db, 'web.de', **args)
            self.assertTrue(reused); self.assertNotEqual(first['identity'], next_draft['identity'])
            self.assertEqual((first['address'], first['password']), (next_draft['address'], next_draft['password']))
            first = next_draft
        drafts.status(self.db,key,'running')
        before=drafts._path(self.db).read_bytes()
        with self.assertRaisesRegex(ValueError,'prüfen'):drafts.get_or_create(self.db,'web.de',**args)
        self.assertEqual(drafts._path(self.db).read_bytes(),before)

    def test_legacy_draft_without_status_gets_new_identity(self):
        args = dict(address='fixture@web.de', password='FIXTURE', identity='abcdef', explicit_address=False,
                    explicit_password=False, details={}, route='browser', country='de', mobile=False)
        key, first, _ = drafts.get_or_create(self.db, 'web.de', **args)
        data = drafts._load(self.db); del data['drafts'][key]['status']
        drafts.accountvault._write(drafts._path(self.db), data)
        _, following, _ = drafts.get_or_create(self.db, 'web.de', **args)
        self.assertNotEqual(first['identity'], following['identity'])
        self.assertEqual(first['address'], following['address'])
