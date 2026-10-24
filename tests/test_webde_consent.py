"""Round 37: personal consent stays with the user in WEB.DE/GMX forms."""
import contextlib
import re
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import test_webde_flow as flow
from socialscraper.mailanbieter import unitedinternet as provider


class WebdeConsentTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for method in ('create_connection', 'getaddrinfo'):
            stack.enter_context(patch.object(socket, method, side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network blocked')))
        stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('External process blocked')))

    def form(self, *, domain='web.de', consent='input[name="agb"]', checked=False, alias=False):
        page, ctx, fields, values = flow.WebdeFlow.form(self)
        fields.clear()
        fields.update({'input[name="alias"]'} if alias else
                      {'input[name="firstName"]', 'input[name="lastName"]'})
        page.url = 'https://register.' + domain + '/'
        ctx.anbieter = ctx.domaene = domain
        ctx.adresse = 'test123@' + domain
        state = {'checked': checked, 'present': True}

        def automatic_check(*_args, **_kwargs):
            state['checked'] = True

        checkbox = SimpleNamespace(
            is_visible=lambda: True, is_enabled=lambda: True,
            is_checked=lambda: state['checked'],
            get_attribute=lambda key: {'type': 'checkbox', 'name': 'agb'}.get(key),
            check=MagicMock(side_effect=automatic_check),
            click=MagicMock(side_effect=automatic_check))
        original_query = page.query_selector.side_effect
        page.query_selector.side_effect = lambda selector: (
            checkbox if consent and state['present'] and selector == consent else original_query(selector))
        page.query_selector_all.side_effect = lambda selector: (
            [field] if (field := page.query_selector(selector)) is not None else [])

        def show_password_step():
            fields.clear()
            fields.add('input[name="password"]')

        page.show_password_step = show_password_step
        return page, ctx, values, state, checkbox

    def consent_hints(self, ctx):
        return [str(call.args[0]) for call in ctx.hinweis.call_args_list
                if re.search(r'AGB|Nutzungsbedingungen|Einwilligung|Zustimmung', str(call.args[0]), re.I)]

    def assert_no_automatic_consent(self, checkbox):
        checkbox.check.assert_not_called()
        checkbox.click.assert_not_called()

    def test_unchecked_visible_consent_waits_without_check_or_submit(self):
        for domain in ('web.de', 'gmx.net'):
            for selector in ('input[name="agb"]', 'input#agb', 'input[name="termsAccepted"]'):
                with self.subTest(domain=domain, selector=selector):
                    page, ctx, values, state, checkbox = self.form(domain=domain, consent=selector)

                    def wait(_page, _ctx, *, tick):
                        for _ in range(3):
                            self.assert_no_automatic_consent(checkbox)
                            ctx.click.assert_not_called()
                            self.assertFalse(state['checked'])
                            self.assertEqual(values['input[name="firstName"]'], ctx.vorname)
                            hints = self.consent_hints(ctx)
                            self.assertEqual(len(hints), 1)
                            self.assertRegex(hints[0], r'(?i)selbst|manuell|im Fenster')
                            tick()

                    with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
                        provider.run(page, ctx)
                    self.assert_no_automatic_consent(checkbox)
                    ctx.click.assert_not_called()

    def test_checked_consent_stays_manual_until_the_next_form_step(self):
        for domain in ('web.de', 'gmx.net'):
            with self.subTest(domain=domain):
                page, ctx, _values, state, checkbox = self.form(domain=domain, checked=True)

                def wait(_page, _ctx, *, tick):
                    ctx.click.assert_not_called()
                    tick()
                    tick()
                    ctx.click.assert_not_called()
                    state['present'] = False
                    page.show_password_step()
                    tick()
                    ctx.click.assert_called_once()

                with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
                    provider.run(page, ctx)
                self.assert_no_automatic_consent(checkbox)
                self.assertTrue(state['checked'])

    def test_later_manual_consent_resumes_without_overwriting_completed_fields(self):
        page, ctx, values, state, checkbox = self.form()

        def wait(_page, _ctx, *, tick):
            self.assert_no_automatic_consent(checkbox)
            ctx.click.assert_not_called()
            initial_fills = ctx.fill.call_count
            self.assertEqual(initial_fills, 2)
            values['input[name="firstName"]'] = 'Manual correction'
            for _ in range(3):
                tick()
                self.assertEqual(ctx.fill.call_count, initial_fills)
                self.assertEqual(values['input[name="firstName"]'], 'Manual correction')
                ctx.click.assert_not_called()
            self.assertEqual(len(self.consent_hints(ctx)), 1)
            # Only this simulated human action changes the checkbox state.
            state['checked'] = True
            tick()
            ctx.click.assert_not_called()
            self.assertEqual(ctx.fill.call_count, initial_fills)
            self.assertEqual(values['input[name="firstName"]'], 'Manual correction')
            state['present'] = False
            page.show_password_step()
            tick()
            ctx.click.assert_called_once()
            self.assertEqual(ctx.fill.call_count, initial_fills + 1)
            self.assertEqual(values['input[name="firstName"]'], 'Manual correction')
            tick()
            ctx.click.assert_called_once()
            self.assert_no_automatic_consent(checkbox)

        with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            provider.run(page, ctx)

    def test_form_without_consent_field_keeps_existing_single_submit(self):
        page, ctx, _values, _state, checkbox = self.form(consent=None)

        def wait(_page, _ctx, *, tick):
            ctx.click.assert_called_once()
            fill_count = ctx.fill.call_count
            tick()
            tick()
            ctx.click.assert_called_once()
            self.assertEqual(ctx.fill.call_count, fill_count)

        with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            result = provider.run(page, ctx)
        self.assertEqual(result['adresse'], 'test123@web.de')
        self.assert_no_automatic_consent(checkbox)
        self.assertEqual(self.consent_hints(ctx), [])

    def test_disappearing_consent_does_not_submit_the_same_form_section(self):
        page, ctx, values, state, checkbox = self.form()

        def wait(_page, _ctx, *, tick):
            ctx.click.assert_not_called()
            before = ctx.fill.call_count
            values['input[name="firstName"]'] = 'Manual correction'
            state['checked'] = True
            state['present'] = False
            tick()
            tick()
            ctx.click.assert_not_called()
            self.assertEqual(ctx.fill.call_count, before)
            self.assertEqual(values['input[name="firstName"]'], 'Manual correction')
            self.assert_no_automatic_consent(checkbox)
            page.show_password_step()
            tick()
            ctx.click.assert_called_once()
            self.assertEqual(ctx.fill.call_count, before + 1)
            tick()
            ctx.click.assert_called_once()

        with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            provider.run(page, ctx)

    def test_visible_label_with_hidden_control_stays_manual_even_on_visibility_error(self):
        for visibility_error in (False, True):
            with self.subTest(visibility_error=visibility_error):
                page, ctx, _values, state, label = self.form(consent='label[for*="agb" i]')
                label.is_checked = MagicMock(side_effect=RuntimeError('Label is not a checkbox'))
                label.get_attribute = lambda key: 'hidden-agb' if key == 'for' else None
                label.evaluate = MagicMock(return_value=False)
                if visibility_error:
                    label.is_visible = MagicMock(side_effect=RuntimeError('Label detached during visibility lookup'))
                hidden = SimpleNamespace(is_visible=lambda: False, is_checked=lambda: state['checked'],
                                         check=MagicMock(), click=MagicMock())
                original_query = page.query_selector.side_effect
                page.query_selector.side_effect = lambda selector: (
                    hidden if selector == 'input[name="agb"]' else original_query(selector))

                def wait(_page, _ctx, *, tick):
                    for _ in range(3):
                        self.assert_no_automatic_consent(label)
                        self.assert_no_automatic_consent(hidden)
                        ctx.click.assert_not_called()
                        self.assertFalse(state['checked'])
                        self.assertEqual(len(self.consent_hints(ctx)), 1)
                        tick()

                with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
                    provider.run(page, ctx)

    def test_checkbox_visibility_lookup_error_stays_manual(self):
        page, ctx, _values, _state, checkbox = self.form()
        checkbox.is_visible = MagicMock(side_effect=RuntimeError('Checkbox was detached'))

        def wait(_page, _ctx, *, tick):
            for _ in range(3):
                self.assert_no_automatic_consent(checkbox)
                ctx.click.assert_not_called()
                self.assertEqual(len(self.consent_hints(ctx)), 1)
                tick()

        with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            provider.run(page, ctx)

    def test_consent_appearing_during_wait_or_dismiss_blocks_the_imminent_submit(self):
        for trigger in ('warte', 'dismiss'):
            with self.subTest(trigger=trigger):
                page, ctx, _values, state, checkbox = self.form(consent=None)
                appeared = [False]
                original_query = page.query_selector.side_effect
                page.query_selector.side_effect = lambda selector: (
                    checkbox if appeared[0] and state['present'] and selector == 'input[name="agb"]' else original_query(selector))

                def appear(*_args, **_kwargs):
                    appeared[0] = True

                getattr(ctx, trigger).side_effect = appear

                def wait(_page, _ctx, *, tick):
                    self.assertTrue(appeared[0])
                    self.assert_no_automatic_consent(checkbox)
                    ctx.click.assert_not_called()
                    self.assertEqual(len(self.consent_hints(ctx)), 1)
                    before = ctx.fill.call_count
                    tick()
                    ctx.click.assert_not_called()
                    state['checked'] = True
                    tick()
                    ctx.click.assert_not_called()
                    self.assertEqual(ctx.fill.call_count, before)
                    state['present'] = False
                    page.show_password_step()
                    tick()
                    ctx.click.assert_called_once()
                    self.assertEqual(ctx.fill.call_count, before + 1)

                with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
                    provider.run(page, ctx)

    def test_available_alias_does_not_select_again_while_new_consent_is_pending(self):
        page, ctx, _values, state, checkbox = self.form(consent=None, alias=True)
        followup = [False]
        original_query = page.query_selector.side_effect
        available = SimpleNamespace(is_visible=lambda: True)

        def query(selector):
            if followup[0]:
                if state['present'] and selector == 'input[name="agb"]':
                    return checkbox
                if selector == 'form#alias-step .alias-step__form-element--available input#alias':
                    return available
            return original_query(selector)

        page.query_selector.side_effect = query
        page.input_value.side_effect = lambda selector, **_kwargs: _values[selector] if selector in _values else {'#alias': 'test123', '#domain': 'web.de'}[selector]

        def wait(_page, _ctx, *, tick):
            ctx.click.assert_called_once()
            before = ctx.fill.call_count
            followup[0] = True
            tick()
            tick()
            ctx.click.assert_called_once()
            self.assert_no_automatic_consent(checkbox)
            self.assertEqual(len(self.consent_hints(ctx)), 1)
            state['checked'] = True
            tick()
            ctx.click.assert_called_once()
            self.assertEqual(ctx.fill.call_count, before)
            state['present'] = False
            page.show_password_step()
            tick()
            self.assertEqual(ctx.click.call_count, 2)
            self.assertEqual(ctx.click.call_args.args[0], provider.SEL['senden'])
            self.assertEqual(sum(call.args[0] == provider.ALIAS_NEXT for call in ctx.click.call_args_list), 1)
            tick()
            self.assertEqual(ctx.click.call_count, 2)
            self.assertEqual(ctx.fill.call_count, before + 1)

        with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            provider.run(page, ctx)

    def test_second_matching_visible_label_keeps_the_whole_step_manual(self):
        selector = 'label[for*="agb" i]'
        page, ctx, _values, state, first = self.form(consent=selector)
        first_state = {'visible': True}
        first.is_visible = lambda: first_state['visible']
        first.is_checked = MagicMock(side_effect=RuntimeError('Label is not a checkbox'))
        first.evaluate = MagicMock(return_value=True)
        second_state = {'checked': False}
        second = SimpleNamespace(is_visible=lambda: state['present'],
                                 is_checked=MagicMock(side_effect=RuntimeError('Label is not a checkbox')),
                                 evaluate=lambda *_args, **_kwargs: second_state['checked'],
                                 check=MagicMock(), click=MagicMock())
        original_all = page.query_selector_all.side_effect
        page.query_selector_all.side_effect = lambda query: (
            [first, second] if query == selector else original_all(query))

        def wait(_page, _ctx, *, tick):
            ctx.click.assert_not_called()
            self.assert_no_automatic_consent(first)
            self.assert_no_automatic_consent(second)
            self.assertEqual(len(self.consent_hints(ctx)), 1)
            # Both labels initially exist; hiding the first panel must not
            # conceal the still-visible second match from the next poll.
            first_state['visible'] = False
            tick()
            ctx.click.assert_not_called()
            second_state['checked'] = True
            tick()
            ctx.click.assert_not_called()
            state['present'] = False
            page.show_password_step()
            tick()
            ctx.click.assert_called_once()

        with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            provider.run(page, ctx)


if __name__ == '__main__':
    unittest.main()
