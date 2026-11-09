"""Small per-box state transactions; only metadata, never webhook/email bodies."""
from contextlib import contextmanager
import json
from pathlib import Path

from .durable_shields import _atomic_write_json
from .safe_files import file_lock, read_json


@contextmanager
def locked_state(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # The lock inode is stable across the atomic replacement of the data file.
    with file_lock(path.with_suffix(path.suffix + '.lock')):
        state = read_json(path) if path.exists() else {}
        if not isinstance(state, dict):
            raise ValueError('Invalid durable state')
        before = json.dumps(state, sort_keys=True)
        yield state
        if json.dumps(state, sort_keys=True) != before:
            _atomic_write_json(path, state)
