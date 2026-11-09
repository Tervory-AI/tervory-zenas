"""Real CPU training proof; the public test-only model is never a serving default."""
import argparse
import json
import math
import os
from pathlib import Path
import sys

SOURCE = 'hf-internal-testing/tiny-random-LlamaForCausalLM'
REVISION = '9fb191250dd56d0ba7ec9785a025ed29c03d5998'
LICENCE = 'unknown; source publishes no licence tag; counsel decides'


def download(destination):
    from huggingface_hub import snapshot_download
    snapshot_download(SOURCE, revision=REVISION, local_dir=destination, allow_patterns=[
        'config.json', 'tokenizer*', 'special_tokens_map.json', 'generation_config.json',
        'model.safetensors', 'pytorch_model.bin'])


def prove(source, output):
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    import torch
    from peft import PeftModel
    from safetensors.torch import load_file
    from transformers import AutoModelForCausalLM, AutoTokenizer
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tank.training_jobs.train import fit
    torch.set_num_threads(2)
    tokenizer = AutoTokenizer.from_pretrained(source, local_files_only=True)
    tokenizer.pad_token = tokenizer.eos_token
    rows = [{'prompt': f'Fixture {i}: extract total. ', 'completion': str(i),
             'source': 'synthetic CI fixture', 'provider': 'Tervory', 'licence': 'counsel pending'} for i in range(6)]
    trainer, metrics = fit(str(source), REVISION, rows[:4], rows[4:], output_dir=str(output),
                           max_length=32, max_steps=1, use_cpu=True, tokenizer=tokenizer)
    assert trainer.state.global_step == 1
    assert math.isfinite(metrics['eval_loss'])
    weights = load_file(output / 'adapter_model.safetensors')
    assert all(torch.isfinite(value).all() for value in weights.values())
    assert any('lora_B' in name and value.abs().sum() > 0 for name, value in weights.items())
    base = AutoModelForCausalLM.from_pretrained(source, local_files_only=True)
    restored = PeftModel.from_pretrained(base, output, local_files_only=True)
    with torch.inference_mode():
        assert torch.isfinite(restored(**tokenizer('Fixture total', return_tensors='pt')).logits).all()
    print(json.dumps({'status': 'passed', 'optimizer_steps': 1, 'adapter_reloaded': True,
        'validation_loss': metrics['eval_loss'], 'source': SOURCE, 'revision': REVISION,
        'licence': LICENCE, 'provider_cost_usd': 0}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('download', 'prove'))
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.operation == 'download':
        download(args.source)
    else:
        if args.output is None:
            parser.error('--output is required for the proof')
        prove(args.source, args.output)
