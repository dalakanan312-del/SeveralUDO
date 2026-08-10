from pathlib import Path
import gzip

_IMPL = Path(__file__).with_name('app_impl.py.gz')
with gzip.open(_IMPL, 'rt', encoding='utf-8') as _f:
    _source = _f.read()
exec(compile(_source, '<app_impl.py>', 'exec'), globals(), globals())
