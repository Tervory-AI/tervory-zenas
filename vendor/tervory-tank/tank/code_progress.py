"""Stage 2 item 10: stop before paying for a second round without progress."""
from __future__ import annotations

import hashlib
import json
import os

from pydantic import BaseModel, ConfigDict, Field

SETTING = 'ENGINE_CODE_PROGRESS_ENABLED'


def enabled():
    return os.environ.get(SETTING, '').strip() == 'true'


class Progress(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    tree: str = Field(pattern=r'^[a-f0-9]{64}$')
    tests_passed: int = Field(ge=0, le=1_000_000_000)
    build_stage: int = Field(ge=0, le=10000)


def stopped(reason, message):
    return {'status': 'stopped', 'reason': reason, 'message': message + ' No further model call was made.'}


def advance(gate, evidence, results, continuation):
    if not enabled():
        return None
    if evidence is None:
        if continuation:
            return stopped('local_checks_required', 'Run local checks and report their progress before starting a new task.')
        return None
    current = evidence.model_dump()
    previous = gate.get('progress')
    # Tools and outputs are transient. Even a private path survives only as part
    # of this digest, under the existing tenant/user-owned thread transaction.
    outputs = []
    for item in results:
        try:
            output = json.loads(item.output)
        except (ValueError, RecursionError):
            output = item.output
        outputs.append({'name': item.name, 'output': output})
    batch = hashlib.sha256(json.dumps(outputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest() if outputs else None
    track = {'tree': current['tree'], 'tests_passed': current['tests_passed'], 'build_stage': current['build_stage'],
             'stalled': 0, 'batch': batch, 'trees': [current['tree']]}
    gate['progress'] = track
    if previous is None:
        if continuation:
            # No initial baseline was submitted: don't award an unproven gain.
            track['stalled'] = 1
        return None
    track['trees'] = [*previous['trees'], current['tree']][-4:]
    if batch is not None and batch == previous['batch']:
        return stopped('repeated_tool_round', 'The same tool results were submitted in two rounds. Review the local state before starting a new task.')
    trees = track['trees']
    if len(trees) == 4 and trees[0] == trees[2] and trees[1] == trees[3] and trees[0] != trees[1]:
        return stopped('oscillating_changes', 'The work was undone and redone. Return to the last green checkpoint before starting a new task.')
    improved = (current['tests_passed'] > previous['tests_passed'] or current['build_stage'] > previous['build_stage'])
    track['stalled'] = 0 if improved else previous['stalled'] + 1
    if track['stalled'] >= 2:
        return stopped('no_progress', 'Two rounds left no more tests passing and no further build progress. Review the local checks before starting a new task.')
    return None
