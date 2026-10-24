"""Round 32: mixed WEB.DE forms must preserve manual personal details."""
import unittest
from unittest.mock import patch

import test_webde_flow as flow
from socialscraper.mailanbieter import unitedinternet as provider


class WebdeManualDetailsTests(unittest.TestCase):
    def form(self, visible, initial=None):
        page, ctx, fields, values = flow.WebdeFlow.form(self)
        fields.clear()
        fields.update(visible)
        values.update(initial or {})
        return page, ctx, fields, values

    def assert_manual_step_remains_unchanged(self, page, ctx, values, personal_fields, initial=None):
        expected = initial or {}

        def assert_state():
            for selector in personal_fields:
                self.assertEqual(values.get(selector), expected.get(selector), selector)
            ctx.click.assert_not_called()
            hints = [call.args[0] for call in ctx.hinweis.call_args_list
                     if call.args[0].startswith('Aktueller Registrierungsabschnitt:')]
            self.assertEqual(len(hints), 1)
            self.assertIn('im Fenster', hints[0])

        def wait(_page, _ctx, **kwargs):
            assert_state()
            for _ in range(3):
                kwargs['tick']()
                assert_state()

        with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            provider.run(page, ctx)

    def test_name_and_address_fields_wait_for_manual_details_without_submit(self):
        personal = {'input[name="postalCode"]', 'input[name="city"]', 'input[name="street"]'}
        for fields in (personal, {'input[name="city"]'}, {'input[name="street"]'}):
            with self.subTest(personal_fields=sorted(fields)):
                page, ctx, _, values = self.form(fields | {
                    'input[name="firstName"]', 'input[name="lastName"]'})
                self.assert_manual_step_remains_unchanged(page, ctx, values, fields)

    def test_password_and_address_fields_wait_for_manual_details_without_submit(self):
        personal = {'input[name="postalCode"]', 'input[name="city"]', 'input[name="street"]'}
        page, ctx, _, values = self.form(personal | {
            'input[name="password"]', 'input[name="passwordConfirmation"]'})
        self.assert_manual_step_remains_unchanged(page, ctx, values, personal)

    def test_existing_postcode_and_salutation_values_are_preserved(self):
        initial = {'input[name="postalCode"]': '10115', 'select[name="salutation"]': 'keine-angabe'}
        page, ctx, _, values = self.form(set(initial) | {
            'input[name="firstName"]', 'input[name="lastName"]'}, initial)
        self.assert_manual_step_remains_unchanged(page, ctx, values, set(initial), initial)

    def test_name_and_salutation_alone_stay_manual(self):
        for selector in ('select[name="salutation"]', 'select#salutation', 'select[name="gender"]'):
            with self.subTest(salutation=selector):
                page, ctx, _, values = self.form({selector, 'input[name="firstName"]', 'input[name="lastName"]'})
                self.assert_manual_step_remains_unchanged(page, ctx, values, {selector})


if __name__ == '__main__':
    unittest.main()
