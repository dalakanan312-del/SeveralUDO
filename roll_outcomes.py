from __future__ import annotations

import re


def _actual_number(value):
    numbers=re.findall(r"-?\d+",str(value or ""))
    return int(numbers[-1]) if numbers else None


def automatic_outcome(actual_roll, bad_results):
    """Return an outcome when a numeric roll can be tested against the bad-result rule."""
    actual=_actual_number(actual_roll)
    rule=str(bad_results or "").strip()
    if actual is None or not rule:
        return None

    bad=False
    for operator,limit in re.findall(r"(<=|>=|<|>)\s*(-?\d+)",rule):
        n=int(limit)
        bad |= {"<":actual<n,"<=":actual<=n,">":actual>n,">=":actual>=n}[operator]
    lowered=rule.casefold()
    for limit in re.findall(r"(-?\d+)\s*(?:or\s+)?(?:less|lower|below|under)",lowered):
        bad |= actual<=int(limit)
    for limit in re.findall(r"(-?\d+)\s*(?:or\s+)?(?:more|higher|above|over)",lowered):
        bad |= actual>=int(limit)
    for left,right in re.findall(r"(-?\d+)\s*[-–—]\s*(-?\d+)",rule):
        lo,hi=sorted((int(left),int(right)))
        bad |= lo<=actual<=hi
    scrubbed=re.sub(r"-?\d+\s*[-–—]\s*-?\d+"," ",rule)
    scrubbed=re.sub(r"(?:<=|>=|<|>)\s*-?\d+"," ",scrubbed)
    standalone={int(n) for n in re.findall(r"(?<![\w])(-?\d+)(?![\w])",scrubbed)}
    bad |= actual in standalone

    if bad:
        labelled=re.findall(r"(-?\d+)(?:\s*[-–—]\s*(-?\d+))?\s*(?::|(?<![<>])=)\s*([^,;]+)",rule)
        for left,right,label in labelled:
            if not right and actual==int(left):
                return label.strip()
            if right and min(int(left),int(right))<=actual<=max(int(left),int(right)):
                return label.strip()
        return "Bad result"
    return "Safe result"
