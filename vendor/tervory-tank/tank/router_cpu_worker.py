#!/usr/bin/env python3
"""One pinned CPU model per process. Input contains no reference labels."""
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
from pathlib import Path
import resource
import socket
import sys
from time import perf_counter


def arch_inputs(tokenizer, prompt):
    # Transformers 5 returns a BatchEncoding when return_dict is explicit.
    encoded = tokenizer.apply_chat_template([{'role':'user','content':prompt}],
        add_generation_prompt=True, return_tensors='pt', return_dict=True)
    if encoded['input_ids'].shape[1] > 1024:
        raise ValueError('Input exceeds CPU token budget')
    return {'input_ids':encoded['input_ids'], 'attention_mask':encoded['attention_mask']}


def main(request, output):
    resource.setrlimit(resource.RLIMIT_CPU, (600, 600))
    resource.setrlimit(resource.RLIMIT_AS, (24 * 1024 ** 3, 24 * 1024 ** 3))
    job = json.loads(Path(request).read_text())
    if len(job['examples']) > 8 or job['model'] not in {'arch', 'routellm'}:
        raise ValueError('Invalid CPU request')
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    if torch.version.cuda is not None:
        raise ValueError('CPU-only Torch required')
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    def offline(*args, **kwargs):
        raise OSError('CPU inference cannot connect to a network')
    socket.socket.connect = offline
    root = Path(job['checkpoint'])
    loaded = perf_counter()
    if job['model'] == 'arch':
        tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True, trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(root, local_files_only=True, trust_remote_code=False,
            use_safetensors=True, torch_dtype=torch.float32).to('cpu').eval()
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tank'))
        import routellm_build
        package = importlib.metadata.distribution('routellm')
        if package.version != '0.2.0':
            raise ValueError('RouteLLM package mismatch')
        source = routellm_build.extract(package.locate_file('routellm/routers/routers.py').read_text())
        path = root / 'routellm_bert.py'
        path.write_text(source)
        spec = importlib.util.spec_from_file_location('_comparison_bert', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        model = module.BERTRouter(str(root))
        model.model.to('cpu').eval()
    load_ms = (perf_counter() - loaded) * 1000
    results = []
    with torch.inference_mode():
        for example in job['examples']:
            started = perf_counter()
            if job['model'] == 'arch':
                # Pinned official card prompt contract, with bounded generation.
                prompt = ('You are a helpful assistant designed to find the best suited route.\n'
                    'You are provided with route description within <routes></routes> XML tags:\n<routes>\n\n'
                    + json.dumps(job['routes']) + '\n\n</routes>\n\n<conversation>\n\n'
                    + json.dumps(example['messages']) + '\n\n</conversation>\n\n'
                    + 'Your task is to decide which route is best suit with user intent on the conversation in <conversation></conversation> XML tags.  Follow the instruction:\n'
                    + '1. If the latest intent from user is irrelevant or user intent is full filled, response with other route {"route": "other"}.\n'
                    + '2. You must analyze the route descriptions and find the best match route for user latest intent.\n'
                    + '3. You only response the name of the route that best matches the user\'s request, use the exact name in the <routes></routes>.\n\n'
                    + 'Based on your analysis, provide your response in the following JSON formats if you decide to match any route:\n{"route": "route_name"}\n')
                inputs = arch_inputs(tokenizer,prompt)
                generated = model.generate(**inputs,max_new_tokens=32,do_sample=False,
                                           pad_token_id=tokenizer.eos_token_id)
                value = tokenizer.decode(generated[0, inputs['input_ids'].shape[1]:], skip_special_tokens=True)
            else:
                prompt = next(m['content'] for m in reversed(example['messages']) if m['role'] == 'user')
                value = float(model.calculate_strong_win_rate(prompt))
            results.append({'id':example['id'], 'value':value, 'classifier_ms':(perf_counter()-started)*1000,
                            'first_after_load':not results})
    versions = {d.metadata['Name'].lower():d.version for d in importlib.metadata.distributions()}
    environment = {'python':sys.version, 'platform':platform.platform(),
        'available_cpus':len(os.sched_getaffinity(0)), 'torch_threads':2,
        'cpu_model':next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), 'unknown')}
    runtime_hash = hashlib.sha256(json.dumps({'versions':versions,'environment':environment},sort_keys=True).encode()).hexdigest()
    Path(output).write_text(json.dumps({'results':results,'load_ms':load_ms,'versions':versions,
        'runtime_environment_sha256':runtime_hash,'environment':environment,'device':'cpu',
        'first_after_load_classifier_ms':results[0]['classifier_ms'] if results else None,
        'warm_classifier_ms':[r['classifier_ms'] for r in results[1:]],
        'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
        'cpu_user_seconds':resource.getrusage(resource.RUSAGE_SELF).ru_utime,
        'cpu_system_seconds':resource.getrusage(resource.RUSAGE_SELF).ru_stime},allow_nan=False))


if __name__ == '__main__':
    main(*sys.argv[1:])
