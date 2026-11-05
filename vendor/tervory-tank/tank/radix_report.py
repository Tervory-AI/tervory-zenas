"""Private code-template reports from persisted Radix observations, no model/fetch."""
import html
import re
from collections import Counter
from urllib.parse import quote,urlsplit
from uuid import UUID

from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import Response,JSONResponse
from tenant_db import tenant_path

LABELS=('Tool','Severity','Rule','Location','Explanation','Remediation')  # Existing Radix card labels.


def pdf_available():
    try:
        import weasyprint  # Optional existing installation only, never installed here.
        return hasattr(weasyprint,'HTML')
    except (ImportError,OSError):
        return False


def base_url():
    from device_login import workspace_url
    value=workspace_url()
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
        raise HTTPException(503,'Radix results are unavailable.')
    return value.rstrip('/')+'/chats/api/radix/reports/'


@tenant_path('actor')
async def load(actor,scan_id):
    from radix_repo import findings
    from ghost_routes import call_room
    try:
        data=await findings(actor,scan_id)
    except HTTPException as error:
        if error.status_code!=404:
            raise
        response=await call_room('GET','radix',f'/passive/scans/{scan_id}/report',params={'tenant_id':str(actor.tenant_id)},tenant=actor.tenant_id)
        if response.status_code!=200:
            raise HTTPException(404 if response.status_code==404 else 503,'Radix results are unavailable.') from None
        data=response.json()
        if data.get('tenant_id')!=str(actor.tenant_id):
            raise HTTPException(404,'Radix results are unavailable.')
    if not isinstance(data,dict) or data.get('scan_id')!=str(scan_id) or data.get('status') not in {'complete','failed'} or not isinstance(data.get('findings'),list) or any(not isinstance(row,dict) for row in data.get('findings',[])):
        raise HTTPException(409,'Radix results are unavailable.')
    if data.get('scan_complete') is False or any(item.get('status') in {'partial','skipped'} for item in (data.get('checks') or {}).values() if isinstance(item,dict)):
        data={**data,'status':'failed'}
    return data


def evidence_url(scan_id,ordinal,row,data):
    file=row.get('file')
    if isinstance(file,str):
        commit=data.get('commit','')
        if (data.get('repository')=='Tervory-AI/tervory-tank' and re.fullmatch('[a-f0-9]{40}',commit)
            and not file.startswith(('/','\\')) and '..' not in file.split('/')):
            line=row.get('line')
            return 'https://github.com/Tervory-AI/tervory-tank/blob/'+commit+'/'+quote(file,safe='/')+(f'#L{line}' if type(line)is int and line>0 else '')
    return base_url()+str(scan_id)+'/evidence/'+str(ordinal)


def render(scan_id,data):
    esc=lambda value:html.escape(str(value),quote=True)
    counts=Counter(str(row.get('severity') or 'unknown') for row in data['findings'])
    output=['<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
        '<style>body{font-family:sans-serif;max-width:70em;margin:2em auto;padding:1em}dt{font-weight:bold}dd{margin:0 0 1em;overflow-wrap:anywhere}article{break-inside:avoid;border-top:1px solid #bbb;padding-top:1em}h2{font-size:1.1em;overflow-wrap:anywhere}</style>',
        '</head><body><dl><dt>Scan ID</dt><dd>'+esc(scan_id)+'</dd><dt>Status</dt><dd>'+esc(data['status'])+'</dd></dl><dl>']
    for severity,count in sorted(counts.items()):
        output.append('<dt>'+esc(severity)+'</dt><dd>'+str(count)+'</dd>')
    output.append('</dl>')
    for index,row in enumerate(data['findings']):
        title=row.get('title') or row.get('explanation') or row.get('rule') or row.get('tool') or ''
        output.append('<article><h2>'+esc(title)+'</h2><dl>')
        fields={'Tool':row.get('tool'),'Severity':row.get('severity'),'Rule':row.get('rule'),
            'Location':row.get('file'),'Explanation':row.get('explanation'),'Remediation':row.get('remediation')}
        for label in LABELS:
            value=fields[label]
            if value is None or value=='':
                continue  # No invented finding/remediation sentence.
            text=esc(value)
            if label=='Location':
                if type(row.get('line')) is int and row['line']>0: text+=':'+str(row['line'])
                text='<a href="'+esc(evidence_url(scan_id,index,row,data))+'" rel="noreferrer">'+text+'</a>'
            output.append('<dt>'+label+'</dt><dd>'+text+'</dd>')
        if not row.get('file'):
            output.append('<dt>Location</dt><dd><a href="'+esc(evidence_url(scan_id,index,row,data))+'">'+esc(scan_id)+':'+str(index)+'</a></dd>')
        output.append('</dl></article>')
    output.append('</body></html>')
    text=''.join(output)
    if len(text.encode())>5*1024*1024:
        raise HTTPException(413,'Radix results are unavailable.')
    return text


@tenant_path('actor')
async def prepare(actor,scan_id):
    data=await load(actor,scan_id)
    render(scan_id,data)  # Validate a complete file before offering its download.
    url=base_url()+str(scan_id)
    from trigger_routes import code_receipt
    ledger=await code_receipt(actor.tenant_id,'radix.report',agent='radix')
    return {'url':url+'/html',**({'pdf_url':url+'/pdf'} if pdf_available() else {}),
            'ledger_id':str(ledger),'cost_usd':0}


def build_router():
    from command import Actor,identity,require_tool
    router=APIRouter(prefix='/radix/reports')

    @router.get('')
    async def report(scan_id:UUID,actor:Actor=Depends(identity)):
        require_tool(actor,'radix.report')
        return JSONResponse(await prepare(actor,scan_id),headers={'Cache-Control':'private, no-store'})

    @router.get('/{scan_id}/evidence/{ordinal}')
    async def evidence(scan_id:UUID,ordinal:int,actor:Actor=Depends(identity)):
        require_tool(actor,'radix.report')
        data=await load(actor,scan_id)
        if not 0<=ordinal<len(data['findings']):
            raise HTTPException(404,'Radix results are unavailable.')
        return JSONResponse(data['findings'][ordinal],headers={'Cache-Control':'private, no-store'})

    @router.get('/{scan_id}/{format}')
    async def download(scan_id:UUID,format:str,actor:Actor=Depends(identity)):
        require_tool(actor,'radix.report')
        if format not in {'html','pdf'} or format=='pdf' and not pdf_available():
            raise HTTPException(404,'Radix results are unavailable.')
        text=render(scan_id,await load(actor,scan_id))
        if format=='pdf':
            from weasyprint import HTML
            from starlette.concurrency import run_in_threadpool
            def no_fetch(*args,**kwargs): raise ValueError('external resource refused')
            content=await run_in_threadpool(lambda:HTML(string=text,url_fetcher=no_fetch).write_pdf())
        else:
            content=text.encode()
        return Response(content,media_type='text/html' if format=='html' else 'application/pdf',headers={
            'Content-Disposition':f"attachment; filename*=UTF-8''radix-{scan_id}.{format}",
            'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; sandbox"})
    return router
