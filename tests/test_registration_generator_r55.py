"""Fictional drafts, manual-edit preservation and the actual local Tk dialog."""
import datetime as dt
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from socialscraper import registration_data as data
from socialscraper.ui import postfaecher, widgets


class GeneratorTests(unittest.TestCase):
    def test_fictional_profiles_are_complete_and_locally_valid_for_each_country(self):
        for country, digits in (('DE', 5), ('AT', 4), ('CH', 4)):
            with self.subTest(country=country):
                value = data.generate_test_data(country=country)
                self.assertEqual(data.normalize(value, required=True), value)
                self.assertEqual(set(value), set(data.FIELDS))
                self.assertTrue(all(value.values()))
                self.assertEqual(value['nachname'], 'Musterperson')
                self.assertEqual(value['plz'], '0' * digits)
                self.assertEqual(value['land'], country)
                self.assertEqual(value['anrede'], 'Keine Angabe')

    def test_birthday_and_house_number_random_boundaries_are_valid(self):
        for edge in ('first', 'last'):
            with self.subTest(edge=edge), patch.object(data.secrets, 'randbelow',
                    side_effect=lambda n: 0 if edge == 'first' else n - 1):
                value = data.generate_test_data()
                birthday = dt.date.fromisoformat(value['geburtsdatum'])
                today = dt.date.today()
                self.assertGreaterEqual(birthday, dt.date(today.year - 60, 1, 1))
                self.assertLessEqual(birthday, dt.date(today.year - 25, 1, 1))
                self.assertIn(value['hausnummer'], ('1', '99'))

    def test_generator_neither_loads_nor_saves_local_profiles(self):
        with patch.object(data, '_read') as read, patch.object(data.accountvault, '_write') as write:
            data.generate_test_data()
            read.assert_not_called(); write.assert_not_called()

    def test_empty_draft_has_readable_date_and_maps_back_to_normalized_profile(self):
        result = data.suggest_test_data({})
        dt.datetime.strptime(result['geburtsdatum'], '%d.%m.%Y')
        self.assertEqual(set(data.normalize(result, required=True)), set(data.FIELDS))

    def test_manual_partial_values_are_preserved_and_country_is_respected(self):
        draft = {'vorname': ' Local edit ', 'geburtsdatum': '29.', 'land': 'AT', 'strasse': 'My street'}
        original = dict(draft)
        result = data.suggest_test_data(draft)
        self.assertEqual(draft, original)
        self.assertFalse(set(result) & set(draft))
        self.assertEqual(result['plz'], '0000')

    def test_whitespace_fields_are_treated_as_empty(self):
        self.assertTrue(data.suggest_test_data({'vorname': '  '})['vorname'].strip())

    def test_repeated_click_does_not_change_completed_draft(self):
        completed = data.suggest_test_data({})
        with patch.object(data, 'generate_test_data') as generate:
            self.assertEqual(data.suggest_test_data(completed), {})
            generate.assert_not_called()

    def test_invalid_country_or_shape_does_not_echo_values(self):
        for value in ({'land': 'PRIVATE'}, {'unknown': 'PRIVATE'}, {'vorname': 1}, []):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError) as error:
                data.suggest_test_data(value)
            self.assertNotIn('PRIVATE', str(error.exception))


class GeneratorDialogTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk(); self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def dialog(self, current=None, **kwargs):
        current = current or {}
        fields = [{'key': key, 'label': label, 'value': current.get(key, '')}
                  for key, label in zip(data.FIELDS, data.LABELS)]
        for field in fields:
            if field['key'] == 'land':
                field.update(kind='choice', choices=[('', 'Leer'), *data.COUNTRIES.items()])
            if field['key'] == 'anrede':
                field.update(kind='choice', choices=[('', 'Leer'), *((x, x) for x in data.SALUTATIONS)])
        return widgets.FormDialog(self.root, 'Offline generator test', fields,
            suggestion=('Testdaten ergänzen', data.suggest_test_data), **kwargs)

    def test_button_populates_draft_without_saving_or_accepting(self):
        dialog = self.dialog()
        with patch.object(data, 'save') as save:
            dialog.suggestion_btn.invoke()
            self.assertIsNone(dialog.result)
            self.assertTrue(dialog.winfo_exists())
            data.normalize(dialog.values(), required=True)
            self.assertEqual(dialog.vars['land'].get(), 'Deutschland')
            self.assertEqual(dialog.values()['land'], 'DE')
            save.assert_not_called()
        dialog.cancel(); self.assertIsNone(dialog.result)

    def test_second_click_preserves_manual_edit_and_accept_returns_it(self):
        dialog = self.dialog()
        dialog.suggestion_btn.invoke()
        dialog.vars['vorname'].set('Edited locally')
        before = dialog.values()
        dialog.suggestion_btn.invoke()
        self.assertEqual(dialog.values(), before)
        dialog.accept(); self.assertEqual(dialog.result, before)

    def test_enter_on_generator_does_not_save_or_close_the_dialog(self):
        dialog = self.dialog(data.generate_test_data())
        dialog.accept(SimpleNamespace(widget=dialog.suggestion_btn))
        self.assertIsNone(dialog.result)
        self.assertTrue(dialog.winfo_exists())
        data.normalize(dialog.values(), required=True)

    def test_invalid_choice_does_not_apply_any_part_of_a_suggestion(self):
        dialog = self.dialog({'vorname': 'Existing'})
        before = dialog.values()
        dialog.apply_suggestion(lambda _: {'vorname': 'Changed', 'land': 'unknown'})
        self.assertEqual(dialog.values(), before)
        self.assertTrue(dialog.error.cget('text'))
        self.assertIsNone(dialog.result)

    def test_unknown_field_does_not_apply_any_part_of_a_suggestion(self):
        dialog = self.dialog()
        before = dialog.values()
        dialog.apply_suggestion(lambda _: {'vorname': 'Changed', 'unknown': 'x'})
        self.assertEqual(dialog.values(), before)
        self.assertTrue(dialog.error.cget('text'))

    def test_validator_still_blocks_invalid_manual_birthday_after_generation(self):
        def validate(values):
            try: data.normalize(values, required=True)
            except ValueError as error: return str(error)
        dialog = self.dialog({'geburtsdatum': '31.02.1995'}, validator=validate)
        dialog.suggestion_btn.invoke()
        dialog.accept()
        self.assertIsNone(dialog.result)
        self.assertTrue(dialog.winfo_exists())
        self.assertIn('Geburtsdatum', dialog.error.cget('text'))

    def test_existing_dialog_without_generator_still_works(self):
        dialog = widgets.FormDialog(self.root, 'Existing dialog', [{'key': 'name', 'value': 'Local'}])
        self.assertIsNone(dialog.suggestion_btn)
        dialog.accept(); self.assertEqual(dialog.result, {'name': 'Local'})

    def test_actual_registration_dialog_wires_generator_but_cancel_does_not_save(self):
        tab = SimpleNamespace(app=SimpleNamespace(), provider=SimpleNamespace(get=lambda: 'web.de'))
        def form(parent, title, fields, **kwargs):
            self.assertIn('fiktiven', kwargs['intro'])
            dialog = widgets.FormDialog(self.root, title, fields, **kwargs)
            dialog.suggestion_btn.invoke()
            data.normalize(dialog.values(), required=True)
            dialog.cancel()
            return dialog.result
        with patch('socialscraper.ui.workflow_components.busy', return_value=False), \
                patch.object(postfaecher, 'cfg_of', return_value=SimpleNamespace(db_path='unused')), \
                patch.object(data, 'load', return_value={}), patch.object(data, 'save') as save, \
                patch.object(postfaecher, 'ask_form', side_effect=form):
            self.assertFalse(postfaecher.PostfaecherTab.registration_details(tab))
        save.assert_not_called()


if __name__ == '__main__': unittest.main()
