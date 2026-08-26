"""Build a compact hosted name dictionary from identifiers already observed locally.

The generated file contains only public localization IDs and labels. It never
copies Sim names, save records, workspace codes, tokens, portraits, or other
personal data.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.game_metadata import _mods_root, _package_localizations, trait_localizations  # noqa: E402


HASH_PATTERN = re.compile(
    r"(?i)(?:hash|localization(?:\s+key)?|string(?:\s+id)?)[\s:#=]*(\d+)"
)

# Profile values reported before the compact dictionary was introduced.
SEED_IDS = {
    102988644, 149372076, 160077728, 160508219, 194303391, 206449329,
    256303844, 271275013, 283794525, 288639268, 325928648, 355425814,
    358284497, 372216920, 407068013, 433148852, 438242865, 494744208,
    526901277, 565444487, 656926678, 693043548, 719430602, 739252487,
    753758147, 800998289, 823568850, 883619215, 927842158, 1004514555,
    1096216538, 1098389379, 1149098177, 1161914267, 1257737068,
    1268351540, 1307595058, 1319128985, 1320404076, 1367865591,
    1377955368, 1422200442, 1424623293, 1437350834, 1469296917,
    1538297200, 1559876043, 1585514296, 1589509073, 1620057823,
    1733857320, 1752565865, 1780032949, 1812528615, 1931651746,
    1958074383, 1976037565, 1999713684, 2026498551, 2037921169,
    2042187911, 2045220620, 2053658442, 2153924508, 2158002604,
    2175762211, 2182590620, 2185179924, 2221416874, 2239644392,
    2243298849, 2281984123, 2295414831, 2316797737, 2354615563,
    2364309712, 2406063591, 2515565335, 2555822724, 2558330589,
    2585929896, 2586107582, 2596400028, 2608118979, 2619730663,
    2641107541, 2694963522, 2712475529, 2741764693, 2766677635,
    2824226952, 2840330162, 2861916042, 2876410247, 2898891466,
    3054711183, 3060102765, 3086964844, 3093731007, 3100611871,
    3102217665, 3122266659, 3124665934, 3130958634, 3133829100,
    3144420271, 3144422643, 3158639654, 3166266065, 3178609587,
    3217991855, 3223891377, 3233395803, 3258635391, 3286810273,
    3326209237, 3373332822, 3374963307, 3540221313, 3629508742,
    3632868738, 3633260284, 3637342097, 3657760546, 3680460894,
    3690990482, 3697386102, 3698702414, 3767702352, 3770158393,
    3792398713, 3875022834, 3912222325, 3920559364, 3924981164,
    3929257854, 3960218842, 3970053104, 3995367241, 4028607521,
    4106189627, 4129320038, 4130086195, 4136254321, 4149873411,
    4157138982, 4166603306, 4211881362, 4265081704, 4276324066,
}

# These base-game flags intentionally have no STBL display text. Their stable
# tuning identifiers are 101389, 101390, and 163783 respectively.
TECHNICAL_FALLBACKS = {
    2515565335: "Had WooHoo",
    3680460894: "Been Kissed",
    4166603306: "Understands Baby",
}


def observed_hashes(database: Path) -> set[int]:
    found = set(SEED_IDS)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT data FROM records WHERE data LIKE ? OR data LIKE ? OR data LIKE ?",
            ("%hash%", "%localization%", "%string id%"),
        )
        for (raw,) in rows:
            found.update(int(value) & 0xFFFFFFFF for value in HASH_PATTERN.findall(str(raw or "")))
    finally:
        connection.close()
    found.discard(0)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "app" / "game_localization_fallbacks.json",
    )
    parser.add_argument(
        "--scan-all-mods",
        action="store_true",
        help="Search every local mod package only for unresolved observed identifiers.",
    )
    args = parser.parse_args()
    catalog = trait_localizations()
    catalog.update(TECHNICAL_FALLBACKS)
    identifiers = observed_hashes(args.database)
    unresolved = identifiers.difference(catalog)
    if unresolved and args.scan_all_mods:
        mods_root = _mods_root()
        packages = mods_root.rglob("*.package") if mods_root.is_dir() else ()
        catalog.update(_package_localizations(packages, unresolved))
    names = {str(key): catalog[key] for key in sorted(identifiers) if catalog.get(key)}
    payload = {
        "version": 1,
        "description": (
            "Compact public Sims localization labels used when the hosted tracker "
            "cannot access a local game installation."
        ),
        "names": names,
    }
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    unresolved = sorted(identifiers.difference(int(key) for key in names))
    print(f"Wrote {len(names)} names; {len(unresolved)} identifiers remain unresolved: {unresolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
