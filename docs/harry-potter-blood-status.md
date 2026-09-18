# Harry Potter blood-status tracking

The tracker uses the recorded mother and father, then each parent's mother and
father. It does not substitute spouses, guardians or adoptive-parent links.
All lookups stay within the current dynasty save, including deceased and frozen
branch relatives. The four-grandparent check is independent of the marriage
kinship-depth preference.

## Automatic classification

- **Pureblood:** all four grandparent positions are confirmed spellcasters.
  Having two magical parents alone is not sufficient.
- **Half-Blood:** magical ability or ancestry is recorded, and at least one
  grandparent is confirmed not to be a spellcaster. Other missing grandparents
  cannot make that family meet the four-spellcaster requirement.
- **Muggle-Born:** a spellcaster with two known non-magical parents, excluding
  Squib parents. A complete four-spellcaster grandparent pedigree takes priority
  under this save rule.
- **Muggle:** explicitly recorded as a Muggle by the player or a birth roll.
- **Unknown:** the available evidence does not establish one of these statuses.
  Missing relatives and missing magical identity are not counted as Muggles.

Squibs retain their ancestry classification, but a Squib grandparent does not
count as a spellcaster. Explicit Squib/Muggle identity takes priority over a
conflicting older game-occult report. Witch/Wizard identity or recorded
Spellcaster occult evidence qualifies; being another occult does not.

The calculation examines four pedigree positions. If two distinct parents share
a grandparent, that relative can legitimately occupy two positions. Self-links
and duplicated parent slots cannot establish a pureblood result.

## Where to find it

The Sim's profile shows the status and an expandable explanation naming each
grandparent and whether their magical identity is known. The read-only Infinite
Decades profile provides the same evidence without changing frozen history.

On **Harry Potter Decades → Magical identity and life**, choose **Automatic ·
four-grandparent ancestry** in Blood status. Choosing a named status is a manual
override, clearly labeled alongside the calculated result. Existing manual
entries are retained. Old automatic birth-roll guesses are recalculated.

Automatic scheduling refreshes active Sims when parent links or magical identity
change; HP birth rolls use the same calculation. The HP-04 and master automation
switches control automatic persistence. Read-only ancestry explanations do not
alter records. Explicit form edits can select automatic calculation even while
general automation is paused. Frozen relatives are evidence only; their records
are never rewritten by the refresh.

This is a tracker-only feature: no new game mod, schema migration or live-save
rewrite is required. It is included in the pending tracker source update, not
yet installed or deployed.
