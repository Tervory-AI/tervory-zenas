"""Read packaged source evidence without changing its original bytes."""
import gzip
from pathlib import Path


def read_fixture(path: Path) -> bytes:
    archived = path if path.suffix == '.gz' else path.with_name(path.name + '.gz')
    if path.is_file() and path.suffix != '.gz':
        return path.read_bytes()
    with gzip.open(archived, 'rb') as source:
        return source.read()
