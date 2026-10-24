"""Late/manual challenge boundaries: no external requests or browser required."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_webde_flow as flow
from socialscraper.mailanbieter import unitedinternet as provider


class LateChallengeTests(unittest.TestCase):
    def form(self, *, alias=False):
        page, ctx, visible, values = flow.WebdeFlow.form(self)
        page.query_selector_all.side_effect = lambda selector: (
            [found] if (found := page.query_selector(selector)) is not None else [])
        if alias:
            visible.clear(); visible.add('input[name="alias"]')
        return page, ctx, visible, values

    def test_captcha_appearing_during_wait_stays_manual_after_it_disappears(self):
        page, ctx, visible, _ = self.form()
        ctx.warte.side_effect = lambda *_: visible.add('text="Ich bin ein Mensch"')
        def wait(_page, _ctx, *, tick):
            ctx.click.assert_not_called()
            self.assertTrue(any('CAPTCHA' in c.args[0] for c in ctx.hinweis.call_args_list))
            visible.remove('text="Ich bin ein Mensch"')
            ctx.warte.side_effect = None
            tick(); tick()
            ctx.click.assert_not_called()
            visible.clear(); visible.add('input[name="password"]')
            tick(); ctx.click.assert_called_once()
        with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            provider.run(page, ctx)

    def test_captcha_during_alias_check_stops_second_click_and_stays_manual(self):
        page, ctx, visible, _ = self.form(alias=True)
        def available(*_):
            visible.add('text="Ich bin ein Mensch"')
            return True
        def wait(_page, _ctx, *, tick):
            ctx.click.assert_called_once()
            tick(); ctx.click.assert_called_once()
            visible.remove('text="Ich bin ein Mensch"')
            tick(); ctx.click.assert_called_once()
        with patch.object(provider, '_alias_available', side_effect=available), \
                patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            provider.run(page, ctx)

    def test_consent_during_alias_check_stops_second_click(self):
        page, ctx, visible, _ = self.form(alias=True)
        def available(*_):
            visible.add('input[name="termsAccepted"]')
            return True
        def wait(_page, _ctx, *, tick):
            ctx.click.assert_called_once()
            tick(); ctx.click.assert_called_once()
            visible.remove('input[name="termsAccepted"]')
            tick(); ctx.click.assert_called_once()
        with patch.object(provider, '_alias_available', side_effect=available), \
                patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            provider.run(page, ctx)

    def test_hidden_first_captcha_does_not_hide_visible_second_match(self):
        page, ctx, _, _ = self.form()
        hidden = SimpleNamespace(is_visible=lambda: False)
        shown = SimpleNamespace(is_visible=lambda: True)
        original_query = page.query_selector.side_effect
        original_all = page.query_selector_all.side_effect
        page.query_selector.side_effect = lambda query: hidden if query == '.captcha' else original_query(query)
        page.query_selector_all.side_effect = lambda query: [hidden, shown] if query == '.captcha' else original_all(query)
        with patch.object(provider, '_warte_bis_fertig'):
            provider.run(page, ctx)
        ctx.click.assert_not_called()

    def test_unreadable_challenge_blocks_submit_and_notifies_once(self):
        page, ctx, _, _ = self.form()
        original_all = page.query_selector_all.side_effect
        def query(selector):
            if selector == '.captcha': raise RuntimeError('Detached control')
            return original_all(selector)
        page.query_selector_all.side_effect = query
        def wait(_page, _ctx, *, tick):
            tick(); tick()
            ctx.click.assert_not_called()
            hints = [c.args[0] for c in ctx.hinweis.call_args_list if 'CAPTCHA' in c.args[0]]
            self.assertEqual(len(hints), 1)
        with patch.object(provider, '_warte_bis_fertig', side_effect=wait):
            provider.run(page, ctx)


if __name__ == '__main__': unittest.main()
