# Family Fortunes

Open **Play → Family Fortunes**. This optional minigame runs locally or on the hosted tracker without AI, API credits, or a Clock Sync update.

## Matchmaker’s Table

Choose a household and a negotiation purse. Add named property or business cards if useful; recorded household heirlooms are included automatically. Choose a household Sim and an eligible partner, then drag cash, property, heirlooms, and personal promises into the proposed dowry. Sims must be living, old enough under the save’s scaled marriage-age setting, unpromised, and outside the configured kinship restriction.

There are three scenarios: a separate home, a workshop alliance, and a witnessed engagement. Submit an opening offer to reveal a scenario-specific complication and a new commitment card. The families explain each demand with actual amounts and names. You have three offers in total and must play at least two before reviewing an ending.

An agreed ending creates a betrothal with a proposed marriage Global Day, an unpaid dowry plan, a follow-up on Today/Story Board, and the exact negotiations in Storyline. Unsuccessful talks create a recorded ending and follow-up, but no relationship or dowry. Promised heirlooms cannot be offered again in a second matchmaking agreement while the first promise remains unpaid.

## Inheritance Dispute

Choose one to ten living beneficiaries and divide the purse, entered property, and recorded heirlooms between them, a household reserve, and a debt reserve. Only cash can enter the debt reserve. The succession order, known debts, and unpaid marriage promises affect the demands. Multiple cash promises to the same recipient are added together.

The sealed codicil, disputed creditor bill, and competing claim scenarios each reveal a different complication. After responding, review either an agreed or a contested settlement. Confirmation updates the household’s estate plan while preserving its existing notes, and records the allocations and outstanding objections in Storyline. A current heir is not silently cleared when no selected beneficiary qualifies.

## Controls and safeguards

- Drag with mouse, pen, or touch. Alternatively select a card, then select a destination’s **Place selected card here** button; Tab and Enter also work.
- **Save layout** stores a draft with the active save. **Submit offer** records that round. Changed layouts need another submitted offer before final review.
- **Review ending** shows the records that confirmation will create or update. The third offer is final. **Return to the board** preserves the negotiations.
- **Put this board away** retains its draft history without creating an outcome.
- Ordinary clock reports and unrelated skill changes do not invalidate an ending. Changes to participants’ eligibility, heirloom ownership, affordability, relevant promises, estate plans, or the active branch/checkpoint are checked before confirmation.
- Confirmation is idempotent: retrying it does not duplicate the ending or its records.

All scenarios and negotiations are fictional, player-chosen plans. The tracker does not transfer in-game money or objects, complete a marriage, kill a Sim, or mark a dowry paid. Carry out the agreed actions in The Sims and update the relevant records afterward.

## Verification

The dedicated Python tests use an isolated in-memory database. Run `python -m unittest tests.test_family_fortunes.FamilyFortunesTests` and `node tests/test_family_fortunes_js.cjs`. No installed tracker saves or game files are used.
