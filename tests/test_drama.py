import unittest

from app import drama, storyline, sync
from app.models import ChronicleSave, Record


class DramaDeckTests(unittest.TestCase):
    def save(self, year=1300, packs=()):
        return ChronicleSave(
            id="save", name="Test Chronicle", start_year=year, global_day=1,
            days_per_year=4, settings={"selected_rule_packs": list(packs)},
        )

    def test_active_decks_include_the_current_era_core_and_enabled_addon(self):
        save = self.save(1300, ("harry_potter_decades",))
        ids = {deck["id"] for deck in drama.deck_options(save)}
        self.assertTrue({"auto", "common", "pre-modern", "severaludo", "harry_potter_decades"}.issubset(ids))
        self.assertTrue(any(card["id"] == "owl-at-dusk" for card in drama.cards_for(save, "auto")))

    def test_two_choices_resolve_to_a_nonmechanical_chronicle_payload(self):
        save = self.save(1900)
        ada = Record(id="ada", kind="sim", label="Ada Test", data={})
        house = Record(id="house", kind="household", label="Test House", data={})
        state = drama.draw_state(save, "modern", ada.id, house.id, card_id="public-choice")
        state = drama.choose_branch(save, state, "speak")
        state = drama.choose_ending(save, state, "community")
        resolved = drama.build_state(save, [ada], [house], state)
        payload = drama.scene_data(resolved)
        self.assertEqual(payload["sim_id"], ada.id)
        self.assertEqual(payload["household_id"], house.id)
        self.assertTrue(payload["player_decision"])
        self.assertEqual(payload["mechanical_effects"], "None — this scene is a voluntary chronicle decision.")
        self.assertIn("Ada Test", payload["body"])

    def test_addon_cards_are_not_available_without_their_rule_pack(self):
        save = self.save(1300)
        self.assertFalse(any(card["id"] == "owl-at-dusk" for card in drama.cards_for(save, "auto")))
        with self.assertRaises(ValueError):
            drama.draw_state(save, "harry_potter_decades")

    def test_every_selectable_deck_has_at_least_twenty_cards(self):
        packs = ("harry_potter_decades", "avatar_decades", "game_of_thrones_decades")
        for year in (1300, 1800, 1950):
            for core in ("severaludo", "morbid_ultimate", "classic_decades_2023"):
                save = ChronicleSave(
                    id=f"{year}-{core}", name="Deck coverage", start_year=year,
                    global_day=1, days_per_year=4,
                    settings={"selected_rule_packs": list(packs), "core_ruleset_id": core},
                )
                for option in drama.deck_options(save):
                    self.assertGreaterEqual(
                        option["count"], 20,
                        f"{option['id']} should always offer at least twenty cards",
                    )

    def test_recorded_scene_keeps_its_exact_decisions_in_storyline(self):
        save = self.save(1300)
        scene = Record(
            kind="drama_scene", label="Ada Test — A quiet alliance", global_day=1,
            data={
                "card_title": "The sealed letter",
                "opening": "Ada receives a letter that cannot be answered in public.",
                "branch_label": "Confide in someone trusted",
                "branch_beat": "A confidence is offered to a trusted ally.",
                "ending_title": "A quiet alliance",
                "ending_text": "Ada and a trusted ally keep the matter private.",
            },
        )
        headline, paragraph = storyline._annual_paragraph(save, 1300, [scene], [], [])
        self.assertIn("chosen scene", headline)
        self.assertIn("The sealed letter", paragraph)
        self.assertIn("Confide in someone trusted", paragraph)
        self.assertIn("Ada and a trusted ally keep the matter private", paragraph)
        self.assertNotIn("player-chosen story", paragraph)
        self.assertIn("drama_scene", sync.SYNC_KINDS)


if __name__ == "__main__":
    unittest.main()
