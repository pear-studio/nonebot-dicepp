import sys
from pathlib import Path

# Add src to path so imports work when running from repository root
repo_root = Path(__file__).resolve().parents[1]
src_path = repo_root / 'src' / 'plugins' / 'DicePP'
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

import importlib.util

# Load utils/time.py directly to avoid importing package dependencies
time_py = src_path / 'utils' / 'time.py'
spec = importlib.util.spec_from_file_location('utils_time', str(time_py))
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)  # type: ignore
str_to_datetime = mod.str_to_datetime
datetime_to_str = mod.datetime_to_str

samples = [
    '2025/10/27 16:32:24',
    '2025-10-27 16:32:24',
    '2025_10_27 16:32:24',
    '2025-10-27_16_32_24',
    '2025_10_27_16_32_24',
]

for s in samples:
    try:
        dt = str_to_datetime(s)
        print(s, '=>', datetime_to_str(dt))
    except Exception as e:
        print(s, '=> ERROR:', e)
