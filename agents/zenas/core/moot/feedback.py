"""Recorded argument/source comparisons; no model grading or fabricated scores."""
from __future__ import annotations

from copy import deepcopy
from . import hearing


def summarize(state):
    hearing._require(state['phase']=='closed' and state['ruling'] is not None,'closed_ruling_required')
    counsel=[]
    for seat in hearing.SIDES:
        arguments=[turn for turn in state['transcript'] if turn['seat']==seat]
        cited={identity for turn in arguments for identity in turn['authority_ids']}
        addressed={turn['issue'] for turn in arguments}
        durations=[sum(part['end_ms']-part['start_ms'] for part in turn['voice']['segments']) for turn in arguments if turn.get('voice')]
        counsel.append({'seat':seat,
            'arguments':[{key:deepcopy(turn[key]) for key in ('number','text','reasoning','responds_to') if key in turn} for turn in arguments],
            'authorities':[{key:deepcopy(authority[key]) for key in ('id','name','quote','source_url','weight') if key in authority}
                           for authority in state['problem']['authorities'] if authority['id'] in cited],
            'unaddressed_issues':[issue for index,issue in enumerate(state['problem']['issues']) if index not in addressed],
            'questions':[turn['number'] for turn in state['transcript'] if turn['kind']=='question' and turn.get('target')==seat],
            'speech_ms':sum(durations) if durations else None,
            'timing_evidence':'browser_reported' if durations else None})
    return {'revision':state['revision'],'counsel':counsel,'court_holding':state['problem']['real_holding'],
            'ruling':deepcopy(state['ruling']),
            'fairness':{'status':'paired_pending' if state.get('fairness_pair_id') else 'not_applicable',
                        'reading':'The paired hearing is incomplete.' if state.get('fairness_pair_id') else 'Not an AI-against-AI match.'}}


async def screen(store, actor, hearing_id, kind):
    hearing._require(kind in {'bench','citations','feedback'},'invalid_request')
    projected=(await store.project(actor,hearing_id))['hearing']
    # Only the existing authorized projection feeds studio screens; private bench
    # notes and controller identities are never sent to a clean feed.
    turns=[{key:deepcopy(turn[key]) for key in ('number','seat','kind','text','voice') if key in turn} for turn in projected['transcript']]
    result={'kind':kind,'id':projected['id'],'revision':projected['revision'],'title':projected['problem']['title'],
            'phase':projected['phase'],'floor':projected['floor'],'problem':projected['problem'],'transcript':turns,'ruling':projected['ruling'],
            'authorities':[a for a in projected['problem']['authorities'] if a['id'] in projected['citations']],
            'first_action':'Load a locked civil appeal problem to begin.','feedback':None}
    if 'reveal' in projected:
        result['reveal']=projected['reveal']
        state=await store.load(actor,hearing_id)
        saved=deepcopy(state.get('feedback') or summarize(state))
        if state.get('fairness_pair_id'):
            from .fairness import public_evidence
            report=await store.fairness(actor,hearing_id)
            saved['fairness']={'status':report['status'],**public_evidence(report)}
        result['feedback']=saved
    return result
