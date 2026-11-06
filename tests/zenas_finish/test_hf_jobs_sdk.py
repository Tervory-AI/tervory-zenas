import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]

def test_real_hf_jobs_sdk_runs_launcher_with_fake_network():
    sdk=os.environ['HF_JOBS_PYTHON']
    result=subprocess.run([sdk,str(Path(__file__).with_name('hf_jobs_sdk_probe.py'))],
                          cwd=ROOT,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'launcher dry run passed' in result.stdout

def test_zenas_image_and_isolated_training_sdk_are_pinned():
    assert 'huggingface-hub==1.7.2' in (ROOT/'agents/zenas/requirements.txt').read_text().splitlines()
    patch=(ROOT/'tank-patches/training-sdk.patch').read_text()
    assert 'huggingface-hub==1.7.2' in patch
    assert 'HF_JOBS_PYTHON=/opt/hf-jobs/bin/python' in patch
