"""Small allowlisted progress events; never include form values or proxy URLs."""
import json
import os

PREFIX = '[SS_REGISTRATION_PHASE] '
PROVIDERS = frozenset(('web.de','gmx.net','gmail.com','outlook.com','tiktok','instagram','reddit','x','youtube','google','trustpilot'))
LABELS = {
    'preparing':'Registrierung vorbereiten', 'opening':'Registrierungsseite öffnen',
    'form':'Registrierungsformular bearbeiten', 'waiting_confirmation':'Deine Eingabe ist erforderlich',
    'account_confirmed':'Konto erstellt; Abschlussprüfung folgt', 'checking_mail':'Zugriff auf den Posteingang prüfen',
    'pending_mail':'Konto erstellt; E-Mail-Zugriff noch nicht bestätigt', 'saving':'Geprüfte Kontodaten speichern',
    'complete':'Konto eingerichtet und erfolgreich geprüft',
    'review_required':'Der Auftrag braucht eine Prüfung; das Fenster bleibt vorerst offen',
}


def description(phase, provider):
    mail = provider in ('web.de', 'gmx.net', 'gmail.com', 'outlook.com')
    messages = {
        'preparing': 'Die Registrierung wird mit den gespeicherten Angaben vorbereitet.',
        'opening': 'Die App oder die Registrierungsseite wird geöffnet.',
        'form': 'Die benötigten Angaben werden eingetragen und die nächsten Schritte geprüft.',
        'waiting_confirmation': 'Deine Hilfe ist nötig. Bitte übernimm den angezeigten Schritt im geöffneten Fenster.',
        'account_confirmed': ('Das Konto ist erstellt. Jetzt wird der Zugriff auf den Posteingang geprüft.' if mail else
                              'Das eigene Konto wurde erkannt. Die Kontodaten werden für die Speicherung geprüft.'),
        'checking_mail': 'Es wird geprüft, ob E-Mails mit den gespeicherten Zugangsdaten abgerufen werden können.',
        'pending_mail': 'Das Konto ist erstellt, aber der E-Mail-Zugriff klappt noch nicht. Bitte prüfe das Kennwort und die Freigabe für E-Mail-Programme. Keine neue Registrierung starten.',
        'saving': 'Die geprüften Kontodaten werden auf deinem Computer gespeichert.',
        'complete': ('Fertig: Das Postfach ist eingerichtet, der E-Mail-Zugriff wurde geprüft und die Daten sind gespeichert.' if mail else
                     'Fertig: Das Konto ist bestätigt und die Daten sind gespeichert.'),
        'review_required': 'Der Auftrag wurde angehalten. Das vorhandene Fenster bleibt vorerst offen, damit der letzte Schritt geprüft werden kann.',
    }
    return messages[phase]

def update(ctx, phase):
    provider = getattr(ctx,'anbieter',None) or getattr(ctx,'platform',None)
    if provider not in PROVIDERS or phase not in LABELS:raise ValueError('Unbekannter Registrierungsschritt.')
    if getattr(ctx,'_registration_phase',None)==phase:return
    ctx._registration_phase=phase
    ctx.melden(PREFIX+json.dumps({'version':1,'provider':provider,'phase':phase,'pid':os.getpid()}))

def parse(line):
    if not isinstance(line,str) or not line.startswith(PREFIX) or len(line)>512:return None
    try:data=json.loads(line[len(PREFIX):])
    except (ValueError,TypeError):return None
    if (not isinstance(data,dict) or set(data)!={'version','provider','phase','pid'} or
        type(data['version']) is not int or data['version']!=1 or
        not isinstance(data['provider'],str) or data['provider'] not in PROVIDERS or
        not isinstance(data['phase'],str) or data['phase'] not in LABELS or
        type(data['pid']) is not int or data['pid']<=0):return None
    return data
