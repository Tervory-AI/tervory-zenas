# /// script
# requires-python = ">=3.11"
# dependencies = ["trl==0.29.1", "peft==0.18.1", "transformers==5.10.1", "datasets==5.1.0", "torch==2.10.0", "huggingface-hub==1.31.0", "sentencepiece==0.2.1", "fsspec==2026.7.0"]
# ///
"""Explicitly configured LoRA. Evaluation loss is not a gold holdout score."""
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
    if agent not in ('', 'zenas', 'tiger'):
        raise ValueError('Unsupported training agent')
    return 4096 if agent in ('zenas', 'tiger') else 1024


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
        for region in (('za',) if selected == 'zenas' else ('us-east', 'za')):
            try:
                path = hf_hub_download(dataset, f'snapshots/{region}/{day}.jsonl', repo_type='dataset', revision=info.sha)
            except EntryNotFoundError:
                continue
            batch = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
            validate_rows(batch)  # Reject the entire run before constructing the trainer.
            if selected == 'zenas' and any(
                    row.get('task_type') != 'zenas.reasoning' or _outcome(row.get('output')) is None
                    for row in batch):
                raise ValueError('Zenas training requires code-labelled court outcomes')
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
    for region in ('za',):
        try:
            path = hf_hub_download(dataset, f'holdouts/{region}/{day}.jsonl', repo_type='dataset', revision=info.sha)
        except Exception as exc:
            if exc.__class__.__name__ == 'EntryNotFoundError':
                continue
            raise
        rows.extend(json.loads(line) for line in Path(path).read_text().splitlines() if line)
    if any(row.get('holdout') is not True or row.get('public') is not True
           or row.get('client_matter') is not False or row.get('scope') != 'platform'
           or row.get('active') is not True or 'tenant_id' in row or row.get('region') != 'za'
           or row.get('task_type') != 'zenas.reasoning' or _outcome(row.get('output')) is None
           for row in rows):
        raise ValueError('Unsafe holdout dataset')
    if len({hashlib.sha256(json.dumps(row['input'], sort_keys=True).encode()).hexdigest()
            for row in rows}) < 200:
        raise ValueError('At least 200 distinct court holdouts are required')
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



def price_fields(value):
    from decimal import Decimal, InvalidOperation
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('Price answer must be an object')
    items = value.get('items', [value] if 'price' in value else None)
    if not isinstance(items, list):
        raise ValueError('Price answer needs items')
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Invalid price item')
        fields = {}
        for key in ('price', 'currency', 'availability'):
            if key not in item:
                continue
            raw = item[key]
            if key == 'price':
                try:
                    amount = Decimal(str(raw))
                    if not amount.is_finite() or amount < 0:
                        raise ValueError('Invalid price')
                except InvalidOperation:
                    raise ValueError('Invalid price') from None
                fields[key] = format(amount.normalize(), 'f')
            elif key == 'currency':
                if not isinstance(raw, str) or not re.fullmatch('[A-Za-z]{3}', raw):
                    raise ValueError('Invalid currency')
                fields[key] = raw.upper()
            else:
                if not isinstance(raw, str):
                    raise ValueError('Invalid availability')
                fields[key] = raw
        if not fields:
            raise ValueError('No labelled price fields')
        result.append(fields)
    return result


def score_prices(rows, answers):
    if not rows or len(rows) != len(answers):
        raise ValueError('Every held-out price needs one answer')
    results = []
    for row, answer in zip(rows, answers, strict=True):
        expected = price_fields(row['output'])
        try:
            actual = price_fields(answer)
            # Match each expected item once, projecting only labelled fields.
            remaining = list(actual)
            correct = len(expected) == len(remaining)
            for item in expected:
                match = next((i for i, candidate in enumerate(remaining)
                              if all(candidate.get(key) == value for key, value in item.items())), None)
                if match is None:
                    correct = False
                    break
                remaining.pop(match)
        except (ValueError, TypeError):
            correct = False
        results.append({'passed': correct})
    passed = sum(row['passed'] for row in results)
    return {'method': 'exact_price_fields', 'n': len(rows), 'passed': passed,
            'agreement': passed / len(rows), 'gold_holdout': True,
            'eligible': passed == len(rows), 'rows': results}


def tiger_data(api, org, day, revision=None):
    from datetime import date
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError
    date.fromisoformat(day)
    dataset = f'{org}/tervory-public-tiger'
    info = api.dataset_info(dataset)
    if info.private is not True:
        raise ValueError('Dataset repository is not private')
    groups = {'snapshots': [], 'holdouts': []}
    for kind in groups:
        for region in ('us-east', 'za'):
            try:
                path = hf_hub_download(dataset, f'{kind}/{region}/{day}.jsonl', repo_type='dataset', revision=revision or info.sha)
            except EntryNotFoundError:
                continue
            rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
            for row in rows:
                if (row.get('holdout') is not (kind == 'holdouts') or row.get('region') != region
                        or row.get('task_type') not in ('tiger.price_extract', 'tiger.price_structured', 'tiger.price_read')
                        or not isinstance(row.get('input'), dict) or 'png_base64' in row['input']):
                    raise ValueError('Invalid Tiger dataset')
                validate_rows([{**row, 'holdout': False}])
                price_fields(row['output'])
            groups[kind].extend(rows)
    train, held = groups['snapshots'], groups['holdouts']
    def digest(row):
        return hashlib.sha256(json.dumps(row['input'], sort_keys=True).encode()).hexdigest()
    if not train or not held or {digest(row) for row in train} & {digest(row) for row in held}:
        raise ValueError('Disjoint training and held-out examples are required')
    from urllib.parse import urlsplit, urlunsplit
    def pages(rows):
        return {urlunsplit(urlsplit(row['input']['url'])._replace(query='', fragment=''))
                for row in rows if row['input'].get('url')}
    if pages(train) & pages(held):
        raise ValueError('A held-out page cannot also be trained')
    partitions = {int(digest(row), 16) % 10 == 0 for row in train}
    if partitions != {False, True}:
        raise ValueError('Training and validation examples are required')
    return train, held


def fit(base, revision, train, validation, *, output_dir='/tmp/lora', max_length=1024,
        use_cpu=False, max_steps=-1, tokenizer=None):
    from datasets import Dataset
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer
    trainer = SFTTrainer(model=base,
        args=SFTConfig(output_dir=output_dir, num_train_epochs=1, max_length=max_length,
                       model_init_kwargs={'revision': revision, 'trust_remote_code': False},
                       per_device_train_batch_size=1, gradient_accumulation_steps=8,
                       gradient_checkpointing=True, bf16=not use_cpu, use_cpu=use_cpu,
                       max_steps=max_steps, report_to='none', logging_strategy='no',
                       save_strategy='no', push_to_hub=False),
        train_dataset=Dataset.from_list(train), eval_dataset=Dataset.from_list(validation),
        peft_config=LoraConfig(r=16, lora_alpha=32, target_modules='all-linear', task_type='CAUSAL_LM'),
        **({'processing_class': tokenizer} if tokenizer is not None else {}))
    trainer.train()
    metrics = trainer.evaluate()
    trainer.save_model(output_dir)
    return trainer, metrics


def load_job_configuration():
    """The founder's explicit nonsecret config travels as a private job artifact."""
    if not all(os.environ.get(key, '').strip() for key in ('HF_TOKEN', 'OUTPUT_REPO')):
        return
    from huggingface_hub import HfApi, hf_hub_download
    api = HfApi(token=os.environ['HF_TOKEN'])
    info = api.model_info(os.environ['OUTPUT_REPO'])
    if info.private is not True:
        raise ValueError('Training configuration must be private')
    path = Path(hf_hub_download(os.environ['OUTPUT_REPO'], 'training-config.json', revision=info.sha))
    if path.stat().st_size > 4096:
        raise ValueError('Training configuration is too large')
    config = json.loads(path.read_text())
    if (not {'base_model', 'base_revision'} <= set(config)
            or set(config) - {'base_model', 'base_revision', 'bundle_sha256'}
            or any(not isinstance(value, str) for value in config.values())):
        raise ValueError('Invalid training configuration')
    os.environ['HF_TRAIN_BASE_MODEL'] = config['base_model']
    os.environ['HF_TRAIN_BASE_REVISION'] = config['base_revision']
    if 'bundle_sha256' in config:
        bundle = Path(hf_hub_download(os.environ['OUTPUT_REPO'], 'training-bundle.json', revision=info.sha))
        if bundle.stat().st_size > 256 * 1024 * 1024:
            raise ValueError('Training bundle is too large')
        if hashlib.sha256(bundle.read_bytes()).hexdigest() != config['bundle_sha256']:
            raise ValueError('Training bundle hash mismatch')
        os.environ['TERVOR_TRAIN_BUNDLE_PATH'] = str(bundle)


def load_tervor_bundle(path, product):
    """Standalone GPU copy of the sealed bundle contract; no shared DB access."""
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    source = Path(path)
    if source.is_symlink() or source.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('Invalid training bundle file')
    data = json.loads(source.read_text())
    training, gold = data['training'], data['gold']
    if data['sha256'] != digest([training, gold]):
        raise ValueError('Training bundle integrity check failed')
    for part in (training, gold):
        if part['tenant_id'] != '00000000-0000-0000-0000-000000000001' or part['product'] != product:
            raise ValueError('Training bundle tenant or product mismatch')
    rows, cases = training['rows'], gold['cases']
    if (not 1 <= len(rows) <= 100000 or training['count'] != len(rows) or training['sha256'] != digest(rows)
            or gold['count'] != len(cases) or gold['sha256'] != digest(cases) or gold['holdout'] is not True
            or len(cases) < 200 or len({digest(case['input']) for case in cases}) != len(cases)
            or any(case['holdout'] is not True for case in cases)):
        raise ValueError('Invalid training or gold snapshot')
    held = {digest(case['input']) for case in cases}
    for row in rows:
        if (row['tenant_id'] != training['tenant_id'] or row['product'] != product
                or row['holdout'] is not False or not row['task_type'].startswith(product + '.')
                or row.get('kind') not in ('shadow', 'correction', 'teacher', 'public')
                or len(json.dumps(row).encode()) > 65536
                or any(not isinstance(row.get(key), str) or not row[key].strip()
                       for key in ('source', 'source_ref', 'provider', 'licence'))
                or digest(row['input']) in held):
            raise ValueError('Unsafe training row or gold overlap')
    return rows, cases


def main() -> dict | None:
    missing = [name for name in ('HF_TOKEN', 'HF_ORG', 'OUTPUT_REPO', 'TRAIN_DAY')
               if not os.environ.get(name, '').strip()]
    if missing:
        result = {'status': 'not_configured', 'settings': missing}
        print(json.dumps(result))
        return result
    base, revision = os.environ.get('HF_TRAIN_BASE_MODEL', ''), os.environ.get('HF_TRAIN_BASE_REVISION', '')
    missing = [name for name in ('HF_TRAIN_BASE_MODEL', 'HF_TRAIN_BASE_REVISION') if not os.environ.get(name, '').strip()]
    if missing:
        result = {'status': 'not_configured', 'settings': missing}
        print(json.dumps(result))
        return result
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', base) or not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('Training requires an explicit base model and immutable revision')
    from huggingface_hub import HfApi
    api = HfApi(token=os.environ['HF_TOKEN'])
    org, repo, day = os.environ['HF_ORG'], os.environ['OUTPUT_REPO'], os.environ['TRAIN_DAY']
    if api.model_info(repo).private is not True:
        raise ValueError('Model repository is not private')
    train_agent = os.environ.get('TRAIN_AGENT', '')
    max_length = context_length(train_agent)
    bundle_path = os.environ.get('TERVOR_TRAIN_BUNDLE_PATH')
    if bundle_path:
        rows, bundle_gold = load_tervor_bundle(bundle_path, train_agent)
    elif train_agent == 'tiger':
        rows, tiger_holdouts = tiger_data(api, org, day, revision=os.environ.get('TIGER_DATASET_REVISION'))
    else:
        rows = load_snapshots(api, org, day)
    holdouts = load_holdouts(api, org, day) if train_agent == 'zenas' and not bundle_path else []
    held_hashes = {hashlib.sha256(json.dumps(row['input'], sort_keys=True).encode()).hexdigest()
                   for row in holdouts}
    if any(hashlib.sha256(json.dumps(row['input'], sort_keys=True).encode()).hexdigest()
           in held_hashes for row in rows):
        raise ValueError('Court holdouts overlap the training snapshot')
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
    trainer, metrics = fit(base, revision, train, validation, max_length=max_length)
    Path('/tmp/lora/evaluation.json').write_text(json.dumps({'validation_loss': metrics['eval_loss'],
                                                          'n': len(validation), 'gold_holdout': False}))
    if bundle_path:
        Path('/tmp/lora/holdout.json').write_text(json.dumps({'method': 'tervor_gold_pending',
            'gold_holdout': True, 'n': len(bundle_gold), 'eligible': False}))
    if train_agent == 'tiger' and not bundle_path:
        answers = []
        model, tokenizer = trainer.model, trainer.processing_class
        model.eval()
        for row in tiger_holdouts:
            prompt = [{'role': 'user', 'content': row['task_type'] + '\n' + json.dumps(row['input'])}]
            inputs = tokenizer.apply_chat_template(prompt, tokenize=True, add_generation_prompt=True,
                enable_thinking=False, return_dict=True, return_tensors='pt').to(model.device)
            import torch
            with torch.inference_mode():
                generated = model.generate(**inputs, max_new_tokens=1024, do_sample=False)
            answers.append(tokenizer.decode(generated[0][inputs['input_ids'].shape[1]:], skip_special_tokens=True))
        Path('/tmp/lora/holdout.json').write_text(json.dumps(score_prices(tiger_holdouts, answers)))
    if train_agent == 'zenas' and not bundle_path:
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
    if train_agent == 'zenas' and not bundle_path:
        api.upload_file(repo_id=repo, path_or_fileobj=gguf, path_in_repo='zenas-q4_k_m.gguf',
                        commit_message='Merged Q4_K_M model')


if __name__ == '__main__':
    try:
        load_job_configuration()
        main()
    except Exception:
        raise SystemExit('Training failed; dataset, model and credential details are withheld.') from None
