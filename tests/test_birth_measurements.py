import asyncio
import importlib.util
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import automation, birth_measurements as bm
from app.db import Base
from app.models import ChronicleSave, Record, Workspace, ClockLink


def report(sid="101", text="Born 20 inches and 7.5 lbs.", **extra):
    return {"game_sim_id": sid, "birth_certificates": [{
        "source": bm.SOURCE, "baby_game_sim_id": sid, "match": "stored_sim_id",
        "certificate_id": "certificate-" + sid, "text": text, **extra}]}


class MeasurementParsingTests(unittest.TestCase):
    def test_imperial_metric_and_compound_weight(self):
        values = bm.parse("Born 20 inches and 7.5 lbs.")
        self.assertEqual(values["length"]["cm"], 50.8)
        self.assertAlmostEqual(values["weight"]["grams"], 3401.9428, places=4)
        self.assertEqual(bm.parse("Weight 3,4 kg; length 50,8 cm")["weight"]["grams"], 3400)
        compound = bm.parse("Born 20.5 inches and 7 lb 8 oz")
        self.assertAlmostEqual(compound["weight"]["grams"], values["weight"]["grams"])
        self.assertEqual(bm.parse("7.3lbs.")["weight"]["unit"], "lb")

    def test_unknown_ambiguous_or_invalid_is_not_invented(self):
        for text in ("Low birth weight", "Weight: 7.5; length 20", "0 kg", "-3 kg", "999 kg",
                     "3 kg or 4 kg", "7 lb 18 oz", "Height slider 0.8", ""):
            with self.subTest(text=text): self.assertFalse(bm.parse(text))
        for value in ("nan", "inf", "-1", True):
            with self.assertRaises(ValueError): bm.measurement("weight", value, "kg")
        with self.assertRaises(ValueError): bm.measurement("weight", "3", "stones")

    def test_partial_updates_and_manual_protection(self):
        data = bm.updates({}, report())
        merged = bm.updates(data, report(text="Length 51 cm"))["birth_measurements"]
        self.assertEqual(merged["weight"], data["birth_measurements"]["weight"])
        self.assertEqual(merged["length"]["cm"], 51)
        self.assertEqual(bm.updates(data, {}), {})
        self.assertEqual(bm.updates(data, report(text="")), {})
        manual = {"birth_measurements": bm.manual({"birth_weight": "3.2", "birth_weight_unit": "kg"})}
        self.assertEqual(bm.updates(manual, report()), {})
        self.assertEqual(bm.display(manual, "weight"), "3.2 kg")
        self.assertEqual(bm.display(manual, "length"), "Not recorded")

    def test_ids_sources_and_conflicting_certificates(self):
        self.assertIsNone(bm.from_report(report(baby_game_sim_id="202")))
        self.assertIsNone(bm.from_report(report(source="Other object")))
        self.assertIsNone(bm.from_report(report(match="inventory_owner")))
        snapshot = report()
        snapshot["birth_certificates"] += report(text="Weight 2 kg")["birth_certificates"]
        self.assertIsNone(bm.from_report(snapshot))
        snapshot = report()
        snapshot["birth_certificates"] *= 2
        self.assertIsNotNone(bm.from_report(snapshot))

    def test_changed_clock_does_not_invalidate_measurement_form(self):
        data = bm.updates({}, report())
        self.assertEqual(bm.fingerprint(data), bm.fingerprint({**data, "last_game_time": 123}))
        self.assertNotEqual(bm.fingerprint(data), bm.fingerprint({}))


class CertificateReaderTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).parents[1] / "clock_bridge/mod_source/severaludo_clock_sync/birth_certificates.py"
        spec = importlib.util.spec_from_file_location("certificate_test", source)
        self.mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.mod)
        self.infos = {101: NS(sim_id=101, first_name="Baby", last_name="One"),
                      202: NS(sim_id=202, first_name="Baby", last_name="Two")}

    def obj(self, oid, sid=None, name="", description="Born 20 inches and 7.5 lbs."):
        return NS(id=oid, definition=NS(id=9454473550490189272), custom_name=name,
            custom_description=description, stored_sim_info_component=NS(get_stored_sim_id=lambda: sid))

    def scan(self, objects):
        members = [{"game_sim_id": "101"}, {"game_sim_id": "202"}]
        self.mod.attach(members, NS(sim_info_manager=lambda:self.infos,
            object_manager=lambda:objects, inventory_manager=lambda:objects))
        return members

    def test_twins_stored_ids_not_mother_inventory_or_order(self):
        a, b = self.scan({4:self.obj(4,202,description="Born 18 inches and 5.5 lbs."),
                          5:self.obj(5,101)})
        self.assertEqual(len(a["birth_certificates"]), 1)  # both managers expose same object
        self.assertEqual(bm.from_report(a)["weight"]["value"], 7.5)
        self.assertEqual(bm.from_report(b)["weight"]["value"], 5.5)

    def test_home_birth_requires_unique_exact_full_name(self):
        a, b = self.scan({1:self.obj(1,name="Baby One")})
        self.assertEqual(a["birth_certificates"][0]["match"], "unique_full_name")
        self.assertNotIn("birth_certificates", b)
        self.infos[303] = NS(sim_id=303, first_name="Baby", last_name="One")
        self.assertNotIn("birth_certificates", self.scan({1:self.obj(1,name="Baby One")})[0])
        self.assertNotIn("birth_certificates", self.scan({1:self.obj(1,name="Baby")})[0])

    def test_never_fallback_to_name_when_stored_id_different(self):
        self.assertNotIn("birth_certificates", self.scan({1:self.obj(1,999,"Baby One")})[0])

    def test_absent_mod_and_blank_certificate(self):
        self.assertNotIn("birth_certificates", self.scan({})[0])
        self.assertNotIn("birth_certificates", self.scan({1:self.obj(1,101,description="")})[0])
        unrelated = self.obj(1,101); unrelated.definition.id = 123
        self.assertIsNone(self.mod.certificate(unrelated))
        self.mod.attach([{"game_sim_id":"101"}], None)  # loading zone, no services

    def test_personal_inventory_and_bad_object_do_not_break_scan(self):
        class Broken:
            @property
            def definition(self): raise RuntimeError("Unloaded")
        self.infos[101].get_sim_instance = lambda:NS(inventory_component=[Broken(),self.obj(8,202)])
        rows = self.scan({})
        self.assertEqual(rows[1]["birth_certificates"][0]["certificate_id"], "8")


class BirthMeasurementIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine); self.s = Session(self.engine)
        ws = Workspace(name="Measurements"); self.s.add(ws); self.s.flush()
        self.save = ChronicleSave(workspace_id=ws.id,name="Test",global_day=10,start_year=1300,days_per_year=4,settings={})
        self.s.add(self.save); self.s.flush()
        self.baby = Record(save_id=self.save.id,kind="sim",label="Baby One",global_day=10,
            data={"birth_global_day":10,"game_sim_id":"101"})
        self.s.add(self.baby); self.s.flush()

    def tearDown(self):
        self.s.close(); self.engine.dispose()

    def test_intake_preserves_per_baby_measurements_and_older_reports(self):
        automation.reconcile_sim(self.s,self.save,self.baby,report())
        self.assertEqual(self.baby.data["birth_measurements"]["length"]["cm"], 50.8)
        old = self.baby.data["birth_measurements"]
        automation.reconcile_sim(self.s,self.save,self.baby,{"game_sim_id":"101"})
        self.assertEqual(self.baby.data["birth_measurements"],old)
        automation.reconcile_sim(self.s,self.save,self.baby,report(baby_game_sim_id="202"))
        self.assertEqual(self.baby.data["birth_measurements"],old)

    def test_receiver_and_newborn_acceptance_retain_certificate(self):
        from app import clock, main
        from starlette.datastructures import FormData
        link=ClockLink(save_id=self.save.id, token_hash="test-birth-measurements")
        self.s.add(link); self.s.commit()
        snapshot={**report("202"), "first_name":"Baby", "last_name":"Two", "is_baby":True, "age_stage":"BABY"}
        result=clock.receive(self.s,link,{"game_day":20,"hour":12,"minute":0,"household_members":[snapshot]})
        self.assertTrue(result["ok"])
        candidate=self.s.scalar(select(Record).where(Record.kind=="game_candidate",Record.data["action"].as_string()=="new_baby"))
        self.assertEqual(bm.from_report(candidate.data["payload"])["weight"]["value"],7.5)
        @contextmanager
        def session(): yield self.s
        class Request:
            headers={}
            session={}
            async def form(self): return FormData()
        with patch.object(main,"db",session),patch.object(main,"owned_save",return_value=self.save):
            response=asyncio.run(main.accept_automation(Request(),candidate.id))
        self.assertEqual(response.status_code,303)
        baby=self.s.scalar(select(Record).where(Record.kind=="sim",Record.data["game_sim_id"].as_string()=="202"))
        self.assertEqual(baby.data["birth_measurements"]["weight"]["value"],7.5)
        self.assertNotIn("birth_measurements",self.baby.data)

    def test_complete_download_contains_new_compiled_reader_and_help(self):
        from app import clock_bundle
        from io import BytesIO
        from zipfile import ZipFile
        with ZipFile(BytesIO(clock_bundle.build_bundle())) as kit:
            guide=kit.read("SeveralUDOClockSync/README - Install Clock Sync.txt").decode()
            self.assertIn("PANDASAMA BIRTH MEASUREMENTS",guide)
            with ZipFile(BytesIO(kit.read("SeveralUDOClockSync/SeveralUDOClockSync.ts4script"))) as script:
                self.assertIn("severaludo_clock_sync/birth_certificates.pyc",script.namelist())

    def route(self, form):
        from app import main
        @contextmanager
        def session(): yield self.s
        class Request:
            async def form(self): return form
        with patch.object(main,"db",session),patch.object(main,"owned_save",return_value=self.save):
            return asyncio.run(main.edit_birth_measurements(Request(),self.baby.id))

    def test_manual_route_protects_values_but_can_allow_updates(self):
        form = {"measurement_token":bm.fingerprint(self.baby.data),"birth_weight":"3.1","birth_weight_unit":"kg"}
        self.assertEqual(self.route(form).status_code,303)
        automation.reconcile_sim(self.s,self.save,self.baby,report())
        self.assertEqual(self.baby.data["birth_measurements"]["weight"]["value"],3.1)
        form.update(measurement_token=bm.fingerprint(self.baby.data),allow_certificate_updates="on")
        self.route(form)
        automation.reconcile_sim(self.s,self.save,self.baby,report())
        self.assertEqual(self.baby.data["birth_measurements"]["weight"]["value"],7.5)

    def test_manual_route_rejects_bad_units_stale_measurements_and_frozen_sim(self):
        from fastapi import HTTPException
        form={"measurement_token":bm.fingerprint(self.baby.data),"birth_weight":"3","birth_weight_unit":"unknown"}
        with self.assertRaises(HTTPException) as raised: self.route(form)
        self.assertEqual(raised.exception.status_code,400)
        form.update(birth_weight_unit="kg",measurement_token="stale")
        with self.assertRaises(HTTPException) as raised: self.route(form)
        self.assertEqual(raised.exception.status_code,409)
        self.baby.data={**self.baby.data,"infinite_frozen":True}
        form["measurement_token"]=bm.fingerprint(self.baby.data)
        with self.assertRaises(HTTPException) as raised: self.route(form)
        self.assertEqual(raised.exception.status_code,409)

    def test_profile_ui_shows_units_provenance_and_escapes_text(self):
        from app import main
        self.baby.data={**self.baby.data,**bm.updates({},report(text="Born 20 inches and 7.5 lbs. <script>alert(1)</script>"))}
        html=main.templates.env.get_template('_birth_measurements.html').render(sim=self.baby)
        self.assertIn('7.5 lb (3.40 kg)',html)
        self.assertIn('Certificate entry',html)
        self.assertNotIn('<script>alert',html)
        self.assertIn('Save birth measurements',html)
