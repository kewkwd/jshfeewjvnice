"""Select one offered value once, then read the resulting choice back."""
import json
from .errors import FinalError
from .form_guard import remember, require_origin

MONTHS = (('januar', 'january', 'jan'), ('februar', 'february', 'feb'), ('märz', 'march', 'mär', 'mar'),
          ('april', 'apr'), ('mai', 'may'), ('juni', 'june', 'jun'), ('juli', 'july', 'jul'),
          ('august', 'aug'), ('september', 'sep', 'sept'), ('oktober', 'october', 'okt', 'oct'),
          ('november', 'nov'), ('dezember', 'december', 'dez', 'dec'))


def normal(value):
    return ' '.join(str(value).strip().casefold().rstrip('.').split())


def matches(option, expected, *, months=False):
    label = normal(option['label'])
    if isinstance(expected, int):
        if label.isascii() and label.isdecimal():
            return int(label) == expected
        return bool(months and 1 <= expected <= 12 and label in MONTHS[expected - 1])
    aliases = ({'männlich', 'male'}, {'weiblich', 'female'}, {'divers', 'nichtbinär', 'non-binary'},
               {'keine angabe', 'keine angabe machen', 'möchte ich nicht sagen', 'rather not say'},
               {'deutschland', 'germany'}, {'österreich', 'austria'}, {'schweiz', 'switzerland'})
    names = next((group for group in aliases if normal(expected) in group), {normal(expected)})
    return str(option['value']) == str(expected) or label in names


def month_options(options):
    return any(normal(option['label']) in names for option in options for names in MONTHS)


def select(page, selectors, expected, *, check=None):
    from ..signup import _native_controls
    from .input_behavior import click
    require_origin(page)
    if not callable(getattr(page, 'locator', None)):
        raise FinalError('Dieser Browser unterstützt die sichere Auswahleingabe nicht. Bitte einen Browser mit nativen Formularfunktionen wählen.')
    for selector in selectors:
        for control, _index, _count in _native_controls(page, selector, check):
            if check:check()
            try:
                tag = control.evaluate('el => el.tagName', timeout=1500)
                if tag == 'SELECT':
                    options = control.evaluate('''el => [...el.options].map((o,index) => ({index,
                      value:o.value,label:o.label.trim(),disabled:o.disabled || !!o.closest('optgroup:disabled')}))''', timeout=1500)
                    allowed = [option for option in options if not option['disabled']]
                    candidates = [option for option in allowed if matches(option, expected, months=month_options(allowed))]
                    if not candidates:
                        continue  # no mutation; another explicit selector/label may fit
                    if len(candidates) != 1:
                        raise FinalError('Die angebotene Auswahl ist mehrdeutig. Keine Auswahl verändert.')
                    chosen = candidates[0]
                    current = control.evaluate('el => [...el.selectedOptions].map(o => o.index)', timeout=1500)
                    if current != [chosen['index']]:
                        if check:check()
                        require_origin(page)
                        try:
                            control.select_option(index=chosen['index'], timeout=1500)
                        except Exception:
                            if check:check()
                            raise FinalError('Die Antwort auf die Auswahl ist unklar. Keine zweite Auswahl versucht.') from None
                        if check:check()
                    selected = control.evaluate('''el => [...el.selectedOptions].map(o =>
                      ({index:o.index,value:o.value,label:o.label.trim()}))''', timeout=1500)
                    if selected != [{key: chosen[key] for key in ('index', 'value', 'label')}]:
                        raise FinalError('Die übernommene Auswahl stimmt nicht mit dem gespeicherten Wert überein. Kein Weiter-Klick ausgeführt.')
                    remember(page, control, chosen['value'], check=check)
                    return True
                if control.get_attribute('role') != 'combobox':
                    continue
                # Options must belong to this control's declared popup.
                click(page, control, check=check, timeout=3000)
                owned_ids = (control.get_attribute('aria-controls') or control.get_attribute('aria-owns') or '').split()
                if not owned_ids:
                    raise FinalError('Die geöffnete Auswahlliste ist dem Feld nicht eindeutig zugeordnet. Bitte im Fenster prüfen.')
                options = []
                for popup_id in owned_ids:
                    popup = page.locator('[id=' + json.dumps(popup_id) + ']')
                    candidates = popup.get_by_role('option')
                    candidates.first.wait_for(state='visible', timeout=1500)
                    for i in range(min(candidates.count(), 500)):
                        option = candidates.nth(i)
                        if option.is_visible() and option.is_enabled():
                            options.append((option, {'label': option.inner_text().strip(),
                                'value': option.get_attribute('data-value') or option.get_attribute('value') or ''}))
                choices = [(node, data) for node, data in options if matches(data, expected, months=month_options([data for _, data in options]))]
                if len(choices) != 1:
                    raise FinalError('Der gespeicherte Wert ist in der geöffneten Liste nicht eindeutig erkennbar. Bitte im Fenster prüfen.')
                option, chosen = choices[0]
                click(page, option, check=check, timeout=3000)
                actual = control.evaluate('''el => 'value' in el ? el.value :
                    (el.getAttribute('aria-valuetext') || el.innerText).trim()''', timeout=1500)
                if not actual or not (actual == chosen['label'] or chosen['value'] and actual == chosen['value']):
                    raise FinalError('Die ausgewählte Angabe wurde am Feld noch nicht bestätigt. Keine zweite Auswahl versucht.')
                remember(page, control, actual, check=check)
                return True
            except FinalError:
                raise
            except Exception:
                if check:check()
                raise FinalError('Die Auswahleingabe ist nicht eindeutig bestätigt. Bitte den aktuellen Formularstand prüfen; kein weiterer Versuch ausgeführt.') from None
    if check:check()
    return False
