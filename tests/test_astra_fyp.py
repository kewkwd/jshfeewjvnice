"""Regressionen für die FYP-Buchung ohne Browser und ohne echte Kontodaten."""
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from socialscraper import cli, personas, repo
from socialscraper.adapters.tiktok import TikTokAdapter
from socialscraper.fyp import konsum, monitor, seed
from socialscraper.store import Store


class FypTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'fyp.db'
        with Store(self.path) as store:
            self.konto = repo.add_account(store.db, 'tiktok', 'FYP Test')
        self.cfg = SimpleNamespace(db_path=self.path, headless=True)

    def test_relative_video_link_has_author_and_uid(self):
        self.assertEqual(konsum._video_meta('/@Alice/video/123?lang=de'), ('alice', '123'))
        self.assertEqual(konsum._video_meta('https://www.tiktok.com/@Alice/video/123'), ('alice', '123'))
        self.assertEqual(konsum._video_meta('https://example.org/@Alice/video/123'), (None, None))

    def test_session_booking_deduplicates_and_rolls_back_together(self):
        persona = {'id': 1}
        konsum._verbuche_sitzung(self.path, self.konto, persona,
                                [('alice', '1'), ('alice', '1'), ('alice', '2')], 3, 3)
        with Store(self.path) as store:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM fyp_treffer').fetchone()[0], 2)
            self.assertEqual(store.db.execute('SELECT fyp_status FROM accounts WHERE identity=?',
                                              (self.konto['identity'],)).fetchone()[0], 'drift')
        with mock.patch.object(konsum, '_verbuche_in_db', side_effect=RuntimeError('unterbrochen')):
            with self.assertRaises(RuntimeError):
                konsum._verbuche_sitzung(self.path, self.konto, persona, [('alice', '3')], 1, 1)
        with Store(self.path) as store:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM fyp_treffer').fetchone()[0], 2)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM konsum_log').fetchone()[0], 1)
            self.assertEqual(store.db.execute('SELECT fyp_status FROM accounts WHERE identity=?',
                                              (self.konto['identity'],)).fetchone()[0], 'drift')
        konsum._verbuche_sitzung(self.path, self.konto, persona, [('alice', '3')], 1, 1)
        with Store(self.path) as store:
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM fyp_treffer').fetchone()[0], 3)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM konsum_log').fetchone()[0], 2)
            self.assertEqual(store.db.execute('SELECT fyp_status FROM accounts WHERE identity=?',
                                              (self.konto['identity'],)).fetchone()[0], 'im_fyp')

    def test_old_null_hits_do_not_promote_account(self):
        with Store(self.path) as store:
            for _ in range(3):
                store.db.execute('INSERT INTO fyp_treffer '
                                 '(identity, kanal, video_uid, persona_id, art, organisch, erstellt_am) '
                                 "VALUES (?, 'alice', NULL, 1, 'konsum', 1, '2026-01-01')",
                                 (self.konto['identity'],))
            store.db.commit()
        konsum._verbuche_sitzung(self.path, self.konto, {'id': 1}, [('alice', '4')], 1, 1)
        with Store(self.path) as store:
            self.assertEqual(store.db.execute('SELECT fyp_status FROM accounts WHERE identity=?',
                                              (self.konto['identity'],)).fetchone()[0], 'drift')

    def _seed_runtime(self):
        im, backend, page, link = mock.MagicMock(), mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        im.stored.return_value = ('de', False)
        backend.page.return_value = page
        page.query_selector_all.return_value = [link]
        link.is_visible.return_value = True
        return im, backend, page

    def test_seed_aborted_after_one_video_keeps_drift(self):
        im, backend, page = self._seed_runtime()
        checks = iter((False, False, True))  # Kanal 1, Video 1, Kanal 2
        persona = {'influencer': '@alice', 'kanaele': ['@alice', 'bob']}
        with mock.patch.object(personas, 'persona_von', return_value=persona), \
             mock.patch.object(cli, 'make_runtime', return_value=(im, backend, None)), \
             mock.patch.object(cli, 'shutdown_runtime'), \
             mock.patch.object(seed, '_cookies'):
            schritte = seed.sitzung(self.cfg, self.konto, rng=random.Random(1),
                                   stop=lambda: next(checks, True))
        self.assertEqual(schritte, 1)
        self.assertEqual(page.goto.call_count, 1)
        with Store(self.path) as store:
            self.assertEqual(store.db.execute('SELECT fyp_status FROM accounts WHERE identity=?',
                                              (self.konto['identity'],)).fetchone()[0], 'drift')

    def test_seed_visits_influencer_once_and_marks_completed_run(self):
        im, backend, page = self._seed_runtime()
        persona = {'influencer': '@alice', 'kanaele': ['@alice', 'bob', '@ALICE']}
        with mock.patch.object(personas, 'persona_von', return_value=persona), \
             mock.patch.object(cli, 'make_runtime', return_value=(im, backend, None)), \
             mock.patch.object(cli, 'shutdown_runtime'), \
             mock.patch.object(seed, '_cookies'):
            schritte = seed.sitzung(self.cfg, self.konto, rng=random.Random(1))
        self.assertEqual(schritte, 2)
        self.assertEqual(page.goto.call_count, 2)
        with Store(self.path) as store:
            self.assertEqual(store.db.execute('SELECT fyp_status FROM accounts WHERE identity=?',
                                              (self.konto['identity'],)).fetchone()[0], 'gesaet')

    def test_seed_with_no_video_keeps_drift(self):
        im, backend, page = self._seed_runtime()
        page.query_selector_all.return_value = []
        persona = {'influencer': '@alice', 'kanaele': ['@alice']}
        with mock.patch.object(personas, 'persona_von', return_value=persona), \
             mock.patch.object(cli, 'make_runtime', return_value=(im, backend, None)), \
             mock.patch.object(cli, 'shutdown_runtime'), \
             mock.patch.object(seed, '_cookies'):
            self.assertEqual(seed.sitzung(self.cfg, self.konto, rng=random.Random(1)), 0)
        with Store(self.path) as store:
            self.assertEqual(store.db.execute('SELECT fyp_status FROM accounts WHERE identity=?',
                                              (self.konto['identity'],)).fetchone()[0], 'drift')

    def test_empty_konsum_does_not_book_warmth(self):
        im, backend = mock.MagicMock(), mock.MagicMock()
        im.stored.return_value = ('de', False)
        persona = {'kanaele': ['alice'], 'verhalten': {}}
        with mock.patch.object(personas, 'persona_von', return_value=persona), \
             mock.patch.object(cli, 'make_runtime', return_value=(im, backend, None)), \
             mock.patch.object(cli, 'shutdown_runtime'), \
             mock.patch.object(konsum, '_cookies'), \
             mock.patch.object(konsum, '_verbuche_sitzung') as buchen:
            self.assertEqual(konsum.sitzung(self.cfg, self.konto, ziel_sekunden=60,
                                           stop=lambda: True), (0, 0))
        buchen.assert_not_called()

    def test_runtime_is_closed_if_identity_loading_fails(self):
        persona = {'influencer': 'alice', 'kanaele': ['alice'], 'verhalten': {}}
        for fail_at in ('stored', 'get'):
            im, backend = mock.MagicMock(), mock.MagicMock()
            im.stored.return_value = ('de', False)
            getattr(im, fail_at).side_effect = ValueError('kaputte Identity')
            with mock.patch.object(personas, 'persona_von', return_value=persona), \
                 mock.patch.object(cli, 'make_runtime', return_value=(im, backend, None)), \
                 mock.patch.object(cli, 'shutdown_runtime') as shutdown:
                for module in (seed, konsum):
                    with self.assertRaises(ValueError):
                        module.sitzung(self.cfg, self.konto, **({'ziel_sekunden': 1} if module is konsum else {}))
                self.assertEqual(shutdown.call_count, 2)

    def test_monitor_reports_only_successfully_inserted_videos(self):
        db = mock.MagicMock()

        def execute(sql, *_):
            if sql.startswith('SELECT'):
                return SimpleNamespace(fetchone=lambda: None)
            return SimpleNamespace(rowcount=0)  # anderer Lauf war beim INSERT schneller

        db.execute.side_effect = execute
        fake_store = mock.MagicMock()
        fake_store.__enter__.return_value = SimpleNamespace(db=db)
        melden = mock.MagicMock()
        with mock.patch('socialscraper.store.Store', return_value=fake_store), \
             mock.patch.object(monitor, 'ensure_schema'), \
             mock.patch.object(cli, 'make_runtime', return_value=(None, None, None)), \
             mock.patch.object(cli, 'shutdown_runtime'), \
             mock.patch.object(TikTokAdapter, 'profile_urls',
                               return_value=['https://www.tiktok.com/@alice/video/42']):
            self.assertEqual(monitor.neue_videos(self.cfg, ['alice'], melden=melden), [])
        melden.assert_not_called()


if __name__ == '__main__':
    unittest.main()
