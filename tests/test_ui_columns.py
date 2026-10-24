"""Gekürzte Tabellenzellen zeigen den vollen Text als Hinweis (ColumnFitter), Reiter „Abrufen“ zeigt je Reiter nur passende Felder."""
import pathlib
import tempfile
import tkinter as tk
import unittest
from tkinter import ttk

from socialscraper.ui.widgets import ColumnFitter
from tests.ui_fake import FakeApp


class CellTipTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.root.geometry('600x200+10+10')
        self.root.attributes('-alpha', 0.0)

    def make(self, rows, width=300):
        frame = ttk.Frame(self.root, width=width, height=150)
        frame.grid_propagate(False)
        frame.pack(side='left')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=('name', 'zahl'), show='headings')
        tree.heading('name', text='Name')
        tree.heading('zahl', text='Zahl')
        tree.grid(row=0, column=0, sticky='nsew')
        for r in rows:
            tree.insert('', 'end', values=r)
        fit = ColumnFitter(tree, {'name': (50, 1), 'zahl': (40, 0)}, arrow=0)
        self.root.update()
        return tree, fit

    def point(self, tree, column):
        row = tree.get_children()[0]
        x, y, w, h = tree.bbox(row, column)
        return x + 4, y + h // 2

    def test_truncated_cell_reports_full_text(self):
        long = 'Ein sehr langer Kontoname, der nie in die schmale Spalte passt'
        tree, fit = self.make([(long, '5')])
        cell, text = fit.cell_text(*self.point(tree, 'name'))
        self.assertEqual(text, long)
        self.assertEqual(cell[1], 'name')

    def test_complete_cell_and_header_give_no_tip(self):
        tree, fit = self.make([('kurz', '5')], width=500)
        self.assertEqual(fit.cell_text(*self.point(tree, 'name')), (None, ''))
        self.assertEqual(fit.cell_text(*self.point(tree, 'zahl')), (None, ''))
        self.assertEqual(fit.cell_text(5, 5), (None, ''))            # Spaltenkopf
        self.assertEqual(fit.cell_text(5, 140), (None, ''))          # leere Fläche unter den Zeilen

    def test_tip_window_appears_and_goes_away(self):
        long = 'Ein sehr langer Kontoname, der nie in die schmale Spalte passt'
        tree, fit = self.make([(long, '5')])
        fit._tip_show(long, 50, 50)
        self.assertIsNotNone(fit.tip)
        fit._tip_hide()
        self.assertIsNone(fit.tip)


class FetchModeTests(unittest.TestCase):
    def setUp(self):
        from socialscraper.ui.tab_fetch import FetchTab
        from socialscraper import guicore
        from unittest.mock import patch
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        for p in (patch.object(guicore, 'ROOT', root), patch.dict('os.environ', {'SS_DB_PATH': str(root / 'data' / 'scrape.db')})):
            p.start()
            self.addCleanup(p.stop)
        self.app = FakeApp(root)
        self.addCleanup(self.app.destroy)
        self.tab = self.app.add('Abrufen', FetchTab)
        self.app.deiconify()
        self.app.attributes('-alpha', 0.0)
        self.app.update()

    def select(self, index):
        self.tab.modes.select(index)
        self.app.update()

    def test_settings_block_follows_the_selected_mode(self):
        tab = self.tab
        self.select(0)
        self.assertTrue(tab.opts.winfo_manager())
        for plat in ('tiktok', 'instagram', 'reddit', 'x'):
            self.assertTrue(tab.acc_boxes[plat].winfo_manager(), plat)
        self.select(1)                                                  # TikTok-Profil: nur das TikTok-Konto
        self.assertTrue(tab.opts.winfo_manager())
        self.assertTrue(tab.acc_boxes['tiktok'].winfo_manager())
        for plat in ('instagram', 'reddit', 'x'):
            self.assertFalse(tab.acc_boxes[plat].winfo_manager(), plat)
            self.assertFalse(tab.acc_labels[plat].winfo_manager(), plat)
        self.assertNotIn(' X ', tab.need_auth_check.cget('text'))
        self.select(2)                                                  # Kanal: der Dialog hat eigene Felder
        self.assertFalse(tab.opts.winfo_manager())
        self.select(0)
        self.assertTrue(tab.opts.winfo_manager())
        self.assertTrue(tab.acc_boxes['x'].winfo_manager())
        self.assertIn('TikTok und X', tab.need_auth_check.cget('text'))

    def test_values_survive_switching(self):
        self.tab.max_comments.set(77)
        self.select(2)
        self.select(0)
        self.assertEqual(self.tab.max_comments.get(), 77)


if __name__ == '__main__':
    unittest.main()
