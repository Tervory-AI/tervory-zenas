# /// script
# requires-python = ">=3.11"
# dependencies = ["trl==0.29.1", "peft==0.18.1", "transformers==5.3.0", "datasets==4.5.0", "torch==2.10.0", "huggingface-hub==1.7.2", "sentencepiece==0.2.1"]
# ///
"""One multi-task Qwen3-4B LoRA. Evaluation loss is not a gold holdout score."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import re

OUTCOMES = frozenset({'upheld', 'dismissed', 'set aside', 'granted', 'refused', 'settled'})


def _outcome(value):
    if isinstance(value, dict):
        value = value.get('outcome')
    text = ' '.join(str(value or '').casefold().split())
    if text in OUTCOMES:
        return text
    match = re.search(r'"outcome"\s*:\s*"([^"]+)"', text)
    return match.group(1) if match and match.group(1) in OUTCOMES else None


def score_outcomes(rows, answers):
    if len(rows) != len(answers):
        raise ValueError('Every holdout row needs one answer')
    results = []
    for row, answer in zip(rows, answers, strict=True):
        expected = _outcome(row.get('output'))
        if expected is None:
            raise ValueError('Holdout row has no code-extracted outcome')
        predicted = _outcome(answer)
        results.append({'expected': expected, 'predicted': predicted, 'passed': predicted == expected})
    passed = sum(item['passed'] for item in results)
    return {'agreement': passed / len(results) if results else 0.0, 'n': len(results),
            'passed': passed, 'gold_holdout': True, 'method': 'exact_outcome', 'rows': results}


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _numeric(value) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value).strip().replace(',', ''))
    except (TypeError, ValueError):
        return False
    return bool(str(value).strip())


def carries_regional_fact(row: dict) -> bool:
    for blob in (row.get('input'), row.get('output')):
        for node in _walk(blob):
            if isinstance(node.get('currency'), str) and node.get('currency').strip():
                return True
            if 'price' in node and _numeric(node.get('price')):
                return True
            if 'value' in node and _numeric(node.get('value')) and ('date' in node or 'unit' in node):
                return True
    return False


def validate_rows(rows: list[dict]) -> None:
    for row in rows:
        labelled = str(row.get('currency') or '').strip() and str(row.get('unit') or '').strip()
        if (row.get('public') is not True or row.get('client_matter') is not False
                or row.get('scope') != 'platform' or row.get('region') not in ('us-east', 'za')
                or row.get('holdout') is not False or row.get('active') is not True
                or 'tenant_id' in row or not row.get('task_type') or 'input' not in row or 'output' not in row
                or (carries_regional_fact(row) and (not labelled or not str(row.get('batch_id') or '').strip()))):
            raise ValueError('Dataset contains unapproved or private rows')


def shared_rows(groups: list[list[dict]]) -> list[dict]:
    """One model sees every region. Each row keeps its own region and batch."""
    rows = [row for group in groups for row in group]
    validate_rows(rows)
    return rows


def context_length(agent: str) -> int:
    if agent not in ('', 'zenas'):
        raise ValueError('Unsupported training agent')
    return 4096 if agent == 'zenas' else 1024


def load_snapshots(api, org: str, day: str) -> list[dict]:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import RepositoryNotFoundError, EntryNotFoundError
    rows = []
    selected = os.environ.get('TRAIN_AGENT', '')
    agents = (selected,) if selected else ('tank', 'base', 'tiger', 'market_lab', 'zenas', 'radix', 'tisan', 'rig')
    for agent in agents:
        dataset = f'{org}/tervory-public-{agent}'
        try:
            info = api.dataset_info(dataset)
        except RepositoryNotFoundError:
            continue
        if info.private is not True:
            raise ValueError('Dataset repository is not private')
        for region in ('us-east', 'za'):
            try:
                path = hf_hub_download(dataset, f'snapshots/{region}/{day}.jsonl', repo_type='dataset', revision=info.sha)
            except EntryNotFoundError:
                continue
            batch = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
            validate_rows(batch)  # Reject the entire run before constructing the trainer.
            if any(row['region'] != region for row in batch):
                raise ValueError('Snapshot region does not match its rows')
            rows.extend(batch)
    return rows


def load_holdouts(api, org: str, day: str) -> list[dict]:
    from huggingface_hub import hf_hub_download
    rows = []
    dataset = f'{org}/tervory-public-zenas'
    info = api.dataset_info(dataset)
    if info.private is not True:
        raise ValueError('Dataset repository is not private')
    for region in ('us-east', 'za'):
        try:
            path = hf_hub_download(dataset, f'holdouts/{region}/{day}.jsonl', repo_type='dataset', revision=info.sha)
        except Exception as exc:
            if exc.__class__.__name__ == 'EntryNotFoundError':
                continue
            raise
        rows.extend(json.loads(line) for line in Path(path).read_text().splitlines() if line)
    if any(row.get('holdout') is not True or row.get('public') is not True
           or row.get('client_matter') is not False for row in rows):
        raise ValueError('Unsafe holdout dataset')
    return rows


def merge_quantize(model, tokenizer, destination: str = '/tmp/zenas-q4_k_m.gguf') -> str:
    """Merge the adapter, then use a pinned llama.cpp revision for Q4_K_M."""
    merged = '/tmp/merged'
    model.merge_and_unload().save_pretrained(merged, safe_serialization=True)
    tokenizer.save_pretrained(merged)
    source = '/tmp/llama.cpp'
    subprocess.run(['git', 'clone', '--depth', '1', '--branch', 'b12063',
                    'https://github.com/ggml-org/llama.cpp.git', source], check=True)
    subprocess.run(['cmake', '-S', source, '-B', source + '/build', '-DLLAMA_CURL=OFF'], check=True)
    subprocess.run(['cmake', '--build', source + '/build', '--target', 'llama-quantize', '-j2'], check=True)
    f16 = '/tmp/zenas-f16.gguf'
    subprocess.run(['python', source + '/convert_hf_to_gguf.py', merged, '--outfile', f16,
                    '--outtype', 'f16'], check=True)
    subprocess.run([source + '/build/bin/llama-quantize', f16, destination, 'Q4_K_M'], check=True)
    return destination


def main() -> None:
    from datasets import Dataset
    from huggingface_hub import HfApi
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer
    api = HfApi(token=os.environ['HF_TOKEN'])
    org, repo, day = os.environ['HF_ORG'], os.environ['OUTPUT_REPO'], os.environ['TRAIN_DAY']
    if api.model_info(repo).private is not True:
        raise ValueError('Model repository is not private')
    train_agent = os.environ.get('TRAIN_AGENT', '')
    max_length = context_length(train_agent)
    rows = load_snapshots(api, org, day)
    train, validation, seen = [], [], set()
    for row in rows:
        digest = hashlib.sha256(json.dumps(row['input'], sort_keys=True).encode()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        sample = {'prompt': [{'role': 'user', 'content': row['task_type'] + '\n' + json.dumps(row['input'])}],
                  'completion': [{'role': 'assistant', 'content': json.dumps(row['output'])}]}
        (validation if int(digest, 16) % 10 == 0 else train).append(sample)
    if not train or not validation:
        raise ValueError('Training and validation examples are required')
    trainer = SFTTrainer(model='Qwen/Qwen3-4B',
        args=SFTConfig(output_dir='/tmp/lora', num_train_epochs=1, max_length=max_length,
                       per_device_train_batch_size=1, gradient_accumulation_steps=8,
                       gradient_checkpointing=True, bf16=True, report_to='none',
                       logging_strategy='no', save_strategy='no', push_to_hub=False),
        train_dataset=Dataset.from_list(train), eval_dataset=Dataset.from_list(validation),
        peft_config=LoraConfig(r=16, lora_alpha=32, target_modules='all-linear', task_type='CAUSAL_LM'))
    trainer.train()
    metrics = trainer.evaluate()
    trainer.save_model('/tmp/lora')
    Path('/tmp/lora/evaluation.json').write_text(json.dumps({'validation_loss': metrics['eval_loss'],
                                                          'n': len(validation), 'gold_holdout': False}))
    if train_agent == 'zenas':
        holdouts = load_holdouts(api, org, day)
        answers = []
        model, tokenizer = trainer.model, trainer.processing_class
        for row in holdouts:
            prompt = row['task_type'] + '\n' + json.dumps(row['input'])
            inputs = tokenizer(prompt, return_tensors='pt').to(model.device)
            generated = model.generate(**inputs, max_new_tokens=64, do_sample=False)
            answers.append(tokenizer.decode(generated[0][inputs['input_ids'].shape[1]:], skip_special_tokens=True))
        Path('/tmp/lora/holdout.json').write_text(json.dumps(score_outcomes(holdouts, answers)))
        gguf = merge_quantize(model, tokenizer)
    if api.model_info(repo).private is not True:
        raise ValueError('Output privacy changed')
    api.upload_folder(repo_id=repo, folder_path='/tmp/lora', commit_message='Multi-task LoRA and validation loss')
    if train_agent == 'zenas':
        api.upload_file(repo_id=repo, path_or_fileobj=gguf, path_in_repo='zenas-q4_k_m.gguf',
                        commit_message='Merged Q4_K_M model')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Training failed; dataset, model and credential details are withheld.') from None
