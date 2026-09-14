import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import decade_portraits, tray_scanner
from app.db import Base
from app.models import ChronicleSave, Portrait, Record, Workspace


def person(rid, name="Oberon Black", stage="Age.YOUNGADULT"):
    return SimpleNamespace(id=rid, label=name, data={"game_age_stage":stage})


def photo(i, stage, stamp=10, household="new", name="Oberon Black"):
    first, last = name.split(" ", 1)
    return tray_scanner.TrayPortrait(first, last, i, Path(f"{i}.sgi"), Path(household), stamp, stage)


class TrayMatchingTests(unittest.TestCase):
    def test_same_name_parent_and_child_both_match(self):
        found, ambiguous = tray_scanner.match_portraits(
            [photo(1,"youngadult"), photo(2,"child"), photo(3,"youngadult",1,"old")],
            [person("father"), person("son",stage="Age.CHILD")])
        self.assertEqual({key:value.tray_sim_id for key,value in found.items()}, {"father":1,"son":2})
        self.assertEqual(ambiguous,0)

    def test_same_name_and_stage_stays_ambiguous(self):
        found, ambiguous = tray_scanner.match_portraits(
            [photo(1,"child"),photo(2,"child")], [person("a",stage="child"),person("b",stage="child")])
        self.assertEqual(found,{})
        self.assertEqual(ambiguous,1)

    def test_one_tracker_name_does_not_choose_between_two_tray_people(self):
        found, _ = tray_scanner.match_portraits([photo(1,"child"),photo(2,"child")], [person("a",stage="child")])
        self.assertEqual(found,{})

    def test_unknown_stages_do_not_resolve_duplicate_names(self):
        found, _ = tray_scanner.match_portraits([photo(1,""),photo(2,"")], [person("a"),person("b",stage="child")])
        self.assertEqual(found,{})

    def test_newest_copy_wins_for_unique_name(self):
        found, _ = tray_scanner.match_portraits([photo(1,"youngadult",1,"old"),photo(2,"adult",20)], [person("a")])
        self.assertEqual(found["a"].tray_sim_id,2)

    def test_real_cas_age_flags(self):
        def varint(v):
            out=bytearray()
            while v>127: out.append((v&127)|128);v>>=7
            out.append(v);return bytes(out)
        def msg(k,v): return varint(k*8+2)+varint(len(v))+v
        for flag, stage in [(1,"newborn"),(2,"toddler"),(4,"child"),(8,"teen"),(16,"youngadult"),(32,"adult"),(64,"elder"),(128,"infant")]:
            sim=b"\x09"+(123).to_bytes(8,"little")+msg(5,b"One")+msg(6,b"Black")+varint(8*8)+varint(flag)
            payload=msg(1,msg(6,sim))
            raw=b"\0"*12+len(payload).to_bytes(4,"little")+payload
            self.assertEqual(tray_scanner._household_sims(raw),[("One","Black",123,stage)])


class DecadeArchiveTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session=Session(self.engine)
        workspace=Workspace(name="Portrait tests")
        self.session.add(workspace);self.session.flush()
        self.save=ChronicleSave(workspace_id=workspace.id,name="Black HP",global_day=52)
        self.session.add(self.save);self.session.flush()
        self.home=Record(save_id=self.save.id,kind="household",label="Black",data={"active":True})
        self.session.add(self.home);self.session.flush()
        self.sims=[]
        for name,stage,death in [("Oberon Black","youngadult",None),("Oberon Black","child",None),("Edgar Black","toddler",52),("Galia Black","child",52)]:
            sim=Record(save_id=self.save.id,kind="sim",label=name,global_day=1,data={
                "current_household_id":self.home.id,"birth_global_day":1,"game_age_stage":stage,
                "death_global_day":death,"game_was_dead":death is not None})
            self.session.add(sim);self.sims.append(sim)
        self.session.flush()
        raw=io.BytesIO();Image.new("RGB",(32,32),"gold").save(raw,"PNG");self.raw=raw.getvalue()
        self.photos=[photo(1,"youngadult"),photo(2,"child"),photo(3,"toddler",name="Edgar Black")]

    def tearDown(self):
        self.session.close();self.engine.dispose()

    def test_repaired_archive_keeps_original_day_and_later_deceased_members(self):
        with patch.object(decade_portraits,"discover_portraits",return_value=self.photos), patch.object(Path,"read_bytes",return_value=self.raw), patch.object(decade_portraits,"decode_sgi",return_value=self.raw):
            result=decade_portraits.save_from_tray(self.session,self.save,989,"#2b2118",capture_day=45,import_individual=False)
        plate=result["records"][0]
        self.assertEqual(plate.data["member_ids"],[sim.id for sim in self.sims[:3]])
        self.assertEqual(plate.data["missing_member_names"],["Galia Black"])
        self.assertEqual((plate.global_day,result["snapshot"].global_day,self.save.global_day),(45,45,52))
        self.assertEqual(result["snapshot"].data["member_count"],3)
        self.assertEqual(result["ambiguous"],0)
        self.assertIsNotNone(self.session.scalar(select(Portrait).where(Portrait.record_id==plate.id)))

    def test_targeted_scan_does_not_hide_same_name_people(self):
        self.sims[0].data={**self.sims[0].data,"game_age_stage":"child"}
        with patch.object(tray_scanner,"discover_portraits",return_value=[photo(1,"child")]):
            result=tray_scanner.import_portraits(self.session,self.save,target_record_id=self.sims[0].id)
        self.assertEqual(result["matched"],0)
        self.assertEqual(result["ambiguous"],1)

    def test_import_uses_tray_photo_age_and_protects_manual_images(self):
        manual=Portrait(save_id=self.save.id,record_id=self.sims[0].id,stage="youngadult",image=self.raw,mime_type="image/png",source="upload")
        self.session.add(manual);self.session.flush()
        with patch.object(tray_scanner,"discover_portraits",return_value=self.photos), patch.object(Path,"read_bytes",return_value=self.raw), patch.object(tray_scanner,"decode_sgi",return_value=self.raw):
            result=tray_scanner.import_portraits(self.session,self.save)
        self.assertEqual(result["protected"],1)
        self.assertEqual(manual.image,self.raw)
        stages=list(self.session.scalars(select(Portrait.stage).where(Portrait.record_id==self.sims[2].id)))
        self.assertEqual(stages,["toddler"])


if __name__=="__main__":
    unittest.main()
