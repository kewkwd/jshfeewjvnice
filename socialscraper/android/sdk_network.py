"""Validate the observed official test profile's private NAT/control channels."""
import ipaddress,re,xml.etree.ElementTree as ET

def read(config):
    adapters=[n for n in ET.parse(config).iter() if n.tag.endswith('}Adapter') and n.get('enabled')=='true']
    if len(adapters)!=2 or {n.get('slot') for n in adapters}!={'0','1'}:raise RuntimeError('SDK-Netzadapter unerwartet.')
    ports=[];gateway=None
    for adapter in adapters:
        if any(n.tag.endswith(('}BridgedInterface','}NATNetwork')) for n in adapter):raise RuntimeError('SDK verwendet keine ausschließliche NAT-Verbindung.')
        nat=next((n for n in adapter if n.tag.endswith('}NAT')),None)
        expected={'0':{'172.17.99.0/24','172.16.3.0/24'},'1':{'172.17.100.0/24','172.16.4.0/24'}}[adapter.get('slot')]
        if nat is None:raise RuntimeError('SDK-NAT-Netz fehlt.')
        address,separator,prefix=(nat.get('network') or '').partition('/')
        if address.count('.')==2:address+='.0'
        if not separator:raise RuntimeError('SDK-NAT-Netz hat kein Praefix.')
        network=ipaddress.IPv4Network(address+'/'+prefix)
        if str(network) not in expected:raise RuntimeError('SDK-NAT-Netz unerwartet.')
        if adapter.get('slot')=='1':gateway=str(network.network_address+2)
        dns=next((n for n in nat if n.tag.endswith('}DNS')),None)
        if dns is None or dns.get('use-host-resolver','false')!='false' or dns.get('use-proxy','false')!='false':raise RuntimeError('SDK verwendet ungeprüften Host-DNS.')
        for forwarding in nat:
            if not forwarding.tag.endswith('}Forwarding'):continue
            if adapter.get('slot')!='0' or not (forwarding.get('name') in ('ss_adb','adb','adbd') or re.fullmatch(r'port[0-9]{1,2}',forwarding.get('name') or '')) or forwarding.get('hostip')!='127.0.0.1':raise RuntimeError('SDK-Steuerport ist nicht ausschließlich lokal.')
            if forwarding.get('guestport')=='5555':
                if forwarding.get('proto')!='1':raise RuntimeError('SDK-ADB nutzt kein TCP.')
                ports.append(forwarding.get('hostport'))
    if not ports or len(ports)>2 or any(not p or not p.isdecimal() or not 1<=int(p)<=65535 for p in ports):raise RuntimeError('SDK-ADB-Port fehlt.')
    return '127.0.0.1:'+ports[0],gateway
