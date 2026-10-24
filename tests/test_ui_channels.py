"""Fenstertests für Kanäle (Dialog, Seite „Kanäle“ im Tab Automatik, Kanal-Bereich im Tab Abrufen) und die Videolänge
im Tab „Gespeicherte Daten“. Alles im Wegwerf-Ordner mit einem Mini-Hauptfenster (FakeApp), ohne Netz."""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import chanlogic, channels, repo
from socialscraper import guicore as core
from socialscraper.models import Video
from socialscraper.store import Store

try:
    import tkinter
    from ui_fake import FakeApp
except Exception:  # pragma: no cover
    FakeApp = None

YT = 'https://www.youtube.com/watch?v='


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / 'data').mkdir()
        self.db = self.root / 'data' / 'scrape.db'
        for p in (patch.dict(os.environ, {'SS_DB_PATH': str(self.db)}), patch.object(core, 'ROOT', self.root)):
            p.start()
            self.addCleanup(p.stop)
        for name in ('showinfo', 'showwarning', 'showerror'):
            p = patch(f'tkinter.messagebox.{name}')
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        p = patch('tkinter.messagebox.askyesno', return_value=True)
        self.ask = p.start()
        self.addCleanup(p.stop)
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self.app.destroy)

    def channel(self, platform='youtube', value='https://www.youtube.com/@test', **kw):
        ref = channels.ChannelRef(platform, 'channel', value, '@test', value)
        with Store(self.db) as s:
            return channels.create_channel(s.db, ref, **kw)

    def add_items(self, target, n, done=0, failed=0):
        with Store(self.db) as s:
            repo.add_channel_items(s.db, target['id'], target['platform'],
                                   [{'url': YT + f'v{i:010d}', 'uid': None, 'title': f'Titel {i}', 'duration_s': 60 + i,
                                     'published_at': f'2026-09-{i % 28 + 1:02d}T00:00:00Z'} for i in range(n)])
            rows = s.db.execute('SELECT id FROM channel_items ORDER BY id').fetchall()
            for (item_id,) in rows[:done]:
                repo.set_channel_item(s.db, item_id, 'done', attempt=True)
            for (item_id,) in rows[done:done + failed]:
                repo.set_channel_item(s.db, item_id, 'failed', error='Video nicht gefunden', attempt=True)


@unittest.skipUnless(os.name == 'nt' and FakeApp is not None, 'Tk-Fenster nur unter Windows')
class DialogTests(Base):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_auto import AutoTab
        self.tab = self.app.add('Automatik', AutoTab)
        self.app.update()

    def open(self, link='', preview=None):
        from socialscraper.ui import channel_dialog
        dialog = channel_dialog.open_dialog(self.tab, self.root, link, modal=False, preview_fn=preview)
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        self.app.update()
        return dialog

    def test_link_is_recognized_with_platform_and_name(self):
        d = self.open('https://www.youtube.com/@LinusTechTips/videos')
        self.assertIn('YouTube', d.recognized.cget('text'))
        self.assertIn('@linustechtips', d.recognized.cget('text'))
        self.assertEqual(d.ref.platform, 'youtube')
        d.link_var.set('https://www.reddit.com/r/python/new/')
        self.assertIn('Subreddit r/python', d.recognized.cget('text'))
        self.assertIn('500', d.scope_hint.cget('text'))             # Subreddit: „alle“ ist nie unbegrenzt
        d.link_var.set('https://www.tiktok.com/@name')
        self.assertEqual(str(d.account_cb.cget('state')), 'readonly')   # Konten gibt es bei TikTok
        d.link_var.set('https://www.youtube.com/@testkanal')
        self.assertEqual(str(d.account_cb.cget('state')), 'disabled')   # YouTube braucht kein Konto

    def test_lookalikes_and_video_links_are_refused_in_the_dialog(self):
        d = self.open('https://youtube.com.evil.com/@a')
        self.assertIsNone(d.ref)
        self.assertIn('kein Link von YouTube', d.recognized.cget('text'))
        d.link_var.set('https://www.youtube.com/watch?v=abcdefghijk')
        self.assertIsNone(d.ref)
        self.assertIn('einzelnen Video', d.recognized.cget('text'))
        self.assertFalse(d.save())
        self.assertIn('✖', d.error.cget('text'))
        self.assertEqual(chanlogic.load(self.db), [])

    def test_estimate_explains_time_formula_and_warnings(self):
        d = self.open('https://www.youtube.com/@testkanal')
        self.assertIn('Beispiel: 1.000 Videos', d.estimate.cget('text'))
        d.profile_var.set('Vorsichtig')
        d._update()
        self.assertIn('rund 17 Tage', d.estimate.cget('text'))
        self.assertIn('60 pro Tag', d.formula.cget('text'))
        self.assertIn('keine Messung', d.formula.cget('text'))
        d.scope_var.set('latest')
        d.latest_var.set('40')
        d._update()
        self.assertIn('40 Videos', d.estimate.cget('text'))
        d.comments_var.set('50')
        self.assertIn('Kostenschätzung', d.warnings.cget('text'))
        self.assertIn('Vorsichtig:', d.profile_hint.cget('text'))
        self.assertIn('60 pro Tag', d.profile_hint.cget('text'))

    def test_preview_runs_in_background_and_shows_name_and_count(self):
        seen = []
        d = self.open('https://www.youtube.com/@testkanal',
                      preview=lambda ref: seen.append(ref.value) or {'name': 'Mein Kanal', 'total': 1380, 'note': '1.380 Videos laut YouTube.'})
        d.run_preview()
        self.assertTrue(self.app.pump(lambda: d.total == 1380))
        self.assertIn('Mein Kanal', d.recognized.cget('text'))
        self.assertIn('1.380 Videos', d.estimate.cget('text'))
        self.assertNotIn('Beispiel', d.estimate.cget('text'))
        self.assertEqual(seen, ['https://www.youtube.com/@testkanal'])

    def test_check_button_is_released_when_the_link_changes_during_the_preview(self):
        import threading
        gate = threading.Event()

        def slow(ref):
            gate.wait(5)
            return {'name': 'Kanal', 'total': 3, 'note': ''}
        d = self.open('https://www.youtube.com/@testkanal', preview=slow)
        d.run_preview()
        self.assertEqual(str(d.check_btn.cget('state')), 'disabled')
        d._preview_gen += 1                      # wie ein Tippen am Link, während die Vorschau läuft
        gate.set()
        self.assertTrue(self.app.pump(lambda: str(d.check_btn.cget('state')) == 'normal'))

    def test_preview_error_is_a_clear_sentence(self):
        def boom(ref):
            raise RuntimeError('Keine Verbindung')
        d = self.open('https://www.youtube.com/@testkanal', preview=boom)
        d.run_preview()
        self.assertTrue(self.app.pump(lambda: 'nicht möglich' in d.recognized.cget('text')))
        self.assertNotIn('Traceback', d.recognized.cget('text'))

    def test_save_creates_channel_with_all_options(self):
        with Store(self.db) as s:
            repo.ensure_default_accounts(s.db)
            group = repo.create_group(s.db, 'Mein Kanal')
        d = self.open('https://www.youtube.com/@testkanal')
        d.group_cb.configure(values=['(keine Gruppe)', 'Mein Kanal'])
        d.groups = [group]
        d.group_var.set('Mein Kanal')
        d.scope_var.set('since')
        d.since_var.set('2026-01-31')
        d.comments_var.set('5')
        d.profile_var.set('Vorsichtig')
        d.quiet_on.set(True)
        d.quiet_from.set('22')
        d.quiet_to.set('6')
        d.interval_var.set('12 Stunden')
        d.observe_var.set(False)
        d.shorts_var.set(False)
        d.owner_var.set(True)
        self.assertTrue(d.save())
        t = d.result
        self.assertEqual((t['kind'], t['platform'], t['scope'], t['scope_value'], t['max_comments']), ('channel', 'youtube', 'since', '2026-01-31', 5))
        self.assertEqual((t['pace_profile'], t['quiet_hours'], t['interval_min'], t['observe'], t['include_shorts']),
                         ('careful', '22-6', 720, 0, 0))
        self.assertEqual((t['group_id'], t['owner_as_creator'], t['backfill_state']), (group['id'], 1, 'queued'))
        self.assertFalse(d.winfo_exists())
        with self.assertRaises(ValueError):                      # derselbe Kanal noch einmal
            chanlogic.create(self.db, channels.parse_channel_link('https://www.youtube.com/@testkanal'))

    def test_bad_inputs_show_messages_and_keep_the_dialog(self):
        d = self.open('https://www.youtube.com/@testkanal')
        for setup, text in ((lambda: (d.scope_var.set('since'), d.since_var.set('31.01.2026')), 'Datum'),
                            (lambda: (d.scope_var.set('latest'), d.latest_var.set('abc')), 'Zahl der neuesten'),
                            (lambda: (d.scope_var.set('all'), d.comments_var.set('9999')), 'Kommentare'),
                            (lambda: (d.comments_var.set('0'), d.quiet_on.set(True), d.quiet_from.set('5'), d.quiet_to.set('5')), 'Ruhezeit'),
                            (lambda: (d.quiet_on.set(False), d.owner_var.set(True)), 'braucht eine Gruppe')):
            setup()
            self.assertFalse(d.save())
            self.assertIn(text, d.error.cget('text'))
            self.assertTrue(d.winfo_exists())
        self.assertEqual(chanlogic.load(self.db), [])

    def test_platform_specific_options(self):
        d = self.open('https://www.reddit.com/r/python')
        self.assertTrue(d.onlyvideo_cb.winfo_manager())
        self.assertTrue(d.sort_cb.winfo_manager())
        self.assertFalse(d.owner_cb.winfo_manager())              # im Subreddit gibt es keinen „Inhaber“
        d.link_var.set('https://www.reddit.com/user/spez')
        self.assertTrue(d.owner_cb.winfo_manager())
        self.assertFalse(d.sort_cb.winfo_manager())
        d.link_var.set('https://www.youtube.com/@testkanal')
        self.assertTrue(d.shorts_cb.winfo_manager())
        self.assertFalse(d.onlyvideo_cb.winfo_manager())
        self.assertFalse(d.owner_var.get())                       # Standard: aus (Personen nur durch Marcs Haken)

    def test_rotation_choice_counts_accounts_in_the_estimate(self):
        with Store(self.db) as s:
            repo.ensure_default_accounts(s.db)
            repo.add_account(s.db, 'tiktok', 'Zweit')
        d = self.open('https://www.tiktok.com/@name')
        self.assertIn('Rotation', ' '.join(d.account_cb.cget('values')))
        d.profile_var.set('Vorsichtig')
        d.account_var.set(d.account_cb.cget('values')[-1])
        d._update()
        self.assertIn('× 2 Konten', d.formula.cget('text'))
        d.account_var.set(d.account_cb.cget('values')[0])
        d._update()
        self.assertNotIn('Konten', d.formula.cget('text').split('Tageslimit')[1].split(';')[0])


@unittest.skipUnless(os.name == 'nt' and FakeApp is not None, 'Tk-Fenster nur unter Windows')
class ChannelPageTests(Base):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_auto import AutoTab
        self.tab = self.app.add('Automatik', AutoTab)
        self.app.update()

    def load(self):
        self.tab.refresh()
        self.assertTrue(self.app.pump(lambda: not self.tab.loading))
        self.app.update()

    def test_empty_page_explains_and_channels_stay_out_of_the_targets_list(self):
        self.load()
        self.assertEqual(self.tab.ctree.get_children(), ())
        self.assertTrue(self.tab.chan_empty.winfo_ismapped() or self.tab.chan_empty.place_info())
        t = self.channel()
        with Store(self.db) as s:
            repo.add_target(s.db, 'hashtag', 'youtube', 'lofi')
        self.load()
        self.assertEqual(len(self.tab.tree.get_children()), 1)      # nur das Hashtag-Ziel
        self.assertEqual(self.tab.ctree.get_children(), (f"c{t['id']}",))

    def test_row_shows_progress_failures_next_step_and_pause(self):
        t = self.channel(pace_profile='careful')
        self.add_items(t, 30, done=12, failed=3)
        self.load()
        values = self.tab.ctree.item(f"c{t['id']}", 'values')
        self.assertEqual(values[0], 'YouTube')
        self.assertIn('■', values[2])
        self.assertIn('40 %', values[2])
        self.assertIn('12 von 30', values[3])
        self.assertIn('3 fehlgeschlagen', values[3])
        self.assertTrue(values[5])      # nächster Schritt: „jetzt“ oder eine Uhrzeit
        self.assertEqual(self.tab.ctree.heading('next', 'text'), 'Nächster Schritt')
        with Store(self.db) as s:
            channels.pause_channel(s.db, t['id'], repo.now() + __import__('datetime').timedelta(hours=3), 'CAPTCHA')
        self.load()
        line = self.tab.ctree.item(f"c{t['id']}", 'values')[4]
        self.assertIn('pausiert bis', line)
        self.assertIn('wegen CAPTCHA', line)
        self.assertIn('Kanäle (1)', self.tab.nb.tab(self.tab.chan_page, 'text'))

    def select(self, t):
        self.tab.ctree.selection_set(f"c{t['id']}")
        self.app.update()

    def test_buttons_follow_selection_and_actions_change_state(self):
        t = self.channel()
        self.add_items(t, 5, done=1, failed=2)
        self.load()
        self.assertEqual(str(self.tab.chan_btn['pace'].cget('state')), 'disabled')
        self.select(t)
        self.assertEqual(str(self.tab.chan_btn['pace'].cget('state')), 'normal')
        self.assertEqual(self.tab.chan_btn['toggle'].cget('text'), 'Pausieren')
        self.tab.channel_toggle()
        self.load()
        self.select(t)
        self.assertEqual(self.tab.chan_btn['toggle'].cget('text'), 'Fortsetzen')
        self.assertIn('angehalten', self.tab.ctree.item(f"c{t['id']}", 'values')[4])
        self.tab.channel_toggle()
        self.load()
        with Store(self.db) as s:
            self.assertEqual(repo.get_target(s.db, t['id'])['enabled'], 1)
        self.select(t)
        self.tab.channel_action('requeue')
        self.load()
        with Store(self.db) as s:
            self.assertEqual(repo.channel_progress(s.db, t['id'])['failed'], 0)
        self.assertIn('2 fehlgeschlagene', self.app.statuses[-1][0])
        self.select(t)
        self.tab.channel_action('now')
        with Store(self.db) as s:
            self.assertEqual(repo.get_target(s.db, t['id'])['force_once'], 1)

    def test_tempo_dialog_changes_profile_and_quiet_hours(self):
        t = self.channel()
        self.load()
        self.select(t)
        with patch('socialscraper.ui.tab_auto.ask_form', return_value={'profile': 'fast', 'quiet': '23-7'}):
            self.tab.channel_pace()
        with Store(self.db) as s:
            row = repo.get_target(s.db, t['id'])
        self.assertEqual((row['pace_profile'], row['quiet_hours']), ('fast', '23-7'))

    def test_remove_asks_and_keeps_saved_videos(self):
        t = self.channel()
        self.add_items(t, 3)
        with Store(self.db) as s:
            s.save_result(Video('youtube', 'abcdefghijk', YT + 'abcdefghijk'), [], None)
        self.load()
        self.select(t)
        self.ask.return_value = False
        self.tab.channel_remove()
        self.assertEqual(len(chanlogic.load(self.db)), 1)
        self.ask.return_value = True
        self.tab.channel_remove()
        self.assertEqual(chanlogic.load(self.db), [])
        with Store(self.db) as s:
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM videos').fetchone()[0], 1)
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM channel_items').fetchone()[0], 0)
        self.assertIn('Videos und Kommentare bleiben erhalten', self.ask.call_args.args[1])

    def test_details_list_queue_with_status_and_errors(self):
        t = self.channel()
        self.add_items(t, 6, done=2, failed=1)
        self.load()
        self.select(t)
        detail = self.tab.channel_details()
        self.addCleanup(lambda: detail.winfo_exists() and detail.destroy())
        self.app.update()
        self.assertEqual(len(detail.tree.get_children()), 6)
        statuses = [detail.tree.item(i, 'values')[0] for i in detail.tree.get_children()]
        self.assertEqual(sorted(set(statuses)), ['abgerufen', 'fehlgeschlagen', 'wartet'])
        failed = [detail.tree.item(i, 'values') for i in detail.tree.get_children() if detail.tree.item(i, 'values')[0] == 'fehlgeschlagen'][0]
        self.assertIn('Video nicht gefunden', failed[5])
        self.assertTrue(any(detail.tree.item(i, 'values')[3] for i in detail.tree.get_children()))   # Länge steht da
        detail.filter_var.set('Fehlgeschlagen')
        detail.reload()
        self.assertEqual(len(detail.tree.get_children()), 1)
        self.assertIn('2 von 6', detail.line.cget('text'))

    def test_add_from_tab_opens_dialog_and_reloads(self):
        from socialscraper.ui import channel_dialog
        with patch.object(channel_dialog, 'ChannelDialog') as dialog_cls:
            dialog_cls.return_value.result = None
            self.tab.add_channel('https://www.youtube.com/@testkanal', modal=False)
            self.assertEqual(dialog_cls.call_args.kwargs['link'], 'https://www.youtube.com/@testkanal')

    def test_fetch_tab_has_its_own_channel_area(self):
        from socialscraper.ui.tab_fetch import FetchTab
        fetch = self.app.add('Abrufen', FetchTab)
        self.app.update()
        self.assertTrue(hasattr(fetch, 'channel_link'))
        from socialscraper.ui import channel_dialog
        fetch.channel_link.set('https://www.youtube.com/@testkanal')
        fake = type('D', (), {'result': {'platform': 'youtube'}})()
        with patch.object(channel_dialog, 'open_dialog', return_value=fake) as opener:
            fetch.add_channel()
        self.assertEqual(opener.call_args.args[2], 'https://www.youtube.com/@testkanal')
        self.assertEqual(fetch.channel_link.get(), '')
        self.assertIn('Automatik', self.app.selected)


@unittest.skipUnless(os.name == 'nt' and FakeApp is not None, 'Tk-Fenster nur unter Windows')
class LengthTabTests(Base):
    def setUp(self):
        super().setUp()
        from socialscraper.ui.tab_data import DataTab
        with Store(self.db) as s:
            repo.ensure_default_accounts(s.db)
            for vid, dur, views in (('a1', 45, 100), ('b2', 125, 300), ('c3', 3725, 50), ('d4', None, 70), ('e5', 600, 200), ('f6', 60, 10)):
                s.save_result(Video('youtube', vid + 'abcdefghi', f'https://youtu.be/{vid}abcdefghi', author='X', caption=vid,
                                    duration_s=dur, view_count=views, like_count=views // 10, comment_count=3), [], None)
            g = repo.create_group(s.db, 'Lang')
            repo.add_videos_to_group(s.db, g['id'], ['youtube:c3abcdefghi', 'youtube:a1abcdefghi'])
        self.tab = self.app.add('Gespeicherte Daten', DataTab)
        self.tab.modal_dialogs = False
        self.app.update()
        self.tab.refresh()
        self.app.update()

    def lengths(self):
        return [self.tab.tree.item(i, 'values')[6] for i in self.tab.tree.get_children()]

    def test_column_shows_m_ss_and_h_mm_ss_and_empty_values(self):
        self.assertIn('Länge', self.tab.tree.heading('length', 'text'))
        self.assertEqual(sorted(self.lengths()), sorted(['0:45', '2:05', '1:02:05', '', '10:00', '1:00']))

    def test_sorting_by_length_puts_empty_values_last_in_both_directions(self):
        for desc in (True, False):
            self.tab.sort.update(key='length', desc=desc)
            self.tab.refresh()
            lengths = self.lengths()
            self.assertEqual(lengths[-1], '')
            known = lengths[:-1]
            self.assertEqual(known, ['1:02:05', '10:00', '2:05', '1:00', '0:45'] if desc else ['0:45', '1:00', '2:05', '10:00', '1:02:05'])

    def test_filter_classes_with_settings_limits(self):
        def pick(label_start):
            label = next(t for t in self.tab.length_box.cget('values') if t.startswith(label_start))
            self.tab.length_var.set(label)
            self.tab._length_changed()
            return sorted(self.lengths())
        self.assertEqual(pick('Länge: kurz'), ['0:45', '1:00'])               # bis 60 s
        self.assertEqual(pick('Länge: mittel'), ['10:00', '2:05'])            # über 60 s bis 10 min
        self.assertEqual(pick('Länge: lang'), ['1:02:05'])
        self.assertEqual(len(pick('Länge: alle')), 6)                          # „alle“ enthält auch das Video ohne Länge
        core.write_settings({'length_short_s': 120, 'length_long_s': 3600}, self.root)
        self.tab.refresh()
        labels = list(self.tab.length_box.cget('values'))
        self.assertIn('Länge: kurz (bis 2 min)', labels)
        self.assertIn('Länge: lang (über 60 min)', labels)
        self.assertEqual(pick('Länge: kurz'), ['0:45', '1:00'])
        self.assertEqual(pick('Länge: mittel'), ['10:00', '2:05'])

    def test_filter_empty_result_message(self):
        with Store(self.db) as s:
            s.db.execute('UPDATE videos SET duration_s=NULL')
            s.db.commit()
        self.tab.length_key = 'short'
        self.tab.refresh()
        self.assertIn('Keine Videos in dieser Längenklasse', self.tab.empty_text.cget('text'))

    def test_video_info_line_shows_length_class_or_unknown(self):
        def select_by_caption(caption):
            iid = next(i for i in self.tab.tree.get_children() if self.tab.tree.item(i, 'values')[2] == caption)
            self.tab.tree.selection_set(iid)
            self.tab._show_details()
        select_by_caption('c3')
        self.assertIn('Länge: 1:02:05 (lang)', self.tab.video_info.cget('text'))
        select_by_caption('d4')
        self.assertIn('Länge: unbekannt', self.tab.video_info.cget('text'))

    def test_overview_shows_average_shortest_and_longest(self):
        values = self.tab.otree.item(self.tab.otree.get_children()[0], 'values')
        # Gruppe „Lang“: 45 s und 1:02:05 -> Ø 31:25, kürzestes 0:45, längstes 1:02:05
        self.assertEqual(values[7:10], ('31:25', '0:45', '1:02:05'))
        self.assertIn('Ø Länge', [self.tab.otree.heading(c, 'text') for c in self.tab.otree['columns']])

    def test_comparison_per_class_uses_only_measured_values(self):
        rows = {self.tab.ltree.item(i, 'values')[0].split(' ')[0]: self.tab.ltree.item(i, 'values') for i in self.tab.ltree.get_children()}
        self.assertEqual(rows['kurz'][1], '2')
        self.assertEqual(rows['kurz'][3], '55')                  # Ø Aufrufe aus 100 und 10
        self.assertEqual(rows['ohne'][1], '1')                   # das Video ohne Länge ist gekennzeichnet
        self.assertEqual(rows['ohne'][2], '')
        self.assertIn('keine Länge bekannt', self.tab.length_note.cget('text'))


if __name__ == '__main__':
    unittest.main()
