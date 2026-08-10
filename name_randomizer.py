
from __future__ import annotations
from pathlib import Path
import json, random
from functools import lru_cache
ROOT=Path(__file__).resolve().parent
LIBRARY_PATH=ROOT/"name_library.json.gz"
@lru_cache(maxsize=1)
def load_library():
    if not LIBRARY_PATH.exists(): return {'source':{},'datasets':{}}
    import gzip
    with gzip.open(LIBRARY_PATH,'rt',encoding='utf-8') as f:return json.load(f)
def dataset_options():
    lib=load_library(); return [(key,ds.get('label',key.replace('_',' ').title())) for key,ds in lib.get('datasets',{}).items()]
def cultures(dataset_key,require=None):
    ds=load_library().get('datasets',{}).get(dataset_key,{}); out=[]
    for culture,data in ds.get('cultures',{}).items():
        if require and not data.get(require,[]):continue
        out.append(culture)
    return out
def counts(dataset_key,culture):
    data=load_library().get('datasets',{}).get(dataset_key,{}).get('cultures',{}).get(culture,{}); return {k:len(data.get(k,[])) for k in ('male','female','surname')}
def available_surname_sources():
    out=[]
    for dkey,label in dataset_options():
        for culture in cultures(dkey):
            count=counts(dkey,culture).get('surname',0)
            if count:out.append((dkey,culture,label,count))
    return out
def random_first(dataset_key,culture,sex):
    key='male' if str(sex).lower().startswith('m') else 'female'; data=load_library().get('datasets',{}).get(dataset_key,{}).get('cultures',{}).get(culture,{}).get(key,[]); return random.choice(data) if data else None
def random_surname(dataset_key,culture):
    data=load_library().get('datasets',{}).get(dataset_key,{}).get('cultures',{}).get(culture,{}).get('surname',[]); return random.choice(data) if data else None
def generate(dataset_key,culture,sex,count=1,surname_source=None):
    count=max(1,min(int(count),100)); results=[]
    for _ in range(count):
        first=random_first(dataset_key,culture,sex); last=None
        if surname_source: last=random_surname(*surname_source)
        results.append({'first_name':first,'last_name':last,'full_name':' '.join(x for x in [first,last] if x)})
    return results
def source_info():return load_library().get('source',{})
