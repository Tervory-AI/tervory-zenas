import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[2]

def test_real_hf_jobs_sdk_runs_launcher_with_fake_network():
    sdk=os.environ['HF_JOBS_PYTHON']
    result=subprocess.run([sdk,str(Path(__file__).with_name('hf_jobs_sdk_probe.py'))],
                          cwd=ROOT,check=False,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'launcher dry run passed' in result.stdout

def test_zenas_image_and_isolated_training_sdk_are_pinned():
    assert 'huggingface-hub==1.7.2' in (ROOT/'agents/zenas/requirements.txt').read_text().splitlines()
    from zenas_finish.test_tank_training_patch import projected
    image=projected('tank/Dockerfile')
    assert 'HF_JOBS_PYTHON=/opt/hf-jobs/bin/python' in image
    assert '--require-hashes -r /tmp/hf-jobs-requirements.lock' in image
    assert projected('tank/training_jobs/requirements.txt').splitlines() == [
        'huggingface-hub==1.7.2', 'asyncpg==0.30.0']
    for filename in ['agents/zenas/Dockerfile','agents/zenas/pool/Dockerfile']:
        image=(ROOT/filename).read_text()
        assert 'COPY zenas/requirements.txt' in image
        assert "assert __version__ == '1.7.2'" in image
        assert 'callable(HfApi.list_jobs_hardware)' in image
        assert 'callable(HfApi.run_uv_job)' in image


@pytest.fixture
def launch_environment(monkeypatch,tmp_path):
    events=tmp_path/'requests.jsonl'
    settings={'HF_JOBS_PYTHON':os.environ['HF_JOBS_PYTHON'],
              'HF_ORG':'placeholder-org','HF_TOKEN':'placeholder-token','HF_TRAIN_MAX_USD':'50',
              'TANK_REGION':'za','HF_HUB_OFFLINE':'0','HF_JOBS_TEST_EVENTS':str(events),
              'HF_TRAIN_BASE_MODEL':'fixture/training-base','HF_TRAIN_BASE_REVISION':'a'*40,
              'PYTHONPATH':str(Path(__file__).with_name('hf_sdk_network'))}
    for name,value in settings.items():monkeypatch.setenv(name,value)
    return events


def assert_hardware_discovered(events):
    assert [json.loads(line) for line in events.read_text().splitlines()]==[
        {'sdk':'1.7.2','method':'GET','path':'/api/jobs/hardware'}]


def test_actual_cli_from_old_sdk_reexecs_pinned_jobs_runtime(launch_environment):
    legacy=os.environ['HF_JOBS_LEGACY_PYTHON']
    old=subprocess.run([legacy,'-c',"from huggingface_hub import HfApi,__version__; assert __version__=='0.34.4'; assert not hasattr(HfApi,'list_jobs_hardware')"],
                       check=False,capture_output=True,text=True,timeout=30)
    assert old.returncode==0,old.stderr
    # Tank's supported entry selects the isolated child before importing Jobs;
    # no obsolete bootstrap is projected into the shipped launcher.
    result=subprocess.run([legacy,str(Path(__file__).with_name('hf_jobs_service_probe.py'))],
                          cwd=ROOT,check=False,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    ready=json.loads(result.stdout.splitlines()[-1])
    assert ready['status']=='ready' and ready['hardware']=='a10g-small'
    assert Decimal(str(ready['cost_usd']))==Decimal('2.4')
    assert_hardware_discovered(launch_environment)


@pytest.mark.asyncio
async def test_service_training_subprocess_calls_real_jobs_sdk(launch_environment):
    import ast
    import asyncio
    from types import SimpleNamespace

    from zenas_finish.test_tank_training_patch import functions
    source=ast.parse((ROOT/'tank/training.py').read_text())
    selected=[node for node in source.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='_training_subprocess']
    namespace=functions(selected,{'os':os,'asyncio':asyncio,'Path':Path,'json':json,
        'sys':SimpleNamespace(executable=os.environ['HF_JOBS_LEGACY_PYTHON']),
        '__file__':str(ROOT/'tank/training.py'),'TRAINING_COMMAND_TIMEOUT_SECONDS':30})
    result=await namespace['_training_subprocess']('--agent','zenas','--minutes','120')
    assert result['status']=='ready' and Decimal(str(result['cost_usd']))==Decimal('2.4')
    assert_hardware_discovered(launch_environment)


@pytest.mark.parametrize('runtime',['old','missing'])
def test_wrong_jobs_runtime_stops_before_network_or_paid_launch(launch_environment,monkeypatch,tmp_path,runtime):
    legacy=os.environ['HF_JOBS_LEGACY_PYTHON']
    monkeypatch.setenv('HF_JOBS_PYTHON',legacy if runtime=='old' else str(tmp_path/'absent-python'))
    result=subprocess.run([legacy,str(Path(__file__).with_name('hf_jobs_service_probe.py'))],
                          cwd=ROOT,check=False,capture_output=True,text=True,timeout=10)
    assert result.returncode!=0
    if runtime=='old':
        assert json.loads(result.stdout)['status']=='Training operation failed; check configuration, budget and provenance.'
    else:
        assert 'FileNotFoundError' in result.stderr
    assert 'AttributeError' not in result.stderr and 'placeholder-token' not in result.stderr
    assert not launch_environment.exists()


def test_single_command_dependencies_are_pinned_in_the_launcher():
    from zenas_finish.test_tank_training_patch import projected
    lock=projected('tank/training_jobs/requirements.lock')
    assert 'huggingface-hub==1.7.2' in lock and 'asyncpg==0.30.0' in lock
    assert '--hash=sha256:' in lock
