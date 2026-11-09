"""Run Tank's exact subprocess entry from the old CPU-router interpreter."""
import ast
import asyncio
import json
import os
import sys
from pathlib import Path
from types import CodeType, FunctionType

source = Path(__file__).resolve().parents[2] / 'tank/training.py'
tree = ast.parse(source.read_text())
nodes = [node for node in tree.body
         if (isinstance(node, ast.AsyncFunctionDef) and node.name == '_training_subprocess')
         or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
             and target.id == 'TRAINING_COMMAND_TIMEOUT_SECONDS' for target in node.targets))]
namespace = {'__file__': str(source), 'asyncio': asyncio, 'json': json,
             'os': os, 'Path': Path, 'sys': sys}
namespace['TRAINING_COMMAND_TIMEOUT_SECONDS'] = ast.literal_eval(
    next(node.value for node in nodes if isinstance(node, ast.Assign)))
compiled = compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec')
body = next(value for value in compiled.co_consts if isinstance(value, CodeType)
            and value.co_name == '_training_subprocess')
entry = FunctionType(body, namespace, '_training_subprocess')
result = asyncio.run(entry('--agent', 'zenas', '--minutes', '120'))
print(json.dumps(result))
if result.get('status') != 'ready':
    raise SystemExit(1)
