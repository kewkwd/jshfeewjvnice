"""Fixed preparation phases, without form values, secrets or arbitrary text."""
import json,os
PREFIX='[SS_ANDROID_PHASE] '
LABELS={
 'waiting_guard':'Bitte die Windows-Abfrage zum Verbindungsschutz bestätigen',
 'guard_verified':'Der Verbindungsschutz für das virtuelle Handy ist bereit',
 'starting':'Virtuelles Handy im Hintergrund starten',
 'booting':'Das virtuelle Handy fährt hoch',
 'proxy_check':'Die zugeordnete Internetverbindung des virtuellen Handys prüfen',
 'device_ready':'Das virtuelle Handy ist bereit; die App wird geöffnet',
 'interactive':'Die Geräteansicht ist bereit',
 'stopping':'Das virtuelle Handy und seine Verbindung beenden',
}
def emit(phase,melden):
    if not isinstance(phase,str) or phase not in LABELS:raise ValueError('Unbekannte Android-Phase.')
    melden(PREFIX+json.dumps({'version':1,'phase':phase,'pid':os.getpid()}))
    melden(LABELS[phase]+' …')
def parse(line):
    if not line.startswith(PREFIX) or len(line)>512:return None
    try:data=json.loads(line[len(PREFIX):])
    except (ValueError,TypeError):return None
    if (not isinstance(data,dict) or type(data.get('version')) is not int or data['version']!=1 or
        not isinstance(data.get('phase'),str) or data['phase'] not in LABELS or
        type(data.get('pid')) is not int or data['pid']<=0):return None
    return {k:data[k] for k in ('version','phase','pid')}
