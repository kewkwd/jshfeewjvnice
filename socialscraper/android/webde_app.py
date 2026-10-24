"""Guided WEB.DE APK registration through the owned AVD and local mail verifier."""
import os
import re
import time
from . import avd
from .adb import AdbError
from .avd_session import Session
from .handoff import ManualHandoff
from .preview import InputRejected
from .webde_fields import PACKAGE, _choice_groups, fields, fill_known, form_signature, own_nodes, registration_form
from .webde_steps import advance, blocked, can_advance
from .webde_controls import fill as fill_selections, selections, reveal
from ..net.errors import FinalError
from ..operation import wait


def account_view(tree, address):
    """An exact sole address in the native account drawer, never a mail body."""
    nodes = own_nodes(tree)
    if registration_form(tree) or any(node.get('class', '').endswith('EditText') for node in nodes):
        return False
    drawers = [node for node in nodes if node.get('resource-id') == PACKAGE + ':id/navigation_drawer_container']
    if len(drawers) != 1 or not any(node.get('resource-id') == PACKAGE + ':id/drawer_layout' for node in nodes):
        return False
    visible = set(nodes)
    addresses = set()
    for node in drawers[0].iter('node'):
        if node not in visible:
            continue
        for key in ('text', 'content-desc'):
            value = (node.get(key) or '').strip().casefold()
            if re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
                addresses.add(value)
    return addresses == {address.strip().casefold()}


def run(cfg, ctx, proxy, *, on_review=None, on_confirmed=None):
    ctx.check_cancelled()
    if os.environ.get('SS_ANDROID_HANDOFF') != '1':
        raise FinalError('Die WEB.DE-App-Registrierung benötigt die begleitete Geräteansicht in Profilwerk.')
    if ctx.anbieter != 'web.de' or ctx.mode != 'assisted':
        raise FinalError('Die WEB.DE-App unterstützt derzeit die begleitete Registrierung.')
    if len(ctx.adresse) > 254:
        raise FinalError('Die gewünschte WEB.DE-Adresse ist zu lang.')
    name = avd.name_for(ctx.identity)
    avd.prepare_profile(name)
    with Session(name, proxy, headless=True, stop_requested=ctx.stop,
                 profile_identity=ctx.identity, profile_cfg=cfg) as adb:
        session = adb._socialscraper_avd_session
        adb.input_tempo = ctx.profile_tempo
        if not adb.sh('pm', 'path', PACKAGE, timeout=5).strip().startswith('package:'):
            raise FinalError('Die WEB.DE-APK fehlt im eigenen AVD. Bitte den lokalen APK-Vorrat vervollständigen.')
        if on_review:
            on_review()  # an interrupted app flow must not be retried blindly
        ctx.progress('WEB.DE-App öffnen')
        adb.app_starten(PACKAGE)
        wait(3, ctx.stop)
        ctx.check_cancelled()
        tree = adb.baum(timeout=8)
        native = own_nodes(tree)
        if any(node.get('resource-id') == PACKAGE + ':id/drawer_layout' for node in native):
            raise FinalError('Die WEB.DE-App zeigt bereits einen Postfachbereich. Bestehenden Zugang prüfen '
                             'und als vorhandenes Postfach übernehmen; keine neue Registrierung gestartet.')
        handoff = ManualHandoff(adb, ctx, cfg)
        ctx.android_credentials_uncertain = False
        ctx.password_filled = ctx.password_confirmed_by_user = False
        instructions = (f'Zieladresse: {ctx.adresse}\n'
                        f'Name: {ctx.vorname} {ctx.nachname}\n'
                        f'Geburtsdatum: {ctx.registrierungsdaten["geburtsdatum"]} · Geschlecht: {ctx.person["geschlecht"]}\n'
                        'Eindeutig erkannte leere Felder werden automatisch ausgefüllt. '
                        '„Gespeicherte Felder eintragen“ ist nur eine zusätzliche Hilfe. '
                        'Normale Weiter-Schritte und erforderliche Bedingungen des kostenlosen Postfachs übernimmt Profilwerk. '
                        'Optionale Werbeauswahl bleibt abgewählt; die gespeicherte Cookie-Einstellung gilt weiter. '
                        'Bitte nur den offenen Schritt übernehmen. Sobald ein bekannter Schritt erkennbar ist, geht es automatisch weiter.')
        def fill(tree, command):
            if not registration_form(tree):
                raise InputRejected('Kein eindeutiges WEB.DE-Registrierungsformular.')
            try:
                count = fill_known(adb, ctx) + fill_selections(adb, ctx)
            except AdbError:
                raise InputRejected('Felder nicht vollständig lesbar; Angaben prüfen.') from None
            if not count:
                ctx.hinweis('Keine neuen eindeutig erkannten Felder ausgefüllt. Vorhandene Eingaben bleiben erhalten; offene Felder bitte selbst prüfen.')
        ctx.progress('WEB.DE-Registrierung in der App')
        prepared_forms, failed_forms, attempted = {}, set(), set()
        automatic_until = time.monotonic() + 10
        def new_fields(current):
            stage = form_signature(current)
            keys = (set(fields(current)) | {'choice:' + key for key in _choice_groups(current)}
                    | {'select:' + key for key in selections(current)})
            return stage, keys, bool(stage is not None and stage not in failed_forms
                                     and keys - prepared_forms.get(stage, set()))
        while True:
            ctx.check_cancelled()
            if time.monotonic() >= handoff.deadline:
                raise FinalError('Das Zeitlimit der WEB.DE-Registrierung ist erreicht.')
            tree = adb.baum(timeout=5)
            if account_view(tree, ctx.adresse):
                break
            signature, keys, needs_fill = new_fields(tree)
            if needs_fill and not blocked(tree):
                prepared_forms.setdefault(signature, set()).update(keys)
                try:
                    filled = fill_known(adb, ctx) + fill_selections(adb, ctx)
                    if filled:
                        ctx.melden('WEB.DE: Gespeicherte Angaben wurden in die erkannten Felder eingetragen.')
                except (InputRejected, AdbError):
                    failed_forms.add(signature)  # no automatic repeat after partial input
                    ctx.hinweis('Die aktuelle Seite benötigt deine Prüfung. Eindeutige Eingaben bleiben erhalten; kein automatischer zweiter Füllversuch.')
                automatic_until = time.monotonic() + 10
                tree = adb.baum(timeout=5)
            try:
                moved = advance(adb, ctx, attempted)
            except AdbError:
                moved = False  # an uncertain dispatch is retained in attempted
            if moved:
                automatic_until = time.monotonic() + 10
                wait(.4, ctx.stop)
                continue
            if time.monotonic() < automatic_until:
                wait(.4, ctx.stop)
                continue
            try:
                if reveal(adb, ctx):
                    automatic_until = time.monotonic() + 10
                    continue
            except AdbError:
                ctx.hinweis('Der nächste Formularabschnitt konnte nicht sicher erreicht werden. Bitte die Geräteansicht prüfen.')
            current_signature = form_signature(tree)
            def next_form(current):
                following = form_signature(current)
                return (account_view(current, ctx.adresse)
                        or (following is not None and following != current_signature)
                        or (not blocked(current) and new_fields(current)[2])
                        or can_advance(current, ctx, attempted))
            handoff.wait('mail_registration', next_form, actions={'fill': fill},
                         instructions=instructions, resume_when_ready=True)
            automatic_until = time.monotonic() + 10
        ctx.check_cancelled()
        ctx.account_confirmed = account_view(adb.baum(timeout=5), ctx.adresse)
        if not ctx.account_confirmed:
            raise FinalError('Die Zieladresse wurde nach der WEB.DE-Registrierung nicht erneut bestätigt.')
        if not ctx.password_filled or ctx.android_credentials_uncertain:
            def confirm(tree, command):
                if not account_view(tree, ctx.adresse):
                    raise InputRejected('Das gewünschte WEB.DE-Postfach ist nicht eindeutig geöffnet.')
                value = command.get('value')
                if (not isinstance(value, str) or not 1 <= len(value) <= 128
                        or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                    raise InputRejected('Ungültiges Kennwortformat.')
                ctx.passwort = value
                ctx.password_confirmed_by_user = True
                ctx.android_credentials_uncertain = False
            handoff.wait('mail_password', lambda tree: bool(ctx.password_confirmed_by_user and account_view(tree, ctx.adresse)),
                         actions={'confirm_password': confirm},
                         instructions=f'Bestätigtes Postfach: {ctx.adresse}\nDas Kennwort wird anschließend mit echtem Mailzugriff über denselben Proxy geprüft.',
                         resume_when_ready=True)
        ctx.check_cancelled()
        if not account_view(adb.baum(timeout=5), ctx.adresse):
            raise FinalError('Das gewünschte WEB.DE-Postfach ist vor der Speicherung nicht mehr eindeutig geöffnet.')
        result = {'adresse': ctx.adresse, 'imap': {'host': 'imap.web.de', 'port': 993}, 'avd': name,
                  'hinweis': 'Begleitete WEB.DE-APK-Registrierung; Kontoansicht bestätigt. Der tatsächliche Mailabruf folgt als separate Abschlussprüfung.'}
        if on_confirmed:
            on_confirmed(result, session.proxy)
        return result, session.proxy
