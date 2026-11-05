"""Offline CLIs in the sealed room; every network operation uses the scoped peer."""
import asyncio
import json
from pathlib import Path
import socket
import struct
import xml.etree.ElementTree as ET

import httpx

from passive_infra_proxy import dns_query,top_ports
from passive_r6 import command,measured
from passive_store import target_url
from passive_tools import PROXY


def nmap_command(domain,directory,config):
    return ['/usr/bin/proxychains4','-q','-f',str(config),'/usr/bin/nmap','--unprivileged','-sT','-Pn','-n',
        '--disable-arp-ping','--max-parallelism','2','--max-retries','0','--host-timeout','600s',
        '-p',','.join(map(str,sorted(top_ports()))),'-oX',str(directory/'nmap.xml'),domain]


def dnsx_command(directory,port):
    return ['/usr/local/bin/dnsx','-l',str(directory/'domains.txt'),'-r',f'tcp:127.0.0.1:{port}',
        '-a','-aaaa','-json','-omit-raw','-resp','-silent','-nc','-t','2','-rl','2','-retry','1','-duc']


async def collect(target,directory,*,run=command,transport=None,proxy_ip=None):
    _,domain=target_url(target)
    directory=Path(directory)
    config=directory/'proxychains.conf'
    peer=proxy_ip or socket.gethostbyname('radix-passive-proxy')  # Fixed internal service, never caller-selected.
    config.write_text('strict_chain\nproxy_dns\nremote_dns_subnet 224\ntcp_read_time_out 30000\ntcp_connect_time_out 30000\n[ProxyList]\nhttp '+peer+' 8082\n')
    names=[prefix+'.'+domain for prefix in ('www','api','mail')]
    (directory/'domains.txt').write_text('\n'.join(names)+'\n')
    findings=[];checks={};bridge_errors=[]
    async with httpx.AsyncClient(trust_env=False,timeout=30,transport=transport) as client:
        async def dns_bridge(reader,writer):
            try:
                async with asyncio.timeout(30):
                    length=struct.unpack('!H',await reader.readexactly(2))[0]
                    if not 0<length<=4096:raise ValueError('query limit')
                    payload=await reader.readexactly(length)
                    dns_query(payload,domain)  # Local and peer enforce the same suffix/type rule.
                    response=await client.post(PROXY+'/infra/dns',content=payload,headers={'Content-Type':'application/dns-message'})
                    if response.status_code!=200 or len(response.content)>65535:raise ValueError('DNS incomplete')
                    writer.write(struct.pack('!H',len(response.content))+response.content)
                    await writer.drain()
            except (OSError,ValueError,TimeoutError,asyncio.IncompleteReadError,httpx.HTTPError):
                bridge_errors.append(True)
            finally:
                writer.close()
                await writer.wait_closed()
        server=await asyncio.start_server(dns_bridge,'127.0.0.1',0)
        try:
            try:
                rc,_,_=await run(nmap_command(domain,directory,config),directory,620)
                if rc:raise ValueError('nmap incomplete')
                path=directory/'nmap.xml'
                if path.stat().st_size>2_000_000:raise ValueError('report limit')
                root=ET.fromstring(path.read_bytes())
                if root.find('runstats/finished') is None or root.find('runstats/finished').get('exit')!='success':
                    raise ValueError('nmap incomplete')
                for port in root.findall('host/ports/port'):
                    number=int(port.get('portid','0'))
                    if number not in top_ports() or port.get('protocol')!='tcp':raise ValueError('port outside policy')
                    state=port.find('state');service=port.find('service')
                    if state is not None and state.get('state')=='open':
                        findings.append(measured('nmap','tcp/'+str(number),domain,
                            {'port':number,'protocol':'tcp','state':'open','service':service.get('name') if service is not None else None},
                            text=service.get('name') if service is not None else None,item='tcp/'+str(number)))
                checks['nmap']={'status':'complete'}
            except (OSError,ValueError,TimeoutError,ET.ParseError):
                checks['nmap']={'status':'partial','reason':'scanner_incomplete'}
            try:
                rc,output,_=await run(dnsx_command(directory,server.sockets[0].getsockname()[1]),directory,120)
                if rc or bridge_errors:raise ValueError('dnsx incomplete')
                for line in output.splitlines():
                    row=json.loads(line)
                    host=row.get('host','')
                    if host not in names:raise ValueError('DNS outside scope')
                    facts={key:row[key] for key in ('host','a','aaaa','rcode','status_code') if key in row}
                    findings.append(measured('dnsx',str(row.get('status_code') or row.get('rcode') or 'DNS'),host,facts,item=host))
                checks['dnsx']={'status':'complete'}
            except (OSError,ValueError,TimeoutError):
                checks['dnsx']={'status':'partial','reason':'scanner_incomplete'}
            try:
                response=await client.get(PROXY+'/infra/status');response.raise_for_status();stats=response.json()
                if set(stats.get('ports',[]))!=set(top_ports()) or stats.get('errors') or stats.get('refused'):
                    checks['nmap']={'status':'partial','reason':'coverage_unconfirmed'}
                if {tuple(q) for q in stats.get('dns_questions',[])}!={(name,kind) for name in names for kind in (1,28)} or stats.get('errors') or stats.get('refused'):
                    checks['dnsx']={'status':'partial','reason':'coverage_unconfirmed'}
            except (httpx.HTTPError,ValueError):
                checks={name:{'status':'partial','reason':'coverage_unconfirmed'} for name in ('nmap','dnsx')}
        finally:
            server.close();await server.wait_closed()
    checks['active']={'status':'complete' if all(c['status']=='complete' for c in checks.values()) else 'partial'}
    for finding in findings:
        data=json.loads(finding['fix']);data['_measured'].update(mode='infra',active=True)
        finding['fix']=json.dumps(data)
    return {'findings':findings,'checks':checks}
