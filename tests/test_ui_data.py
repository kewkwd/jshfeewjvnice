"""Tests für Gruppen, Videoliste, Kommentare, Verlauf, Export (Logik ohne Tk) und den Tab „Gespeicherte Daten“."""
import csv
from contextlib import closing
import datetime
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from socialscraper import guicore as core, repo
from socialscraper.models import Comment, Video
from socialscraper.store import Store
from socialscraper.ui import logic


def utc(**delta):
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(**delta)


class DataCase(unittest.TestCase):
    """Wegwerf-Ordner mit Datenbank; SS_DB_PATH zeigt darauf, ROOT ebenso."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.db = self.root / 'data' / 'scrape.db'
        for p in (patch.dict(os.environ, {'SS_DB_PATH': str(self.db)}), patch.object(core, 'ROOT', self.root)):
            p.start(); self.addCleanup(p.stop)
        with Store(self.db) as s:
            repo.ensure_default_accounts(s.db)
            self.videos = [
                Video('youtube', 'a1', 'https://youtu.be/a1', author='Anna', caption='Hallo 100% _Welt_', view_count=9, like_count=1,
                      comment_count=5, created_at='2026-01-03T00:00:00+00:00'),
                Video('tiktok', 'b2', 'https://www.tiktok.com/@u/video/b2', author='Bert', caption='Zweites', view_count=100, like_count=50,
                      comment_count=None, created_at='2026-02-03T00:00:00+00:00'),
                Video('instagram', 'c3', 'https://www.instagram.com/p/c3/', author='Cleo', caption='Drittes', view_count=1000, like_count=7,
                      comment_count=30, created_at=None),
            ]
            for v in self.videos:
                s.save_result(v, [], None)
            s.save_result(self.videos[0], [Comment('youtube', 'a1', 'k1', 'alt', author='X', like_count=1),
                                            Comment('youtube', 'a1', 'k2', 'neu', author='Y', like_count=9)], None)
            s.db.execute("UPDATE comments SET first_seen_at='2026-09-01T10:00:00Z' WHERE comment_id='k1'")
            s.db.execute("UPDATE comments SET first_seen_at=? WHERE comment_id='k2'", (repo.iso(),))
            s.db.commit()
        self.uids = [v.uid for v in self.videos]

    def writer(self):
        return logic.writer(self.db)


class QueryTests(DataCase):
    def test_sort_is_numeric_and_empty_values_always_last(self):
        views = lambda **kw: [r['view_count'] for r in logic.query_videos(self.db, **kw)]
        self.assertEqual(views(sort='views', descending=True), [1000, 100, 9])   # nicht „9“ vor „100“
        self.assertEqual(views(sort='views', descending=False), [9, 100, 1000])
        self.assertEqual([r['comment_count'] for r in logic.query_videos(self.db, sort='comments', descending=True)], [30, 5, None])
        self.assertEqual([r['comment_count'] for r in logic.query_videos(self.db, sort='comments', descending=False)], [5, 30, None])
        self.assertEqual([r['video_id'] for r in logic.query_videos(self.db, sort='date', descending=True)], ['b2', 'a1', 'c3'])
        self.assertEqual([r['author'] for r in logic.query_videos(self.db, sort='author', descending=False)], ['Anna', 'Bert', 'Cleo'])
        with self.assertRaises(ValueError): logic.query_videos(self.db, sort='x; DROP TABLE videos')

    def test_filters_search_and_escape(self):
        q = lambda **kw: [r['video_id'] for r in logic.query_videos(self.db, **kw)]
        self.assertEqual(q(platform='tiktok'), ['b2'])
        self.assertEqual(q(search='100%'), ['a1']); self.assertEqual(q(search='%'), ['a1']); self.assertEqual(q(search="x' OR 1=1 --"), [])
        self.assertEqual(sorted(q()), ['a1', 'b2', 'c3'])

    def test_search_ignores_case_and_umlauts_in_the_window_list(self):
        with self.writer() as db:
            db.execute("UPDATE videos SET author='müller', caption='Käse ÖL' WHERE video_id='b2'"); db.commit()
        q = lambda s: [r['video_id'] for r in logic.query_videos(self.db, search=s)]
        self.assertEqual(q('MÜLLER'), ['b2']); self.assertEqual(q('käse'), ['b2']); self.assertEqual(q('KÄSE öl'), ['b2'])
        self.assertEqual(q('100%'), ['a1']); self.assertEqual(q('%'), ['a1']); self.assertEqual(q("x' OR 1=1 --"), [])

    def test_group_views_and_group_names_column(self):
        with self.writer() as db:
            g = repo.create_group(db, 'Eins'); h = repo.create_group(db, 'Zwei')
            repo.add_videos_to_group(db, g['id'], [self.uids[0], self.uids[1]]); repo.add_videos_to_group(db, h['id'], [self.uids[0]])
        by_view = lambda view: sorted(r['video_id'] for r in logic.query_videos(self.db, group=view))
        self.assertEqual(by_view(g['id']), ['a1', 'b2']); self.assertEqual(by_view(h['id']), ['a1'])
        self.assertEqual(by_view(logic.UNGROUPED), ['c3']); self.assertEqual(by_view(logic.ALL), ['a1', 'b2', 'c3'])
        row = next(r for r in logic.query_videos(self.db) if r['video_id'] == 'a1')
        self.assertEqual(sorted(row['group_names'].split(', ')), ['Eins', 'Zwei'])
        ov = logic.group_overview(self.db)
        self.assertEqual((ov['total'], ov['ungrouped']), (3, 1))
        self.assertEqual({g['name']: g['video_count'] for g in ov['groups']}, {'Eins': 2, 'Zwei': 1})

    def test_missing_database_is_empty_not_an_error(self):
        nope = self.root / 'nichts.db'
        self.assertEqual(logic.query_videos(nope), []); self.assertEqual(logic.count_videos(nope), (0, 0))
        self.assertEqual(logic.group_overview(nope)['total'], 0); self.assertEqual(logic.history(nope, 'x'), [])
        self.assertEqual(logic.query_comments(nope, 'youtube', 'a1'), []); self.assertEqual(logic.groups_of(nope, ['x']), [])

    def test_comments_new_since(self):
        allc = logic.query_comments(self.db, 'youtube', 'a1')
        self.assertEqual([c['author'] for c in allc], ['Y', 'X'])   # meiste Likes zuerst
        since = logic.parse_since('Neu in der letzten Stunde')
        self.assertEqual([c['author'] for c in logic.query_comments(self.db, 'youtube', 'a1', since)], ['Y'])
        self.assertEqual(logic.query_comments(self.db, 'youtube', 'a1', repo.iso(utc(days=1))), [])

    def test_tracked_flag_and_history(self):
        with self.writer() as db:
            repo.add_target(db, 'video', 'youtube', 'https://youtu.be/a1')
            db.execute('INSERT INTO video_snapshots VALUES (?,?,?,?,?,?)', (self.uids[0], '2026-10-01T00:00:00+00:00', 5, 1, 2, 0))
            db.execute('INSERT INTO video_snapshots VALUES (?,?,?,?,?,?)', (self.uids[0], '2026-10-02T00:00:00+00:00', 9, 2, 3, 0))
            db.commit()
        tracked = {r['video_id']: r['tracked'] for r in logic.query_videos(self.db)}
        self.assertEqual(tracked, {'a1': 1, 'b2': 0, 'c3': 0})
        self.assertEqual([h['view_count'] for h in logic.history(self.db, self.uids[0])], [5, 9])


class ParseSinceTests(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 11, 12, 0, tzinfo=datetime.timezone.utc)

    def test_presets_and_empty(self):
        self.assertIsNone(logic.parse_since('Alle Kommentare')); self.assertIsNone(logic.parse_since(''))
        self.assertEqual(logic.parse_since('Neu in den letzten 24 Stunden', self.NOW), '2026-10-10T12:00:00Z')
        self.assertEqual(logic.parse_since('neu in den letzten 7 tagen', self.NOW), '2026-10-04T12:00:00Z')

    def test_typed_dates(self):
        for text in ('31.12.2026', '31.12.2026 18:30', '2026-12-31', '2026-12-31 18:30'):
            self.assertRegex(logic.parse_since(text, self.NOW), r'^2026-1[12]-\d\dT\d\d:\d\d:00Z$')

    def test_garbage_gives_friendly_error(self):
        for bad in ('gestern', '31/12/26', '99.99.2026', 'DROP'):
            with self.assertRaises(ValueError) as ctx: logic.parse_since(bad, self.NOW)
            self.assertIn('Datum', str(ctx.exception))


class LocalStampTests(unittest.TestCase):
    def test_converts_utc_to_local_and_tolerates_junk(self):
        expect = datetime.datetime(2026, 10, 11, 4, 15, tzinfo=datetime.timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')
        self.assertEqual(logic.local_stamp('2026-10-11T04:15:33Z'), expect)
        self.assertEqual(logic.local_stamp('2026-10-11T04:15:33+00:00'), expect)
        self.assertEqual(logic.local_stamp('2026-10-11 04:15:33'), expect)       # ältere Zeilen ohne T und Z
        self.assertEqual(logic.local_stamp('wer weiß'), 'wer weiß'); self.assertEqual(logic.local_stamp(None), '')


class ChartTests(unittest.TestCase):
    def test_empty_and_none_values_are_skipped(self):
        self.assertEqual(logic.chart_layout([], 300, 100), [])
        self.assertEqual(logic.chart_layout([('2026-10-01T00:00:00Z', None)], 300, 100), [])

    def test_single_point_in_the_middle(self):
        (x, y, _t, v), = logic.chart_layout([('2026-10-01T00:00:00Z', 5)], 300, 100)
        self.assertEqual(v, 5); self.assertAlmostEqual(x, 46 + (300 - 46 - 14) / 2)

    def test_rising_values_go_up_and_time_is_proportional(self):
        pts = logic.chart_layout([('2026-10-01T00:00:00Z', 10), ('2026-10-02T00:00:00Z', 20), ('2026-10-05T00:00:00Z', 40)], 300, 100)
        self.assertGreater(pts[0][1], pts[1][1]); self.assertGreater(pts[1][1], pts[2][1])   # größerer Wert = weiter oben (kleineres y)
        self.assertAlmostEqual((pts[1][0] - pts[0][0]) / (pts[2][0] - pts[0][0]), 0.25)
        self.assertEqual(pts[0][0], 46); self.assertEqual(pts[2][0], 300 - 14)

    def test_flat_line_and_unreadable_stamps_use_order(self):
        pts = logic.chart_layout([('wer weiß', 7), ('wann auch', 7), ('immer', 7)], 300, 100)
        self.assertEqual({round(p[1], 3) for p in pts}, {14 + (100 - 14 - 26) / 2}); self.assertLess(pts[0][0], pts[1][0])


class GroupLogicTests(DataCase):
    def test_duplicate_names_are_refused_case_insensitively(self):
        logic.create_group(self.db, 'Konkurrenz')
        for dup in ('Konkurrenz', ' konkurrenz ', 'KONKURRENZ'):
            with self.assertRaises(ValueError) as ctx: logic.create_group(self.db, dup)
            self.assertIn('gibt es schon', str(ctx.exception))
        for bad in ('', '   ', 'x' * 61):
            with self.assertRaises(ValueError): logic.create_group(self.db, bad)

    def test_rename_and_color(self):
        a = logic.create_group(self.db, 'A'); b = logic.create_group(self.db, 'B')
        logic.update_group(self.db, a['id'], 'A', '#dc2626')                  # gleicher Name bei sich selbst ist erlaubt
        logic.update_group(self.db, a['id'], 'Alpha', '#dc2626')
        with self.assertRaises(ValueError): logic.update_group(self.db, b['id'], 'alpha', '#2563eb')
        with self.assertRaises(ValueError): logic.update_group(self.db, b['id'], 'B', 'rot')
        self.assertEqual({g['name']: g['color'] for g in logic.group_overview(self.db)['groups']}, {'Alpha': '#dc2626', 'B': b['color']})

    def test_add_remove_and_delete_never_deletes_videos(self):
        g = logic.create_group(self.db, 'G')
        self.assertEqual(logic.add_to_group(self.db, g['id'], self.uids[:2]), 2)
        self.assertEqual(logic.add_to_group(self.db, g['id'], self.uids[:3]), 1)       # nur eines ist neu
        self.assertEqual([x['name'] for x in logic.groups_of(self.db, self.uids)], ['G'])
        self.assertEqual(logic.remove_from_group(self.db, g['id'], [self.uids[0]]), 1)
        logic.delete_group(self.db, g['id'])
        self.assertEqual(logic.count_videos(self.db)[0], 3)
        self.assertEqual(logic.group_overview(self.db)['groups'], [])
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM group_videos').fetchone()[0], 0)
        with self.assertRaises(ValueError): logic.add_to_group(self.db, 999, self.uids)
        with self.assertRaises(ValueError): logic.add_to_group(self.db, g['id'], [])


class TrackAndFindTests(DataCase):
    def test_track_videos_added_existing_and_skipped(self):
        with self.writer() as db:
            db.execute("UPDATE videos SET url='' WHERE video_id='c3'"); db.commit()
        out = logic.track_videos(self.db, self.uids, 60)
        self.assertEqual((out['added'], out['existing'], len(out['skipped'])), (2, 0, 1))
        out2 = logic.track_videos(self.db, self.uids, 60)
        self.assertEqual((out2['added'], out2['existing']), (0, 2))                 # doppelt: freundlich gezählt, kein Fehler
        with self.writer() as db:
            targets = repo.list_targets(db)
            tiktok = next(t for t in targets if t['platform'] == 'tiktok')
            self.assertEqual((tiktok['kind'], tiktok['interval_min'], tiktok['account_id']), ('video', 60, repo.default_account(db, 'tiktok')['id']))
            self.assertIsNone(next(t for t in targets if t['platform'] == 'youtube')['account_id'])
        with self.assertRaises(ValueError): logic.track_videos(self.db, [], 60)
        out3 = logic.track_videos(self.db, ['youtube:weg'], 60)
        self.assertEqual((out3['added'], len(out3['skipped'])), (0, 1))

    def test_track_rejects_bad_interval_without_crashing(self):
        out = logic.track_videos(self.db, self.uids[:1], 1)
        self.assertEqual(out['added'], 0); self.assertIn('Intervall', out['skipped'][0])

    def test_find_fetched_uids_by_time_platform_and_link(self):
        old = '2020-01-01 00:00:00'
        with self.writer() as db:
            db.execute('UPDATE videos SET fetched_at=?', (old,))
            db.execute("UPDATE videos SET fetched_at=CURRENT_TIMESTAMP WHERE video_id IN ('a1','b2')"); db.commit()
        since = utc(minutes=-1)
        self.assertEqual(logic.find_fetched_uids(self.db, since, ['youtube', 'tiktok']), ['youtube:a1', 'tiktok:b2'])
        self.assertEqual(logic.find_fetched_uids(self.db, since, ['instagram']), [])
        self.assertEqual(logic.find_fetched_uids(self.db, since, ['youtube'], {'youtube': ['https://youtu.be/a1']}), ['youtube:a1'])
        # Kurzlink ohne erkennbare Kennung: alle Videos der Plattform aus dem Zeitfenster
        self.assertEqual(logic.find_fetched_uids(self.db, since, ['tiktok'], {'tiktok': ['https://vm.tiktok.com/xyz/']}), ['tiktok:b2'])
        # ein anderes (nicht angefragtes) Video derselben Plattform wird mit Link-Zuordnung nicht eingesammelt
        with self.writer() as db:
            db.execute("UPDATE videos SET fetched_at=CURRENT_TIMESTAMP WHERE video_id='c3'"); db.commit()
        self.assertEqual(sorted(logic.find_fetched_uids(self.db, since, ['instagram', 'youtube'],
                                                        {'youtube': ['https://youtu.be/a1'], 'instagram': ['https://www.instagram.com/p/c3/']})),
                         ['instagram:c3', 'youtube:a1'])
        self.assertEqual(logic.find_fetched_uids(self.db, utc(days=1), ['youtube']), [])


class ExportTests(DataCase):
    def rows(self, folder, name, fmt='csv', delimiter=','):
        path = pathlib.Path(folder) / f'{name}.{fmt}'
        if fmt == 'csv':
            with path.open(encoding='utf-8-sig', newline='') as f: return list(csv.DictReader(f, delimiter=delimiter))
        return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]

    def test_group_and_selection_filters_with_formula_guard(self):
        with self.writer() as db:
            g = repo.create_group(db, 'G'); repo.add_videos_to_group(db, g['id'], [self.uids[0]])
            db.execute("UPDATE comments SET text='=1+1' WHERE comment_id='k1'"); db.commit()
        out = self.root / 'ex'
        core.export_data(self.db, out / 'all', 'csv')
        self.assertEqual(len(self.rows(out / 'all', 'videos')), 3)
        core.export_data(self.db, out / 'grp', 'csv', group_id=g['id'])
        self.assertEqual([r['Link'] for r in self.rows(out / 'grp', 'videos')], ['https://youtu.be/a1'])
        texts = {r['Text'] for r in self.rows(out / 'grp', 'kommentare')}
        self.assertIn("'=1+1", texts)                                      # Formel-Schutz bleibt
        core.export_data(self.db, out / 'sel', 'jsonl', uids=[self.uids[1], self.uids[2]])
        self.assertEqual({r['video_id'] for r in self.rows(out / 'sel', 'videos', 'jsonl')}, {'b2', 'c3'})
        self.assertEqual(self.rows(out / 'sel', 'comments', 'jsonl'), [])   # deren Kommentare gibt es nicht
        core.export_data(self.db, out / 'both', 'csv', group_id=g['id'], uids=[self.uids[1]])
        self.assertEqual(self.rows(out / 'both', 'videos'), [])             # Schnittmenge ist leer
        with self.assertRaises(ValueError): core.export_data(self.db, out, 'xml', group_id=g['id'])

    def _rich(self):
        """Zwei Gruppen, Verlaufspunkte für zwei Videos, eine Antwort."""
        with self.writer() as db:
            g = repo.create_group(db, 'Eins'); h = repo.create_group(db, 'Zwei')
            repo.add_videos_to_group(db, g['id'], [self.uids[0], self.uids[1]]); repo.add_videos_to_group(db, h['id'], [self.uids[0]])
            for uid, when, views in ((self.uids[0], '2026-10-01T10:00:00Z', 5), (self.uids[0], '2026-10-02T10:00:00Z', 9),
                                     (self.uids[1], '2026-10-01T10:00:00Z', 100)):
                db.execute('INSERT INTO video_snapshots VALUES (?,?,?,?,?,?)', (uid, when, views, 1, 2, 3))
            db.execute("UPDATE comments SET parent_id='k1' WHERE comment_id='k2'"); db.commit()
        return g, h

    def test_excel_csv_uses_semicolon_bom_and_german_columns(self):
        self._rich()
        out = self.root / 'xl'
        files = core.export_data(self.db, out, 'csv-excel')
        self.assertEqual([f.name for f in files], ['videos.csv', 'kommentare.csv', 'gruppen.csv', 'gruppen_zuordnung.csv', 'verlauf.csv', 'personen.csv', 'gruppen_personen.csv'])
        raw = (out / 'videos.csv').read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))                       # UTF-8 mit BOM
        first = raw.decode('utf-8-sig').splitlines()[0]
        self.assertFalse(first.startswith('sep='))                           # keine sep-Zeile
        self.assertEqual(first, 'Plattform;Autor;Beschreibung;Link;Aufrufe;Likes;Kommentare;Länge (m:ss);Länge (Sekunden);Veröffentlicht;Zuerst gesehen;Zuletzt abgerufen;Gruppen')
        videos = {r['Autor']: r for r in self.rows(out, 'videos', delimiter=';')}
        self.assertEqual(videos['Anna']['Plattform'], 'YouTube')
        self.assertEqual(videos['Anna']['Gruppen'], 'Eins, Zwei')
        self.assertEqual(videos['Anna']['Aufrufe'], '9')
        self.assertEqual(videos['Cleo']['Gruppen'], '')
        self.assertNotIn('uid', videos['Anna']); self.assertNotIn('raw', videos['Anna'])
        comments = {r['Autor']: r for r in self.rows(out, 'kommentare', delimiter=';')}
        self.assertEqual(comments['Y']['Antwort auf'], 'X'); self.assertEqual(comments['X']['Antwort auf'], '')
        self.assertEqual(comments['Y']['Video-Link'], 'https://youtu.be/a1')
        self.assertEqual(list(comments['Y']), ['Plattform', 'Video-Link', 'Autor', 'Text', 'Likes', 'Geschrieben', 'Zuerst gesehen', 'Antwort auf',
                                          'Stimmung (KI-Einschätzung)', 'Stimmungswert (KI-Einschätzung)'])
        groups = {r['Gruppe']: r for r in self.rows(out, 'gruppen', delimiter=';')}
        self.assertEqual((groups['Eins']['Videos'], groups['Zwei']['Videos']), ('2', '1'))
        self.assertEqual(len(self.rows(out, 'gruppen_zuordnung', delimiter=';')), 3)
        history = self.rows(out, 'verlauf', delimiter=';')
        self.assertEqual(len(history), 3)
        self.assertEqual(list(history[0]), ['Zeit', 'Plattform', 'Video-Link', 'Aufrufe', 'Likes', 'Kommentare', 'Shares'])
        self.assertEqual(sorted(r['Aufrufe'] for r in history), ['100', '5', '9'])

    def test_comma_csv_and_jsonl_contain_groups_and_history(self):
        self._rich()
        out = self.root / 'k'
        core.export_data(self.db, out, 'csv')
        self.assertEqual(len(self.rows(out, 'verlauf')), 3)
        j = self.root / 'j'
        files = core.export_data(self.db, j, 'jsonl')
        self.assertEqual([f.name for f in files], ['videos.jsonl', 'comments.jsonl', 'groups.jsonl', 'group_videos.jsonl', 'group_people.jsonl', 'comment_sentiment.jsonl', 'video_snapshots.jsonl'])
        self.assertEqual(len(self.rows(j, 'groups', 'jsonl')), 2)
        self.assertEqual(len(self.rows(j, 'group_videos', 'jsonl')), 3)
        self.assertEqual(len(self.rows(j, 'video_snapshots', 'jsonl')), 3)
        self.assertIn('uid', self.rows(j, 'videos', 'jsonl')[0])             # Rohspalten nur hier

    def test_group_filter_applies_to_every_file(self):
        g, _h = self._rich()
        out = self.root / 'g_csv'
        core.export_data(self.db, out, 'csv-excel', group_id=g['id'])
        self.assertEqual([r['Gruppe'] for r in self.rows(out, 'gruppen', delimiter=';')], ['Eins'])
        self.assertEqual({r['Gruppe'] for r in self.rows(out, 'gruppen_zuordnung', delimiter=';')}, {'Eins'})
        self.assertEqual(len(self.rows(out, 'verlauf', delimiter=';')), 3)
        vids = {r['Autor']: r['Gruppen'] for r in self.rows(out, 'videos', delimiter=';')}
        self.assertEqual(vids, {'Anna': 'Eins, Zwei', 'Bert': 'Eins'})       # Videos nennen weiter alle ihre Gruppen
        out = self.root / 'g_json'
        core.export_data(self.db, out, 'jsonl', group_id=g['id'])
        self.assertEqual({r['video_uid'] for r in self.rows(out, 'video_snapshots', 'jsonl')}, {self.uids[0], self.uids[1]})
        self.assertEqual([r['name'] for r in self.rows(out, 'groups', 'jsonl')], ['Eins'])
        self.assertEqual(len(self.rows(out, 'group_videos', 'jsonl')), 2)
        out = self.root / 'g_one'
        core.export_data(self.db, out, 'csv-excel', uids=[self.uids[1]])
        self.assertEqual(len(self.rows(out, 'verlauf', delimiter=';')), 1)

    def test_csv_cells_have_no_line_breaks_and_formulas_are_guarded(self):
        with self.writer() as db:
            db.execute("UPDATE videos SET caption='=SUMME(A1)' WHERE video_id='a1'")
            db.execute("UPDATE videos SET caption='zwei\nZeilen' WHERE video_id='b2'"); db.commit()
        out = self.root / 'f'
        core.export_data(self.db, out, 'csv-excel')
        caps = {r['Autor']: r['Beschreibung'] for r in self.rows(out, 'videos', delimiter=';')}
        self.assertEqual(caps['Anna'], "'=SUMME(A1)"); self.assertEqual(caps['Bert'], 'zwei Zeilen')


class DbPathTests(unittest.TestCase):
    def test_env_relative_and_default(self):
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            with patch.dict(os.environ, {'SS_DB_PATH': str(root / 'x' / 'a.db')}):
                self.assertEqual(core.db_path(root), root / 'x' / 'a.db')
            with patch.dict(os.environ, {'SS_DB_PATH': 'rel/b.db'}):
                self.assertEqual(core.db_path(root), root / 'rel' / 'b.db')
            env = {k: v for k, v in os.environ.items() if k != 'SS_DB_PATH'}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(core.db_path(root), root / 'data' / 'scrape.db')
                (root / 'settings.toml').write_text('db_path = "mein/ort.db"\n', encoding='utf-8')
                self.assertEqual(core.db_path(root), root / 'mein' / 'ort.db')
                (root / 'settings.toml').write_text('kaputt = = =', encoding='utf-8')
                self.assertEqual(core.db_path(root), root / 'data' / 'scrape.db')   # kaputte Datei verhindert das Lesen nicht


class FriendlyErrorTests(unittest.TestCase):
    def test_messages_are_german_and_stack_free(self):
        self.assertIn('gesperrt', logic.friendly_error(sqlite3.OperationalError('database is locked')))
        self.assertEqual(logic.friendly_error(ValueError('Eigener Text')), 'Eigener Text')
        self.assertIn('gesperrt', logic.friendly_error(PermissionError(13, 'denied')))
        self.assertIn('Dateifehler', logic.friendly_error(FileNotFoundError(2, 'nicht da')))
        self.assertIn('Unerwarteter Fehler', logic.friendly_error(KeyError('x')))


# ---------- Fenster ----------

@unittest.skipUnless(os.name == 'nt', 'Tk-Fenster nur unter Windows')
class DataTabWindowTests(DataCase):
    def setUp(self):
        super().setUp()
        import tkinter
        from ui_fake import FakeApp
        from socialscraper.ui.tab_data import DataTab
        try:
            self.app = FakeApp(self.root)
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self.app.destroy)
        self.tab = self.app.add('Gespeicherte Daten', DataTab)
        self.app.update()

    def ask(self, values):
        """Ersatz für den Dialog: ruft wie der echte die Prüfung auf und gibt die Werte zurück (None, wenn sie Einspruch erhebt)."""
        def fake(parent, title, fields, intro='', ok_text='OK', validator=None):
            problem = validator(values) if validator else None
            self.last_problem = problem
            return None if problem else values
        return patch('socialscraper.ui.tab_data.ask_form', side_effect=fake)

    def test_lists_groups_and_videos_and_empty_state(self):
        t = self.tab
        t.refresh(); self.assertEqual(len(t.tree.get_children()), 3)
        self.assertEqual([t.gtree.item(i, 'text') for i in t.gtree.get_children()], ['Alle Videos (3)', 'Ohne Gruppe (3)'])
        self.assertEqual(t.empty.place_info(), {})                       # keine Hinweisfläche, wenn Zeilen da sind
        t.search.set('gibt es nicht'); t.refresh()
        self.assertEqual(len(t.tree.get_children()), 0)
        self.assertIn('Keine Treffer', t.empty_text.cget('text')); self.assertTrue(t.empty.place_info())

    def test_missing_database_file_refreshes_without_error(self):
        """Vor dem ersten Abruf gibt es keine Datenbankdatei: Neuladen samt Längenvergleich darf nicht abstürzen."""
        with patch.dict(os.environ, {'SS_DB_PATH': str(self.root / 'data' / 'noch-nicht-da.db')}):
            self.tab.refresh()
        self.assertEqual(len(self.tab.tree.get_children()), 0)
        self.assertEqual(len(self.tab.ltree.get_children()), 0)
        self.assertIn('Noch keine Videos', self.tab.length_note.cget('text'))

    def test_empty_database_shows_next_step(self):
        with self.writer() as db:
            db.execute('DELETE FROM videos'); db.commit()
        self.tab.refresh()
        self.assertIn('Abrufen', self.tab.empty_text.cget('text')); self.assertTrue(self.tab.empty_btn.winfo_manager())

    def test_sort_header_click_sorts_numerically_and_toggles(self):
        t = self.tab; t.refresh()
        t.headers.click('views')
        self.assertEqual([t.tree.set(i, 'views') for i in t.tree.get_children()], ['1.000', '100', '9'])
        t.headers.click('views')
        self.assertEqual([t.tree.set(i, 'views') for i in t.tree.get_children()], ['9', '100', '1.000'])
        self.assertIn('▲', t.tree.heading('views')['text']); self.assertNotIn('▼', t.tree.heading('likes')['text'])

    def test_create_group_refuses_duplicates_inline_and_lists_it(self):
        t = self.tab
        with self.ask({'name': 'Ideen', 'color': '#16a34a'}):
            t.group_new()
        self.assertIn('Ideen', [t.gtree.item(i, 'text') for i in t.gtree.get_children()][-1])
        with self.ask({'name': 'ideen', 'color': '#16a34a'}):
            t.group_new()
        self.assertIn('gibt es schon', self.last_problem)
        self.assertEqual(len(logic.group_overview(self.db)['groups']), 1)

    def test_add_to_group_new_group_remove_and_delete_group(self):
        t = self.tab; t.refresh()
        t.tree.selection_set(t.tree.get_children()[:2]); self.app.update()
        with self.ask({'new': 'Neu', 'group': ''}), patch('tkinter.messagebox.showinfo'):
            t.add_to_group()
        groups = logic.group_overview(self.db)['groups']
        self.assertEqual([(g['name'], g['video_count']) for g in groups], [('Neu', 2)])
        with self.ask({'new': '', 'group': ''}):                          # nichts gewählt: freundlicher Hinweis statt Fehler
            t.tree.selection_set(t.tree.get_children()[:1]); t.add_to_group()
        self.assertIn('Gruppe wählen', self.last_problem)
        t.show_group(groups[0]['id']); self.app.update()
        self.assertEqual(len(t.tree.get_children()), 2)
        t.tree.selection_set(t.tree.get_children()[:1]); self.app.update()
        with patch('tkinter.messagebox.askyesno', return_value=True):
            t.remove_from_group()
        self.assertEqual(len(t.tree.get_children()), 1)
        with patch('tkinter.messagebox.askyesno', return_value=False) as ask:
            t.group_delete(); self.assertIn('bleiben gespeichert', ask.call_args[0][1])
        self.assertEqual(len(logic.group_overview(self.db)['groups']), 1)   # „Nein“ löscht nichts
        with patch('tkinter.messagebox.askyesno', return_value=True):
            t.group_delete()
        self.assertEqual(logic.group_overview(self.db)['groups'], [])
        self.assertEqual(logic.count_videos(self.db)[0], 3); self.assertEqual(t.view, logic.ALL)

    def test_track_selected_videos_and_report_duplicates(self):
        t = self.tab; t.refresh()
        t.tree.selection_set(t.tree.get_children()); self.app.update()
        with self.ask({'interval': 60}):
            t.track()
        self.assertIn('3 neu verfolgt', self.app.statuses[-1][0])
        t.refresh(); self.assertEqual([t.tree.set(i, 'tracked') for i in t.tree.get_children()], ['✓'] * 3)
        t.tree.selection_set(t.tree.get_children()); self.app.update()
        with self.ask({'interval': 60}):
            t.track()
        self.assertIn('3 wurde(n) schon verfolgt', self.app.statuses[-1][0])

    def test_actions_without_selection_say_what_to_do(self):
        t = self.tab; t.refresh(); t.tree.selection_set(())
        with patch('tkinter.messagebox.showinfo') as info:
            t.add_to_group(); t.track(); t.open_selected()
        self.assertEqual(info.call_count, 3)
        self.assertEqual(str(t.vbtn['add']['state']), 'disabled')

    def test_unreadable_counts_say_so_instead_of_crashing(self):
        t = self.tab
        with patch.object(logic, 'count_videos', return_value=(None, None)):
            t.refresh()
        self.assertIn('Datenbank gerade nicht lesbar', t.data_info.cget('text'))

    def test_open_selected_refuses_a_backslash_host_link(self):
        t = self.tab; t.refresh()
        uid = self.uids[0]
        t.tree.selection_set(uid); self.app.update()
        t.video_rows[uid] = dict(t.video_rows[uid], url='https://evil.com\\.tiktok.com/x')
        with patch('webbrowser.open') as opened, patch('tkinter.messagebox.showinfo') as info:
            t.open_selected()
        opened.assert_not_called(); self.assertEqual(info.call_count, 1)

    def test_comment_filter_and_bad_date_message(self):
        t = self.tab; t.refresh()
        t.tree.selection_set(f'{self.uids[0]}'); self.app.update()
        t._show_comments(); self.assertEqual(len(t.ctree.get_children()), 2)
        t.since.set('Neu in der letzten Stunde'); t._show_comments(); self.assertEqual(len(t.ctree.get_children()), 1)
        t.since.set('Neu in der letzten Stunde'); self.assertIn('neu', t.comment_info.cget('text'))
        t.since.set('irgendwann'); t._show_comments()
        self.assertIn('Datum nicht verstanden', t.comment_info.cget('text')); self.assertEqual(len(t.ctree.get_children()), 0)

    def test_history_chart_states(self):
        t = self.tab; t.refresh()
        t.tree.selection_set(self.uids[0]); self.app.update(); t._show_history()
        self.assertIn('Noch kein Verlauf', t.chart._data[2]); self.assertEqual(t.chart._data[0], [])
        with self.writer() as db:
            for d, v in ((2, 5), (1, 8), (0, 13)):
                db.execute('INSERT INTO video_snapshots VALUES (?,?,?,?,?,?)', (self.uids[0], repo.iso(utc(days=-d)), v, v, v, 0))
            db.commit()
        t._show_history(); self.assertEqual([v for _t, v in t.chart._data[0]], [5, 8, 13])
        t.metric.set('likes'); t._show_history(); self.assertEqual(t.chart._data[1], 'Likes')
        t.chart.update(); self.assertTrue(t.chart.find_all())            # zeichnet ohne Fehler

    def test_export_selection_runs_in_thread_and_writes_files(self):
        t = self.tab; t.refresh()
        t.tree.selection_set(self.uids[1]); self.app.update()
        out = self.root / 'ziel'; out.mkdir()
        with self.ask({'fmt': 'csv', 'scope': 'selection'}), patch('tkinter.filedialog.askdirectory', return_value=str(out)):
            t.export()
        self.app.pump(lambda: self.app.opened)
        folder = self.app.opened[-1][0]
        self.assertTrue(folder.name.startswith('socialscraper-auswahl-'))
        with (folder / 'videos.csv').open(encoding='utf-8-sig', newline='') as f:
            self.assertEqual([r['Link'] for r in csv.DictReader(f)], ['https://www.tiktok.com/@u/video/b2'])

    def test_unreadable_database_is_reported_in_the_window(self):
        self.db.write_bytes(b'das ist keine datenbank' * 50)
        self.tab.refresh()
        self.assertIn('nicht lesbar', self.tab.data_info.cget('text'))
        self.assertTrue(any(tag == 'ERROR' for _l, tag in self.app.logs))


class AfterCleanupTests(unittest.TestCase):
    """Nachlaufende after-Aufrufe enden mit ihrem Fenster (kein Tcl-Fehler „invalid command name …poll“)."""

    def setUp(self):
        import tkinter
        try:
            self.root = tkinter.Tk()
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *a: self.errors.append(a)

    def pending(self):
        return list(self.root.tk.splitlist(self.root.tk.call('after', 'info')))

    def test_tracked_after_is_cancelled_when_the_widget_is_destroyed(self):
        import tkinter
        from socialscraper.ui.common import track_after
        frame = tkinter.Frame(self.root); ran = []
        track_after(frame, 50, lambda: ran.append(1)); track_after(frame, 60, lambda: ran.append(2))
        self.assertEqual(len(self.pending()), 2)
        frame.destroy()
        self.assertEqual(self.pending(), [])
        end = time.time() + 0.3
        while time.time() < end: self.root.update(); time.sleep(0.01)
        self.assertEqual(ran, []); self.assertEqual(self.errors, [])
        self.root.destroy()

    def test_tracked_after_runs_normally_and_forgets_itself(self):
        import tkinter
        from socialscraper.ui.common import track_after
        frame = tkinter.Frame(self.root); ran = []
        track_after(frame, 10, lambda: ran.append(1))
        end = time.time() + 2
        while not ran and time.time() < end: self.root.update(); time.sleep(0.01)
        self.assertEqual(ran, [1]); self.assertEqual(frame._tracked_after, set())
        self.root.destroy()

    def test_background_poller_of_a_tab_stops_with_the_tab(self):
        import tkinter
        from socialscraper.ui.forms import run_async
        frame = tkinter.Frame(self.root); done = []
        run_async(frame, lambda: time.sleep(0.4), lambda r, e: done.append(1))
        frame.destroy()
        self.assertEqual(self.pending(), [])
        end = time.time() + 0.7
        while time.time() < end: self.root.update(); time.sleep(0.01)
        self.assertEqual(done, []); self.assertEqual(self.errors, [])
        self.root.destroy()

    def test_main_window_destroy_leaves_nothing_pending(self):
        from socialscraper.ui.common import cancel_all_after
        self.root.after(10_000, lambda: None); self.root.after(20_000, lambda: None)
        interp = self.root.tk
        cancel_all_after(self.root)
        self.assertEqual(list(interp.splitlist(interp.call('after', 'info'))), [])
        self.root.destroy()


class ColumnFitTests(unittest.TestCase):
    """Spaltenbreiten passen zum Inhalt: Zahlen werden nie abgeschnitten, flexible Spalten teilen den Rest."""

    def setUp(self):
        import tkinter
        from tkinter import ttk
        try:
            self.root = tkinter.Tk()
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self.root.destroy)
        self.root.geometry('900x300+10+10'); self.root.attributes('-alpha', 0.0); self.ttk = ttk   # eingeblendet (nötig für echte Breiten), aber unsichtbar

    def make(self, width, rows):
        from tkinter import ttk
        from socialscraper.ui.widgets import ColumnFitter
        frame = self.ttk.Frame(self.root, width=width, height=200); frame.grid_propagate(False); frame.pack(fill='y', side='left')
        tree = self.ttk.Treeview(frame, columns=('name', 'likes', 'views'), show='headings')
        for c, t in (('name', 'Beschreibung'), ('likes', 'Likes'), ('views', 'Aufrufe')):
            tree.heading(c, text=t)
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky='nsew')
        for r in rows:
            tree.insert('', 'end', values=r)
        scroll = self.ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        scroll.grid(row=1, column=0, sticky='ew'); scroll.grid_remove()
        fit = ColumnFitter(tree, {'name': (60, 1), 'likes': (40, 0), 'views': (40, 0)}, scroll)
        self.root.update()
        return tree, fit

    def test_numbers_get_their_full_width_and_the_rest_goes_to_flexible_columns(self):
        import tkinter.font as tkfont
        from socialscraper.ui import common
        font = tkfont.Font(font=common.FONT)
        rows = [('Ein langer Text ' * 5, '20.049.428', '80.197.712'), ('kurz', '5', '12')]
        tree, _fit = self.make(700, rows)
        for col, text in (('likes', '20.049.428'), ('views', '80.197.712')):
            self.assertGreaterEqual(int(tree.column(col, 'width')), font.measure(text) + 8, col)
        widths = [int(tree.column(c, 'width')) for c in ('name', 'likes', 'views')]
        self.assertLessEqual(sum(widths), tree.winfo_width() + 2)           # alles passt, nichts ragt heraus
        self.assertGreater(widths[0], 150)                                    # der flexible Text bekommt den Rest

    def test_numbers_stay_complete_even_when_the_table_is_narrow(self):
        import tkinter.font as tkfont
        from socialscraper.ui import common
        font = tkfont.Font(font=common.FONT)
        tree, _fit = self.make(200, [('x' * 80, '20.049.428', '80.197.712')])
        self.assertGreaterEqual(int(tree.column('likes', 'width')), font.measure('20.049.428') + 8)
        self.assertEqual(int(tree.column('name', 'width')), 60)               # nur die flexible Spalte wird knapp (Mindestbreite)

    def test_tree_column_is_counted_so_the_last_column_is_not_cut_off(self):
        from socialscraper.ui.widgets import ColumnFitter
        frame = self.ttk.Frame(self.root, width=620, height=200); frame.grid_propagate(False); frame.pack(fill='y', side='left')
        tree = self.ttk.Treeview(frame, columns=('name', 'views', 'likes'), show='tree headings')
        tree.column('#0', width=38, stretch=False)
        for c, t in (('name', 'Gruppe'), ('views', 'Aufrufe (Summe)'), ('likes', 'Likes (Summe)')):
            tree.heading(c, text=t)
        frame.rowconfigure(0, weight=1); frame.columnconfigure(0, weight=1); tree.grid(row=0, column=0, sticky='nsew')
        tree.insert('', 'end', values=('Wettbewerb', '409.801.548', '102.450.000'))
        ColumnFitter(tree, {'name': (90, 1), 'views': (80, 0), 'likes': (70, 0)}, arrow=0)
        self.root.update()
        used = 38 + sum(int(tree.column(c, 'width')) for c in ('name', 'views', 'likes'))
        self.assertLessEqual(used, tree.winfo_width() + 1)

    def test_horizontal_scrollbar_only_when_needed(self):
        tree, fit = self.make(200, [('x' * 80, '20.049.428', '80.197.712')])
        self.assertTrue(fit.hscroll.winfo_manager())                           # zu eng: Bildlaufleiste da
        tree2, fit2 = self.make(900, [('kurz', '5', '12')])
        self.assertEqual(fit2.hscroll.winfo_manager(), '')                     # genug Platz: keine Leiste


class DialogPlacementTests(unittest.TestCase):
    """Dialoge stehen mittig über dem Hauptfenster und sperren es (modal)."""

    def setUp(self):
        import tkinter
        try:
            self.root = tkinter.Tk()
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.addCleanup(self.root.destroy)
        self.root.geometry('900x500+150+120'); self.root.attributes('-alpha', 0.0); self.root.update()

    def centre_offset(self, dialog):
        dx = dialog.winfo_rootx() + dialog.winfo_width() // 2 - (self.root.winfo_rootx() + self.root.winfo_width() // 2)
        dy = dialog.winfo_rooty() + dialog.winfo_height() // 2 - (self.root.winfo_rooty() + self.root.winfo_height() // 2)
        return abs(dx), abs(dy)

    def test_form_dialog_is_centered_over_the_main_window_and_modal(self):
        import tkinter
        from socialscraper.ui.widgets import FormDialog
        tab = tkinter.Frame(self.root); tab.place(x=300, y=300, width=100, height=60)     # Tab irgendwo im Fenster, nicht mittig
        d = FormDialog(tab, 'Test', [{'key': 'n', 'label': 'Name', 'value': 'a'}], intro='Hallo')
        d.attributes('-alpha', 0.0)
        seen = {}

        def probe():
            seen['grab'] = str(d.grab_current()); seen['offset'] = self.centre_offset(d); seen['viewable'] = d.winfo_viewable()
            d.accept()
        self.root.after(200, probe)
        self.assertEqual(d.run(), {'n': 'a'})
        self.assertEqual(seen['grab'], str(d)); self.assertTrue(seen['viewable'])           # modal: der Dialog hält den Eingabefokus
        self.assertLessEqual(seen['offset'][0], 20); self.assertLessEqual(seen['offset'][1], 20)

    def test_target_dialog_is_centered_over_the_main_window(self):
        from socialscraper.ui.target_dialog import TargetDialog
        dlg = TargetDialog(self.root, lambda *a: None, [], [], ':memory:', modal=False)
        dlg.attributes('-alpha', 0.0); dlg.update()
        self.assertLessEqual(self.centre_offset(dlg)[0], 20); self.assertLessEqual(self.centre_offset(dlg)[1], 20)
        dlg.destroy()

    def test_long_paths_are_shortened_in_the_middle(self):
        import tkinter.font as tkfont
        from socialscraper.ui import common
        from socialscraper.ui.widgets import shorten_middle
        font = tkfont.Font(font=common.FONT)
        path = r'C:\Users\Rene-\Documents\Codex\2026-09-17\wa\outputs\pulse-ui\socialscraper-startbereit\data\scrape.db'
        short = shorten_middle(path, font, 300)
        self.assertLessEqual(font.measure(short), 300)
        self.assertTrue(short.startswith('C:\\Users')); self.assertTrue(short.endswith('scrape.db')); self.assertIn('…', short)
        self.assertEqual(shorten_middle('kurz', font, 300), 'kurz')


class SortHeaderAndDialogTests(unittest.TestCase):
    def setUp(self):
        import tkinter
        try:
            self.root = tkinter.Tk()
        except tkinter.TclError as error:
            self.skipTest(f'Kein Bildschirm: {error}')
        self.root.withdraw(); self.addCleanup(self.root.destroy)

    def test_form_dialog_keeps_open_on_problem_and_maps_choices(self):
        from socialscraper.ui.widgets import FormDialog
        seen = []
        d = FormDialog(self.root, 'T', [{'key': 'n', 'label': 'Name'},
                                        {'key': 'g', 'label': 'G', 'kind': 'choice', 'choices': [(1, 'Eins'), (2, 'Zwei')], 'value': 2},
                                        {'key': 'f', 'label': 'F', 'kind': 'radio', 'choices': [('csv', 'CSV'), ('jsonl', 'J')]},
                                        {'key': 'c', 'label': 'C', 'kind': 'color'}],
                       validator=lambda v: seen.append(v) or ('Name fehlt' if not v['n'] else None))
        d.update()
        d.accept(); self.assertTrue(d.winfo_exists()); self.assertEqual(d.error.cget('text'), 'Name fehlt')
        d.vars['n'].set('X'); d.accept()
        self.assertEqual(d.result, {'n': 'X', 'g': 2, 'f': 'csv', 'c': '#2563eb'})

    def test_form_dialog_cancel_gives_none(self):
        from socialscraper.ui.widgets import FormDialog
        d = FormDialog(self.root, 'T', [{'key': 'n', 'label': 'Name', 'value': 'a'}]); d.cancel(); self.assertIsNone(d.result)

    def test_color_swatch_click_selects(self):
        from socialscraper.ui.widgets import FormDialog
        d = FormDialog(self.root, 'T', [{'key': 'c', 'label': 'C', 'kind': 'color', 'value': repo.DEFAULT_COLORS[0]}])
        ev = type('E', (), {'x': 4 + 32 * 3 + 10})()
        d._pick_color(ev, d._color['c'], d.vars['c']); d.accept()
        self.assertEqual(d.result['c'], repo.DEFAULT_COLORS[3])


if __name__ == '__main__':
    unittest.main()
