"""Separate infrastructure policy. Passive and active HTTPS proxies are unchanged."""
import errno
import json
from pathlib import Path
import socket
import time

import dns.message
import dns.resolver
import dns.rdatatype

from egress import Proxy,routable,ProxyError
from passive_store import domain_name


def top_ports():
    ports=json.loads(Path(__file__).with_name('infra-ports.json').read_text())['tcp']
    if len(ports)!=100 or len(set(ports))!=100 or any(type(p)is not int or not 1<=p<=65535 for p in ports):
        raise ValueError('invalid port policy')
    return frozenset(ports)


def connect_infra(address,port):
    if not routable(address) or port not in top_ports():
        raise ProxyError('destination refused')
    return socket.create_connection((address,port),timeout=10)


def resolve_infra(host):
    values=[]
    for kind in ['A','AAAA']:
        try:
            values.extend(str(row) for row in dns.resolver.resolve(host,kind,lifetime=5,search=False))
        except dns.resolver.NoAnswer:
            pass
    if not values or any(not routable(value) for value in values):
        raise ProxyError('non-public answer')
    return values[0]


def dns_query(payload,domain):
    query=dns.message.from_wire(payload)
    if len(query.question)!=1 or query.opcode()!=0:
        raise ValueError('invalid query')
    q=query.question[0]
    name=domain_name(q.name.to_text().removesuffix('.'))
    if not name.endswith('.'+domain) or q.rdtype not in {dns.rdatatype.A,dns.rdatatype.AAAA}:
        raise ValueError('query outside proven domain')
    return query,name


def resolve_dns(payload,domain):
    query,name=dns_query(payload,domain)
    resolver=dns.resolver.Resolver()
    try:
        answer=resolver.resolve(name,query.question[0].rdtype,search=False,lifetime=5,raise_on_no_answer=False)
        response=dns.message.make_response(query)
        response.answer=answer.response.answer
    except dns.resolver.NXDOMAIN:
        response=dns.message.make_response(query)
        response.set_rcode(3)
    return response.to_wire()


class InfraProxy(Proxy):
    def __init__(self,*,dns_exchange=resolve_dns,**kwargs):
        kwargs.setdefault('connect',connect_infra)
        kwargs.setdefault('resolve',resolve_infra)
        super().__init__(**kwargs)
        self.ports=top_ports()
        self.dns_exchange=dns_exchange
        self.observed=set()
        self.dns_names=set()
        self.dns_questions=set()
        self.refused=0
        self.errors=0

    def response(self,conn,status,body=b''):
        conn.sendall(f'HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()+body)

    def handle(self,conn):
        deadline=time.monotonic()+self._max_duration
        admitted=False
        upstream=None
        try:
            conn.settimeout(self._max_duration)
            head=self._read_headers(conn)
            if head is None:return
            headers,_,body=head.partition(b'\r\n\r\n')
            method,path,*_=headers.split(b'\r\n',1)[0].decode('ascii').split()
            if method=='GET' and path=='/infra/status':
                self.response(conn,'200 OK',json.dumps({'ports':sorted(self.observed),'dns_names':sorted(self.dns_names),
                    'dns_questions':sorted(self.dns_questions),'refused':self.refused,'errors':self.errors}).encode())
                return
            target=None
            if method=='CONNECT' and path.count(':')==1:
                host,port=path.split(':')
                if host==self._verified_host and port.isdigit() and int(port) in self.ports and not body:
                    target=(host,int(port))
            if not target and not (method=='POST' and path=='/infra/dns'):
                self.refused+=1;self._refuse(conn);return
            if self._revoked or not self._active_lease() or not self._admit(False):
                self.refused+=1;self._refuse(conn);return
            admitted=True
            if target:
                pinned=self._resolve(target[0])
                if not routable(pinned):raise ValueError('non-public answer')
                try:
                    upstream=self._connect(pinned,target[1])
                except OSError as error:
                    if error.errno==errno.ECONNREFUSED:
                        self.observed.add(target[1]);self.response(conn,'502 Connection Refused');return
                    raise
                self.observed.add(target[1])
                self.response(conn,'200 Connection Established')
                # -sT measures connect only. Do not forward application payloads
                # or remote banners through this infrastructure-only endpoint.
            else:
                lengths=[line.split(b':',1)[1].strip() for line in headers.split(b'\r\n')[1:] if line.lower().startswith(b'content-length:')]
                if len(lengths)!=1 or not lengths[0].isdigit() or not 0<int(lengths[0])<=4096:
                    raise ValueError('invalid DNS message')
                length=int(lengths[0])
                while len(body)<length:
                    conn.settimeout(max(.001,deadline-time.monotonic()))
                    chunk=conn.recv(length-len(body))
                    if not chunk:raise ValueError('truncated DNS message')
                    body+=chunk
                if len(body)!=length:raise ValueError('invalid DNS length')
                query,name=dns_query(body,self._verified_host)  # Before any resolver request.
                if deadline-time.monotonic()<5:raise TimeoutError('DNS budget')
                output=self.dns_exchange(body,self._verified_host)
                if len(output)>self._max_bytes:raise ValueError('DNS output limit')
                self.dns_names.add(name)
                self.dns_questions.add((name,query.question[0].rdtype))
                self.response(conn,'200 OK',output)
        except (OSError,ValueError,UnicodeError,ProxyError,dns.exception.DNSException):
            self.errors+=1
            try:self.response(conn,'503 Incomplete')
            except OSError:pass
        finally:
            if admitted:self._release(False)
            if upstream:upstream.close()
            conn.close()
