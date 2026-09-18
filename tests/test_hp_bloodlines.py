import copy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import domain, harry_potter_rules as hp, hp_bloodlines as blood, infinite_dynasty as dynasty, main
from app.models import ChronicleSave, Record
from tests import test_infinite_decades as fixture


class HarryPotterBloodlineTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.InfiniteDecadesTests(); self.f.setUp()
        self.s, self.save = self.f.session, self.f.save
        self.save.settings = {**self.save.settings, "selected_rule_packs": [hp.PACK_ID]}
        self.child, self.mother, self.father = self.f.people[:3]
        self.grandparents = []
        for n in range(4):
            gp = Record(save_id=self.save.id, kind="sim", label=f"Grandparent {n+1}",
                global_day=-100, data={"birth_global_day": -100, "species_occult": "Spellcaster"})
            self.s.add(gp); self.grandparents.append(gp)
        self.s.flush()
        self.mother.data = {"hp_magical_ability": "Witch", "mother_id": self.grandparents[0].id, "father_id": self.grandparents[1].id}
        self.father.data = {"hp_magical_ability": "Wizard", "mother_id": self.grandparents[2].id, "father_id": self.grandparents[3].id}
        self.child.data = {"species_occult": "Spellcaster", "mother_id": self.mother.id, "father_id": self.father.id, "birth_global_day": 1}
        self.s.commit()

    def tearDown(self):
        self.f.tearDown()

    def result(self):
        return blood.classify(self.child, blood.people(self.s, self.save))

    def test_four_spellcaster_grandparents_are_required(self):
        self.assertEqual(self.result()["status"], "Pureblood")
        self.grandparents[3].data = {"species_occult": "Human"}
        result = self.result()
        self.assertEqual(result["status"], "Half-Blood")
        self.assertEqual(result["spellcasters"], 3)
        self.assertIn("Grandparent 4", [r["name"] for r in result["grandparents"]])

    def test_two_magical_parents_alone_do_not_make_pureblood(self):
        self.mother.data = {"hp_magical_ability": "Witch"}
        self.father.data = {"hp_magical_ability": "Wizard"}
        self.assertEqual(self.result()["status"], "Unknown")

    def test_missing_and_unknown_grandparents_are_not_muggles(self):
        self.grandparents[3].data = {}
        result = self.result()
        self.assertEqual((result["status"], result["unknown"]), ("Unknown", 1))
        self.mother.data = {"hp_magical_ability": "Witch"}
        self.assertEqual(self.result()["unknown"], 3)

    def test_confirmed_mixed_ancestry_can_be_known_with_other_gaps(self):
        self.grandparents[0].data = {"hp_magical_ability": "Muggle"}
        self.grandparents[3].data = {}
        self.assertEqual(self.result()["status"], "Half-Blood")

    def test_magical_child_of_two_muggles_is_muggle_born(self):
        self.mother.data = {"hp_magical_ability": "Muggle"}
        self.father.data = {"hp_magical_ability": "Muggle"}
        self.assertEqual(self.result()["status"], "Muggle-Born")
        self.child.data = {**self.child.data, "hp_magical_ability": "Muggle"}
        self.assertEqual(self.result()["status"], "Muggle")

    def test_squibs_retain_ancestry_but_do_not_count_as_spellcasters(self):
        self.child.data = {**self.child.data, "hp_magical_ability": "Squib"}
        self.assertEqual(self.result()["status"], "Pureblood")
        self.grandparents[0].data = {"species_occult": "Spellcaster", "hp_magical_ability": "Squib"}
        self.assertEqual(self.result()["status"], "Half-Blood")
        self.assertFalse(domain._hp_magical(self.grandparents[0]))

    def test_squib_parents_are_not_classified_as_muggle_born(self):
        self.mother.data = {"hp_magical_ability": "Squib"}
        self.father.data = {"hp_magical_ability": "Muggle"}
        self.assertEqual(self.result()["status"], "Unknown")

    def test_deceased_and_frozen_grandparents_count(self):
        self.grandparents[0].data = {**self.grandparents[0].data, "death_confirmed": True}
        self.grandparents[1].data = {**self.grandparents[1].data, "infinite_frozen": True, "infinite_frozen_global_day": 10}
        self.grandparents[1].deleted = True
        self.assertEqual(self.result()["status"], "Pureblood")

    def test_archived_missing_and_foreign_links_do_not_supply_evidence(self):
        self.grandparents[0].deleted = True
        self.assertEqual(self.result()["status"], "Unknown")
        self.grandparents[0].deleted = False
        other = ChronicleSave(workspace_id=self.save.workspace_id, name="Other save")
        self.s.add(other); self.s.flush()
        self.grandparents[0].save_id = other.id
        self.assertEqual(self.result()["status"], "Unknown")
        # Even a caller-supplied mixed map must not cross save boundaries.
        rows = {r.id: r for r in [self.child, self.mother, self.father, *self.grandparents]}
        self.assertEqual(blood.classify(self.child, rows)["status"], "Unknown")

    def test_cycle_and_duplicate_parent_slots_cannot_establish_pureblood(self):
        self.child.data = {**self.child.data, "father_id": self.mother.id}
        self.assertEqual(self.result()["status"], "Unknown")
        self.child.data = {**self.child.data, "father_id": self.father.id}
        self.mother.data = {**self.mother.data, "mother_id": self.child.id}
        self.assertEqual(self.result()["status"], "Unknown")

    def test_other_occults_are_not_spellcasters_and_hybrids_are(self):
        self.grandparents[0].data = {"species_occult": "Vampire"}
        self.assertEqual(self.result()["status"], "Half-Blood")
        self.grandparents[0].data = {"species_occult": "Vampire", "game_occult_types": ["Vampire", "Spellcaster"]}
        self.assertEqual(self.result()["status"], "Pureblood")

    def test_refresh_repairs_legacy_birth_guess_and_is_idempotent(self):
        self.child.data = {**self.child.data, "hp_birth_roll_id": "old-birth-roll", "hp_blood_status": "Pureblood"}
        self.grandparents[3].data = {"species_occult": "Human"}
        self.assertGreater(blood.refresh(self.s, self.save), 0)
        self.assertEqual(self.child.data["hp_blood_status"], "Half-Blood")
        self.assertEqual(self.child.data["hp_blood_status_source"], blood.SOURCE)
        self.assertEqual(blood.refresh(self.s, self.save), 0)
        self.grandparents[3].data = {"species_occult": "Spellcaster"}
        blood.refresh(self.s, self.save)
        self.assertEqual(self.child.data["hp_blood_status"], "Pureblood")

    def test_manual_legacy_entries_are_preserved_and_disagreement_explained(self):
        self.child.data = {**self.child.data, "hp_blood_status": "Half-Blood"}
        blood.refresh(self.s, self.save)
        result = self.result()
        self.assertEqual((result["display"], result["status"], result["manual"]), ("Half-Blood", "Pureblood", True))
        self.child.data = {**self.child.data, **blood.form_updates("auto")}
        blood.refresh(self.s, self.save)
        self.assertEqual(self.child.data["hp_blood_status"], "Pureblood")

    def test_pack_module_and_master_toggles_are_respected(self):
        original = copy.deepcopy(self.child.data)
        self.save.settings = {**self.save.settings, "selected_rule_packs": []}
        self.assertEqual(blood.refresh(self.s, self.save), 0)
        self.save.settings = {**self.save.settings, "selected_rule_packs": [hp.PACK_ID], "automation_enabled": False}
        self.assertEqual(blood.refresh(self.s, self.save), 0)
        self.save.settings = {**self.save.settings, "automation_enabled": True}
        rule = Record(save_id=self.save.id, kind="addon_rule", label="Blood status", data={"code": "HP-04", "rule_pack_id": hp.PACK_ID, "active": False})
        self.s.add(rule); self.s.flush()
        self.assertEqual(blood.refresh(self.s, self.save), 0)
        self.assertEqual(self.child.data, original)

    def test_refresh_never_rewrites_frozen_relatives(self):
        self.f.enable()
        gp = self.grandparents[0]
        self.assertTrue(gp.data["infinite_frozen"])
        before = copy.deepcopy(gp.data)
        blood.refresh(self.s, self.save)
        self.assertEqual(gp.data, before)
        self.assertEqual(self.child.data["hp_blood_status"], "Pureblood")

    def test_birth_roll_uses_grandparents_not_magical_parent_count(self):
        self.grandparents[0].data = {"species_occult": "Human"}
        roll = Record(save_id=self.save.id, kind="roll", global_day=1, label="Birth", data={
            "hp_rule_code": "HP-05", "sim_id": self.child.id, "hp_birth_branch": "magical-parent",
            "hp_parent_ids": [self.mother.id, self.father.id]})
        self.s.add(roll); self.s.flush()
        domain._apply_hp_roll_result(self.s, self.save, roll, 2)
        self.assertEqual(self.child.data["hp_blood_status"], "Half-Blood")
        domain._apply_hp_roll_result(self.s, self.save, roll, 1)
        self.assertEqual(self.child.data["hp_blood_status"], "Half-Blood")
        self.assertEqual(self.child.data["hp_magical_ability"], "Squib")

    def test_birth_roll_preserves_explicit_player_override(self):
        self.child.data = {**self.child.data, **blood.form_updates("Half-Blood")}
        roll = Record(save_id=self.save.id, kind="roll", global_day=1, label="Birth", data={"hp_rule_code": "HP-05", "sim_id": self.child.id, "hp_birth_branch": "magical-parent"})
        self.s.add(roll); self.s.flush()
        domain._apply_hp_roll_result(self.s, self.save, roll, 2)
        self.assertEqual(self.child.data["hp_blood_status"], "Half-Blood")
        self.assertTrue(blood.manual(self.child.data))

    def test_profile_and_hp_page_render_evidence_and_auto_control(self):
        with patch.object(main, "SessionLocal", self.f.sessions):
            client = TestClient(main.app)
            client.post("/saves/select", data={"save_id": self.save.id})
            for url in ("/sims/"+self.child.id, "/p/harry-potter"):
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.text[:500])
                self.assertIn("4/4 spellcaster grandparents", response.text)
                self.assertIn("Maternal grandmother", response.text)
            response = client.get("/p/harry-potter")
            self.assertIn('value="auto"', response.text)
            client.close()

    def test_form_validation(self):
        with self.assertRaises(ValueError): blood.form_updates("Anything")
        self.assertEqual(blood.form_updates("auto")["hp_blood_status_mode"], "auto")
        self.assertEqual(blood.form_updates("Unknown")["hp_blood_status_mode"], "manual")

    def test_editing_grandparent_magic_refreshes_descendant_and_manual_controls(self):
        with patch.object(main, "SessionLocal", self.f.sessions):
            client = TestClient(main.app)
            client.post("/saves/select", data={"save_id": self.save.id})
            response = client.post("/api/harry-potter/sims/"+self.grandparents[0].id,
                data={"magical_ability": "Muggle", "blood_status": "auto"}, follow_redirects=False)
            self.assertEqual(response.status_code, 303, response.text)
            self.s.refresh(self.child)
            self.assertEqual(self.child.data["hp_blood_status"], "Half-Blood")
            response = client.post("/api/harry-potter/sims/"+self.child.id,
                data={"magical_ability": "Wizard", "blood_status": "Unknown"}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.s.refresh(self.child)
            self.assertTrue(blood.manual(self.child.data))
            response = client.post("/api/harry-potter/sims/"+self.child.id,
                data={"magical_ability": "Wizard", "blood_status": "auto"}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.s.refresh(self.child)
            self.assertEqual(self.child.data["hp_blood_status"], "Half-Blood")
            client.close()

    def test_birth_roll_does_not_override_paused_blood_module(self):
        self.s.add(Record(save_id=self.save.id, kind="addon_rule", label="Blood status", data={
            "code": "HP-04", "rule_pack_id": hp.PACK_ID, "active": False}))
        roll = Record(save_id=self.save.id, kind="roll", global_day=1, label="Birth", data={
            "hp_rule_code": "HP-05", "sim_id": self.child.id, "hp_birth_branch": "magical-parent"})
        self.s.add(roll); self.s.flush()
        domain._apply_hp_roll_result(self.s, self.save, roll, 2)
        self.assertNotIn("hp_blood_status", self.child.data)

    def test_non_hp_profile_does_not_display_blood_status(self):
        self.save.settings = {**self.save.settings, "selected_rule_packs": []}
        self.s.commit()
        with patch.object(main, "SessionLocal", self.f.sessions):
            client = TestClient(main.app)
            response = client.get("/sims/"+self.child.id)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("spellcaster grandparents", response.text)
            client.close()

    def test_frozen_profile_displays_calculation_without_rewriting_history(self):
        self.f.enable()
        self.f.capture(index=0)
        before = copy.deepcopy(self.child.data)
        with patch.object(main, "SessionLocal", self.f.sessions):
            client = TestClient(main.app)
            client.post("/saves/select", data={"save_id": self.save.id})
            response = client.get("/sims/"+self.child.id)
            self.assertEqual(response.status_code, 200, response.text[:500])
            self.assertIn("4/4 spellcaster grandparents", response.text)
            client.close()
        self.s.refresh(self.child)
        self.assertEqual(self.child.data, before)

    def test_catalog_upgrades_only_original_text(self):
        old = "Assign Pureblood from fully magical recent ancestry, Half-Blood from mixed magical/Muggle ancestry, Muggle-Born to a magical child of two Muggles, and Muggle to a non-magical child. Squibs retain magical ancestry."
        rule = Record(save_id=self.save.id, kind="addon_rule", label="Blood Status", data={"code": "HP-04", "rule_pack_id": hp.PACK_ID, "rule_text": old})
        self.s.add(rule); self.s.flush()
        hp.sync_pack(self.s, self.save, [hp.PACK_ID])
        self.assertIn("all four grandparent", rule.data["rule_text"])
        rule.data = {**rule.data, "rule_text": "My custom ancestry notes"}
        hp.sync_pack(self.s, self.save, [hp.PACK_ID])
        self.assertEqual(rule.data["rule_text"], "My custom ancestry notes")
