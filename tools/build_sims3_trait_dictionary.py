"""Emit a trait-name module from local game metadata, without loading game code.

One-time build dependency: dnfile (https://github.com/malwarefrank/dnfile).
The generated dictionary contains only public trait IDs and identifiers, never
Sim data. The tracker uses the resulting module, not dnfile or game assemblies.
"""
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('assembly', type=Path)
    parser.add_argument('--library-path', type=Path)
    args = parser.parse_args()
    if args.library_path: sys.path.insert(0, str(args.library_path))
    import dnfile
    pe = dnfile.dnPE(str(args.assembly), clr_lazy_load=True)
    definition = next(row for row in pe.net.mdtables.TypeDef
                      if str(row.TypeName) == 'TraitNames' and str(row.TypeNamespace) == 'Sims3.Gameplay.ActorSystems')
    fields = {id(field.row):str(field.row.Name) for field in definition.FieldList}
    values = {}
    for constant in pe.net.mdtables.Constant:
        name = fields.get(id(constant.Parent.row))
        if name:
            identity = int.from_bytes(constant.Value.value, 'little')
            if identity and name != 'Unknown': values[str(identity)] = name
    if len(values) < 100: raise ValueError('The trait-name table is unexpectedly incomplete.')
    print('"""Sims 3 TraitNames constants, generated read-only from gameplay metadata."""')
    print('TRAIT_NAMES = ' + json.dumps(values, indent=2, sort_keys=True))


if __name__ == '__main__': main()
