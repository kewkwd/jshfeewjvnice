"""Current web.de step flow, without external services or native windows."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
from socialscraper.mailanbieter import unitedinternet as provider
from socialscraper.signup import _fill

class WebdeFlow(unittest.TestCase):
    def form(self):
        page=MagicMock();page.url='https://register.web.de/'
        visible={'input[name="firstName"]','input[name="lastName"]',
                 'input[name="birthDay"]','input[name="birthMonth"]','input[name="birthYear"]'}
        values={}
        page.input_value.side_effect=lambda selector,**_kwargs:values.get(selector,'')
        page.eval_on_selector.return_value=False
        def field(selector):
            if selector not in visible:return None
            return SimpleNamespace(is_visible=lambda:True)
        page.query_selector.side_effect=field
        page.query_selector_all.side_effect=lambda selector: [found] if (found := page.query_selector(selector)) is not None else []
        ctx=SimpleNamespace(anbieter='web.de',domaene='web.de',adresse='test123@web.de',
            vorname='Social',nachname='Scraper',passwort='FAKE_PASSWORD',password_filled=False,
            timeout=90,stop=None,mode='assisted',hinweis=MagicMock(),warte=MagicMock(),dismiss=MagicMock(),
            registrierungsdaten={'geburtsdatum':'1995-01-01'})
        def fill(selectors,value):
            found=next((s for s in selectors if s in visible),None)
            if found:
                values[found]=value
                if 'password' in found:ctx.password_filled=True
            return found
        ctx.fill=MagicMock(side_effect=fill);ctx.click=MagicMock(return_value='submit')
        return page,ctx,visible,values

    def test_person_step_does_not_require_a_password_on_the_same_page(self):
        page,ctx,visible,values=self.form()
        def guided_wait(_page,_ctx,**kwargs):
            self.assertIn('input[name="birthDay"]',values)
            self.assertIn('input[name="birthMonth"]',values)
            self.assertIn('input[name="birthYear"]',values)
            self.assertFalse(ctx.password_filled)
            ctx.click.assert_called_once()
            # Repeated polling must not resubmit a CAPTCHA/validation page.
            kwargs['tick']();kwargs['tick']();ctx.click.assert_called_once()
            visible.clear();visible.add('input[name="email"]')
            kwargs['tick']();self.assertEqual(values['input[name="email"]'],'test123')
            visible.clear();visible.add('input[name="password"]')
            kwargs['tick']();self.assertEqual(values['input[name="password"]'],'FAKE_PASSWORD')
        with patch.object(provider,'_warte_bis_fertig',side_effect=guided_wait):
            result=provider.run(page,ctx)
        self.assertEqual(result['adresse'],'test123@web.de')
        self.assertTrue(ctx.password_filled)

    def test_generic_welcome_or_wrong_address_is_not_confirmation(self):
        page,ctx,_,_=self.form()
        for url,body in [('https://register.web.de/','Willkommen'),
                         ('https://register.web.de/','Dein Postfach ist bereit: other@web.de'),
                         ('https://web.de.evil.invalid/','Dein Postfach ist bereit: test123@web.de')]:
            page.url=url;page.inner_text.return_value=body
            with self.subTest(url=url,body=body):
                self.assertFalse(provider._confirmed_mailbox(page,ctx))

    def test_success_binds_the_exact_address_to_the_provider(self):
        page,ctx,_,_=self.form()
        page.url='https://register.web.de/success'
        page.inner_text.return_value='Dein Postfach ist bereit: TEST123@WEB.DE'
        self.assertTrue(provider._confirmed_mailbox(page,ctx))
        page.inner_text.return_value='Dein Postfach ist bereit: test123@web.de.evil.invalid'
        self.assertFalse(provider._confirmed_mailbox(page,ctx))

    def test_wait_uses_the_requested_timeout_and_rejects_unknown_password(self):
        page,ctx,_,_=self.form();ctx.timeout=45
        with patch.object(provider,'_confirmed_mailbox',return_value=False),patch.object(provider.time,'monotonic',side_effect=[0,1,46]):
            with self.assertRaises(RuntimeError):provider._warte_bis_fertig(page,ctx)
        with patch.object(provider,'_confirmed_mailbox',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'Kennwort'):provider._warte_bis_fertig(page,ctx)

    def test_navigation_during_confirmation_keeps_the_registration_open(self):
        page,ctx,_,_=self.form()
        page.inner_text.side_effect=TimeoutError('body is loading')
        self.assertFalse(provider._confirmed_mailbox(page,ctx))
        page.inner_text.side_effect=RuntimeError('browser is closed')
        with self.assertRaisesRegex(RuntimeError,'browser is closed'):provider._confirmed_mailbox(page,ctx)

    def test_late_provider_rejection_stops_waiting_and_does_not_submit_again(self):
        page,ctx,_,_=self.form();ctx.timeout=.01
        def redirect():page.url='https://hilfe.web.de/kundenservice/flexreg.html'
        with patch.object(provider,'_confirmed_mailbox',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'Registrierung fehlgeschlagen'):
                provider._warte_bis_fertig(page,ctx,tick=redirect)
        page.wait_for_timeout.assert_not_called()
        ctx.click.assert_not_called()

    def test_failure_page_before_provider_run_receives_no_form_inputs(self):
        page,ctx,_,_=self.form();ctx.timeout=.01
        for host in ('hilfe.web.de','hilfe.gmx.net'):
            page.url='https://'+host+'/kundenservice/flexreg.html'
            with self.assertRaisesRegex(RuntimeError,'Registrierung fehlgeschlagen'):
                provider.run(page,ctx)
        ctx.fill.assert_not_called()
        ctx.click.assert_not_called()

    def test_failed_required_input_does_not_submit_or_consume_the_step(self):
        for failed in ('firstName','lastName','birthDay','birthMonth','birthYear','email','password','passwordConfirmation'):
            with self.subTest(field=failed):
                page,ctx,visible,values=self.form()
                visible.update({'input[name="email"]','input[name="password"]','input[name="passwordConfirmation"]'})
                fill=ctx.fill.side_effect
                ctx.fill.side_effect=lambda selectors,value: None if 'input[name="'+failed+'"]' in selectors else fill(selectors,value)
                def guided_wait(_page,_ctx,**kwargs):
                    ctx.click.assert_not_called()
                    ctx.fill.side_effect=fill
                    kwargs['tick']()
                    ctx.click.assert_called_once()
                    kwargs['tick']()
                    ctx.click.assert_called_once()
                with patch.object(provider,'_warte_bis_fertig',side_effect=guided_wait),patch.object(provider,'_waehle',return_value=False):
                    provider.run(page,ctx)

    def test_http_registration_page_receives_no_generated_data(self):
        page,ctx,_,_=self.form();page.url='http://register.web.de/'
        with patch.object(provider,'_warte_bis_fertig'):
            with self.assertRaisesRegex(RuntimeError,'HTTPS'):
                provider.run(page,ctx)
        ctx.fill.assert_not_called()
        ctx.click.assert_not_called()

    def test_cancellation_before_first_step_does_not_fill_or_submit(self):
        page,ctx,_,_=self.form();ctx.stop=lambda:True
        with patch.object(provider,'_warte_bis_fertig'):
            with self.assertRaisesRegex(RuntimeError,'Abgebrochen'):
                provider.run(page,ctx)
        ctx.fill.assert_not_called()
        ctx.click.assert_not_called()

    def test_native_input_failure_is_not_overridden_with_javascript(self):
        field=MagicMock();field.count.return_value=1;field.is_visible.return_value=True
        field.fill.side_effect=TimeoutError('field is disabled')
        page=SimpleNamespace(locator=lambda _:SimpleNamespace(first=field),evaluate=MagicMock(return_value='#password'))
        self.assertIsNone(_fill(page,['#password'],'FAKE_PASSWORD'))
        page.evaluate.assert_not_called()

    def test_retry_does_not_overwrite_already_filled_fields(self):
        page,ctx,visible,values=self.form()
        fill=ctx.fill.side_effect
        ctx.fill.side_effect=lambda selectors,value:None if 'input[name="lastName"]' in selectors else fill(selectors,value)
        def guided_wait(_page,_ctx,**kwargs):
            values['input[name="firstName"]']='Manual correction'
            ctx.fill.side_effect=fill
            kwargs['tick']()
            self.assertEqual(values['input[name="firstName"]'],'Manual correction')
            ctx.click.assert_called_once()
        with patch.object(provider,'_warte_bis_fertig',side_effect=guided_wait):
            provider.run(page,ctx)

    def test_current_alias_form_checks_availability_then_selects_once(self):
        page,ctx,visible,values=self.form()
        visible.clear();visible.add('input[name="alias"]')
        selected={'alias':'provider.suggestion','domain':'web.de'}
        page.input_value.side_effect=lambda selector,**kwargs:values[selector] if selector in values else selected[selector.removeprefix('#')]
        def guided_wait(_page,_ctx,**kwargs):
            self.assertEqual(values.get('input[name="alias"]'),'test123')
            ctx.click.assert_called_once()
            kwargs['tick']();ctx.click.assert_called_once()
            # Only the provider's positive state for this exact target permits
            # the second click; an unchanged suggestion is not confirmation.
            visible.add('form#alias-step .alias-step__form-element--available input#alias')
            kwargs['tick']();ctx.click.assert_called_once()
            selected['alias']='test123';selected['domain']='gmx.net'
            kwargs['tick']();ctx.click.assert_called_once()
            selected['domain']='web.de'
            kwargs['tick']();self.assertEqual(ctx.click.call_count,2)
            self.assertEqual(ctx.click.call_args.args[0],['form#alias-step button[data-testid="next-button"]'])
            kwargs['tick']();self.assertEqual(ctx.click.call_count,2)
        with patch.object(provider,'_warte_bis_fertig',side_effect=guided_wait):provider.run(page,ctx)

    def test_step_change_replaces_the_old_captcha_hint(self):
        page,ctx,visible,values=self.form();visible.add('text="Ich bin ein Mensch"')
        def guided_wait(_page,_ctx,**kwargs):
            self.assertIn('CAPTCHA',ctx.hinweis.call_args.args[0])
            visible.clear();visible.add('input[name="email"]')
            kwargs['tick']()
            self.assertIn('E-Mail-Adresse',ctx.hinweis.call_args.args[0])
            self.assertNotIn('CAPTCHA',ctx.hinweis.call_args.args[0])
            before=ctx.hinweis.call_count
            kwargs['tick']();self.assertEqual(ctx.hinweis.call_count,before)
        with patch.object(provider,'_warte_bis_fertig',side_effect=guided_wait):provider.run(page,ctx)

    def test_standalone_address_step_hands_off_then_password_filling_resumes(self):
        page,ctx,visible,values=self.form();visible.add('text="Ich bin ein Mensch"')
        def guided_wait(_page,_ctx,**kwargs):
            self.assertIn('CAPTCHA',ctx.hinweis.call_args.args[0])
            visible.clear()
            visible.update({'input[name="postalCode"]','input#postalCode',
                'input[name="city"]','input#city','input[name="street"]','input#street',
                'select[name="country"]','select#country','input[name="gender"]',
                'input#FEMALE','input#MALE','input#OTHER'})
            ctx.fill.reset_mock();ctx.click.reset_mock()
            previous_hints=ctx.hinweis.call_count
            kwargs['tick']()
            self.assertEqual(ctx.hinweis.call_count,previous_hints+1)
            hint=ctx.hinweis.call_args.args[0]
            self.assertIn('Anschrift',hint);self.assertIn('im Fenster',hint)
            self.assertNotIn('CAPTCHA',hint)
            ctx.fill.assert_not_called();ctx.click.assert_not_called()
            kwargs['tick']();kwargs['tick']()
            self.assertEqual(ctx.hinweis.call_count,previous_hints+1)
            ctx.fill.assert_not_called();ctx.click.assert_not_called()
            # Only a human's move to a supported next step resumes automation.
            visible.clear();visible.update({'input[name="password"]','input[name="passwordConfirmation"]'})
            kwargs['tick']()
            self.assertEqual(values['input[name="password"]'],'FAKE_PASSWORD')
            self.assertEqual(values['input[name="passwordConfirmation"]'],'FAKE_PASSWORD')
            ctx.click.assert_called_once()
            self.assertGreater(ctx.hinweis.call_count,previous_hints+1)
            self.assertRegex(ctx.hinweis.call_args.args[0],r'Kennwort|Passwort')
            self.assertNotIn('Anschrift',ctx.hinweis.call_args.args[0])
        with patch.object(provider,'_warte_bis_fertig',side_effect=guided_wait):provider.run(page,ctx)

    def test_country_and_gender_only_step_requests_input_without_submit(self):
        page,ctx,visible,_=self.form();visible.add('text="Ich bin ein Mensch"')
        def guided_wait(_page,_ctx,**kwargs):
            self.assertIn('CAPTCHA',ctx.hinweis.call_args.args[0])
            visible.clear();visible.update({'select[name="country"]','select#country',
                'input[name="gender"]','input#FEMALE','input#MALE','input#OTHER'})
            ctx.fill.reset_mock();ctx.click.reset_mock()
            previous_hints=ctx.hinweis.call_count
            kwargs['tick']()
            self.assertEqual(ctx.hinweis.call_count,previous_hints+1)
            self.assertIn('Anschrift',ctx.hinweis.call_args.args[0])
            self.assertIn('im Fenster',ctx.hinweis.call_args.args[0])
            self.assertNotIn('CAPTCHA',ctx.hinweis.call_args.args[0])
            kwargs['tick']()
            self.assertEqual(ctx.hinweis.call_count,previous_hints+1)
            ctx.fill.assert_not_called();ctx.click.assert_not_called()
        with patch.object(provider,'_warte_bis_fertig',side_effect=guided_wait):provider.run(page,ctx)


@unittest.skipUnless(os.environ.get('SS_RUN_WEBDE_DOM')=='1','local browser fixture disabled')
class WebdeInputDOM(unittest.TestCase):
    def browser(self,playwright):
        from socialscraper.backends.local import LocalBackend
        blocked={'server':'http://127.0.0.1:9'}
        return playwright.chromium.launch(headless=True,channel='chrome',proxy=blocked,
            args=LocalBackend._proxy_args(blocked)+['--disable-background-networking'])

    def test_disabled_hidden_and_readonly_inputs_stay_unchanged(self):
        from patchright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser=self.browser(playwright)
            try:
                page=browser.new_page();page.route('**/*',lambda route:route.abort())
                page.set_content('<input id="disabled" disabled value="original">'
                    '<input id="readonly" readonly value="original">'
                    '<input id="hidden" style="display:none" value="original">'
                    '<fieldset disabled><input id="inherited" value="original"></fieldset>'
                    '<input id="editable" value="original">')
                js_only=SimpleNamespace(evaluate=page.evaluate)
                for control in (page,js_only):
                    for selector in ('#disabled','#readonly','#hidden','#inherited'):
                        with self.subTest(backend='native' if control is page else 'js',selector=selector):
                            self.assertIsNone(_fill(control,[selector],'FAKE_PASSWORD'))
                            self.assertEqual(page.locator(selector).input_value(),'original')
                    self.assertEqual(_fill(control,['#hidden','#editable'],'test-value'),'#editable')
                    self.assertEqual(page.locator('#editable').input_value(),'test-value')
            finally:browser.close()

    def test_real_alias_dom_checks_then_selects_without_submit_loop(self):
        from patchright.sync_api import sync_playwright
        from socialscraper.signup import _click
        with sync_playwright() as playwright:
            browser=self.browser(playwright)
            try:
                page=browser.new_page();page.route('**/*',lambda route:route.abort())
                page.set_content('''<form id="alias-step">
                    <span class="alias-step__form-element alias-step__form-element--available">
                    <label for="alias">E-Mail-Adresse</label>
                    <input id="alias" name="alias" value="provider.suggestion"></span>
                    <select id="domain"><option>web.de</option></select>
                    <button type="submit" data-testid="next-button">Weiter</button></form>
                    <script>
                    document.body.dataset.clicks='0';
                    const input=document.getElementById('alias');
                    input.addEventListener('input',()=>input.parentElement.classList.remove('alias-step__form-element--available'));
                    document.querySelector('form').addEventListener('submit',e=>{
                      e.preventDefault();document.body.dataset.clicks=String(Number(document.body.dataset.clicks)+1);
                      if(document.body.dataset.clicks==='1'){setTimeout(()=>input.parentElement.classList.add('alias-step__form-element--available'),50);}
                    });
                    </script>''')
                class RegistrationPage:
                    url='https://register.web.de/'
                    def __getattr__(self,key):return getattr(page,key)
                ctx=SimpleNamespace(anbieter='web.de',domaene='web.de',adresse='test123@web.de',
                    vorname='Social',nachname='Scraper',passwort='FAKE_PASSWORD',stop=None,
                    hinweis=MagicMock(),warte=page.wait_for_timeout,dismiss=lambda:None,
                    fill=lambda selectors,value:_fill(page,selectors,value),
                    click=lambda selectors:_click(page,selectors))
                def guided_wait(_page,_ctx,**kwargs):
                    self.assertEqual(page.locator('#alias').input_value(),'test123')
                    self.assertEqual(page.locator('body').get_attribute('data-clicks'),'1')
                    page.wait_for_function('document.querySelector(".alias-step__form-element--available")')
                    kwargs['tick']();self.assertEqual(page.locator('body').get_attribute('data-clicks'),'2')
                    kwargs['tick']();kwargs['tick']()
                    self.assertEqual(page.locator('body').get_attribute('data-clicks'),'2')
                with patch.object(provider,'_warte_bis_fertig',side_effect=guided_wait):
                    provider.run(RegistrationPage(),ctx)
            finally:browser.close()

if __name__=='__main__':unittest.main()
