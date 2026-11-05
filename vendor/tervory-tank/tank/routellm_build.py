"""Package upstream BERT unchanged, excluding unrelated paid-router imports.

RouteLLM 0.2.0 constructs OpenAI() while importing its other router implementations.
Select the original BERT class and its two base definitions from the verified
package, byte-for-byte. This is a build adapter, not a routing implementation.
"""
from __future__ import annotations

import ast
import hashlib
from importlib.metadata import distribution
from pathlib import Path
import sys
import urllib.request

REVISION = "86237e3df400762178ea98379477b8296e66d5e4"
SOURCE_SHA256 = "2393da77f9049970f57dd6d8eada3cc68e99506b2a547e7662e7d2d56b5065b8"
WEIGHTS_SHA256 = "e2a92240bad1c7fb9befbf021a62fad5feb1e2a3990d53a28d160a39bb1cac7b"
FILES = (
    "config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
    "special_tokens_map.json", "sentencepiece.bpe.model", "LICENSE",
)


def extract(source: str) -> str:
    if hashlib.sha256(source.encode()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Unexpected RouteLLM source; review the dependency pin")
    lines = source.splitlines(keepends=True)
    selected = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in {"Router", "BERTRouter", "no_parallel"}:
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)]) - 1
            selected.append("".join(lines[start:node.end_lineno]))
    if len(selected) != 3:
        raise ValueError("RouteLLM BERT definitions missing")
    return (
        "# Generated from the hash-pinned RouteLLM 0.2.0 package; Apache-2.0.\n"
        "import abc\nimport numpy as np\nimport torch\n"
        "from transformers import AutoModelForSequenceClassification, AutoTokenizer\n\n"
        + "\n\n".join(selected) + '\nNAME_TO_CLS = {BERTRouter: "bert"}\n'
    )


def build(target: Path) -> None:
    package = distribution("routellm")
    if package.version != "0.2.0":
        raise ValueError("RouteLLM version differs from the reviewed pin")
    target.mkdir(parents=True, exist_ok=True)
    source = package.locate_file("routellm/routers/routers.py").read_text()
    (target / "routellm_bert.py").write_text(extract(source))
    license_file = next(f for f in package.files if str(f).endswith("/LICENSE"))
    (target / "RouteLLM.LICENSE").write_bytes(package.locate_file(license_file).read_bytes())
    checkpoint = target / "checkpoint"
    checkpoint.mkdir(exist_ok=True)
    for name in FILES:
        destination = checkpoint / name
        url = f"https://huggingface.co/routellm/bert_gpt4_augmented/resolve/{REVISION}/{name}"
        # The revision is immutable. Only safetensors weights are included; no
        # optimizer pickle, training code or remote execution is downloaded.
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
                digest.update(block)
        if name == "model.safetensors" and digest.hexdigest() != WEIGHTS_SHA256:
            raise ValueError("RouteLLM weights checksum mismatch")
        print(f"downloaded {name}: {destination.stat().st_size} bytes", flush=True)
    (target / "REVISION").write_text(REVISION + "\n")


if __name__ == "__main__":
    build(Path(sys.argv[1]))
