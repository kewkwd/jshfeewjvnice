"""Small selector helpers; callers retain their provider-specific rules."""
from .net.errors import FinalError


def first_selector(page, selectors, *, visible=False):
    """Return the first existing selector, optionally requiring visibility."""
    for selector in selectors:
        try:
            if visible and callable(getattr(page, 'locator', None)):
                matches = page.locator(selector)
                count = matches.count()
                if type(count) is not int or not 0 <= count <= 200:
                    raise FinalError('Die sichtbaren Formularfelder sind nicht sicher lesbar. Bitte den aktuellen Stand prüfen.')
                found = [index for index in range(count) if matches.nth(index).is_visible()]
                if len(found) > 1:
                    # Existence checks (e.g. two CAPTCHA frames) may succeed.
                    # Mutating callers must still resolve this selector through
                    # _native_controls(), which refuses multiple usable targets.
                    return selector
                if found:
                    return selector if count == 1 else f'{selector} >> nth={found[0]}'
                continue
            field = page.query_selector(selector)
            if field and (not visible or field.is_visible()):
                return selector
        except FinalError:
            raise
        except Exception:
            continue
    return None
