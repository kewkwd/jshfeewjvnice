"""Fenstertests (FakeApp, Wegwerf-Ordner) für Stimmung (KI-Einschätzung), Personen in Gruppen und neue Autoren:
Tab „Gespeicherte Daten“, Dialog „Stimmung bestimmen“, Einstellungen „KI / Stimmung“, Schalter im Tab „Automatik“."""
import gc
import json
import os
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from fake_anthropic import KEY, FakeAnthropic

from socialscraper import aiconfig, guicore as core, people_repo as pr, repo, sentiment, sentiment_repo as srepo
from socialscraper.models import Comment, Video
from socialscraper.store import Store
from socialscraper.ui import logic
from test_people import build

try:
    import tkinter
    from ui_fake import FakeApp
    from socialscraper.ui.tab_auto import AutoTab
    from socialscraper.ui.tab_data import DataTab
    from socialscraper.ui.tab_settings import SettingsTab
except Exception:  # pragma: no cover
    FakeApp = None


@unittest.skipUnless(os.name == 'nt' and FakeApp is not None, 'Tk-Fenster nur unter Windows')
class UiBase(unittest.TestCase):
    tabs = ()

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
        patch.object(core, 'ROOT', self.root).start()
        self.addCleanup(patch.stopall)
        self.videos = build(self.db)
        with Store(self.db) as s:
            s.db.execute("UPDATE comments SET first_seen_at='2026-01-01T00:00:00Z'")
            s.db.execute("UPDATE videos SET first_seen_at='2026-01-01T00:00:00Z'")
            s.db.execute("UPDATE comments SET first_seen_at=? WHERE author='Zoë'", (repo.iso(),))
            s.db.commit()
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self._close)
        for name in ('showinfo', 'showwarning', 'showerror'):
            p = patch(f'tkinter.messagebox.{name}')
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        p = patch('tkinter.messagebox.askyesno', return_value=True)
        self.askyesno = p.start()
        self.addCleanup(p.stop)
        self.built = {title: self.app.add(title, cls) for title, cls in self.tabs}
        self.app.update()

    def _close(self):
        for tab in self.built.values():
            if hasattr(tab, 'loading'):
                self.app.pump(lambda t=tab: not t.loading, 5)
        self.app.destroy()
        self.app = None
        self.built.clear()
        gc.collect()

    def ask(self, values):
        """Ersatz für den Dialog der Tabs: ruft wie der echte die Prüfung auf und gibt die Werte zurück."""
        def fake(parent, title, fields, intro='', ok_text='OK', validator=None):
            problem = validator(values) if validator else None
            self.last_problem = problem
            self.last_dialog = (title, fields, intro)
            return None if problem else values
        return patch('socialscraper.ui.tab_data.ask_form', side_effect=fake)

    def save_key(self, **extra):
        aiconfig.save(dict(aiconfig.default_config(), api_key=KEY, **extra), self.root)

    def classify_all(self):
        with Store(self.db) as s:
            items = srepo.open_comments(s.db)
            srepo.save_results(s.db, [(i['uid'], i['hash'], *sentiment_label(i['text'])) for i in items], 'claude-haiku-5-5', 1)


def sentiment_label(text):
    from fake_anthropic import default_classify
    return default_classify(text)


class DataTabMoodTests(UiBase):
    tabs = (('Gespeicherte Daten', DataTab),)

    def setUp(self):
        super().setUp()
        self.tab = self.built['Gespeicherte Daten']
        self.tab.modal_dialogs = False

    def test_bar_comment_column_filter_and_chart(self):
        self.classify_all()
        t = self.tab
        t.refresh()
        t.tree.selection_set('tiktok:a1')
        t._show_details()
        dist, caption = t.mood_bar._data
        self.assertEqual((dist['positive'], dist['neutral'], dist['negative'], dist['analyzed']), (1, 1, 1, 3))
        self.assertIn('1 Video gewählt', caption)
        t.mood_bar.update()
        self.assertTrue(t.mood_bar.find_all())
        texts = [t.mood_bar.itemcget(i, 'text') for i in t.mood_bar.find_all() if t.mood_bar.type(i) == 'text']
        self.assertTrue(any('KI-Einschätzung' in x for x in texts))                           # überall als Schätzung beschriftet
        self.assertTrue(any('Positiv 1 (33 %)' in x for x in texts))
        moods = {t.ctree.set(i, 'text'): t.ctree.set(i, 'mood') for i in t.ctree.get_children()}
        self.assertEqual(moods, {'Super': '▲ positiv', 'Mist': '▼ negativ', 'ok': '● neutral'})
        t.mood_filter.set('nur negative')
        t._show_comments()
        self.assertEqual([t.ctree.set(i, 'text') for i in t.ctree.get_children()], ['Mist'])
        self.assertIn('Filter: nur negative', t.comment_info.cget('text'))
        t.mood_filter.set('nur positive')
        t._show_comments()
        self.assertEqual([t.ctree.set(i, 'text') for i in t.ctree.get_children()], ['Super'])
        t.mood_filter.set('noch nicht eingestuft')
        t._show_comments()
        self.assertEqual(t.ctree.get_children(), ())
        self.assertIn('Keine Kommentare mit diesem Filter', t.comment_info.cget('text'))
        t.mood_filter.set('Alle Kommentare')
        t.metric.set('mood')
        t._show_history()
        self.assertTrue(t.mood_chart.winfo_manager() and not t.chart.winfo_manager())
        self.assertEqual(len(t.mood_chart._data[0]), 3)                                       # Tage 2026-10-01, 2026-10-03 und der Tag ohne Datum der Plattform
        t.mood_chart.update()
        self.assertTrue(t.mood_chart.find_all())
        t.metric.set('views')
        t._show_history()
        self.assertTrue(t.chart.winfo_manager())

    def test_chart_empty_state_and_bar_without_data(self):
        t = self.tab
        t.refresh()
        t._show_details()
        t.metric.set('mood')
        t._show_history()
        self.assertEqual(t.mood_chart._data[0], [])
        self.assertIn('Stimmung bestimmen', t.mood_chart._data[1])                           # leerer Zustand mit Hinweis
        self.assertEqual(t.mood_bar._data[0]['analyzed'], 0)
        t.mood_bar.update()
        text = ' '.join(t.mood_bar.itemcget(i, 'text') for i in t.mood_bar.find_all() if t.mood_bar.type(i) == 'text')
        self.assertIn('Noch nicht bestimmt', text)

    def test_group_overview_sums_and_member_mood(self):
        self.classify_all()
        with Store(self.db) as s:
            g = repo.create_group(s.db, 'Beobachtung')
            repo.add_videos_to_group(s.db, g['id'], ['tiktok:a1'])
            pr.add_people(s.db, g['id'], 'commenter', [pr.person('tiktok', None, 'Max')])
            pr.add_people(s.db, g['id'], 'creator', [pr.person('tiktok', 'uid-9', 'Küchenfee')])
        t = self.tab
        t.refresh()
        row = t.otree.item(str(g['id']), 'values')
        cols = dict(zip(t.otree['columns'], row))
        self.assertEqual((cols['videos'], cols['creators'], cols['commenters'], cols['comments']), ('1', '1', '1', '3'))
        self.assertEqual((cols['pos'], cols['neu'], cols['neg']), ('1', '1', '1'))
        self.assertIn('▲', cols['members'])
        self.assertIn('(1 · 1 · 1)', t.gtree.item(str(g['id']), 'text'))

    def test_mood_button_without_key_shows_friendly_hint_and_goes_to_settings(self):
        t = self.tab
        t.refresh()
        seen = {}

        def fake_form(parent, title, fields, intro='', ok_text='OK', validator=None):
            seen.update(intro=intro, ok=ok_text)
            return {}
        with patch('socialscraper.ui.sentiment_ui.ask_form', side_effect=fake_form):
            self.assertIsNone(t.mood_start())
        self.assertEqual(seen['ok'], 'Zu den Einstellungen')
        self.assertIn('API-Schlüssel', seen['intro'])
        self.assertEqual(self.app.selected, ['Einstellungen'])
        self.app.selected.clear()
        with patch('socialscraper.ui.sentiment_ui.ask_form', return_value=None):      # „Abbrechen“: bleibt hier
            t.mood_start()
        self.assertEqual(self.app.selected, [])
        self.assertEqual(self.fake.requests, [])

    def test_mood_button_without_data(self):
        self.db.unlink()
        self.tab.mood_start()
        self.assertEqual(self.showinfo.call_count, 1)

    def test_dialog_estimate_scope_choices_and_full_run_against_fake_api(self):
        self.save_key(price_in_per_mtok=3.0, price_out_per_mtok=15.0)
        t = self.tab
        t.refresh()
        t.tree.selection_set(['tiktok:a1', 'tiktok:a2'])
        self.app.update()
        dialog = t.mood_start()
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        self.assertEqual([o['key'] for o in dialog.options], ['selection', 'all'])
        self.assertEqual(dialog.scope_var.get(), 'selection')
        text = dialog.estimate_label.cget('text')
        self.assertIn('Offene Kommentare: 4', text)                                          # 3 + 1 der zwei Videos
        self.assertIn('Anfragen an die API: 1', text)
        self.assertIn('Eingabe-Tokens', text)
        self.assertIn('Geschätzter Betrag', text)
        self.assertEqual(dialog.question.cget('text'), 'Jetzt bestimmen?')
        self.assertEqual(self.fake.requests, [])                                              # bis hierher nichts gesendet
        dialog.scope_var.set('all')
        dialog._estimate()
        self.assertIn('Offene Kommentare: 6', dialog.estimate_label.cget('text'))
        dialog.scope_var.set('selection')
        dialog._estimate()
        dialog.start()
        self.assertEqual(dialog.state_name, 'running')
        self.assertEqual(str(dialog.start_btn['state']), 'disabled')
        self.assertTrue(self.app.pump(lambda: dialog.state_name == 'done', 20))
        self.assertEqual(dialog.result['status'], 'ok')
        self.assertIn('4 Kommentare als KI-Einschätzung eingestuft', dialog.result_label.cget('text'))
        self.assertEqual(dialog.cancel_btn.cget('text'), 'Schließen')
        self.assertTrue(any('KI-Einschätzung' in line for line, _t in self.app.logs))
        self.assertNotIn(KEY, json.dumps(self.app.logs) + json.dumps(self.app.statuses))
        # die Tabs wurden danach neu geladen: Balken zeigt die neue Verteilung
        t._show_details()
        self.assertEqual(t.mood_bar._data[0]['analyzed'], 4)
        dialog.cancel()
        self.assertFalse(dialog.winfo_exists())

    def test_dialog_cancel_during_run_keeps_window_until_thread_ends(self):
        self.save_key()
        release = threading.Event()
        started = threading.Event()

        def slow_run(db_path, scope, *, cfg, cancel, progress, redo=False, **kw):
            progress(1, 6, {'requests': 1})
            started.set()
            while not cancel.is_set() and not release.wait(0.02):
                pass
            return {'status': 'cancelled', 'message': 'Abgebrochen. 1 Kommentare als KI-Einschätzung eingestuft.', 'analyzed': 1}
        t = self.tab
        t.refresh()
        with patch('socialscraper.sentiment.run', side_effect=slow_run):
            dialog = t.mood_start()
            self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
            dialog.start()
            self.assertTrue(started.wait(5))
            self.app.update()
            self.assertEqual(dialog.state_name, 'running')
            dialog.cancel()                                                                   # „Abbrechen“ während des Laufs
            self.assertTrue(dialog.winfo_exists())
            self.assertTrue(dialog.cancel_event.is_set())
            self.assertTrue(self.app.pump(lambda: dialog.state_name == 'done', 10))
        self.assertEqual(dialog.result['status'], 'cancelled')
        self.assertIn('Abgebrochen', dialog.result_label.cget('text'))
        self.assertEqual((dialog.cancel_btn.cget('text'), str(dialog.cancel_btn.cget('state'))), ('Schließen', 'normal'))

    def test_dialog_failure_shows_message_without_traceback(self):
        self.save_key()
        self.fake.key = 'anderer-schluessel-0123456789'                                       # der Server lehnt den Schlüssel ab
        t = self.tab
        t.refresh()
        dialog = t.mood_start()
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        dialog.start()
        self.assertTrue(self.app.pump(lambda: dialog.state_name == 'done', 20))
        self.assertEqual(dialog.result['status'], 'failed')
        self.assertIn('Der API-Schlüssel wird nicht akzeptiert', dialog.result_label.cget('text'))
        self.assertNotIn('Traceback', dialog.result_label.cget('text'))
        self.assertEqual(self.app.statuses[-1][1], 'err')

    def test_nothing_to_do_disables_start(self):
        self.save_key()
        self.classify_all()
        t = self.tab
        t.refresh()
        dialog = t.mood_start()
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        self.assertEqual(str(dialog.start_btn['state']), 'disabled')
        self.assertIn('Nichts zu tun', dialog.estimate_label.cget('text'))
        dialog.redo_var.set(True)                                                             # „noch einmal einstufen“ rechnet alles neu
        dialog._estimate()
        self.assertEqual(str(dialog.start_btn['state']), 'normal')
        self.assertIn('Offene Kommentare: 6', dialog.estimate_label.cget('text'))


class DataTabPeopleTests(UiBase):
    tabs = (('Gespeicherte Daten', DataTab),)

    def setUp(self):
        super().setUp()
        self.tab = self.built['Gespeicherte Daten']
        self.tab.modal_dialogs = False
        with Store(self.db) as s:
            self.g = repo.create_group(s.db, 'Beobachtung')
        self.tab.refresh()

    def names(self, lst):
        return [lst.tree.set(i, 'name') for i in lst.tree.get_children()]

    def test_switch_shows_creators_and_commenters_with_numbers(self):
        t = self.tab
        t.mode.set('creator')
        t.refresh()
        self.assertTrue(t.ppage.winfo_manager() and not t.vpage.winfo_manager())
        creators = t.plist['creator']
        rows = {creators.tree.set(i, 'name'): creators.tree.item(i, 'values') for i in creators.tree.get_children()}
        self.assertEqual(set(rows), {'Küchenfee', 'ÄRGER'})
        self.assertEqual(creators.tree.set(creators.tree.get_children()[0], 'platform') in ('TikTok', 'YouTube', 'Instagram'), True)
        t.mode.set('commenter')
        t.refresh()
        cm = t.plist['commenter']
        self.assertEqual(len(cm.tree.get_children()), 4)
        first = cm.tree.get_children()[0]
        self.assertEqual((cm.tree.set(first, 'name'), cm.tree.set(first, 'comments'), cm.tree.set(first, 'videos')), ('Max', '3', '2'))
        self.assertEqual(cm.tree.set(first, 'positive'), '–')                                  # noch nichts eingestuft
        self.classify_all()
        t.refresh()
        first = cm.tree.get_children()[0]
        self.assertIn('%', cm.tree.set(first, 'positive'))
        self.assertEqual(cm.tree.heading('positive')['text'].count('KI'), 1)                   # Spalten tragen „(KI)“
        t.mode.set('videos')
        t.refresh()
        self.assertTrue(t.vpage.winfo_manager())

    def test_search_platform_and_group_filter_in_people_views(self):
        t = self.tab
        t.mode.set('commenter')
        t.search.set('ZOË')
        t.refresh()
        self.assertEqual(self.names(t.plist['commenter']), ['Zoë'])
        t.search.set('max'); t.filter_platform.set('YouTube')
        t.refresh()
        self.assertEqual(len(t.plist['commenter'].tree.get_children()), 1)
        t.search.set(''); t.filter_platform.set('Alle')
        t.view = self.g['id']
        t.refresh()
        self.assertEqual(t.plist['commenter'].tree.get_children(), ())
        self.assertIn('keine Kommentarschreiber', t.plist['commenter'].empty.cget('text'))      # leerer Zustand mit nächstem Schritt
        self.assertIn('Zu Gruppe hinzufügen', t.plist['commenter'].empty.cget('text'))
        self.assertIn('nur Kommentarschreiber', t.view_label.cget('text'))
        t.view = logic.UNGROUPED
        t.refresh()
        self.assertEqual(len(t.plist['commenter'].tree.get_children()), 4)
        self.assertIn('in keiner Gruppe', t.view_label.cget('text'))

    def test_add_selected_commenters_to_new_group_then_remove_again(self):
        t = self.tab
        t.mode.set('commenter')
        t.refresh()
        lst = t.plist['commenter']
        lst.tree.selection_set(lst.tree.get_children()[:2])
        self.app.update()
        self.assertEqual(str(t.pbtn['add']['state']), 'normal')
        self.assertEqual(str(t.pbtn['remove']['state']), 'disabled')                            # keine Gruppe gewählt
        with self.ask({'new': 'Neue Beobachtung'}):
            msg = t.people_add()
        self.assertIn('2 Kommentarschreiber zur Gruppe „Neue Beobachtung“ hinzugefügt', msg)
        self.assertIn('Kommentarschreiber', self.app.statuses[-1][0])
        with Store(self.db) as s:
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM group_people').fetchone()[0], 2)
            gid = s.db.execute("SELECT id FROM groups WHERE name='Neue Beobachtung'").fetchone()[0]
        self.assertIn('Neue Beobachtung', ' '.join(lst.tree.set(i, 'groups') for i in lst.tree.get_children()))
        with self.ask({'group': str(gid), 'new': ''}):                                         # zweites Mal: schon drin
            msg = t.people_add()
        self.assertIn('2 waren schon drin', msg)
        t.view = gid
        t.refresh()
        self.assertEqual(len(lst.tree.get_children()), 2)
        lst.tree.selection_set(lst.tree.get_children()[:1])
        self.app.update()
        self.assertEqual(str(t.pbtn['remove']['state']), 'normal')
        t.people_remove()
        self.assertEqual(len(lst.tree.get_children()), 1)
        self.askyesno.assert_called()
        self.assertIn('entfernen', self.askyesno.call_args[0][1])

    def test_new_group_with_selection_button(self):
        t = self.tab
        t.mode.set('creator')
        t.refresh()
        lst = t.plist['creator']
        lst.tree.selection_set(lst.tree.get_children())
        self.app.update()

        def fake_dialog(title, group=None):
            with Store(self.db) as s:
                return repo.create_group(s.db, 'Aus Auswahl')
        with patch.object(t, '_group_dialog', side_effect=fake_dialog):
            msg = t.people_new_group()
        self.assertIn('Gruppe „Aus Auswahl“ angelegt, 3 Ersteller hinzugefügt', msg)
        self.assertEqual(t.view, [g for g in t.groups if t.groups[g]['name'] == 'Aus Auswahl'][0])
        self.assertEqual(len(t.plist['creator'].tree.get_children()), 3)

    def test_creators_of_selected_videos_and_commenters_of_selected_comments_join_groups(self):
        t = self.tab
        t.mode.set('videos')
        t.refresh()
        t.tree.selection_set(['tiktok:a1', 'tiktok:a2'])
        self.app.update()
        with self.ask({'group': str(self.g['id']), 'new': ''}):
            msg = t.add_creators()
        self.assertIn('1 Ersteller zur Gruppe „Beobachtung“ hinzugefügt', msg)                  # beide Videos: eine Person
        # Videos der Gruppe ODER ihrer Ersteller: beide TikTok-Videos erscheinen in der Gruppenansicht, auch ohne Zuordnung
        t.show_group(self.g['id'])
        self.assertEqual({u for u in t.video_rows}, {'tiktok:a1', 'tiktok:a2'})
        self.assertIn('Videos von Erstellern der Gruppe', t.view_label.cget('text'))
        self.assertIn('Beobachtung', t.tree.set('tiktok:a1', 'groups'))
        # Kommentare: Mehrfachauswahl in der Kommentarliste
        t.show_group(None)
        t.tree.selection_set('tiktok:a1')
        self.app.pump(lambda: t._detail_job is None and t.ctree.get_children())   # erst warten, bis der verzögerte Aufbau durch ist
        ids = t.ctree.get_children()
        t.ctree.selection_set(ids)
        self.app.update()
        self.assertNotIn(t.cbtn_people, t.cbar.hidden)                                           # der Knopf erscheint erst bei Auswahl
        with self.ask({'group': str(self.g['id']), 'new': ''}):
            msg = t.add_commenters()
        self.assertIn('2 Kommentarschreiber zur Gruppe', msg)                                    # 3 Kommentare, aber Max und @max sind eine Person
        with Store(self.db) as s:
            keys = {r[0] for r in s.db.execute("SELECT author_key FROM group_people WHERE role='commenter'")}
        self.assertEqual(keys, {'max', 'z-1'})

    def test_add_without_selection_says_what_to_do(self):
        t = self.tab
        t.mode.set('videos')
        t.refresh()
        t.tree.selection_set(())
        t.add_creators()
        t.add_commenters()
        self.assertEqual(self.showinfo.call_count, 2)
        t.mode.set('commenter')
        t.refresh()
        t.people_add()
        self.assertEqual(self.showinfo.call_count, 3)

    def test_comments_of_group_members_filter_and_group_comment_view(self):
        with Store(self.db) as s:
            pr.add_people(s.db, self.g['id'], 'commenter', [pr.person('tiktok', None, 'Max')])
        t = self.tab
        t.refresh()
        t.view = self.g['id']
        t.refresh()
        t.tree.selection_set(())
        t._show_details()
        self.assertEqual(len(t.ctree.get_children()), 3)                                         # alle Kommentare der Mitglieder über alle Videos
        self.assertIn('über alle Videos', t.comment_info.cget('text'))
        self.assertIn('video', t.ctree['displaycolumns'])
        t.members_only.set(False)
        t.show_group(None)
        t.tree.selection_set('tiktok:a1')
        t.view = self.g['id']
        t.refresh()                                                                              # Gruppenansicht enthält a1 nicht: Auswahl fällt weg
        t.show_group(None)
        t.tree.selection_set('tiktok:a1')
        t._show_details()
        self.assertEqual(len(t.ctree.get_children()), 3)
        t.view = self.g['id']
        t.members_only.set(True)
        t._show_comments()
        self.assertEqual(len(t.ctree.get_children()), 2)                                         # nur Max und @max, nicht Zoë

    def test_people_sort_header_and_select_all(self):
        t = self.tab
        t.mode.set('commenter')
        t.refresh()
        lst = t.plist['commenter']
        lst.headers.click('likes')
        likes = [lst.tree.set(i, 'likes') for i in lst.tree.get_children()]
        self.assertEqual(likes, sorted(likes, key=int, reverse=True))
        lst.headers.click('name')
        self.assertEqual(self.names(lst), sorted(self.names(lst), key=lambda n: pr.sort_key(n)))
        self.assertEqual(lst._select_all(), 'break')
        self.assertEqual(len(lst.tree.selection()), 4)
        self.assertEqual(len(lst.selected()), 4)


class NewAuthorsUiTests(UiBase):
    tabs = (('Gespeicherte Daten', DataTab),)

    def setUp(self):
        super().setUp()
        self.tab = self.built['Gespeicherte Daten']

    def test_new_filter_counter_marker_and_known(self):
        t = self.tab
        t.mode.set('commenter')
        t.refresh()
        lst = t.plist['commenter']
        self.assertIn('1 neuer Kommentarschreiber in den letzten 24 Stunden', t.new_counter.cget('text'))
        new_rows = [i for i in lst.tree.get_children() if 'new' in lst.tree.item(i, 'tags')]
        self.assertEqual([lst.tree.set(i, 'name') for i in new_rows], ['Zoë'])
        self.assertIn('★ neu', lst.tree.set(new_rows[0], 'seen'))
        t.show_filter.set('Nur neue Autoren')
        t.refresh()
        self.assertEqual(self.names(lst) if hasattr(self, 'names') else [lst.tree.set(i, 'name') for i in lst.tree.get_children()], ['Zoë'])
        t.show_filter.set('Nur bekannte (nicht neu)')
        t.refresh()
        self.assertEqual(len(lst.tree.get_children()), 3)
        t.show_filter.set('Alle')
        t.new_window.set('in den letzten 30 Tagen')
        t.refresh()
        self.assertIn('1 neuer Kommentarschreiber in den letzten 30 Tagen', t.new_counter.cget('text'))
        t.new_window.set('irgendwann')
        t.refresh()
        self.assertIn('nicht verstanden', t.new_counter.cget('text'))
        self.assertEqual(len(lst.tree.get_children()), 4)                                        # kaputte Eingabe filtert nichts
        t.new_window.set('seit dem letzten Lauf')
        t.refresh()
        self.assertIn('noch keinen abgeschlossenen Lauf', t.new_counter.cget('text'))
        with Store(self.db) as s:
            run = repo.start_run(s.db, None, 'x', 'manual')
            repo.finish_run(s.db, run, 'ok')
        t.refresh()
        self.assertIn('seit dem letzten Lauf', t.new_counter.cget('text'))

    def test_new_authors_can_be_selected_and_grouped_directly(self):
        t = self.tab
        t.mode.set('commenter')
        t.show_filter.set('Nur neue Autoren')
        t.refresh()
        lst = t.plist['commenter']
        lst._select_all()
        self.app.update()
        with self.ask({'new': 'Neue Gesichter'}):
            msg = t.people_add()
        self.assertIn('1 Kommentarschreiber zur Gruppe „Neue Gesichter“', msg)

    def test_comment_list_marks_new_author(self):
        t = self.tab
        t.refresh()
        t.tree.selection_set('tiktok:a1')
        t._show_details()
        authors = [t.ctree.set(i, 'author') for i in t.ctree.get_children()]
        self.assertIn('Zoë  ★ neu', authors)
        self.assertEqual(sum('neu' in a for a in authors), 1)
        t.new_window.set('irgendwann')                                                           # kaputt: keine Markierung, kein Absturz
        t._show_comments()
        self.assertFalse(any('neu' in t.ctree.set(i, 'author') for i in t.ctree.get_children()))


class SettingsAiTests(UiBase):
    tabs = (('Einstellungen', SettingsTab),)

    def setUp(self):
        super().setUp()
        self.tab = self.built['Einstellungen']

    def test_section_exists_with_defaults_hidden_key_and_privacy_note(self):
        t = self.tab
        t.show_section('ki')
        self.assertIn('KI / Stimmung', [t.nav.get(i).strip() for i in range(t.nav.size())])
        self.assertEqual((t.ai_vars['model'].get(), t.ai_vars['max_per_run'].get(), t.ai_vars['auto'].get()), ('claude-haiku-5-5', '5000', False))
        self.assertEqual(t.ai_key_entry.cget('show'), '•')
        self.assertIn('Anthropic', t.ai_warn_label.cget('text'))
        self.assertIn('Kommentartexte', t.ai_warn_label.cget('text'))
        self.assertIn('KI-Einschätzung', t.ai_warn_label.cget('text'))
        self.assertIn('Es ist noch kein Schlüssel', t.ai_key_state.cget('text'))
        self.assertFalse(t.dirty)
        self.assertFalse((self.root / 'data' / 'ai.json').exists())                              # beim Ansehen wird nichts geschrieben

    def test_edit_save_writes_ai_json_and_never_logs_the_key(self):
        t = self.tab
        t.ai_vars['api_key'].set(KEY)
        t.ai_vars['max_per_run'].set('300')
        t.ai_vars['price_in'].set('3,5')
        t.ai_vars['model'].set('claude-sonnet-5-5')
        t.ai_vars['auto'].set(True)
        self.assertTrue(t.dirty)
        self.assertIn('●', ' '.join(t.nav.get(i) for i in range(t.nav.size())))
        self.assertEqual(str(t.save_btn['state']), 'normal')
        self.assertTrue(t.save())
        cfg = aiconfig.load(self.root)
        self.assertEqual((cfg['api_key'], cfg['max_per_run'], cfg['price_in_per_mtok'], cfg['price_out_per_mtok'], cfg['model'], cfg['auto_after_runs']),
                         (KEY, 300, 3.5, None, 'claude-sonnet-5-5', True))
        self.assertFalse(t.dirty)
        self.assertIn('Ein Schlüssel ist eingetragen', t.ai_key_state.cget('text'))
        blob = json.dumps(self.app.logs) + json.dumps(self.app.statuses)
        self.assertNotIn(KEY, blob)
        self.assertTrue(any('KI-Einstellungen gespeichert' in line for line, _t in self.app.logs))
        # nur der Schalter geändert: der Schlüssel bleibt erhalten
        t.ai_vars['auto'].set(False)
        self.assertTrue(t.save())
        self.assertEqual(aiconfig.load(self.root)['api_key'], KEY)

    def test_invalid_values_show_error_and_write_nothing(self):
        t = self.tab
        t.ai_vars['max_per_run'].set('viele')
        self.assertFalse(t.save())
        self.assertFalse((self.root / 'data' / 'ai.json').exists())
        self.assertEqual(t.current, 'ki')
        self.assertIn('✖', t.ai_error.cget('text'))
        self.assertIn('Kommentare pro Lauf', t.ai_error.cget('text'))
        self.assertIn('⚠', ' '.join(t.nav.get(i) for i in range(t.nav.size())))
        t.ai_vars['max_per_run'].set('10')
        t.ai_vars['price_out'].set('teuer')
        self.assertFalse(t.save())
        self.assertIn('Preis', t.ai_error.cget('text'))
        t.ai_vars['price_out'].set('')
        t.ai_vars['api_key'].set('mit leerzeichen drin')
        self.assertFalse(t.save())
        self.assertNotIn('leerzeichen drin', t.ai_error.cget('text'))
        t.reset()
        self.assertFalse(t.dirty)
        self.assertEqual(t.ai_vars['max_per_run'].get(), '5000')

    def test_show_hide_and_copy_key_buttons(self):
        t = self.tab
        t.ai_vars['api_key'].set(KEY)
        t.ai_key_entry.configure(show='')
        self.assertEqual(t.ai_key_entry.cget('show'), '')
        t._copy_ai_key()
        self.assertEqual(t.clipboard_get(), KEY)
        t.ai_vars['api_key'].set('')
        t._copy_ai_key()
        self.assertEqual(self.showinfo.call_count, 1)

    def test_connection_test_only_on_click_with_form_values(self):
        t = self.tab
        t.show_section('ki')
        self.app.update()
        self.assertEqual(self.fake.requests, [])                                                 # nie von allein
        t.test_ai()
        self.assertIn('Schlüssel eintragen', t.ai_result.cget('text'))
        self.assertEqual(self.fake.requests, [])
        t.ai_vars['api_key'].set(KEY)                                                            # noch nicht gespeichert: trotzdem testbar
        t.test_ai()
        self.assertEqual(str(t.ai_test_btn['state']), 'disabled')
        self.assertTrue(self.app.pump(lambda: len(t.ai_result.cget('text')) and 'Prüfe' not in t.ai_result.cget('text'), 10))
        self.assertIn('✔', t.ai_result.cget('text'))
        self.assertEqual(len(self.fake.requests), 1)
        self.assertEqual(self.fake.requests[0]['body']['max_tokens'], 16)
        self.assertEqual(str(t.ai_test_btn['state']), 'normal')
        self.fake.key = 'andere-0123456789'
        t.test_ai()
        self.assertTrue(self.app.pump(lambda: '✖' in t.ai_result.cget('text'), 10))
        self.assertIn('wird nicht akzeptiert', t.ai_result.cget('text'))
        self.assertNotIn(KEY, t.ai_result.cget('text'))

    def test_environment_variable_is_reported(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-env-0123456789'}):
            self.tab._ai_key_state()
            self.assertIn('ANTHROPIC_API_KEY', self.tab.ai_key_state.cget('text'))


class AutoSwitchTests(UiBase):
    tabs = (('Automatik', AutoTab),)

    def setUp(self):
        super().setUp()
        self.tab = self.built['Automatik']
        self.app.pump(lambda: not self.tab.loading)

    def test_switch_is_off_by_default_and_toggles_ai_json(self):
        t = self.tab
        self.assertFalse(t.ai_auto.get())
        self.assertIn('Aus', t.ai_auto_note.cget('text'))
        self.assertIn('automatisch einstufen', t.ai_auto_cb.cget('text'))
        t.ai_auto.set(True)
        t._ai_auto_toggled()
        self.assertTrue(aiconfig.load(self.root)['auto_after_runs'])
        self.assertIn('API-Schlüssel', t.ai_auto_note.cget('text'))                              # an, aber Schlüssel fehlt: freundlicher Hinweis
        self.save_key(auto_after_runs=True)
        t.refresh()
        self.app.pump(lambda: not t.loading)
        self.assertIn('höchstens 5000', t.ai_auto_note.cget('text'))
        t.ai_auto.set(False)
        t._ai_auto_toggled()
        self.assertFalse(aiconfig.load(self.root)['auto_after_runs'])
        self.assertEqual(aiconfig.load(self.root)['api_key'], KEY)                               # der Schlüssel bleibt

    def test_runs_column_and_feed_marker_for_new_authors(self):
        with Store(self.db) as s:
            t1 = repo.add_target(s.db, 'video', 'tiktok', 'https://www.tiktok.com/@u/video/a1')
            run = repo.start_run(s.db, t1['id'], 'x', 'daemon')
            repo.finish_run(s.db, run, 'ok', videos_seen=1, comments_new=3, authors_new=2)
            s.db.execute("UPDATE comments SET first_seen_at=? WHERE author='Zoë'", (repo.iso(),))
            s.db.commit()
        t = self.tab
        t.refresh()
        self.assertTrue(self.app.pump(lambda: not t.loading))
        self.assertIn('Neue Autoren', t.runs_tree.heading('authors')['text'])
        self.assertEqual(t.runs_tree.set(t.runs_tree.get_children()[0], 'authors'), '2')
        authors = [t.feed_tree.set(i, 'author') for i in t.feed_tree.get_children()]
        self.assertEqual(authors, ['Zoë  ★ neu'])
        with patch('tkinter.messagebox.showinfo') as info:
            t.runs_tree.selection_set(t.runs_tree.get_children()[0])
            t.show_run_details()
        self.assertIn('neue Autoren: 2', info.call_args[0][1])


if __name__ == '__main__':
    unittest.main()
