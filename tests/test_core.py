from __future__ import annotations

import os
import unittest
import io
import json
import struct
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

os.environ["DATABASE_URL"] = "sqlite:///./data/automated-tests.db"
os.environ["DECADES_SKIP_STARTUP_MIGRATIONS"] = "1"

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, func, select

from app import accounts, auth, automation, backup_service, exports, insights, legacy_neon, names, notifications, sync, telemetry
from app.automation import candidate as automation_candidate, classify_game_relationship, reconcile_sim, repair_relationship_inbox
from app.calendar_utils import date_range_label, exact_historical_label
from app.clock import _game_illnesses, attach_game_identity, estimate_new_sim_birth, imported_sim_match, report_checksum, receive as receive_clock
from app.config import _automatic_snapshots
from app.db import SessionLocal, application_schema
from app.dice import notation_for_roll, parse, verify
from app.domain import apply_married_surnames, backfill_married_surnames, backfill_pregnancy_allowances, complete_roll, due_on_today, duplicate_event_summary, duplicate_obligation_summary, end_illnesses_for_death, failed, marriage_roll_result, multiple_birth_limit, pregnancy_count_result, purge_sim, repair_duplicate_events, repair_duplicate_obligations, schedule_event_rolls, schedule_rolls, schedule_occult_rolls, seed_occult_rules, sync_generations, validate_multiple_birth_count
from app.game_metadata import _refpack_decompress, bundled_localizations, enrich_illness_snapshot, localization_hash, occult_identity, readable_named_labels, readable_trait_labels, trait_illnesses
from app.insights import household_census, illness_statistics, pregnancy_dashboard, statistics as challenge_statistics
from app.main import FEATURES, app, birth_calendar_fields, create_rule_roll_record, death_calendar_fields, marriage_calendar_fields, resolve_birth_input, sim_birth_display, sim_weekday
from app.models import ChronicleSave, ClockLink, Conflict, DiceAudit, LegacyWorkspaceCode, Membership, Record, User, Workspace
from app.portraits import normalize_image
from app.storyline import build as build_storyline
from app.save_scanner import _parse_save_slot, _parse_sim, compare_scan, protobuf_fields
from app.session_policy import BROWSER_MODE, PERSISTENT_MODE, REMEMBER_DEVICE_SECONDS, StaySignedInMiddleware
from starlette.middleware.sessions import SessionMiddleware


class CoreSmokeTests(unittest.TestCase):
    def test_bundled_medieval_name_library_is_complete_and_source_grounded(self):
        summary = names.medieval_summary()
        pool = names.medieval_libraries()
        self.assertEqual(summary["cultures"], 13)
        self.assertEqual(summary["total_names"], 49009)
        self.assertIn("1Cr-MFsjQycEF17XsVXZrjScwF8z39wmHJCLXlpBOWPU", summary["source_url"])
        english = pool["Medieval — English"]
        self.assertEqual(len(english["Male"]["first"]), 1282)
        self.assertEqual(len(english["Female"]["first"]), 2341)
        self.assertEqual(len(english["Any"]["surname"]), 13360)

    def test_medieval_randomizer_uses_selected_given_and_surname_regions(self):
        pool = names.medieval_libraries()
        suggestions = names.generate(
            pool,
            "Medieval — Irish",
            "Female",
            10,
            surname_culture="Medieval — Welsh",
        )
        self.assertEqual(len(suggestions), 10)
        self.assertTrue(all(item["first_name"] in pool["Medieval — Irish"]["Female"]["first"] for item in suggestions))
        self.assertTrue(all(item["last_name"] in pool["Medieval — Welsh"]["Any"]["surname"] for item in suggestions))
        any_gender = names.generate(pool, "Medieval — Turkish", "Any", 10, no_surname=True)
        allowed = set(pool["Medieval — Turkish"]["Male"]["first"]) | set(pool["Medieval — Turkish"]["Female"]["first"])
        self.assertTrue(all(item["first_name"] in allowed and not item["last_name"] for item in any_gender))

    def test_name_culture_dropdown_does_not_require_copying_the_full_library(self):
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                session.add(Record(
                    save_id=save.id,
                    kind="name_entry",
                    label="Test name",
                    data={"culture": "Test culture", "sex": "Any", "name_kind": "first"},
                ))
                session.flush()
                cultures = names.library_names(session, save.id)
                self.assertIn("Medieval — English", cultures)
                self.assertIn("Test culture", cultures)
                self.assertIn("Names already recorded in this save", cultures)
                session.rollback()

    def test_static_assets_are_fingerprinted_and_browser_cacheable(self):
        with TestClient(app) as client:
            page = client.get("/")
            asset = client.get("/static/app.css")
            self.assertEqual(page.status_code, 200)
            self.assertRegex(page.text, r'/static/app\.css\?v=[0-9a-f]{12}')
            self.assertEqual(asset.headers.get("cache-control"), "public, max-age=604800, immutable")

    def test_automatic_snapshots_default_to_local_and_can_be_overridden(self):
        with mock.patch.dict(os.environ, {"DECADES_AUTOMATIC_SNAPSHOTS": ""}):
            self.assertTrue(_automatic_snapshots("sqlite:///./data/test.db"))
            self.assertFalse(_automatic_snapshots("postgresql://example.invalid/tracker"))
        with mock.patch.dict(os.environ, {"DECADES_AUTOMATIC_SNAPSHOTS": "true"}):
            self.assertTrue(_automatic_snapshots("postgresql://example.invalid/tracker"))
        with mock.patch.dict(os.environ, {"DECADES_AUTOMATIC_SNAPSHOTS": "false"}):
            self.assertFalse(_automatic_snapshots("sqlite:///./data/test.db"))

    def test_windows_release_avoids_upx_and_publishes_identity_and_checksum(self):
        build_script = Path("build_desktop.ps1").read_text(encoding="utf-8")
        spec = Path("Decades Tracker.spec").read_text(encoding="utf-8")
        installer_script = Path("build_installer.ps1").read_text(encoding="utf-8")
        installer_definition = Path("installer/DecadesTracker.iss").read_text(encoding="utf-8")
        launcher = Path("desktop_launcher.py").read_text(encoding="utf-8")
        desktop_requirements = Path("requirements-desktop.txt").read_text(encoding="utf-8")
        desktop_readme = Path("assets/README - Native Desktop.txt").read_text(encoding="utf-8")
        version_info = Path("assets/decades-version-info.txt").read_text(encoding="utf-8")
        self.assertIn("--noupx", build_script)
        self.assertIn("--version-file", build_script)
        self.assertIn("--collect-all webview", build_script)
        self.assertIn("START HERE - Decades Tracker.txt", build_script)
        self.assertNotIn("upx=True", spec)
        self.assertIn("version='assets/decades-version-info.txt'", spec)
        self.assertIn("collect_all('webview')", spec)
        self.assertIn("game_localization_fallbacks.json", spec)
        self.assertIn("('clock_bridge', 'clock_bridge')", spec)
        self.assertIn("SeveralUDO", version_info)
        self.assertIn("Get-FileHash", installer_script)
        self.assertIn(".sha256", installer_script)
        self.assertIn("pywebview", desktop_requirements)
        self.assertIn("Local AppData", desktop_readme)
        self.assertIn("WebView2", desktop_readme)
        self.assertIn("gui=\"edgechromium\"", launcher)
        self.assertIn('webview.settings["ALLOW_DOWNLOADS"] = True', launcher)
        self.assertNotIn("webbrowser.open", launcher)
        self.assertIn("F3017226-FE2A-4295-8BDF-00C3A9A7E4C5", installer_definition)

    def test_native_desktop_server_stops_only_when_owned(self):
        from desktop_launcher import LocalTracker, startup_failure_html
        from types import SimpleNamespace

        tracker = LocalTracker()
        tracker.server = SimpleNamespace(should_exit=False)
        tracker.thread = mock.Mock()
        tracker.thread.is_alive.return_value = True
        tracker.owned = True
        tracker.stop()
        self.assertTrue(tracker.server.should_exit)
        tracker.thread.join.assert_called_once_with(timeout=8)
        self.assertIn("No save data was removed", startup_failure_html(Path("diagnostics.log")))

        shared = LocalTracker()
        shared.server = SimpleNamespace(should_exit=False)
        shared.owned = False
        shared.stop()
        self.assertFalse(shared.server.should_exit)

    def test_stay_signed_in_cookie_policy_is_per_login(self):
        test_app = FastAPI()
        test_app.add_middleware(
            SessionMiddleware,
            secret_key="cookie-policy-test",
            max_age=REMEMBER_DEVICE_SECONDS,
        )
        test_app.add_middleware(
            StaySignedInMiddleware,
            persistent_max_age=REMEMBER_DEVICE_SECONDS,
        )

        @test_app.get("/session/{mode}")
        def choose_session_mode(request: Request, mode: str):
            request.session["_session_mode"] = mode
            request.session["user_id"] = "test-user"
            return {"ok": True}

        with TestClient(test_app) as client:
            browser_cookie = client.get(f"/session/{BROWSER_MODE}").headers["set-cookie"].casefold()
            self.assertNotIn("max-age=", browser_cookie)
            self.assertIn("httponly", browser_cookie)

            persistent_cookie = client.get(f"/session/{PERSISTENT_MODE}").headers["set-cookie"].casefold()
            self.assertIn(f"max-age={REMEMBER_DEVICE_SECONDS}", persistent_cookie)
            self.assertIn("httponly", persistent_cookie)

    def test_new_user_can_create_recovery_workspace_and_first_save(self):
        with TestClient(app):
            with SessionLocal() as session:
                email=f"new-{uuid.uuid4().hex}@example.test"
                user,workspace,save,recovery=accounts.create_recovery_workspace(
                    session,email,"New Historian","New Chronicle","First Challenge",1450,
                )
                session.flush()
                self.assertEqual((user.email,user.display_name),(email,"New Historian"))
                self.assertEqual((workspace.name,save.name,save.start_year),("New Chronicle","First Challenge",1450))
                membership=session.scalar(select(Membership).where(Membership.user_id==user.id,Membership.workspace_id==workspace.id))
                self.assertEqual(membership.role,"owner")
                self.assertEqual(auth.recover_user(session,email,recovery).id,user.id)
                with self.assertRaisesRegex(ValueError,"already has an account"):
                    accounts.create_recovery_workspace(session,email)
                session.rollback()

    def test_duplicate_event_repair_repoints_links_and_archives_extra_event_rolls(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Duplicate events",global_day=20)
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label="Anne Test",data={})
                session.add(sim);session.flush()
                legacy=Record(save_id=save.id,kind="event",label="Harvest Failure",global_day=20,data={"event_id":"legacy-harvest","legacy_id":"legacy-harvest","legacy_table":"events","start_global_day":20,"end_global_day":22,"location":"England","roll_required":True,"notes":"A detailed imported note"})
                catalog=Record(save_id=save.id,kind="event",label=" Harvest  Failure ",global_day=20,data={"catalog_id":"EVT-TEST","start_global_day":20,"end_global_day":22,"location":"England","roll_required":True,"notes":""})
                session.add_all([legacy,catalog]);session.flush()
                first_roll=Record(save_id=save.id,kind="roll",label="Harvest — Anne",global_day=20,data={"sim_id":sim.id,"roll_type":"Event — Harvest Failure","event_id":legacy.id,"source_id":legacy.id,"source":f"event:{legacy.id}:{sim.id}","die":"d20","completed":False})
                second_roll=Record(save_id=save.id,kind="roll",label="Harvest — Anne",global_day=20,data={"sim_id":sim.id,"roll_type":"Event — Harvest Failure","event_id":catalog.id,"source_id":catalog.id,"source":f"event:{catalog.id}:{sim.id}","die":"d20","completed":False})
                rule=Record(save_id=save.id,kind="event_rule",label="Harvest rule",data={"event_id":"EVT-TEST","die":"d20","bad_results":"1"})
                session.add_all([first_roll,second_roll,rule]);session.flush()

                summary=duplicate_event_summary([legacy,catalog,first_roll,second_roll,rule,sim])
                self.assertEqual((summary["groups"],summary["repairable"]),(1,1))
                result=repair_duplicate_events(session,save);session.flush()
                self.assertEqual((result["archived"],result["repointed"],result["rolls_archived"]),(1,2,1))
                active_events=[item for item in (legacy,catalog) if not item.deleted]
                self.assertEqual(len(active_events),1)
                keeper=active_events[0];removed=catalog if keeper is legacy else legacy
                self.assertEqual(removed.data["duplicate_of"],keeper.id)
                self.assertIn("EVT-TEST",keeper.data["duplicate_event_aliases"])
                active_rolls=[item for item in (first_roll,second_roll) if not item.deleted]
                self.assertEqual(len(active_rolls),1)
                self.assertEqual(active_rolls[0].data["event_id"],keeper.id)
                self.assertEqual(rule.data["event_id"],"legacy-harvest")
                self.assertEqual(repair_duplicate_events(session,save)["archived"],0)
                session.rollback()

    def test_duplicate_obligation_repair_archives_only_redundant_pending_rolls(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Duplicate repair",global_day=20)
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label="Anne Test",data={"sim_number":"SIM-1"})
                session.add(sim);session.flush()
                sparse=Record(save_id=save.id,kind="roll",label="Anne aging",global_day=20,data={"sim_id":sim.id,"roll_type":"Teen","completed":False})
                rich=Record(save_id=save.id,kind="roll",label="Anne aging",global_day=20,data={"sim_id":sim.id,"roll_type":" Teen ","die":"d20","bad_results":"1","source":"aging","completed":False})
                completed=Record(save_id=save.id,kind="roll",label="Anne event",global_day=21,data={"sim_id":sim.id,"roll_type":"Event — Plague","die":"d20","actual":8,"outcome":"Passed","completed":True})
                pending_after_result=Record(save_id=save.id,kind="roll",label="Anne event",global_day=21,data={"sim_id":sim.id,"roll_type":"event — plague","die":"d20","completed":False})
                completed_copy=Record(save_id=save.id,kind="roll",label="Anne marriage",global_day=22,data={"sim_id":sim.id,"roll_type":"Marriage","actual":1,"outcome":"Marries","completed":True})
                completed_copy_2=Record(save_id=save.id,kind="roll",label="Anne marriage",global_day=22,data={"sim_id":sim.id,"roll_type":"Marriage","actual":2,"outcome":"Marries","completed":True})
                distinct_day=Record(save_id=save.id,kind="roll",label="Anne aging later",global_day=23,data={"sim_id":sim.id,"roll_type":"Teen","completed":False})
                session.add_all([sparse,rich,completed,pending_after_result,completed_copy,completed_copy_2,distinct_day]);session.flush()

                summary=duplicate_obligation_summary([sparse,rich,completed,pending_after_result,completed_copy,completed_copy_2,distinct_day])
                self.assertEqual((summary["groups"],summary["repairable"],summary["protected_completed"]),(3,2,1))
                result=repair_duplicate_obligations(session,save);session.flush()
                self.assertEqual((result["archived"],result["protected_completed"]),(2,1))
                self.assertTrue(sparse.deleted)
                self.assertFalse(rich.deleted)
                self.assertTrue(pending_after_result.deleted)
                self.assertFalse(completed.deleted)
                self.assertFalse(completed_copy.deleted)
                self.assertFalse(completed_copy_2.deleted)
                self.assertFalse(distinct_day.deleted)
                self.assertEqual(sparse.data["duplicate_of"],rich.id)
                self.assertEqual(pending_after_result.data["duplicate_of"],completed.id)
                self.assertEqual(repair_duplicate_obligations(session,save)["archived"],0)
                session.rollback()

    def test_v4_uses_explicit_public_schema_only_for_postgres(self):
        self.assertEqual(application_schema("postgresql://example.invalid/decades"), "public")
        self.assertEqual(application_schema("postgres://example.invalid/decades"), "public")
        self.assertIsNone(application_schema("sqlite:///./data/decades-v4.db"))

    def test_family_tree_groups_couples_labels_kin_and_deduplicates_connections(self):
        marker=uuid.uuid4().hex
        mother=Record(id="mother-"+marker,kind="sim",label="Tree Mother",data={"sex":"Female","birth_global_day":1})
        father=Record(id="father-"+marker,kind="sim",label="Tree Father",data={"sex":"Male","birth_global_day":1})
        focus=Record(id="focus-"+marker,kind="sim",label="Tree Focus",data={"sex":"Female","birth_global_day":20,"mother_id":mother.id,"father_id":father.id})
        sibling=Record(id="sibling-"+marker,kind="sim",label="Tree Sibling",data={"sex":"Male","birth_global_day":22,"mother_id":mother.id,"father_id":father.id})
        spouse=Record(id="spouse-"+marker,kind="sim",label="Tree Spouse",data={"sex":"Male","birth_global_day":20})
        child=Record(id="child-"+marker,kind="sim",label="Tree Child",data={"sex":"Female","birth_global_day":40,"mother_id":focus.id,"father_id":spouse.id})
        ended=Record(id="ended-"+marker,kind="relationship",label="Old duplicate",data={"partner1_id":focus.id,"partner2_id":spouse.id,"type":"Marriage","status":"Ended","start_global_day":20})
        active=Record(id="active-"+marker,kind="relationship",label="Current marriage",data={"partner1_id":focus.id,"partner2_id":spouse.id,"type":"Marriage","status":"Active","legally_married":True,"start_global_day":25})
        tree=insights.family_view([mother,father,focus,sibling,spouse,child,ended,active],focus.id,"family",3)
        self.assertEqual(tree["roles"][mother.id],"Mother")
        self.assertEqual(tree["roles"][father.id],"Father")
        self.assertEqual(tree["roles"][sibling.id],"Brother")
        self.assertEqual(tree["roles"][spouse.id],"Spouse")
        self.assertEqual(tree["roles"][child.id],"Daughter")
        partner_edges=[edge for edge in tree["edges"] if edge["type"]=="partner"]
        self.assertEqual(len(partner_edges),1)
        self.assertTrue(partner_edges[0]["active"])
        focus_generation=next(level for level in tree["levels"] if level["level"]==0)
        ids=[sim.id for sim in focus_generation["members"]]
        self.assertEqual(abs(ids.index(focus.id)-ids.index(spouse.id)),1)
        direct=insights.family_view([mother,father,focus,sibling,spouse,child,ended,active],focus.id,"direct",3)
        self.assertEqual({sim.id for level in direct["levels"] for sim in level["members"]},{mother.id,father.id,focus.id,sibling.id,spouse.id,child.id})
        self.assertEqual([level["label"] for level in direct["levels"]],["Parents","Focus & close family","Children"])

    def test_generations_follow_parents_then_fall_back_to_spouses(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Generation inference",global_day=10,start_year=1550,days_per_year=4)
                session.add(save);session.flush()
                parent=Record(save_id=save.id,kind="sim",label="Known Parent",data={"generation":2,"generation_source":"manual"})
                child=Record(save_id=save.id,kind="sim",label="Child",data={})
                spouse=Record(save_id=save.id,kind="sim",label="Known Spouse",data={"generation":4,"generation_source":"manual"})
                spouse_match=Record(save_id=save.id,kind="sim",label="Unknown Parents",data={})
                priority=Record(save_id=save.id,kind="sim",label="Parent Priority",data={})
                session.add_all([parent,child,spouse,spouse_match,priority]);session.flush()
                child.data={"mother_id":parent.id}
                priority.data={"father_id":parent.id}
                session.add_all([
                    Record(save_id=save.id,kind="relationship",label="Spouse pair",global_day=8,data={"partner1_id":spouse.id,"partner2_id":spouse_match.id,"type":"Marriage","status":"Active","legally_married":True}),
                    Record(save_id=save.id,kind="relationship",label="Priority pair",global_day=9,data={"partner1_id":spouse.id,"partner2_id":priority.id,"type":"Marriage","status":"Active","legally_married":True}),
                ]);session.flush()
                changed=sync_generations(session,save)
                self.assertGreaterEqual(changed,3)
                self.assertEqual((child.data["generation"],child.data["generation_source"]),(3,"parents"))
                self.assertEqual((spouse_match.data["generation"],spouse_match.data["generation_source"]),(4,"spouse"))
                self.assertEqual((priority.data["generation"],priority.data["generation_source"]),(3,"parents"))
                self.assertEqual(spouse.data["generation"],4)
                session.rollback()

    def test_marriage_preserves_birth_surname_and_updates_displayed_name(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Marriage names",global_day=10)
                session.add(save);session.flush()
                first=Record(save_id=save.id,kind="sim",label="Anne Tudor",data={"first_name":"Anne","last_name":"Tudor","sex":"Female"})
                second=Record(save_id=save.id,kind="sim",label="Henry Stuart",data={"first_name":"Henry","last_name":"Stuart","sex":"Male"})
                session.add_all([first,second]);session.flush()
                marriage=Record(save_id=save.id,kind="relationship",label="Anne Tudor & Henry Stuart",global_day=10,data={"partner1_id":first.id,"partner2_id":second.id,"type":"Marriage","status":"Active","legally_married":True})
                session.add(marriage);session.flush()
                changed=apply_married_surnames(session,marriage,first,second,"automatic")
                self.assertEqual(changed,2)
                self.assertEqual(first.data["surname_at_birth"],"Tudor")
                self.assertEqual(first.data["married_surname"],"Stuart")
                self.assertEqual((first.data["last_name"],first.label),("Stuart","Anne Stuart"))
                self.assertEqual(second.data["surname_at_birth"],"Stuart")
                session.rollback()

    def test_marriage_name_rules_support_retained_and_hyphenated_surnames(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Marriage rules",global_day=10)
                session.add(save);session.flush()
                first=Record(save_id=save.id,kind="sim",label="Alex North",data={"first_name":"Alex","last_name":"North"})
                second=Record(save_id=save.id,kind="sim",label="Robin West",data={"first_name":"Robin","last_name":"West"})
                session.add_all([first,second]);session.flush()
                marriage=Record(save_id=save.id,kind="relationship",label="Pair",global_day=10,data={"partner1_id":first.id,"partner2_id":second.id,"type":"Marriage","status":"Active","legally_married":True})
                session.add(marriage);session.flush()
                apply_married_surnames(session,marriage,first,second,"keep")
                self.assertEqual((first.data["last_name"],second.data["last_name"]),("North","West"))
                apply_married_surnames(session,marriage,first,second,"hyphenate")
                self.assertEqual((first.data["last_name"],second.data["last_name"]),("North-West","North-West"))
                session.rollback()

    def test_sim_profile_can_add_spouse_without_duplicate_marriages(self):
        marker = uuid.uuid4().hex[:8]
        with TestClient(app) as client:
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name=f"Profile spouse {marker}",global_day=14,start_year=1550,days_per_year=4)
                session.add(save);session.flush()
                first=Record(save_id=save.id,kind="sim",label=f"Anne Tudor {marker}",data={"first_name":"Anne","last_name":"Tudor","sex":"Female","birth_global_day":1})
                second=Record(save_id=save.id,kind="sim",label=f"Henry Stuart {marker}",data={"first_name":"Henry","last_name":"Stuart","sex":"Male","birth_global_day":1})
                session.add_all([first,second]);session.commit();save_id,first_id,second_id=save.id,first.id,second.id
            client.post("/saves/select",data={"save_id":save_id},follow_redirects=False)

            profile=client.get(f"/sims/{first_id}")
            self.assertEqual(profile.status_code,200)
            self.assertIn(f'action="/sims/{first_id}/spouse"',profile.text)
            self.assertIn(f'value="{second_id}"',profile.text)

            form={"spouse_id":second_id,"marriage_global_day":"14","marriage_game_hour":"9","marriage_game_minute":"30","location":"Parish church","surname_rule":"automatic","notes":"Recorded from the Sim profile"}
            created=client.post(f"/sims/{first_id}/spouse",data=form,follow_redirects=False)
            duplicate=client.post(f"/sims/{first_id}/spouse",data=form,follow_redirects=False)
            self.assertEqual((created.status_code,duplicate.status_code),(303,303))

            with SessionLocal() as session:
                marriages=list(session.scalars(select(Record).where(Record.save_id==save_id,Record.kind=="relationship",Record.deleted.is_(False))))
                self.assertEqual(len(marriages),1)
                marriage=marriages[0]
                self.assertEqual({marriage.data["partner1_id"],marriage.data["partner2_id"]},{first_id,second_id})
                self.assertEqual((marriage.data["type"],marriage.data["status"],marriage.data["legally_married"]),("Marriage","Active",True))
                self.assertEqual((marriage.data["marriage_global_day"],marriage.data["marriage_time"]),(14,"09:30"))
                self.assertEqual(marriage.data["location"],"Parish church")
                self.assertEqual(session.get(Record,first_id).data["last_name"],"Stuart")

            self.assertEqual(client.post(f"/sims/{first_id}/spouse",data={"spouse_id":first_id},follow_redirects=False).status_code,400)

    def test_editable_multiple_birth_limits_are_enforced_by_historical_year(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Multiple birth rule",global_day=1,start_year=1750,days_per_year=4)
                session.add(save);session.flush()
                rule=Record(save_id=save.id,kind="multiple_birth_rule",label="Sourced limit",data={"start_year":1750,"end_year":1800,"max_babies":2,"active":True})
                session.add(rule);session.flush()
                self.assertEqual(multiple_birth_limit(session,save,1)["max_babies"],2)
                validate_multiple_birth_count(session,save,1,2)
                with self.assertRaises(ValueError): validate_multiple_birth_count(session,save,1,3)
                session.rollback()

    def test_save_scanner_decodes_names_stage_and_exact_game_clock(self):
        def varint(value):
            out=bytearray()
            while value>127: out.append((value&127)|128);value>>=7
            out.append(value);return bytes(out)
        def scalar(field,value): return varint(field<<3)+varint(value)
        def fixed64(field,value): return varint((field<<3)|1)+int(value).to_bytes(8,"little")
        def fixed32_float(field,value): return varint((field<<3)|5)+struct.pack("<f",value)
        def text(field,value):
            raw=value.encode();return varint((field<<3)|2)+varint(len(raw))+raw
        sim_raw=(fixed64(1,123456)+fixed64(4,999)+text(5,"Anne")+text(6,"Capp")+
                 scalar(7,8192)+scalar(8,16)+fixed32_float(13,.5)+fixed64(15,654321)+fixed32_float(48,.75))
        sim=_parse_sim(sim_raw)
        self.assertEqual(sim["game_sim_id"],"123456");self.assertEqual(sim["name"],"Anne Capp")
        self.assertEqual(sim["age_stage"],"teen");self.assertEqual(sim["sex"],"Female")
        self.assertEqual(sim["age_progress_percentage"],50.0)
        self.assertEqual(sim["significant_other_game_id"],"654321")
        self.assertTrue(sim["is_pregnant"])
        self.assertEqual(sim["pregnancy_progress_percentage"],75.0)
        ticks=(61*86400+7*3600+30*60)*25
        gameplay=scalar(1,ticks);slot=text(9,"Elizabethan")+scalar(11,999)+varint((8<<3)|2)+varint(len(gameplay))+gameplay
        parsed=_parse_save_slot(slot)
        self.assertEqual((parsed["game_day"],parsed["game_hour"],parsed["game_minute"]),(61,7,30))
        self.assertEqual(protobuf_fields(text(1,"ok"))[1][0][1],b"ok")

    def test_imported_event_rules_supply_die_outcome_and_lethality(self):
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Imported event rules", global_day=8, start_year=1550, days_per_year=4)
                session.add(save); session.flush()
                sim = Record(save_id=save.id, kind="sim", label="Eligible Sim", global_day=1, data={"birth_global_day":1,"sex":"Male","country":"England"})
                enlist = Record(save_id=save.id, kind="event", label="Enlistment", global_day=8, data={"event_id":"evt-enlist","roll_required":True,"active":True,"location":"England","affected_class":"Eligible Male Sims"})
                fatal = Record(save_id=save.id, kind="event", label="Fatal siege", global_day=8, data={"event_id":"evt-fatal","roll_required":True,"active":True,"location":"England","affected_class":"Eligible Male Sims"})
                enlist_rule = Record(save_id=save.id, kind="event_rule", label="Imported event rule configs", data={"event_id":"evt-enlist","die":"d12","bad_results":"3: they are enlisted","eligibility":"Eligible Male Sims"})
                fatal_rule = Record(save_id=save.id, kind="event_rule", label="Imported event rule configs", data={"event_id":"evt-fatal","die":"d8","bad_results":"5: they are killed in the siege","eligibility":"Eligible Male Sims"})
                session.add_all([sim,enlist,fatal,enlist_rule,fatal_rule]); session.flush()
                schedule_rolls(session,save); session.flush()
                rolls=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["event_id"].as_string().is_not(None))))
                by_event={item.data["event_id"]:item for item in rolls}
                self.assertEqual(by_event[enlist.id].data["die"],"d12")
                self.assertEqual(by_event[enlist.id].data["bad_results"],"3")
                result=complete_roll(session,save,by_event[enlist.id],3)
                self.assertIn("enlisted",result["outcome"])
                self.assertIsNone(result["death"])
                result=complete_roll(session,save,by_event[fatal.id],5)
                self.assertIn("killed",result["outcome"])
                self.assertIsNotNone(result["death"])
                session.rollback()

    def test_birth_clock_maps_to_exact_historical_date(self):
        save = ChronicleSave(start_year=1550, days_per_year=4)
        self.assertEqual(date_range_label(1, 1550, 4), "Jan 1–Mar 31, 1550")
        self.assertEqual(date_range_label(-1, 1550, 4), "Jul 1–Sep 30, 1549")
        self.assertEqual(exact_historical_label(1, 0, 0, 1550, 4), "January 1, 1550")
        self.assertEqual(exact_historical_label(4, 23, 59, 1550, 4), "December 31, 1550")
        fields = birth_calendar_fields(save, 1, 12, 0)
        self.assertEqual(fields["birth_time"], "12:00")
        self.assertEqual(fields["historical_birth_date"], "February 15, 1550")
        self.assertEqual(fields["birth_date_precision"], "exact")
        self.assertEqual(death_calendar_fields(save, 1, 12, 0)["historical_death_date"], "February 15, 1550")
        self.assertEqual(marriage_calendar_fields(save, 1, 12, 0)["historical_marriage_date"], "February 15, 1550")

    def test_historical_birth_year_becomes_an_explicit_approximate_tracker_day(self):
        save = ChronicleSave(start_year=1550, days_per_year=4)
        birth_day, fields = resolve_birth_input(save, 999, 1540, 12, 30)
        self.assertEqual(birth_day, -38)
        self.assertEqual(fields["birth_year"], 1540)
        self.assertEqual(fields["estimated_birth_global_day_range_start"], -39)
        self.assertEqual(fields["estimated_birth_global_day_range_end"], -36)
        self.assertTrue(fields["birth_year_only"])
        self.assertTrue(fields["birth_global_day_estimated"])
        self.assertEqual(fields["birth_date_precision"], "historical-year-only")
        self.assertNotIn("birth_time", fields)
        sim = Record(kind="sim", label="Imported Sim", data={"birth_global_day":birth_day, **fields})
        self.assertEqual(sim_birth_display(save, sim), "1540 (exact date unknown)")

        exact_day, exact_fields = resolve_birth_input(save, 5, "", 8, 15)
        self.assertEqual(exact_day, 5)
        self.assertEqual(exact_fields["birth_time"], "08:15")
        self.assertNotIn("birth_year", exact_fields)
        second_day, second_fields = resolve_birth_input(save, 5, "", 8, 15, 9)
        self.assertEqual(second_day, 5)
        self.assertEqual(second_fields["birth_time"], "08:15:09")
        self.assertEqual(second_fields["birth_game_second"], 9)

    def test_newborn_clock_candidate_preserves_detection_time(self):
        game_sim_id = "newborn-" + uuid.uuid4().hex
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Birth clock test", global_day=20, start_year=1550, days_per_year=4)
                session.add(save); session.flush()
                link = ClockLink(save_id=save.id, token_hash=uuid.uuid4().hex, game_anchor_day=300, tracker_anchor_day=20)
                session.add(link); session.flush()
                result = receive_clock(session, link, {
                    "game_day": 300, "hour": 21, "minute": 37, "second": 42,
                    "household_members": [{"game_sim_id": game_sim_id, "first_name": "New", "last_name": "Baby", "is_baby": True}],
                })
                self.assertEqual(result["new_candidates"], 1)
                candidate = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "game_candidate"))
                self.assertEqual(candidate.data["action"], "new_baby")
                self.assertEqual(candidate.data["payload"]["detected_tracker_global_day"], 20)
                self.assertEqual(candidate.data["payload"]["detected_game_hour"], 21)
                self.assertEqual(candidate.data["payload"]["detected_game_minute"], 37)
                self.assertEqual(candidate.data["payload"]["detected_game_second"], 42)
                session.rollback()

    def test_clock_protocol_rejects_corruption_and_wrong_save_without_mutation(self):
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Protocol guard test", global_day=20)
                session.add(save); session.flush()
                link = ClockLink(save_id=save.id, token_hash=uuid.uuid4().hex)
                session.add(link); session.flush()
                first = {
                    "protocol_version":2, "report_sequence":1, "report_id":"slot-a-1",
                    "report_kind":"full", "previous_report_checksum":"", "save_identity":"slot-a",
                    "save_slot_id":"A", "game_day":500, "game_hour":5, "game_minute":6,
                    "game_second":7, "population_complete":True, "population_sim_ids":[],
                    "household_sims":[],
                }
                first["report_checksum"] = report_checksum(first)
                accepted = receive_clock(session, link, first)
                self.assertTrue(accepted["ok"])
                self.assertEqual(accepted["report_sequence"], 1)
                self.assertEqual(save.global_day, 20)
                duplicate = receive_clock(session, link, dict(first))
                self.assertTrue(duplicate["duplicate"])
                corrupted = {**first, "report_sequence":2, "game_day":999}
                self.assertEqual(receive_clock(session, link, corrupted)["reason"], "checksum_mismatch")
                self.assertEqual(save.global_day, 20)
                wrong = {
                    **first, "report_sequence":2, "report_id":"slot-b-2", "save_identity":"slot-b",
                    "previous_report_checksum":first["report_checksum"],
                }
                wrong["report_checksum"] = report_checksum(wrong)
                rejected = receive_clock(session, link, wrong)
                self.assertEqual(rejected["reason"], "wrong_game_save")
                self.assertEqual(link.last_game_day, 500)
                session.rollback()

    def test_read_only_save_comparison_identifies_changed_new_and_missing_sims(self):
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Scanner comparison", global_day=10)
                session.add(save); session.flush()
                linked = Record(save_id=save.id, kind="sim", label="Ada Known", data={
                    "game_sim_id":"101", "first_name":"Ada", "last_name":"Known",
                    "game_age_stage":"adult", "game_household_id":"H1",
                })
                missing = Record(save_id=save.id, kind="sim", label="Missing Known", data={"game_sim_id":"202"})
                session.add_all([linked, missing]); session.flush()
                scan = {
                    "slot":{"active_household_game_id":"H1"},
                    "households":[{"game_household_id":"H1", "name":"Known House", "is_player":True}],
                    "sims":[
                        {"game_sim_id":"101", "name":"Ada Known", "first_name":"Ada", "last_name":"Known", "age_stage":"elder", "game_household_id":"H1"},
                        {"game_sim_id":"303", "name":"New Person", "first_name":"New", "last_name":"Person", "age_stage":"adult", "game_household_id":"H1"},
                    ],
                }
                comparison = compare_scan(session, save, scan)
                self.assertEqual(comparison["counts"], {"matched":0, "changed":1, "new":1, "missing":1})
                self.assertEqual(comparison["rows"][0]["differences"][0]["field"], "life stage")
                self.assertEqual(comparison["missing"][0]["tracker_record_label"], "Missing Known")
                session.rollback()

    def test_clock_automatically_creates_household_and_connects_known_members_once(self):
        marker = uuid.uuid4().hex
        game_household_id = "game-house-" + marker
        game_sim_id = "known-sim-" + marker
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Automatic household test", global_day=25)
                session.add(save); session.flush()
                sim = Record(save_id=save.id, kind="sim", label="Known Member", data={"game_sim_id":game_sim_id})
                link = ClockLink(save_id=save.id, token_hash=uuid.uuid4().hex, game_anchor_day=700, tracker_anchor_day=25)
                session.add_all([sim, link]); session.flush()
                report = {
                    "game_day":700, "hour":11, "minute":15,
                    "household_members":[{
                        "game_sim_id":game_sim_id, "first_name":"Known", "last_name":"Member",
                        "household_id":game_household_id, "household_name":"Willow House",
                        "world_name":"Windenburg", "lot_name":"Olde Mill Hill", "household_funds":2400,
                        "is_household_head":True,
                    }],
                }
                first = receive_clock(session, link, report)
                home = session.scalar(select(Record).where(
                    Record.save_id==save.id, Record.kind=="household",
                    Record.data["game_household_id"].as_string()==game_household_id,
                ))
                self.assertIsNotNone(home)
                self.assertEqual(first["households_created"], 1)
                self.assertEqual(first["household_members_linked"], 1)
                self.assertEqual(home.label, "Willow House")
                self.assertEqual(home.data["head_sim_id"], sim.id)
                self.assertEqual(home.data["last_game_world"], "Windenburg")
                self.assertEqual(sim.data["current_household_id"], home.id)
                second = receive_clock(session, link, report)
                duplicate_count = session.scalar(select(func.count()).select_from(Record).where(
                    Record.save_id==save.id, Record.kind=="household", Record.deleted.is_(False),
                    Record.data["game_household_id"].as_string()==game_household_id,
                ))
                self.assertEqual(second["households_created"], 0)
                self.assertEqual(second["household_members_linked"], 0)
                self.assertEqual(duplicate_count, 1)
                session.rollback()

    def test_clock_reuses_manual_household_and_new_sim_candidate_inherits_it(self):
        marker = uuid.uuid4().hex
        game_household_id = "manual-game-house-" + marker
        known_game_id = "manual-known-" + marker
        new_game_id = "manual-new-" + marker
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Manual household reuse", global_day=40)
                session.add(save); session.flush()
                home = Record(save_id=save.id, kind="household", label="Cedar Family", data={"active":True})
                session.add(home); session.flush()
                known = Record(save_id=save.id, kind="sim", label="Known Cedar", data={
                    "game_sim_id":known_game_id, "current_household_id":home.id,
                })
                link = ClockLink(save_id=save.id, token_hash=uuid.uuid4().hex, game_anchor_day=800, tracker_anchor_day=40)
                session.add_all([known, link]); session.flush()
                result = receive_clock(session, link, {
                    "game_day":800, "hour":7, "minute":30,
                    "household_members":[
                        {"game_sim_id":known_game_id,"first_name":"Known","last_name":"Cedar","household_id":game_household_id,"household_name":"Cedar Family"},
                        {"game_sim_id":new_game_id,"first_name":"New","last_name":"Cedar","household_id":game_household_id,"household_name":"Cedar Family","age_stage":"Age.CHILD"},
                    ],
                })
                candidate = session.scalar(select(Record).where(
                    Record.save_id==save.id, Record.kind=="game_candidate",
                    Record.data["source_key"].as_string()==f"new_sim:{new_game_id}",
                ))
                households = list(session.scalars(select(Record).where(
                    Record.save_id==save.id, Record.kind=="household", Record.deleted.is_(False),
                )))
                self.assertEqual(result["households_created"], 0)
                self.assertEqual(len(households), 1)
                self.assertEqual(home.data["game_household_id"], game_household_id)
                self.assertEqual(candidate.data["payload"]["inferred_household_id"], home.id)
                session.rollback()

    def test_non_newborn_clock_candidate_estimates_birth_from_game_age(self):
        marker = "older-sim-" + uuid.uuid4().hex
        with TestClient(app) as client:
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Older Sim birth estimate", global_day=100, start_year=1550, days_per_year=4)
                session.add(save); session.flush()
                self.assertEqual(estimate_new_sim_birth(session, save, {"age_stage":"Age.ADULT"}, 10)["estimated_birth_global_day"], -150)
                link = ClockLink(save_id=save.id, token_hash=uuid.uuid4().hex, game_anchor_day=300, tracker_anchor_day=100)
                session.add(link); session.flush()
                result = receive_clock(session, link, {
                    "game_day":300, "hour":9, "minute":15,
                    "household_members":[{"game_sim_id":marker,"first_name":"Older","last_name":"Sim","age_stage":"Age.TEEN","age_progress_percentage":50,"is_baby":False}],
                })
                self.assertEqual(result["new_candidates"], 1)
                candidate = session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="game_candidate"))
                self.assertEqual(candidate.data["action"], "new_sim")
                self.assertEqual(candidate.data["payload"]["estimated_age_days"], 62)
                self.assertEqual(candidate.data["payload"]["estimated_birth_global_day"], 38)
                self.assertEqual(candidate.data["payload"]["birth_estimate_precision"], "life-stage-progress")
                session.commit(); candidate_id, save_id = candidate.id, save.id
            client.post("/saves/select", data={"save_id":save_id})
            page = client.get("/p/automation")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Estimated birth: Global Day 38", page.text)
            response = client.post(f"/automation/{candidate_id}/accept", data={"first_name":"Older","last_name":"Sim","age_stage":"Age.TEEN"}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            with SessionLocal() as session:
                sim = session.scalar(select(Record).where(Record.save_id==save_id,Record.kind=="sim",Record.data["game_sim_id"].as_string()==marker))
                self.assertEqual(sim.data["birth_global_day"], 38)
                self.assertEqual(sim.data["game_age_days_at_detection"], 62)
                self.assertEqual(sim.data["original_birth_estimate_global_day"], 38)
                self.assertTrue(sim.data["birth_global_day_estimated"])
                self.assertIn("Teen at 50%", sim.data["birth_time_source"])
                session.execute(delete(Record).where(Record.save_id==save_id)); session.execute(delete(ClockLink).where(ClockLink.save_id==save_id)); session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id)); session.commit()

    def test_accepting_non_newborn_with_birth_year_overrides_clock_age_estimate(self):
        marker = "year-only-" + uuid.uuid4().hex
        with TestClient(app) as client:
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Year-only Sim import", global_day=100, start_year=1550, days_per_year=4)
                session.add(save); session.flush()
                candidate = Record(save_id=save.id, kind="game_candidate", label="Imported Year Sim", global_day=100, data={
                    "action":"new_sim", "status":"pending", "source_key":"new_sim:" + marker,
                    "payload":{"game_sim_id":marker,"first_name":"Imported","last_name":"Year Sim","age_stage":"Age.ADULT","estimated_birth_global_day":-150},
                })
                session.add(candidate); session.commit(); candidate_id, save_id = candidate.id, save.id
            client.post("/saves/select", data={"save_id":save_id})
            page = client.get("/p/automation")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Historical birth year", page.text)
            response = client.post(f"/automation/{candidate_id}/accept", data={
                "first_name":"Imported", "last_name":"Year Sim", "birth_global_day":"-150", "birth_year":"1540", "age_stage":"Age.ADULT",
            }, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            with SessionLocal() as session:
                sim = session.scalar(select(Record).where(Record.save_id==save_id, Record.kind=="sim", Record.data["game_sim_id"].as_string()==marker))
                self.assertEqual(sim.data["birth_global_day"], -38)
                self.assertEqual(sim.data["birth_year"], 1540)
                self.assertTrue(sim.data["birth_year_only"])
                self.assertEqual(sim.data["birth_estimate_precision"], "historical-year-only")
                self.assertIn("Historical birth year 1540", sim.data["birth_time_source"])
                session.execute(delete(Record).where(Record.save_id==save_id)); session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id)); session.commit()

    def test_newborn_clock_candidate_infers_parents_from_completed_pregnancy(self):
        marker = uuid.uuid4().hex
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Newborn parent inference", global_day=30)
                session.add(save); session.flush()
                mother = Record(save_id=save.id, kind="sim", label="Known Mother", data={"game_sim_id":"mother-"+marker,"game_was_pregnant":True})
                father = Record(save_id=save.id, kind="sim", label="Known Father", data={"game_sim_id":"father-"+marker})
                session.add_all([mother,father]); session.flush()
                pregnancy = Record(save_id=save.id, kind="pregnancy", label="Known pregnancy", global_day=30, data={"mother_id":mother.id,"mother_name":mother.label,"father_id":father.id,"father_name":father.label,"status":"Active"})
                link = ClockLink(save_id=save.id, token_hash=uuid.uuid4().hex, game_anchor_day=500, tracker_anchor_day=30)
                session.add_all([pregnancy,link]); session.flush()
                receive_clock(session, link, {"game_day":500,"hour":4,"minute":12,"household_members":[
                    {"game_sim_id":"mother-"+marker,"first_name":"Known","last_name":"Mother","is_pregnant":False},
                    {"game_sim_id":"baby-"+marker,"first_name":"Clock","last_name":"Baby","is_baby":True},
                ]})
                baby = session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="game_candidate",Record.data["source_key"].as_string()=="new_sim:baby-"+marker))
                self.assertEqual(baby.data["payload"]["inferred_mother_id"], mother.id)
                self.assertEqual(baby.data["payload"]["inferred_father_id"], father.id)
                self.assertEqual(baby.data["payload"]["pregnancy_id"], pregnancy.id)
                self.assertEqual(baby.data["payload"]["parent_match_confidence"], "exact")
                session.rollback()

    def test_accepting_newborn_records_exact_birth_time_and_date(self):
        marker = uuid.uuid4().hex
        with TestClient(app) as client:
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Newborn acceptance test", global_day=8, start_year=1600, days_per_year=4)
                session.add(save); session.flush()
                candidate = Record(
                    save_id=save.id, kind="game_candidate", label="Clock Baby", global_day=8,
                    data={
                        "action":"new_baby", "status":"pending", "source_key":"new_sim:" + marker,
                        "hour":6, "minute":30,
                        "payload":{"game_sim_id":marker,"first_name":"Clock","last_name":"Baby","detected_tracker_global_day":8,"detected_game_hour":6,"detected_game_minute":30},
                    },
                )
                session.add(candidate); session.commit(); candidate_id, save_id = candidate.id, save.id
            response = client.post(f"/automation/{candidate_id}/accept", data={
                "first_name":"Clock", "last_name":"Baby", "birth_global_day":"8",
                "birth_game_hour":"6", "birth_game_minute":"30",
            }, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            with SessionLocal() as session:
                newborn = session.scalar(select(Record).where(Record.save_id == save_id, Record.kind == "sim", Record.data["game_sim_id"].as_string() == marker))
                self.assertEqual(newborn.data["birth_time"], "06:30")
                self.assertEqual(newborn.data["historical_birth_date"], "October 25, 1601")
                self.assertEqual(newborn.data["birth_date_precision"], "exact")
                self.assertEqual(newborn.data["birth_time_source"], "Clock Sync newborn detection")
                session.rollback()

    def test_accepting_inferred_newborn_links_parents_and_pregnancy(self):
        marker = uuid.uuid4().hex
        with TestClient(app) as client:
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Accept inferred parents", global_day=12)
                session.add(save); session.flush()
                mother = Record(save_id=save.id,kind="sim",label="Review Mother",data={})
                father = Record(save_id=save.id,kind="sim",label="Review Father",data={})
                session.add_all([mother,father]);session.flush()
                pregnancy = Record(save_id=save.id,kind="pregnancy",label="Review pregnancy",global_day=12,data={"mother_id":mother.id,"father_id":father.id,"status":"Active"})
                session.add(pregnancy);session.flush()
                candidate = Record(save_id=save.id,kind="game_candidate",label="Review Baby",global_day=12,data={"action":"new_baby","status":"pending","source_key":"new_sim:"+marker,"payload":{"game_sim_id":marker,"first_name":"Review","last_name":"Baby","inferred_mother_id":mother.id,"inferred_father_id":father.id,"pregnancy_id":pregnancy.id}})
                session.add(candidate);session.commit();candidate_id=candidate.id;save_id=save.id;pregnancy_id=pregnancy.id;mother_id=mother.id;father_id=father.id
            response=client.post(f"/automation/{candidate_id}/accept",data={"first_name":"Review","last_name":"Baby","birth_global_day":"12"},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            with SessionLocal() as session:
                newborn=session.scalar(select(Record).where(Record.save_id==save_id,Record.kind=="sim",Record.data["game_sim_id"].as_string()==marker))
                pregnancy=session.get(Record,pregnancy_id)
                self.assertEqual(newborn.data["mother_id"],mother_id)
                self.assertEqual(newborn.data["father_id"],father_id)
                self.assertEqual(newborn.data["pregnancy_id"],pregnancy_id)
                self.assertIn(newborn.id,pregnancy.data["linked_newborn_ids"])
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_death_and_marriage_transitions_preserve_clock_time(self):
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Life transition telemetry", global_day=8, start_year=1600, days_per_year=4)
                session.add(save);session.flush()
                sim = Record(save_id=save.id, kind="sim", label="Transition Sim", data={"game_sim_id":"transition-sim","game_was_dead":False,"game_relationship_keys":[]})
                session.add(sim);session.flush()
                made = reconcile_sim(session,save,sim,{"is_dead":True,"detected_game_day":400,"detected_game_hour":6,"detected_game_minute":30,"detected_tracker_global_day":8,"relationships":[{"other_game_sim_id":"transition-other","category":"marriage"}]})
                self.assertEqual({item.data["action"] for item in made},{"sim_death","relationship_change"})
                for item in made:
                    self.assertEqual(item.data["payload"]["detected_game_hour"],6)
                    self.assertEqual(item.data["payload"]["detected_game_minute"],30)
                    self.assertEqual(item.data["payload"]["detected_tracker_global_day"],8)
                session.rollback()

    def test_accepting_death_and_marriage_records_exact_historical_dates(self):
        marker=uuid.uuid4().hex
        with TestClient(app) as client:
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Life event acceptance",global_day=8,start_year=1600,days_per_year=4)
                session.add(save);session.flush()
                first=Record(save_id=save.id,kind="sim",label="First Partner",data={"game_sim_id":"first-"+marker})
                second=Record(save_id=save.id,kind="sim",label="Second Partner",data={"game_sim_id":"second-"+marker})
                session.add_all([first,second]);session.flush()
                existing=Record(save_id=save.id,kind="relationship",label="First Partner & Second Partner",global_day=2,data={"partner1_id":first.id,"partner2_id":second.id,"type":"Marriage","status":"Active","start_global_day":2,"legally_married":True})
                death_candidate=Record(save_id=save.id,kind="game_candidate",label="Death detected",global_day=8,data={"action":"sim_death","sim_id":first.id,"status":"pending","source_key":"death-"+marker,"payload":{"detected_tracker_global_day":8,"detected_game_hour":6,"detected_game_minute":30,"death_type":"Old age"}})
                marriage_candidate=Record(save_id=save.id,kind="game_candidate",label="Marriage detected",global_day=8,data={"action":"relationship_change","sim_id":first.id,"status":"pending","source_key":"marriage-"+marker,"payload":{"other_game_sim_id":"second-"+marker,"category":"marriage","detected_tracker_global_day":8,"detected_game_hour":6,"detected_game_minute":30}})
                session.add_all([existing,death_candidate,marriage_candidate]);session.commit();save_id,first_id,second_id,relationship_id,death_candidate_id,marriage_candidate_id=save.id,first.id,second.id,existing.id,death_candidate.id,marriage_candidate.id
            death_response=client.post(f"/automation/{death_candidate_id}/accept",data={"death_global_day":"8","death_game_hour":"6","death_game_minute":"30","cause_of_death":"Old age","death_place":"Home"},follow_redirects=False)
            marriage_response=client.post(f"/automation/{marriage_candidate_id}/accept",data={"other_sim_id":second_id,"relationship_type":"Marriage","relationship_status":"Active","start_global_day":"8","marriage_game_hour":"6","marriage_game_minute":"30","legally_married":"on"},follow_redirects=False)
            self.assertEqual(death_response.status_code,303);self.assertEqual(marriage_response.status_code,303)
            with SessionLocal() as session:
                first=session.get(Record,first_id);relationship=session.get(Record,relationship_id)
                self.assertEqual(first.data["death_time"],"06:30");self.assertEqual(first.data["historical_death_date"],"October 25, 1601")
                death=session.scalar(select(Record).where(Record.save_id==save_id,Record.kind=="death",Record.data["sim_id"].as_string()==first_id))
                self.assertTrue(death.data["completed"]);self.assertEqual(death.data["historical_death_date"],"October 25, 1601")
                relationships=list(session.scalars(select(Record).where(Record.save_id==save_id,Record.kind=="relationship")))
                self.assertEqual(len(relationships),1);self.assertEqual(relationship.data["marriage_time"],"06:30");self.assertEqual(relationship.data["historical_marriage_date"],"October 25, 1601")
                self.assertEqual(session.get(Record,marriage_candidate_id).data["resolved_record_id"],relationship_id)
                session.rollback()

    def test_sims_weekday(self):
        self.assertEqual(sim_weekday(42), "Sunday")
        self.assertEqual(sim_weekday(43), "Monday")

    def test_dice_parser(self):
        self.assertEqual(parse("2d6+1"), (2, 6, 1))
        self.assertEqual(notation_for_roll("d20"), ("d20", "d20"))
        self.assertEqual(notation_for_roll("RNG", "60–120"), ("d61+59", "60–120"))
        with self.assertRaises(ValueError):
            parse("coin")

    def test_legacy_neon_mapping_preserves_catalog_identity_and_groups_causes(self):
        payload = legacy_neon.normalize_payload("events", {
            "event_id":"EVT-0001", "event_name":"Test event", "active":1,
            "start_global_day":-1399,
        }, "EVT-0001")
        mapped = legacy_neon.remap_payload(payload, {"EVT-0001":"record-id"})
        self.assertEqual(mapped["catalog_id"], "EVT-0001")
        self.assertEqual(mapped["event_id"], "record-id")
        self.assertTrue(mapped["active"])
        grouped = legacy_neon._prepared_rows("death_cause_pools", [
            {"death_group":"Adult", "cause":"Fever", "active":1},
            {"death_group":"Adult", "cause":"Accident", "active":1},
            {"death_group":"Adult", "cause":"Fever", "active":1},
        ])
        self.assertEqual(grouped, [{"death_group":"Adult", "causes":["Fever","Accident"], "active":True}])
        self.assertIn("event_rule", sync.SYNC_KINDS)
        self.assertIn("roll_rule_era", sync.SYNC_KINDS)

    def test_bad_roll_parser_supports_imported_lists_and_ranges(self):
        self.assertTrue(failed(5, "1 5"))
        self.assertFalse(failed(4, "1 5"))
        self.assertTrue(failed(20, "3 9 20"))
        self.assertTrue(failed(75, "60–120"))
        self.assertFalse(failed(59, "60—120"))

    def test_application_routes_and_audit(self):
        with TestClient(app) as client:
            health = client.get("/healthz")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["version"], "4.2.4")
            self.assertTrue(health.json()["clock_sync_ready"])
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/p/sims").status_code, 200)
            self.assertEqual(client.get("/p/automation").status_code, 200)
            result = client.post("/api/dice", json={"notation": "d20"})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json()["verified"])
            self.assertGreaterEqual(result.json()["total"], 1)
            self.assertLessEqual(result.json()["total"], 20)
            with SessionLocal() as session:
                audit = session.get(DiceAudit, result.json()["audit_id"])
                self.assertTrue(verify(audit))
                audit.total += 1
                self.assertFalse(verify(audit))

    def test_every_navigation_page_renders(self):
        with TestClient(app) as client:
            for page in FEATURES:
                with self.subTest(page=page):
                    response=client.get(f"/p/{page}")
                    self.assertEqual(response.status_code,200,response.text[:500])

    def test_tutorial_covers_setup_daily_play_clock_sync_and_backups(self):
        with TestClient(app) as client:
            page=client.get("/p/tutorial")
            self.assertEqual(page.status_code,200)
            self.assertIn("How to use the tracker",page.text)
            self.assertIn('id="quick-start"',page.text)
            self.assertIn('id="daily-routine"',page.text)
            self.assertIn('id="clock-sync"',page.text)
            self.assertIn('id="backups"',page.text)
            self.assertIn("Start SeveralUDO Clock Relay.bat",page.text)
            self.assertIn('href="/p/tutorial"',page.text)

    def test_events_catalog_filters_year_scope_location_and_roll_behavior(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                original=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=original.workspace_id,name=f"Event filters {marker}",global_day=13,start_year=1600,days_per_year=4)
                session.add(save);session.flush()
                records=[
                    Record(save_id=save.id,kind="event",label="English Plague",global_day=5,data={"start_global_day":5,"end_global_day":8,"scope":"Country","location":"England","roll_required":True,"active":True}),
                    Record(save_id=save.id,kind="event",label="French Festival",global_day=9,data={"start_global_day":9,"end_global_day":9,"scope":"Region","location":"France","roll_required":False,"active":True}),
                    Record(save_id=save.id,kind="event",label="Current War",global_day=13,data={"start_global_day":13,"end_global_day":16,"scope":"Global","location":"Europe","roll_required":True,"active":True}),
                ]
                session.add_all(records);session.flush();current_war_id=records[2].id
                event_roll=Record(save_id=save.id,kind="roll",label="Current War — Test Sim",global_day=13,data={"event_id":current_war_id,"roll_type":"Event — Current War","die":"d20","completed":False})
                session.add(event_roll);session.commit();original_id,save_id=original.id,save.id
            client.post("/saves/select",data={"save_id":save_id})
            page=client.get("/p/events?year=1601&scope=Country&location=England&rolls=required")
            self.assertEqual(page.status_code,200);self.assertIn("English Plague",page.text);self.assertNotIn("French Festival",page.text);self.assertNotIn("Current War",page.text)
            page=client.get("/p/events?status=active")
            self.assertIn("Current War",page.text);self.assertNotIn("French Festival",page.text)
            page=client.get("/p/events?rolls=reference")
            self.assertIn("French Festival",page.text);self.assertNotIn("English Plague",page.text)
            hidden=client.post(f"/api/events/{current_war_id}/interest",data={"hidden":"true","return_to":"/p/events"})
            self.assertEqual(hidden.status_code,200);self.assertNotIn("<strong>Current War</strong>",hidden.text)
            page=client.get("/p/events?interest=hidden")
            self.assertIn("Current War",page.text);self.assertIn("Show event again",page.text)
            self.assertNotIn("Current War",client.get("/p/today?task=events").text)
            self.assertNotIn("Current War — Test Sim",client.get("/p/rolls").text)
            with SessionLocal() as session:
                hidden_event=session.get(Record,current_war_id);self.assertTrue(hidden_event.data.get("ignored"))
                sim=Record(save_id=save_id,kind="sim",label="Event Test Sim",global_day=1,data={"birth_global_day":1,"country":"Europe"});session.add(sim);session.flush()
                schedule_rolls(session,session.get(ChronicleSave,save_id))
                generated=session.scalar(select(Record.id).where(Record.save_id==save_id,Record.kind=="roll",Record.data["source_id"].as_string()==current_war_id))
                self.assertIsNone(generated);session.commit()
            shown=client.post(f"/api/events/{current_war_id}/interest",data={"hidden":"false","return_to":"/p/events"})
            self.assertEqual(shown.status_code,200);self.assertIn("Current War",shown.text)
            self.assertIn("Current War — Test Sim",client.get("/p/rolls").text)
            with SessionLocal() as session:
                generated=session.scalar(select(Record.id).where(Record.save_id==save_id,Record.kind=="roll",Record.data["source_id"].as_string()==current_war_id))
                self.assertIsNotNone(generated)
            client.post("/saves/select",data={"save_id":original_id})
            with SessionLocal() as session:
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_clock_receiver_and_sync_protocol(self):
        docker_ignores = {
            line.strip() for line in Path(".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertNotIn("clock_bridge/SeveralUDOClockRelay.ps1", docker_ignores)
        self.assertNotIn("clock_bridge/Start SeveralUDO Clock Relay.bat", docker_ignores)
        with TestClient(app) as client:
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save_id, before = save.id, save.global_day
                session.execute(delete(ClockLink).where(ClockLink.save_id == save_id))
                session.commit()
            page = client.get("/p/clock")
            self.assertIn("Download a ready-to-install private kit", page.text)
            reusable = client.get("/downloads/clock-sync")
            self.assertEqual(reusable.status_code, 200)
            self.assertIn("Complete.zip", reusable.headers["content-disposition"])
            with zipfile.ZipFile(io.BytesIO(reusable.content)) as package:
                names = set(package.namelist())
                self.assertIn("SeveralUDOClockSync/SeveralUDOClockSync.ts4script", names)
                self.assertIn("SeveralUDOClockSync/SeveralUDOClockRelay.ps1", names)
                self.assertIn("SeveralUDOClockSync/Start SeveralUDO Clock Relay.bat", names)
                self.assertIn("SeveralUDOClockSync/Test SeveralUDO Clock Sync.bat", names)
                self.assertIn("SeveralUDOClockSync/Install or Update SeveralUDO Clock Sync.ps1", names)
                self.assertIn("SeveralUDOClockSync/Install or Update SeveralUDO Clock Sync.bat", names)
                self.assertIn("SeveralUDOClockSync/SeveralUDOClockRelay.ps1.backup.txt", names)
                self.assertIn("SeveralUDOClockSync/Start SeveralUDO Clock Relay.bat.backup.txt", names)
                self.assertIn("SeveralUDOClockSync/KIT CONTENTS - VERIFY.txt", names)
                self.assertIn("START HERE - SeveralUDO Clock Sync.txt", names)
                self.assertIn("SeveralUDOClockSync/config-template.json", names)
                self.assertIn("SeveralUDOClockSync/README - Install Clock Sync.txt", names)
                self.assertIn("SeveralUDOClockSync/TROUBLESHOOTING.txt", names)
                self.assertNotIn("SeveralUDOClockSync/config.json", names)
                self.assertEqual(
                    package.read("SeveralUDOClockSync/SeveralUDOClockRelay.ps1"),
                    package.read("SeveralUDOClockSync/SeveralUDOClockRelay.ps1.backup.txt"),
                )
                self.assertEqual(
                    package.read("SeveralUDOClockSync/Start SeveralUDO Clock Relay.bat"),
                    package.read("SeveralUDOClockSync/Start SeveralUDO Clock Relay.bat.backup.txt"),
                )
                self.assertIn(b"SeveralUDOClockRelay.ps1", package.read("SeveralUDOClockSync/KIT CONTENTS - VERIFY.txt"))
            relay = client.get("/downloads/clock-sync/relay")
            starter = client.get("/downloads/clock-sync/starter")
            self.assertEqual(relay.status_code, 200)
            self.assertEqual(starter.status_code, 200)
            self.assertIn("SeveralUDOClockRelay.ps1", relay.headers["content-disposition"])
            self.assertIn("Start SeveralUDO Clock Relay.bat", starter.headers["content-disposition"])
            configured = client.post("/downloads/clock-sync/configured")
            self.assertEqual(configured.status_code, 200)
            self.assertIn("Private.zip", configured.headers["content-disposition"])
            with zipfile.ZipFile(io.BytesIO(configured.content)) as package:
                private_config = json.loads(package.read("SeveralUDOClockSync/config.json"))
                self.assertTrue(private_config["enabled"])
                self.assertTrue(private_config["capture_portraits"])
                self.assertTrue(private_config["receiver_url"].endswith("/api/clock/report"))
                self.assertGreaterEqual(len(private_config["sync_token"]), 32)
            ping = client.get("/api/clock/ping", headers={"Authorization": f"Bearer {private_config['sync_token']}"})
            self.assertEqual(ping.status_code, 200)
            self.assertTrue(ping.json()["ok"])
            self.assertEqual(ping.json()["clock_sync_version"], "2.2.6")
            private_report = client.post("/api/clock/report", headers={"Authorization": f"Bearer {private_config['sync_token']}"}, json={"game_day": 60, "hour": 12, "minute": 0, "household_members": []})
            self.assertEqual(private_report.status_code, 200)
            clock_link = client.post("/api/clock/links").json()
            report = client.post("/api/clock/report", headers={"Authorization": f"Bearer {clock_link['token']}"}, json={"game_day": 60, "hour": 13, "minute": 45, "future_report_context":{"channel":"test"}, "household_members": [{
                "clock_sync_version":"2.1.0", "game_build":"1.999.1", "installed_packs":["Base Game"],
                "telemetry_capabilities":{"pregnancy":True,"portraits":False},
                "clock_sync_diagnostics":{"healthy":True,"errors":[]},
            }]})
            self.assertEqual(report.status_code, 200)
            self.assertEqual(report.json()["tracker_global_day"], before)
            self.assertTrue(report.json()["diagnostics_updated"])
            with SessionLocal() as session:
                diagnostic=session.scalar(select(Record).where(
                    Record.save_id==save_id,Record.kind=="clock_diagnostic",Record.deleted.is_(False),
                ))
                self.assertEqual(diagnostic.data["unmapped_report_telemetry"]["future_report_context"],{"channel":"test"})
            diagnostics_page = client.get("/p/clock")
            self.assertIn("SELF-DIAGNOSTICS", diagnostics_page.text)
            self.assertIn("Clock Sync 2.1.0", diagnostics_page.text)
            self.assertIn("future_report_context",diagnostics_page.text)
            report = client.post("/api/clock/report", headers={"Authorization": f"Bearer {clock_link['token']}"}, json={"game_day": 61, "hour": 2, "minute": 5, "household_members": []})
            self.assertEqual(report.json()["tracker_global_day"], before + 1)
            device = client.post("/api/sync/devices", data={"name": "Test desktop"}).json()
            record_id, change_id = uuid.uuid4().hex, uuid.uuid4().hex
            pushed = client.post("/api/sync/push", headers={"Authorization": f"Bearer {device['token']}"}, json={"after": 0, "changes": [{"change_id": change_id, "record_id": record_id, "kind": "sim", "operation": "upsert", "base_version": 0, "payload": {"label": "Ada Test", "global_day": 1, "birth_global_day": 1}}]})
            self.assertEqual(pushed.status_code, 200)
            self.assertEqual(pushed.json()["results"][0]["status"], "applied")
            duplicate = client.post("/api/sync/push", headers={"Authorization": f"Bearer {device['token']}"}, json={"after": 0, "changes": [{"change_id": change_id, "record_id": record_id, "kind": "sim", "operation": "upsert", "base_version": 0, "payload": {"label": "Ada Test"}}]})
            self.assertEqual(duplicate.json()["results"][0]["status"], "duplicate")
            conflict_change = uuid.uuid4().hex
            conflict = client.post("/api/sync/push", headers={"Authorization": f"Bearer {device['token']}"}, json={"after": 0, "changes": [{"change_id": conflict_change,"record_id":record_id,"kind":"sim","operation":"upsert","base_version":0,"payload":{"id":record_id,"kind":"sim","label":"Ada Desktop","global_day":2,"data":{"birth_global_day":2,"notes":"desktop copy"},"version":1,"deleted":False}}]})
            self.assertEqual(conflict.json()["results"][0]["status"],"conflict")
            conflict_id=conflict.json()["results"][0]["conflict_id"]
            resolved=client.post(f"/api/sync/conflicts/{conflict_id}/resolve",data={"keep":"desktop"},follow_redirects=False)
            self.assertEqual(resolved.status_code,303)
            with SessionLocal() as session:
                synced=session.get(Record,record_id)
                self.assertEqual(synced.label,"Ada Desktop")
                self.assertEqual(synced.data,{"birth_global_day":2,"notes":"desktop copy"})
            self.assertEqual(client.post(f"/api/sync/devices/{device['device_id']}/revoke",follow_redirects=False).status_code,303)
            rejected=client.get("/api/sync/pull",headers={"Authorization":f"Bearer {device['token']}"})
            self.assertEqual(rejected.status_code,401)

    def test_google_identity_provisioning_and_portrait_normalization(self):
        unique = uuid.uuid4().hex
        with SessionLocal() as session:
            user, workspace, recovery = auth.provision_google_user(session, {"sub": unique, "email": f"{unique}@example.test", "name": "Test Historian"})
            session.commit()
            self.assertTrue(recovery)
            self.assertEqual(user.display_name, "Test Historian")
            self.assertIsNotNone(auth.recover_user(session, user.email, recovery))
        image = Image.new("RGB", (2200, 1800), "#a17846")
        source = io.BytesIO(); image.save(source, "PNG")
        converted, mime = normalize_image(source.getvalue())
        self.assertEqual(mime, "image/webp")
        self.assertLess(len(converted), len(source.getvalue()))

    def test_returning_legacy_login_does_not_rerun_import_or_write(self):
        raw_code = f"legacy-{uuid.uuid4().hex}"
        email = f"returning-{uuid.uuid4().hex}@example.test"
        with SessionLocal() as session:
            template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
            workspace = session.get(Workspace, template.workspace_id)
            user = User(email=email, display_name="Returning player")
            session.add(user); session.flush()
            session.add(Membership(user_id=user.id, workspace_id=workspace.id, role="owner"))
            session.add(LegacyWorkspaceCode(
                workspace_id=workspace.id,
                code_hash=accounts.hash_secret(raw_code),
                label="Already migrated",
                created_by_user_id=user.id,
            ))
            session.commit(); user_id, workspace_id = user.id, workspace.id
        with SessionLocal() as session, mock.patch.object(
            legacy_neon, "import_owner_workspace", side_effect=AssertionError("legacy import must not run")
        ):
            user = session.get(User, user_id)
            workspace, imported = accounts.claim_legacy_code(session, user, raw_code)
            self.assertEqual(workspace.id, workspace_id)
            self.assertEqual(imported, [])
            self.assertFalse(session.new)
            self.assertFalse(session.dirty)
        self.assertIn("database is full", accounts.legacy_database_error_message(
            RuntimeError("could not extend file because project size limit has been exceeded")
        ).casefold())

    def test_today_excludes_past_and_completed_items(self):
        self.assertFalse(due_on_today(Record(kind="event", global_day=10, data={"active": 1, "end_global_day": 20}), 70))
        self.assertTrue(due_on_today(Record(kind="event", global_day=60, data={"active": 1, "end_global_day": 80}), 70))
        self.assertFalse(due_on_today(Record(kind="roll", global_day=60, data={"completed": True}), 70))
        self.assertTrue(due_on_today(Record(kind="roll", global_day=60, data={"completed": False}), 70))
        self.assertFalse(due_on_today(Record(kind="pregnancy", global_day=60, data={"status": "Delivered"}), 70))
        self.assertFalse(due_on_today(Record(kind="illness", global_day=60, data={"status": "Recovered"}), 70))

    def test_dead_sim_rolls_are_hidden_and_retired(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim=Record(save_id=save.id,kind="sim",label=f"Dead Roll Sim {marker}",global_day=1,data={"birth_global_day":1,"death_global_day":save.global_day})
                session.add(sim);session.flush()
                pending=Record(save_id=save.id,kind="roll",label=f"SHOULD-NOT-APPEAR-{marker}",global_day=max(1,save.global_day-1),data={"sim_id":sim.id,"roll_type":"Old obligation","die":"d20","completed":False})
                completed=Record(save_id=save.id,kind="roll",label=f"Completed-{marker}",global_day=max(1,save.global_day-1),data={"sim_id":sim.id,"roll_type":"Old completed obligation","die":"d20","completed":True})
                ghost=Record(save_id=save.id,kind="roll",label=f"GHOST-ROLL-SHOULD-APPEAR-{marker}",global_day=save.global_day,data={"sim_id":sim.id,"sim_name":sim.label,"roll_type":"Spirit remains after death","die":"d6","completed":False,"occult_roll":True,"occult_rule_key":"ghost_persistence","occult_type":"Ghost"})
                detected_dead=Record(save_id=save.id,kind="sim",label=f"Clock Dead Sim {marker}",global_day=1,data={"birth_global_day":1,"game_was_dead":True})
                session.add_all([pending,completed,ghost,detected_dead]);session.flush()
                pending_detected=Record(save_id=save.id,kind="roll",label=f"CLOCK-DEAD-SHOULD-NOT-APPEAR-{marker}",global_day=max(1,save.global_day-1),data={"sim_id":detected_dead.id,"roll_type":"Old obligation","die":"d20","completed":False})
                session.add(pending_detected);session.commit();save_id,sim_id,pending_id,completed_id,ghost_id,pending_detected_id=save.id,sim.id,pending.id,completed.id,ghost.id,pending_detected.id
            page=client.get("/p/today?task=rolls")
            self.assertEqual(page.status_code,200)
            self.assertNotIn(f"SHOULD-NOT-APPEAR-{marker}",page.text)
            self.assertNotIn(f"CLOCK-DEAD-SHOULD-NOT-APPEAR-{marker}",page.text)
            self.assertIn(f"GHOST-ROLL-SHOULD-APPEAR-{marker}",page.text)
            with SessionLocal() as session:
                save=session.get(ChronicleSave,save_id)
                schedule_rolls(session,save)
                session.flush()
                self.assertTrue(session.get(Record,pending_id).deleted)
                self.assertEqual(session.get(Record,pending_id).data["retired_reason"],"Sim is deceased")
                self.assertTrue(session.get(Record,pending_detected_id).deleted)
                self.assertFalse(session.get(Record,completed_id).deleted)
                self.assertFalse(session.get(Record,ghost_id).deleted)
                session.rollback()

    def test_today_dashboard_task_views_and_day_undo(self):
        with TestClient(app) as client:
            for task in ("rolls", "pregnancies", "events", "illnesses", "deaths"):
                response = client.get(f"/p/today?task={task}")
                self.assertEqual(response.status_code, 200)
                self.assertIn("workload-card", response.text)
                self.assertIn("Skip 7 days", response.text)
            simplified = client.get("/p/today?task=rolls")
            self.assertIn("Today’s work", simplified.text)
            self.assertIn("More from today", simplified.text)
            self.assertIn("tool-drawer", simplified.text)
            self.assertIn("Pregnancy-count roll", simplified.text)
            self.assertIn("Today settings", simplified.text)
            self.assertIn("/api/occult-rolls/toggle", simplified.text)
            self.assertIn("/today-focus", simplified.text)
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save_id, original = save.id, save.global_day
            changed = client.post(f"/api/saves/{save_id}/advance", data={"days": 7}, follow_redirects=False)
            self.assertEqual(changed.status_code, 303)
            with SessionLocal() as session:
                self.assertEqual(session.get(ChronicleSave, save_id).global_day, min(20000, original + 7))
            client.post("/api/today/undo", follow_redirects=False)
            with SessionLocal() as session:
                self.assertEqual(session.get(ChronicleSave, save_id).global_day, original)

    def test_today_death_confirmation_preserves_existing_date_and_time(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name=f"Death confirmation {marker}",global_day=20,start_year=1600,days_per_year=4)
                session.add(save);session.flush()
                existing={"death_global_day":12,"death_date":"Original date","historical_death_date":"February 14, 1602","historical_death_date_range":"Oct 1–Dec 31, 1602","death_date_precision":"exact","death_game_hour":6,"death_game_minute":30,"death_time":"06:30","death_confirmed":False,"cause_of_death":"Scheduled cause"}
                sim=Record(save_id=save.id,kind="sim",label=f"Preserved Death {marker}",global_day=1,data=existing)
                session.add(sim);session.flush()
                death=Record(save_id=save.id,kind="death",label=f"Death of {sim.label}",global_day=12,data={"sim_id":sim.id,"completed":False,**{key:value for key,value in existing.items() if key.startswith("death_") or key.startswith("historical_death_")}})
                session.add(death);session.commit();save_id,sim_id,death_id=save.id,sim.id,death.id
            response=client.post(f"/api/today/deaths/{sim_id}/confirm",data={"cause_of_death":"Confirmed cause","death_place":"Home","death_game_hour":"19","death_game_minute":"45"},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            with SessionLocal() as session:
                sim=session.get(Record,sim_id);death=session.get(Record,death_id)
                self.assertEqual(sim.data["death_global_day"],12);self.assertEqual(sim.data["death_date"],"Original date")
                self.assertEqual(sim.data["historical_death_date"],"February 14, 1602");self.assertEqual(sim.data["death_time"],"06:30")
                self.assertEqual(sim.data["death_game_hour"],6);self.assertEqual(sim.data["death_game_minute"],30)
                self.assertTrue(sim.data["death_confirmed"]);self.assertEqual(sim.data["cause_of_death"],"Confirmed cause")
                self.assertTrue(death.data["completed"]);self.assertEqual(death.data["death_time"],"06:30")
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_today_death_confirmation_creates_a_death_ledger_record(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name=f"Unscheduled death {marker}",global_day=23,start_year=1600,days_per_year=4)
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label=f"Unscheduled {marker}",global_day=1,data={"birth_global_day":1})
                session.add(sim);session.commit();save_id,sim_id=save.id,sim.id
            response=client.post(f"/api/today/deaths/{sim_id}/confirm",data={"cause_of_death":"Malaria","death_place":"Home","death_game_hour":"2","death_game_minute":"25"},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            with SessionLocal() as session:
                sim=session.get(Record,sim_id)
                death=session.scalar(select(Record).where(Record.save_id==save_id,Record.kind=="death",Record.data["sim_id"].as_string()==sim_id))
                self.assertIsNotNone(death);self.assertTrue(death.data["completed"])
                self.assertEqual(death.global_day,23);self.assertEqual(death.data["cause"],"Malaria");self.assertEqual(death.data["death_time"],"02:25")
                self.assertTrue(sim.data["death_confirmed"]);self.assertEqual(sim.data["cause_of_death"],"Malaria")
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_due_marriage_roll_appears_on_today_and_is_nonlethal(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name=f"Marriage roll {marker}",global_day=80,start_year=1500,days_per_year=4,settings={"marriage_min_age_days":48})
                session.add(save);session.flush()
                eligible=Record(save_id=save.id,kind="sim",label=f"Eligible {marker}",global_day=20,data={"birth_global_day":20})
                heir=Record(save_id=save.id,kind="sim",label=f"Heir {marker}",global_day=20,data={"birth_global_day":20})
                married=Record(save_id=save.id,kind="sim",label=f"Married {marker}",global_day=20,data={"birth_global_day":20})
                spouse=Record(save_id=save.id,kind="sim",label=f"Spouse {marker}",global_day=20,data={"birth_global_day":20})
                future=Record(save_id=save.id,kind="sim",label=f"Future {marker}",global_day=40,data={"birth_global_day":40})
                pretracking=Record(save_id=save.id,kind="sim",label=f"Before tracking {marker}",global_day=-60,data={"birth_global_day":-60})
                session.add_all([eligible,heir,married,spouse,future,pretracking]);session.flush();save.settings={"marriage_min_age_days":48,"roll_tracking_start_day":1,"current_heir_id":heir.id}
                relationship=Record(save_id=save.id,kind="relationship",label=f"Married pair {marker}",global_day=60,data={"partner1_id":married.id,"partner2_id":spouse.id,"type":"Marriage","legally_married":True})
                rule=Record(save_id=save.id,kind="planner_rule",label="Imported planner rules",data={"rule_key":"non_heir_marriage","start_year":1500,"end_year":1699,"die":"d8","bad_results":"1: Does not marry; 2-8: May marry","active":True})
                session.add_all([relationship,rule]);session.flush()
                self.assertEqual(schedule_rolls(session,save),1);session.commit();save_id,eligible_id=save.id,eligible.id
            client.post("/saves/select",data={"save_id":save_id},follow_redirects=False)
            page=client.get("/p/today?task=rolls&roll_kind=marriage")
            self.assertEqual(page.status_code,200);self.assertIn(f"Eligible {marker}",page.text);self.assertIn("Non-Heir Marriage Eligibility",page.text);self.assertIn("Roll d8",page.text)
            self.assertNotIn(f"<h3>Heir {marker}</h3>",page.text);self.assertNotIn(f"<h3>Married {marker}</h3>",page.text);self.assertNotIn(f"<h3>Future {marker}</h3>",page.text);self.assertNotIn(f"<h3>Before tracking {marker}</h3>",page.text)
            with SessionLocal() as session:
                save=session.get(ChronicleSave,save_id);roll=session.scalar(select(Record).where(Record.save_id==save_id,Record.kind=="roll",Record.data["sim_id"].as_string()==eligible_id))
                self.assertEqual(roll.global_day,68);self.assertEqual(roll.data["bad_results"],"1");self.assertTrue(roll.data["nonlethal"])
                self.assertEqual(schedule_rolls(session,save),0);result=complete_roll(session,save,roll,1);session.commit();roll_id=roll.id
                self.assertEqual(result["outcome"],"Does not marry")
            with SessionLocal() as session:
                self.assertIsNone(session.get(Record,eligible_id).data.get("death_global_day"));self.assertEqual(session.get(Record,roll_id).data["outcome"],"Does not marry")
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_pregnancy_count_roll_uses_current_era_and_saves_exact_allowance(self):
        marker=uuid.uuid4().hex[:10]
        self.assertEqual(pregnancy_count_result(13,"1-11: Schedule that many pregnancies; 12-15: One pregnancy; 16-20: No pregnancy"),(1,"1 pregnancy"))
        self.assertEqual(pregnancy_count_result(17,"1-11: Schedule that many pregnancies; 12-15: One pregnancy; 16-20: No pregnancy"),(0,"No pregnancies"))
        with TestClient(app) as client:
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name=f"Pregnancy count {marker}",global_day=80,start_year=1500,days_per_year=4)
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label=f"Planner Sim {marker}",global_day=10,data={"birth_global_day":10})
                rule=Record(save_id=save.id,kind="planner_rule",label="Imported planner rules",data={"rule_key":"side_pregnancy","start_year":1500,"end_year":1699,"die":"d12","bad_results":"1-10: Schedule that many pregnancies; 11-12: No pregnancy","active":True})
                session.add_all([sim,rule]);session.flush()
                active=Record(save_id=save.id,kind="pregnancy",label="Counted active pregnancy",global_day=83,data={"mother_id":sim.id,"conception_global_day":79,"due_global_day":83,"status":"Active"})
                loss=Record(save_id=save.id,kind="pregnancy",label="Counted miscarriage",global_day=82,data={"mother_id":sim.id,"conception_global_day":78,"due_global_day":82,"status":"Miscarriage"})
                cancelled=Record(save_id=save.id,kind="pregnancy",label="Excluded cancellation",global_day=81,data={"mother_id":sim.id,"conception_global_day":77,"due_global_day":81,"status":"Cancelled"})
                session.add_all([active,loss,cancelled]);session.commit();save_id,sim_id=save.id,sim.id
            client.post("/saves/select",data={"save_id":save_id},follow_redirects=False)
            first=client.post("/api/today/pregnancy-count-rolls",data={"sim_id":sim_id},follow_redirects=False)
            second=client.post("/api/today/pregnancy-count-rolls",data={"sim_id":sim_id},follow_redirects=False)
            self.assertEqual(first.status_code,303);self.assertEqual(second.status_code,303)
            page=client.get("/p/today?task=rolls&roll_kind=pregnancy-count")
            self.assertEqual(page.status_code,200);self.assertIn(f"Planner Sim {marker}",page.text);self.assertIn("Pregnancy Count",page.text);self.assertIn("Roll d12",page.text);self.assertIn("Result table:",page.text)
            with SessionLocal() as session:
                rolls=list(session.scalars(select(Record).where(Record.save_id==save_id,Record.kind=="roll",Record.data["pregnancy_count_roll"].as_boolean().is_(True))))
                self.assertEqual(len(rolls),1);roll=rolls[0]
                self.assertEqual(roll.global_day,80);self.assertEqual(roll.data["planner_year"],1519);self.assertEqual(roll.data["die"],"d12");self.assertTrue(roll.data["nonlethal"]);self.assertEqual(roll.data["bad_results"],"")
                result=complete_roll(session,session.get(ChronicleSave,save_id),roll,7);session.commit();roll_id=roll.id
                self.assertEqual(result["outcome"],"7 pregnancies");self.assertEqual(result["pregnancy_count"],7)
            with SessionLocal() as session:
                roll=session.get(Record,roll_id);sim=session.get(Record,sim_id)
                self.assertEqual(roll.data["pregnancy_count"],7);self.assertEqual(roll.data["outcome"],"7 pregnancies");self.assertIsNone(sim.data.get("death_global_day"))
                self.assertEqual(sim.data["pregnancy_allowance_count"],7);self.assertEqual(sim.data["pregnancy_allowance_year"],1519);self.assertEqual(sim.data["pregnancy_allowances"]["1519"]["roll_id"],roll_id)
            profile=client.get(f"/sims/{sim_id}")
            self.assertEqual(profile.status_code,200);self.assertIn("Pregnancy allowance",profile.text);self.assertIn("<span>Allowed</span><strong>7</strong>",profile.text);self.assertIn("<span>Used</span><strong>2</strong>",profile.text);self.assertIn("<span>Remaining</span><strong>5</strong>",profile.text)
            with SessionLocal() as session:
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_imported_marriage_table_is_interpreted_and_never_kills(self):
        marker=uuid.uuid4().hex[:10]
        table="1: Does not marry; 2-8: May marry"
        self.assertEqual(marriage_roll_result(1,table),"Does not marry");self.assertEqual(marriage_roll_result(7,table),"May marry")
        with SessionLocal() as session:
            template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
            save=ChronicleSave(workspace_id=template.workspace_id,name=f"Marriage safety {marker}",global_day=80,start_year=1500,days_per_year=4)
            session.add(save);session.flush()
            sim=Record(save_id=save.id,kind="sim",label=f"Legacy marriage Sim {marker}",global_day=1,data={"birth_global_day":1})
            session.add(sim);session.flush()
            roll=Record(save_id=save.id,kind="roll",label=f"Marriage roll {marker}",global_day=80,data={"sim_id":sim.id,"sim_name":sim.label,"roll_type":"Non-Heir Marriage Eligibility","die":"d8","bad_results":table,"completed":False})
            session.add(roll);session.flush();result=complete_roll(session,save,roll,1);session.commit();save_id,sim_id,roll_id=save.id,sim.id,roll.id
            self.assertEqual(result["outcome"],"Does not marry")
        with SessionLocal() as session:
            self.assertIsNone(session.get(Record,sim_id).data.get("death_global_day"));self.assertTrue(session.get(Record,roll_id).data["nonlethal"])
            session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_completed_pregnancy_count_roll_is_backfilled_without_rerolling(self):
        marker=uuid.uuid4().hex[:10]
        with SessionLocal() as session:
            template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
            save=ChronicleSave(workspace_id=template.workspace_id,name=f"Allowance backfill {marker}",global_day=121,start_year=1500,days_per_year=4)
            session.add(save);session.flush()
            sim=Record(save_id=save.id,kind="sim",label=f"Backfilled Sim {marker}",global_day=1,data={"birth_global_day":1})
            session.add(sim);session.flush()
            roll=Record(save_id=save.id,kind="roll",label=f"Backfilled allowance {marker}",global_day=121,data={"sim_id":sim.id,"pregnancy_count_roll":True,"planner_year":1530,"actual":4,"pregnancy_count":4,"outcome":"4 pregnancies","completed":True,"completed_global_day":121})
            session.add(roll);session.flush();self.assertEqual(backfill_pregnancy_allowances(session,save),1);self.assertEqual(backfill_pregnancy_allowances(session,save),0);session.commit();save_id,sim_id,roll_id=save.id,sim.id,roll.id
        with SessionLocal() as session:
            sim=session.get(Record,sim_id);self.assertEqual(sim.data["pregnancy_allowance_count"],4);self.assertEqual(sim.data["pregnancy_allowances"]["1530"]["roll_id"],roll_id)
            session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_today_displays_every_roll_result_completed_on_the_current_day(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                manual=Record(save_id=save.id,kind="roll",label=f"Manual result {marker}",global_day=save.global_day,data={"roll_type":"Aging","die":"d20","bad_results":"1","completed":False})
                existing=Record(save_id=save.id,kind="roll",label=f"Existing result {marker}",global_day=max(1,save.global_day-1),data={"roll_type":"Event","die":"d12","bad_results":"1","actual":7,"outcome":"Passed","completed":True,"completed_global_day":save.global_day})
                session.add_all([manual,existing]);session.commit();manual_id,existing_id=manual.id,existing.id
            completed=client.post(f"/api/rolls/{manual_id}/complete",data={"actual":"1","outcome":""},headers={"referer":"/p/today?task=rolls"},follow_redirects=True)
            self.assertEqual(completed.status_code,200)
            self.assertIn("THE CARVED DICE HAVE SPOKEN",completed.text)
            page=client.get("/p/today?task=rolls")
            self.assertEqual(page.status_code,200)
            self.assertIn("Completed roll results",page.text)
            self.assertIn(f"Manual result {marker}",page.text)
            self.assertIn(f"Existing result {marker}",page.text)
            self.assertIn(">1<",page.text)
            self.assertIn(">7<",page.text)
            with SessionLocal() as session:
                session.execute(delete(Record).where(Record.id.in_([manual_id,existing_id])));session.commit()

    def test_today_summarizes_every_occult_roll_and_detected_change(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                pending=Record(save_id=save.id,kind="roll",label=f"Pending vampire hunt {marker}",global_day=save.global_day,data={"occult_roll":True,"occult_type":"Vampire","roll_type":"Vampire hunt","die":"d2","result_rules":"1: Hunt; 2: No hunt","completed":False})
                resolved=Record(save_id=save.id,kind="roll",label=f"Resolved ghost fate {marker}",global_day=max(1,save.global_day-1),data={"occult_roll":True,"occult_type":"Ghost","roll_type":"Spirit remains","die":"d6","actual":1,"outcome":"Ghost remains","result_rules":"1: Ghost remains; 2-6: Moves on","completed":True,"completed_global_day":save.global_day})
                detected=Record(save_id=save.id,kind="game_history",label=f"Occult state changed {marker}",global_day=save.global_day,data={"category":"occult","sim_name":"Detected Sim","from":"Human","to":"Werewolf"})
                session.add_all([pending,resolved,detected]);session.commit();record_ids=[pending.id,resolved.id,detected.id]
            page=client.get("/p/today")
            self.assertEqual(page.status_code,200)
            self.assertIn("Occult chronicle",page.text)
            self.assertIn(f"Pending vampire hunt {marker}",page.text)
            self.assertIn(f"Resolved ghost fate {marker}",page.text)
            self.assertIn(f"Occult state changed {marker}",page.text)
            self.assertIn("Ghost remains",page.text)
            self.assertIn("Human → Werewolf",page.text)
            with SessionLocal() as session:
                session.execute(delete(Record).where(Record.id.in_(record_ids)));session.commit()

    def test_occult_roll_automation_has_a_global_one_click_toggle(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                original=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                temporary=ChronicleSave(workspace_id=original.workspace_id,name=f"Occult toggle {marker}",global_day=25,start_year=1500,days_per_year=4,settings={})
                session.add(temporary);session.commit();original_id,save_id=original.id,temporary.id
            client.post("/saves/select",data={"save_id":save_id})
            page=client.get("/p/today")
            self.assertEqual(page.status_code,200)
            self.assertIn('aria-label="Occult roll automation"',page.text)
            self.assertIn("Automatic occult rolls are off",page.text)
            enabled=client.post("/api/occult-rolls/toggle",data={"enabled":"true","return_to":"/p/today"},follow_redirects=False)
            self.assertEqual(enabled.status_code,303);self.assertEqual(enabled.headers["location"],"/p/today")
            page=client.get("/p/today")
            self.assertIn("Automatic occult rolls are on",page.text)
            self.assertIn("Occult roll auto-generation is on",page.text)
            disabled=client.post("/api/occult-rolls/toggle",data={"enabled":"false","return_to":"/p/today"},follow_redirects=False)
            self.assertEqual(disabled.status_code,303)
            with SessionLocal() as session:
                self.assertFalse(session.get(ChronicleSave,save_id).settings["automatic_occult_rolls"])
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()
            client.post("/saves/select",data={"save_id":original_id})

    def test_today_rule_workbench_creates_deduplicated_future_rule_followups(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim=Record(save_id=save.id,kind="sim",label=f"Workbench Sim {marker}",global_day=1,data={"birth_global_day":1})
                parent=Record(save_id=save.id,kind="future_rule",label=f"Future parent {marker}",data={"rule_key":f"parent-{marker}","die":"d8","trigger_results":"1","result_rules":"1: Something happens","active":True})
                child=Record(save_id=save.id,kind="future_rule",label=f"Future follow-up {marker}",data={"rule_key":f"child-{marker}","triggered_by":f"parent-{marker}","die":"d6","trigger_results":"1","result_rules":"1: Follow-up occurs","active":True})
                session.add_all([sim,parent,child]);session.flush()
                origin=Record(save_id=save.id,kind="roll",label=f"Triggered future outcome {marker}",global_day=save.global_day,data={"sim_id":sim.id,"sim_name":sim.label,"source_rule_key":f"parent-{marker}","rule_generated":True,"die":"d8","actual":1,"outcome":"Something happens","completed":True,"completed_global_day":save.global_day,"triggered":True})
                session.add(origin);session.commit();save_id,sim_id,parent_id,child_id,origin_id=save.id,sim.id,parent.id,child.id,origin.id
            page=client.get("/p/today")
            self.assertEqual(page.status_code,200);self.assertIn("Act on the rules",page.text);self.assertIn(f"Future follow-up {marker}",page.text)
            form={"rule_id":child_id,"sim_id":sim_id,"global_day":"12","origin_roll_id":origin_id,"context_note":"Verified future-rule situation","return_to":"/p/today#rule-workbench"}
            self.assertEqual(client.post("/api/rule-rolls/create",data=form,follow_redirects=False).status_code,303)
            self.assertEqual(client.post("/api/rule-rolls/create",data=form,follow_redirects=False).status_code,303)
            self.assertEqual(client.post(f"/api/rule-actions/{origin_id}/reviewed",follow_redirects=False).status_code,303)
            with SessionLocal() as session:
                created=list(session.scalars(select(Record).where(Record.save_id==save_id,Record.kind=="roll",Record.data["origin_roll_id"].as_string()==origin_id)))
                self.assertEqual(len(created),1);self.assertEqual(created[0].data["source_rule_kind"],"future_rule")
                self.assertEqual(created[0].data["rule_context"],"Verified future-rule situation")
                complete_roll(session,session.get(ChronicleSave,save_id),created[0],1);self.assertTrue(created[0].data["triggered"])
                self.assertTrue(session.get(Record,origin_id).data["rule_followup_reviewed"])
                session.execute(delete(Record).where(Record.save_id==save_id,Record.id.in_([sim_id,parent_id,child_id,origin_id,created[0].id])));session.commit()

    def test_lethal_occult_followup_uses_automatic_death_handling(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name=f"Lethal occult {marker}",global_day=40,start_year=1500,days_per_year=4,settings={"automatic_death_causes":True})
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label=f"Accused Vampire {marker}",global_day=1,data={"birth_global_day":1})
                rule=Record(save_id=save.id,kind="occult_rule",label="Accused Sim dies in a vampire hunt",data={"rule_key":"vampire_accused_death","occult":"Vampire","die":"d4","trigger_results":"3","result_rules":"3: Accused Sim dies; all others: Survives","active":True})
                session.add_all([sim,rule]);session.flush();roll,created=create_rule_roll_record(session,save,rule,sim,40)
                self.assertTrue(created);self.assertEqual(roll.data["bad_results"],"3");self.assertFalse(roll.data["nonlethal"])
                result=complete_roll(session,save,roll,3)
                self.assertTrue(result["death_created"]);self.assertEqual(sim.data["death_global_day"],40)
                session.rollback()

    def test_imported_roll_rules_use_lifecycle_days_only(self):
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Roll scheduling test", global_day=1, start_year=1550, days_per_year=4)
                session.add(save); session.flush()
                sim = Record(save_id=save.id, kind="sim", label="Test Sim", global_day=9, data={"birth_global_day": 9})
                infant = Record(save_id=save.id, kind="roll_rule", label="Infant", data={"die": "d20", "active": True})
                maternal = Record(save_id=save.id, kind="roll_rule", label="Maternal — Adult", data={"die": "d20", "active": True})
                session.add_all([sim, infant, maternal]); session.flush()
                self.assertEqual(schedule_rolls(session, save), 1)
                roll = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "roll"))
                self.assertEqual(roll.global_day, 10)
                self.assertEqual(roll.data["due_global_day"], 10)
                session.rollback()

    def test_scheduler_never_creates_prechallenge_rolls_and_retires_pending_ones(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Challenge boundary rolls",global_day=20,start_year=1550,days_per_year=4,settings={"maternal_rolls_enabled":True})
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label="Pre-challenge Sim",global_day=-60,data={"birth_global_day":-60,"country":"England"})
                pre_age=Record(save_id=save.id,kind="roll_rule",label="Early milestone",data={"age_days":20,"die":"d20","active":True})
                post_age=Record(save_id=save.id,kind="roll_rule",label="Challenge milestone",data={"age_days":70,"die":"d20","active":True})
                maternal=Record(save_id=save.id,kind="roll_rule",label="Maternal — Teen",data={"age_days":None,"die":"d20","bad_results":"1","active":True})
                session.add_all([sim,pre_age,post_age,maternal]);session.flush()
                pre_pregnancy=Record(save_id=save.id,kind="pregnancy",label="Before challenge",global_day=-2,data={"mother_id":sim.id,"due_global_day":-2,"status":"Active","maternal_rolls_required":True})
                post_pregnancy=Record(save_id=save.id,kind="pregnancy",label="During challenge",global_day=4,data={"mother_id":sim.id,"due_global_day":4,"status":"Active","maternal_rolls_required":True})
                pre_event=Record(save_id=save.id,kind="event",label="Earlier plague",global_day=-5,data={"start_global_day":-5,"end_global_day":-2,"scope":"Global","location":"Global","roll_required":True,"active":True})
                post_event=Record(save_id=save.id,kind="event",label="Challenge plague",global_day=3,data={"start_global_day":3,"end_global_day":6,"scope":"Global","location":"Global","roll_required":True,"active":True})
                stale=Record(save_id=save.id,kind="roll",label="Old pending obligation",global_day=-8,data={"sim_id":sim.id,"roll_type":"Old roll","due_global_day":-8,"source":"event:old","completed":False})
                completed=Record(save_id=save.id,kind="roll",label="Completed historical result",global_day=-7,data={"sim_id":sim.id,"roll_type":"Historical roll","due_global_day":-7,"source":"aging:old","completed":True})
                session.add_all([pre_pregnancy,post_pregnancy,pre_event,post_event,stale,completed]);session.flush()
                self.assertEqual(schedule_rolls(session,save),3)
                active_rolls=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.deleted.is_(False))))
                self.assertEqual(sorted(int(roll.global_day) for roll in active_rolls),[-7,3,4,10])
                self.assertTrue(completed in active_rolls);self.assertTrue(stale.deleted)
                self.assertNotIn("Earlier plague",{roll.label.split(" — ",1)[0] for roll in active_rolls})
                self.assertEqual(schedule_rolls(session,save),0)
                session.rollback()

    def test_scheduler_recognizes_imported_roll_without_source_token(self):
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Imported roll identity test", global_day=20, start_year=1550, days_per_year=4)
                session.add(save); session.flush()
                sim = Record(save_id=save.id, kind="sim", label="Imported Sim", global_day=9, data={"birth_global_day": 9})
                rule = Record(save_id=save.id, kind="roll_rule", label="Infant", data={"age_days": 1, "die": "d20", "active": True})
                session.add_all([sim, rule]); session.flush()
                imported = Record(save_id=save.id, kind="roll", label="Imported Sim — Infant", global_day=10, data={"sim_id":sim.id,"roll_type":"Infant","completed":True,"outcome":"Safe result"})
                session.add(imported); session.flush()
                self.assertEqual(schedule_rolls(session, save), 0)
                self.assertEqual(len(list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll")))), 1)
                session.rollback()

    def test_maternal_rolls_are_scheduled_and_miscarriages_are_excluded(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Maternal scheduling",global_day=20,start_year=1550,days_per_year=4,settings={"maternal_rolls_enabled":True})
                session.add(save);session.flush()
                mother=Record(save_id=save.id,kind="sim",label="Test Mother",global_day=1,data={"birth_global_day":1})
                rule=Record(save_id=save.id,kind="roll_rule",label="Maternal — Preteen",data={"die":"d20","bad_results":"1 5","active":True})
                session.add_all([mother,rule]);session.flush()
                active=Record(save_id=save.id,kind="pregnancy",label="Active",global_day=20,data={"mother_id":mother.id,"due_global_day":20,"status":"Active","maternal_rolls_required":True})
                miscarriage=Record(save_id=save.id,kind="pregnancy",label="Loss",global_day=20,data={"mother_id":mother.id,"due_global_day":20,"status":"Miscarriage","maternal_rolls_required":True})
                session.add_all([active,miscarriage]);session.flush()
                self.assertEqual(schedule_rolls(session,save),1)
                roll=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll"))
                self.assertEqual(roll.data["source_id"],active.id)
                self.assertIn("Maternal",roll.data["roll_type"])
                session.rollback()

    def test_household_membership_and_pregnancy_newborn_workflows(self):
        marker = uuid.uuid4().hex[:8]
        with TestClient(app) as client:
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                mother = Record(save_id=save.id, kind="sim", label=f"Mother {marker}", global_day=1, data={"sim_number":"SIM-9801","birth_global_day":1,"first_name":"Mother"})
                father = Record(save_id=save.id, kind="sim", label=f"Father {marker}", global_day=1, data={"sim_number":"SIM-9802","birth_global_day":1,"first_name":"Father"})
                session.add_all([mother, father]); session.commit(); mother_id, father_id = mother.id, father.id

            household_response = client.post("/households", data={"name":f"House {marker}","head_sim_id":mother_id,"member_ids":[mother_id,father_id],"active":"on"}, follow_redirects=False)
            self.assertEqual(household_response.status_code, 303)
            household_id = household_response.headers["location"].rsplit("/", 1)[-1]
            self.assertEqual(client.get(f"/households/{household_id}").status_code, 200)
            with SessionLocal() as session:
                self.assertEqual(session.get(Record,mother_id).data["current_household_id"],household_id)
                self.assertEqual(session.get(Record,father_id).data["current_household_id"],household_id)

            pregnancy_response = client.post("/pregnancies", data={"mother_id":mother_id,"father_id":father_id,"conception_global_day":"30","due_global_day":"34","babies_expected":"1","status":"Active","maternal_rolls":"on","newborn_rolls":"on"}, follow_redirects=False)
            self.assertEqual(pregnancy_response.status_code, 303)
            pregnancy_id = pregnancy_response.headers["location"].rsplit("/", 1)[-1]
            newborn_response = client.post(f"/pregnancies/{pregnancy_id}/newborns", data={"first_name":f"Baby{marker}","last_name":"Test","sex":"Female","birth_global_day":"34"}, follow_redirects=False)
            self.assertEqual(newborn_response.status_code, 303)
            with SessionLocal() as session:
                newborn = session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="sim",Record.data["pregnancy_id"].as_string()==pregnancy_id))
                pregnancy = session.get(Record,pregnancy_id)
                self.assertIsNotNone(newborn)
                self.assertNotIn(newborn.data["sim_number"], {"SIM-9801","SIM-9802"})
                self.assertEqual(newborn.data["current_household_id"],household_id)
                self.assertEqual(newborn.data["mother_id"],mother_id)
                self.assertEqual(pregnancy.data["status"],"Delivered")
                self.assertEqual(pregnancy.data["babies_delivered"],1)
            self.assertEqual(client.get(f"/pregnancies/{pregnancy_id}").status_code,200)

    def test_native_roll_uses_configured_die_and_completes(self):
        with TestClient(app) as client:
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                roll = Record(save_id=save.id, kind="roll", label="Native d6 test", global_day=save.global_day, data={"die": "d6", "bad_results": "1", "completed": False})
                session.add(roll); session.commit(); roll_id = roll.id
            response = client.post(f"/api/rolls/{roll_id}/roll", headers={"referer": "http://testserver/p/rolls"}, follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn("THE CARVED DICE HAVE SPOKEN", response.text)
            with SessionLocal() as session:
                completed = session.get(Record, roll_id)
                self.assertTrue(completed.data["completed"])
                self.assertGreaterEqual(completed.data["actual"], 1)
                self.assertLessEqual(completed.data["actual"], 6)
                audit = session.scalar(select(DiceAudit).where(DiceAudit.context == "roll", DiceAudit.context_id == roll_id))
                self.assertEqual(audit.notation, "d6")
                session.delete(completed); session.delete(audit); session.commit()

    def test_death_automatically_ends_active_illnesses(self):
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim = Record(save_id=save.id, kind="sim", label="Ill Test", data={"birth_global_day": 1, "death_confirmed": True})
                session.add(sim); session.flush()
                active = Record(save_id=save.id, kind="illness", label="Ill Test — Fever", global_day=5, data={"sim_id": sim.id, "status": "Active", "onset_global_day": 5})
                recovered = Record(save_id=save.id, kind="illness", label="Ill Test — Cold", global_day=2, data={"sim_id": sim.id, "status": "Recovered", "end_global_day": 4})
                session.add_all([active, recovered]); session.flush()
                self.assertEqual(end_illnesses_for_death(session, save, sim, 12), 1)
                self.assertEqual(active.data["status"], "Deceased")
                self.assertEqual(active.data["end_global_day"], 12)
                self.assertEqual(recovered.data["end_global_day"], 4)
                session.rollback()

    def test_any_failed_sim_roll_automatically_schedules_death(self):
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(
                    workspace_id=template.workspace_id,
                    name="Universal failed-roll death",
                    global_day=40,
                    start_year=1550,
                    days_per_year=4,
                    settings={"automatic_death_causes": True},
                )
                session.add(save); session.flush()
                sim = Record(save_id=save.id, kind="sim", label="Roll Test Sim", global_day=1, data={"birth_global_day": 1})
                session.add(sim); session.flush()
                illness = Record(save_id=save.id, kind="illness", label="Roll Test Sim — Fever", global_day=38, data={"sim_id": sim.id, "status": "Active", "onset_global_day": 38})
                roll = Record(save_id=save.id, kind="roll", label="Roll Test Sim — Custom danger", global_day=40, data={"sim_id": sim.id, "sim_name": sim.label, "roll_type": "Custom danger", "die": "d20", "bad_results": "1", "completed": False})
                future = Record(save_id=save.id, kind="roll", label="Roll Test Sim — Later obligation", global_day=50, data={"sim_id": sim.id, "roll_type": "Later obligation", "die": "d20", "bad_results": "1", "completed": False})
                session.add_all([illness, roll, future]); session.flush()

                result = complete_roll(session, save, roll, 1)

                self.assertEqual(result["outcome"], "Failed")
                self.assertIsNotNone(result["death"])
                self.assertEqual(sim.data["death_global_day"], 40)
                self.assertTrue(sim.data["cause_of_death"])
                self.assertEqual(illness.data["status"], "Deceased")
                self.assertTrue(future.deleted)
                death = session.get(Record, result["death"]["id"])
                self.assertEqual(death.data["sim_id"], sim.id)
                self.assertEqual(death.data["source_roll_id"], roll.id)
                session.rollback()

    def test_failed_roll_moves_an_existing_death_earlier_but_never_later(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Earlier death wins",global_day=40,start_year=1550,days_per_year=4)
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label="Reschedule Test",global_day=1,data={"birth_global_day":1,"death_global_day":80,"death_date":"Old exact date","historical_death_date":"Old exact date","cause_of_death":"Old schedule","death_confirmed":False})
                session.add(sim);session.flush()
                scheduled=Record(save_id=save.id,kind="death",label="Death of Reschedule Test",global_day=80,data={"sim_id":sim.id,"cause":"Old schedule","completed":False})
                illness=Record(save_id=save.id,kind="illness",label="Reschedule Test — Fever",global_day=35,data={"sim_id":sim.id,"status":"Deceased","outcome":"Ended by death","end_global_day":80})
                roll=Record(save_id=save.id,kind="roll",label="Reschedule Test — Danger",global_day=40,data={"sim_id":sim.id,"roll_type":"Danger","die":"d20","bad_results":"1","death_window_start":50,"death_window_end":50,"completed":False})
                future=Record(save_id=save.id,kind="roll",label="Reschedule Test — Future",global_day=70,data={"sim_id":sim.id,"roll_type":"Future","die":"d20","bad_results":"1","completed":False})
                session.add_all([scheduled,illness,roll,future]);session.flush()
                result=complete_roll(session,save,roll,1)
                self.assertTrue(result["death_changed"])
                self.assertFalse(result["death_created"])
                self.assertEqual(result["death"]["id"],scheduled.id)
                self.assertEqual(sim.data["death_global_day"],50)
                self.assertNotIn("death_date",sim.data)
                self.assertNotIn("historical_death_date",sim.data)
                self.assertEqual(scheduled.global_day,50)
                self.assertEqual(scheduled.data["rescheduled_from_global_day"],80)
                self.assertEqual(illness.data["end_global_day"],80)
                self.assertTrue(future.deleted)

                sooner=Record(save_id=save.id,kind="sim",label="Sooner Schedule",global_day=1,data={"birth_global_day":1,"death_global_day":45,"cause_of_death":"Earlier cause","death_confirmed":False})
                session.add(sooner);session.flush()
                sooner_death=Record(save_id=save.id,kind="death",label="Death of Sooner Schedule",global_day=45,data={"sim_id":sooner.id,"cause":"Earlier cause","completed":False})
                later_failure=Record(save_id=save.id,kind="roll",label="Sooner Schedule — Danger",global_day=40,data={"sim_id":sooner.id,"roll_type":"Danger","die":"d20","bad_results":"1","death_window_start":50,"death_window_end":50,"completed":False})
                session.add_all([sooner_death,later_failure]);session.flush()
                unchanged=complete_roll(session,save,later_failure,1)
                self.assertFalse(unchanged["death_changed"])
                self.assertEqual(sooner.data["death_global_day"],45)
                self.assertEqual(sooner_death.global_day,45)
                session.rollback()

    def test_event_rolls_use_lethal_rule_and_event_cause(self):
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Event roll test", global_day=12, start_year=1200, days_per_year=4)
                session.add(save); session.flush()
                sim = Record(save_id=save.id, kind="sim", label="Event Test Sim", global_day=1, data={"birth_global_day": 1, "sex": "Male", "country": "England"})
                event = Record(save_id=save.id, kind="event", label="Test Battle", global_day=9, data={"start_global_day": 9, "end_global_day": 12, "scope": "War / Conflict", "location": "England", "affected_class": "Eligible Male Sims", "roll_required": True, "active": True, "notes": "Roll a D12; 4 means enlisted. Roll a D6; 1 means they die."})
                session.add_all([sim, event]); session.flush()

                self.assertEqual(schedule_rolls(session, save), 1)
                roll = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "roll"))
                self.assertEqual(roll.data["die"], "d6")
                self.assertEqual(roll.data["bad_results"], "1")
                self.assertEqual(roll.data["event_id"], event.id)
                result = complete_roll(session, save, roll, 1)
                self.assertEqual(result["outcome"], "Failed")
                self.assertEqual(sim.data["cause_of_death"], "Killed during Test Battle")
                self.assertGreaterEqual(sim.data["death_global_day"], save.global_day)
                session.rollback()

    def test_global_event_backfill_needs_no_location_and_uses_editable_start_day(self):
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(
                    workspace_id=template.workspace_id,
                    name="Global event backfill test",
                    global_day=12,
                    start_year=1300,
                    days_per_year=4,
                )
                session.add(save)
                session.flush()
                sims = [
                    Record(save_id=save.id, kind="sim", label="No Location One", global_day=1, data={"birth_global_day": 1}),
                    Record(save_id=save.id, kind="sim", label="No Location Two", global_day=1, data={"birth_global_day": 1}),
                ]
                dead_sim = Record(
                    save_id=save.id,
                    kind="sim",
                    label="No Longer Living",
                    global_day=1,
                    data={"birth_global_day": 1, "death_global_day": 11},
                )
                event = Record(
                    save_id=save.id,
                    kind="event",
                    label="Worldwide Test Event",
                    # Reproduces an older import whose indexed day was not updated
                    # when its editable historical date was corrected.
                    global_day=80,
                    data={
                        "start_global_day": 9,
                        "end_global_day": 12,
                        "scope": "Global",
                        "location": "Global / See Notes",
                        "roll_required": True,
                        "active": True,
                        "notes": "Roll a d6; 1 means death.",
                    },
                )
                session.add_all([*sims, dead_sim, event])
                session.flush()

                self.assertEqual(schedule_event_rolls(session, save), 2)
                rolls = list(session.scalars(select(Record).where(
                    Record.save_id == save.id,
                    Record.kind == "roll",
                    Record.data["event_id"].as_string() == event.id,
                )))
                self.assertEqual({roll.data["sim_id"] for roll in rolls}, {sim.id for sim in sims})
                self.assertTrue(all(roll.global_day == 9 for roll in rolls))
                self.assertEqual(schedule_event_rolls(session, save), 0)
                session.rollback()

    def test_pan_european_famine_matches_england_and_deduplicates_imports(self):
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(
                    workspace_id=template.workspace_id,
                    name="Pan-European famine roll test",
                    global_day=141,
                    start_year=1550,
                    days_per_year=4,
                    settings={},
                )
                session.add(save); session.flush()
                household = Record(
                    save_id=save.id, kind="household", label="English household",
                    data={"location": "England", "active": True},
                )
                session.add(household); session.flush()
                assigned = Record(
                    save_id=save.id, kind="sim", label="Assigned English Sim", global_day=1,
                    data={"birth_global_day": 1, "current_household_id": household.id},
                )
                unassigned = Record(
                    save_id=save.id, kind="sim", label="Unassigned English Challenge Sim", global_day=1,
                    data={"birth_global_day": 1},
                )
                event_data = {
                    "start_global_day": 141, "end_global_day": 152,
                    "scope": "Famine", "location": "Italy, France, Low Countries, Britain, Ireland",
                    "affected_class": "All Sims / Households", "roll_required": True, "active": True,
                    "notes": "Flip for household impact. D4 for all Sims; 3 means famine death.",
                }
                migrated = Record(
                    save_id=save.id, kind="event", label="Pan-European Famine",
                    global_day=141, data=dict(event_data),
                )
                catalog = Record(
                    save_id=save.id, kind="event", label="Pan-European Famine", global_day=141,
                    data={**event_data, "catalog_id": "EVT-0401"},
                )
                session.add_all([assigned, unassigned, migrated, catalog]); session.flush()

                self.assertEqual(schedule_rolls(session, save), 2)
                rolls = list(session.scalars(select(Record).where(
                    Record.save_id == save.id, Record.kind == "roll",
                    Record.data["event_id"].as_string() == catalog.id,
                )))
                self.assertEqual({item.data["sim_id"] for item in rolls}, {assigned.id, unassigned.id})
                self.assertTrue(all(item.data["die"] == "d4" for item in rolls))
                self.assertTrue(all(item.data["bad_results"] == "3" for item in rolls))
                self.assertEqual(schedule_rolls(session, save), 0)
                session.rollback()

    def test_first_clock_snapshot_preserves_death_and_significant_relationship(self):
        marker = uuid.uuid4().hex
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                first = Record(save_id=save.id, kind="sim", label="Initial State A",
                               data={"game_sim_id":"a-"+marker,"first_name":"Initial","last_name":"State A"})
                second = Record(save_id=save.id, kind="sim", label="Initial State B",
                                data={"game_sim_id":"b-"+marker,"first_name":"Initial","last_name":"State B"})
                session.add_all([first, second]); session.flush()
                made = reconcile_sim(session, save, first, {
                    "game_sim_id":"a-"+marker, "first_name":"Initial", "last_name":"State A",
                    "is_dead":True, "death_type":"Malaria",
                    "relationships":[{"other_game_sim_id":"b-"+marker,"category":"marriage"}],
                    "detected_game_hour":21, "detected_game_minute":29,
                    "detected_tracker_global_day":save.global_day,
                })
                self.assertEqual({item.data["action"] for item in made}, {"sim_death", "relationship_change"})
                again = reconcile_sim(session, save, first, {
                    "game_sim_id":"a-"+marker, "first_name":"Initial", "last_name":"State A",
                    "is_dead":True,
                    "relationships":[{"other_game_sim_id":"b-"+marker,"category":"marriage"}],
                })
                self.assertEqual(again, [])
                session.rollback()

    def test_parent_links_retry_after_parent_is_later_connected(self):
        marker = uuid.uuid4().hex
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                child = Record(save_id=save.id, kind="sim", label="Linked Child", data={
                    "game_sim_id":"child-"+marker,
                    "parent_game_sim_ids":["parent-"+marker],
                    "game_parents":[{"game_sim_id":"parent-"+marker,"sex":"Gender.FEMALE"}],
                })
                session.add(child); session.flush()
                self.assertEqual(automation.resolve_parent_links(session, save), 0)
                parent = Record(save_id=save.id, kind="sim", label="Linked Mother",
                                data={"game_sim_id":"parent-"+marker,"sex":"Female"})
                session.add(parent); session.flush()
                self.assertEqual(automation.resolve_parent_links(session, save), 1)
                self.assertEqual(child.data["mother_id"], parent.id)
                self.assertEqual(child.data["parent_ids"], [parent.id])
                session.rollback()

    def test_parent_child_inverse_genealogy_fills_missing_child_parent(self):
        marker = uuid.uuid4().hex
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                child = Record(save_id=save.id, kind="sim", label="Inverse Child",
                               data={"game_sim_id":"inverse-child-"+marker})
                parent = Record(save_id=save.id, kind="sim", label="Inverse Mother", data={
                    "game_sim_id":"inverse-parent-"+marker, "sex":"Female",
                    "child_game_sim_ids":["inverse-child-"+marker],
                })
                session.add_all([child, parent]); session.flush()
                self.assertEqual(automation.resolve_parent_links(session, save), 1)
                self.assertEqual(child.data["mother_id"], parent.id)
                self.assertEqual(child.data["parent_ids"], [parent.id])
                self.assertEqual(child.data["parent_game_sim_ids"], ["inverse-parent-"+marker])
                session.rollback()

    def test_zero_newborn_pregnancy_ending_stays_zero_and_requires_review(self):
        marker = uuid.uuid4().hex
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim = Record(save_id=save.id, kind="sim", label="Zero Outcome", data={
                    "game_sim_id":"zero-outcome-"+marker, "game_was_pregnant":True,
                    "last_game_pregnancy_count":2,
                })
                session.add(sim); session.flush()
                session.add(Record(save_id=save.id, kind="pregnancy", label="Zero Outcome pregnancy",
                                   global_day=save.global_day, data={"mother_id":sim.id,"status":"Active","babies_expected":2}))
                session.flush()
                made = reconcile_sim(session, save, sim, {
                    "telemetry_version":4, "is_pregnant":False, "detected_newborn_count":0,
                })
                outcome = next(item for item in made if item.data["action"] == "pregnancy_outcome")
                self.assertEqual(outcome.data["payload"]["babies_delivered"], 0)
                self.assertEqual(outcome.data["payload"]["babies_delivered_source"], "newborn detection")
                self.assertEqual(outcome.data["payload"]["suggested_status"], "Miscarriage")
                session.rollback()

    def test_simultaneous_births_are_scoped_to_each_mother(self):
        marker = uuid.uuid4().hex
        with TestClient(app):
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Scoped newborn detection", global_day=20)
                session.add(save); session.flush()
                mother_a = Record(save_id=save.id, kind="sim", label="Mother A", data={
                    "game_sim_id":"mother-a-"+marker,"game_was_pregnant":True,"current_household_id":"home-a"})
                mother_b = Record(save_id=save.id, kind="sim", label="Mother B", data={
                    "game_sim_id":"mother-b-"+marker,"game_was_pregnant":True,"current_household_id":"home-b"})
                session.add_all([mother_a, mother_b]); session.flush()
                session.add_all([
                    Record(save_id=save.id,kind="pregnancy",label="Pregnancy A",global_day=20,data={"mother_id":mother_a.id,"status":"Active"}),
                    Record(save_id=save.id,kind="pregnancy",label="Pregnancy B",global_day=20,data={"mother_id":mother_b.id,"status":"Active"}),
                ])
                link = ClockLink(save_id=save.id,token_hash=uuid.uuid4().hex,game_anchor_day=100,tracker_anchor_day=20)
                session.add(link); session.flush()
                receive_clock(session, link, {"game_day":100,"hour":5,"minute":5,"household_members":[
                    {"game_sim_id":"mother-a-"+marker,"first_name":"Mother","last_name":"A","household_id":"game-home-a","is_pregnant":False},
                    {"game_sim_id":"mother-b-"+marker,"first_name":"Mother","last_name":"B","household_id":"game-home-b","is_pregnant":False},
                    {"game_sim_id":"baby-a-"+marker,"first_name":"Baby","last_name":"A","household_id":"game-home-a","is_baby":True},
                    {"game_sim_id":"baby-b1-"+marker,"first_name":"Baby","last_name":"B1","household_id":"game-home-b","is_baby":True},
                    {"game_sim_id":"baby-b2-"+marker,"first_name":"Baby","last_name":"B2","household_id":"game-home-b","is_baby":True},
                ]})
                outcomes = list(session.scalars(select(Record).where(
                    Record.save_id==save.id, Record.kind=="game_candidate",
                    Record.data["action"].as_string()=="pregnancy_outcome",
                )))
                counts = {item.data["sim_id"]:item.data["payload"]["babies_delivered"] for item in outcomes}
                self.assertEqual(counts[mother_a.id], 1)
                self.assertEqual(counts[mother_b.id], 2)
                session.rollback()

    def test_accepting_new_sim_reconciles_complete_first_snapshot(self):
        marker = uuid.uuid4().hex
        with TestClient(app) as client:
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id,name="Complete first snapshot",global_day=30)
                session.add(save);session.flush()
                partner=Record(save_id=save.id,kind="sim",label="Known Partner",data={"game_sim_id":"partner-"+marker})
                session.add(partner);session.flush()
                candidate=Record(save_id=save.id,kind="game_candidate",label="New Complete Sim",global_day=30,data={
                    "action":"new_sim","status":"pending","source_key":"new_sim:"+marker,
                    "payload":{"game_sim_id":marker,"first_name":"New","last_name":"Complete Sim","age_stage":"Age.ADULT",
                               "is_dead":True,"death_type":"Malaria","is_pregnant":True,
                               "illness_scan_supported":True,"illnesses":[{"source_key":"hcr:malaria","name":"Malaria"}],
                               "relationships":[{"other_game_sim_id":"partner-"+marker,"category":"marriage"}],
                               "detected_tracker_global_day":30,"detected_game_hour":9,"detected_game_minute":29},
                })
                session.add(candidate);session.commit();candidate_id=candidate.id;save_id=save.id
            response=client.post(f"/automation/{candidate_id}/accept",data={"first_name":"New","last_name":"Complete Sim","birth_global_day":"1","age_stage":"Age.ADULT"},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            with SessionLocal() as session:
                sim=session.scalar(select(Record).where(Record.save_id==save_id,Record.kind=="sim",Record.data["game_sim_id"].as_string()==marker))
                illness=session.scalar(select(Record).where(Record.save_id==save_id,Record.kind=="illness",Record.data["sim_id"].as_string()==sim.id))
                actions={item.data["action"] for item in session.scalars(select(Record).where(Record.save_id==save_id,Record.kind=="game_candidate",Record.data["status"].as_string()=="pending"))}
                self.assertEqual(illness.data["illness_name"],"Malaria")
                self.assertTrue({"sim_death","pregnancy_discovered","relationship_change","illness_detected"}.issubset(actions))
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_optional_game_illness_detection_is_safe_and_reconciles(self):
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim = Record(save_id=save.id, kind="sim", label="Game Health Test", data={"game_sim_id": "health-test"})
                session.add(sim); session.flush()
                self.assertEqual(_game_illnesses(session, save, sim, {"illnesses": []}), (0, 0))
                detected = {"illness_scan_supported": True, "illnesses": [{"source_key": "base:flu", "name": "Llama Flu", "provider": "buff"}]}
                self.assertEqual(_game_illnesses(session, save, sim, detected), (1, 0))
                self.assertEqual(_game_illnesses(session, save, sim, detected), (0, 0))
                self.assertEqual(_game_illnesses(session, save, sim, {"illness_scan_supported": True, "illnesses": []}), (0, 1))
                session.rollback()

    def test_detected_illness_and_recovery_appear_once_in_automation_inbox(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                original=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=original.workspace_id,name=f"Illness inbox {marker}",global_day=31)
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label=f"Inbox Patient {marker}",data={"game_sim_id":f"illness-inbox-{marker}"})
                session.add(sim);session.flush()
                snapshot={"illness_scan_supported":True,"detected_game_hour":14,"detected_game_minute":25,
                          "illnesses":[{"source_key":f"base:clock-flu-{marker}","name":f"Clock Flu {marker}","provider":"base game","severity":"Mild"}]}
                self.assertEqual(_game_illnesses(session,save,sim,snapshot),(1,0))
                self.assertEqual(_game_illnesses(session,save,sim,snapshot),(0,0))
                illness=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="illness"))
                detected=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="game_candidate",Record.data["action"].as_string()=="illness_detected")))
                self.assertEqual(len(detected),1)
                session.commit();save_id,sim_id,illness_id,candidate_id,original_id=save.id,sim.id,illness.id,detected[0].id,original.id
            client.post("/saves/select",data={"save_id":save_id},follow_redirects=False)
            inbox=client.get("/p/automation")
            self.assertEqual(inbox.status_code,200);self.assertIn(f"Clock Flu {marker}",inbox.text)
            self.assertIn("A new illness episode was recorded automatically",inbox.text)
            accepted=client.post(f"/automation/{candidate_id}/accept",data={"illness_name":f"Reviewed Flu {marker}","onset_global_day":"30","severity":"Moderate","status":"Active","contagious":"on"},follow_redirects=False)
            self.assertEqual(accepted.status_code,303)
            with SessionLocal() as session:
                illness=session.get(Record,illness_id)
                self.assertEqual(illness.data["illness_name"],f"Reviewed Flu {marker}");self.assertEqual(illness.data["onset_global_day"],30)
                self.assertEqual(illness.data["severity"],"Moderate");self.assertTrue(illness.data["contagious"])
                save=session.get(ChronicleSave,save_id);sim=session.get(Record,sim_id)
                self.assertEqual(_game_illnesses(session,save,sim,{"illness_scan_supported":True,"illnesses":[]}),(0,1))
                recovery=session.scalar(select(Record).where(Record.save_id==save_id,Record.kind=="game_candidate",Record.data["action"].as_string()=="illness_recovered"))
                session.commit();recovery_id=recovery.id
            inbox=client.get("/p/automation")
            self.assertIn("This illness is no longer detected in the game",inbox.text)
            accepted=client.post(f"/automation/{recovery_id}/accept",data={"illness_name":f"Reviewed Flu {marker}","recovery_global_day":"31","status":"Active","outcome":"Still symptomatic"},follow_redirects=False)
            self.assertEqual(accepted.status_code,303)
            with SessionLocal() as session:
                illness=session.get(Record,illness_id);self.assertEqual(illness.data["status"],"Active");self.assertIsNone(illness.data["end_global_day"])
            client.post("/saves/select",data={"save_id":original_id},follow_redirects=False)
            with SessionLocal() as session:
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_healthcare_trait_hashes_detect_disease_but_not_immunization(self):
        localizations = {1:"Pneumonia",2:"Meningitis Immunization",3:"Has Current Illness",4:"Malaria",5:"Influenza"}
        found = trait_illnesses({"traits":["hash: 1","hash: 2","hash: 3","hash: 4","hash: 5","hash: 999"]}, localizations)
        self.assertEqual({item["name"] for item in found},{"Pneumonia","Malaria","Influenza"})

    def test_healthcare_readable_chronic_condition_and_native_illness_payload_are_detected(self):
        localizations = {1:"Healthcare Redux Core Trait",2:"Meningitis Immunization"}
        found = trait_illnesses({"traits":[
            "Sleep Disorder","Anxiety","Flat Head Syndrome","Llama Flu",
            "Recent Malaria","hash: 2",
        ]}, localizations)
        self.assertEqual(
            {item["name"] for item in found},
            {"Sleep Disorder","Anxiety","Flat Head Syndrome","Llama Flu"},
        )
        native = enrich_illness_snapshot({"illnesses":[
            {"source_key":"hcr:malaria","name":"Malaria"},
            {"source_key":"hcr:malaria-immunization","name":"Malaria Immunization"},
            {"source_key":"adeepindigo_HealthcareRedux_Diseases_buff_RecentMalaria","name":"Feeling better"},
            {"source_key":"hcr:meningitis","name":"Meningitis"},
            {"source_key":"adeepindigo_HealthcareRedux_Diseases_FluBuff","name":"adeepindigo HealthcareRedux Diseases FluBuff","provider":"Healthcare Redux"},
            {"source_key":"adeepindigo_HealthcareRedux_Diseases_FluImmuneTrait","name":"adeepindigo HealthcareRedux Diseases FluImmuneTrait","provider":"Healthcare Redux"},
            {"source_key":"hcr:sleep-disorder-treatment","name":"Sleep Disorder Treatment"},
        ]})
        self.assertTrue(native["illness_scan_supported"])
        self.assertEqual({item["name"] for item in native["illnesses"]},{"Malaria","Meningitis","Influenza"})
        with mock.patch("app.game_metadata.healthcare_localizations", return_value=localizations):
            healthy = enrich_illness_snapshot({"traits":["hash: 1"]})
        self.assertTrue(healthy["illness_scan_supported"])
        self.assertEqual(healthy["illnesses"],[])

    def test_trait_hashes_render_as_localized_names_without_mod_changes(self):
        self.assertEqual(_refpack_decompress(b"\x10\xfb\x00\x00\x03\xffabc", 3), b"abc")
        localizations = {1:"Genius", 2:"Trait_HasHadPreg_ForInteractions", 3:"{T0.Mess Around}{DAE0.WooHoo}"}
        self.assertEqual(
            readable_trait_labels(["hash: 1","hash: 2","hash: 3","Creative","hash: 0","hash: 999"], localizations),
            ["Genius","Has Had Preg For Interactions","Mess Around / WooHoo","Creative","Unidentified custom trait (ID 999)"],
        )
        with TestClient(app):
            with SessionLocal() as session, mock.patch("app.game_metadata.trait_localizations", return_value={1:"Genius"}):
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim=Record(save_id=save.id,kind="sim",label="Readable Trait Test",data={"game_sim_id":"trait-readable-test"})
                session.add(sim);session.flush()
                reconcile_sim(session,save,sim,{"telemetry_version":2,"traits":["hash: 1"]})
                self.assertEqual(sim.data["game_traits"],["Genius"])
                session.rollback()

    def test_all_clock_sync_hash_formats_resolve_to_game_names(self):
        localizations = {1:"Self-Assured", 2:"Skill_Logic", 0xABC:"Milestone_FirstSteps"}
        self.assertEqual(localization_hash("hash#1"), 1)
        self.assertEqual(localization_hash("localization key = 0xABC"), 0xABC)
        self.assertEqual(
            readable_named_labels(["hash#2 (level 7)"], kind="skill", localizations=localizations),
            ["Logic (level 7)"],
        )
        self.assertEqual(
            readable_named_labels(["string id: 0xABC"], kind="milestone", localizations=localizations),
            ["First Steps"],
        )
        self.assertEqual(readable_trait_labels(["hash#1"], localizations), ["Self-Assured"])

    def test_hosted_fallback_resolves_observed_profile_names(self):
        labels = bundled_localizations()
        self.assertEqual(labels[2243298849], "Gardening")
        self.assertEqual(labels[1004514555], "First Visit to the Doctor")
        self.assertEqual(
            readable_named_labels(["hash: 2364309712 (level 4)"], kind="skill", localizations=labels),
            ["Charisma (level 4)"],
        )
        self.assertEqual(
            readable_named_labels(["hash: 739252487"], kind="milestone", localizations=labels),
            ["Manifested as a Fairy"],
        )

    def test_clock_sync_details_are_a_safe_online_fallback_for_hashes(self):
        self.assertEqual(
            readable_named_labels(
                ["hash#999"],
                [{"name":"Skill_HomestyleCooking","tuning_id":1234}],
                kind="skill",
                localizations={},
            ),
            ["Homestyle Cooking"],
        )
        # Details with a different number of rows must never be paired by
        # position, because that could put another aspiration's name on a Sim.
        self.assertEqual(
            readable_named_labels(
                ["hash#999", "hash#1000"],
                [{"name":"Aspiration_BestsellingAuthor","tuning_id":1234}],
                kind="aspiration",
                localizations={},
            ),
            ["Unidentified aspiration (ID 999)", "Unidentified aspiration (ID 1000)"],
        )

    def test_imported_sim_is_linked_by_unique_exact_name(self):
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim = Record(save_id=save.id,kind="sim",label="Lady — Anne — Veral",data={"first_name":"Anne","last_name":"Veral"})
                pending = Record(save_id=save.id,kind="game_candidate",label="Anne Veral",data={"source_key":"new_sim:12345","status":"pending","action":"new_sim"})
                session.add_all([sim,pending]);session.flush()
                snapshot={"game_sim_id":"12345","first_name":"Anne","last_name":"Veral","age_stage":"Age.TEEN","household_id":"home"}
                self.assertEqual(imported_sim_match(session,save,snapshot).id,sim.id)
                self.assertEqual(attach_game_identity(session,save,sim,snapshot),1)
                self.assertEqual(sim.data["game_sim_id"],"12345")
                self.assertEqual(pending.data["status"],"linked")
                self.assertEqual(pending.data["linked_sim_id"],sim.id)
                session.rollback()

    def test_resolved_automation_candidate_never_reappears(self):
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim = Record(save_id=save.id,kind="sim",label="Persistent Resolution",data={})
                session.add(sim);session.flush()
                first=automation_candidate(session,save,"relationship_change",sim,"Marriage detected",{"category":"marriage"},"other:marriage")
                self.assertIsNotNone(first)
                first.data={**first.data,"status":"dismissed"};session.flush()
                second=automation_candidate(session,save,"relationship_change",sim,"Marriage detected",{"category":"marriage"},"other:marriage")
                self.assertIsNone(second)
                session.rollback()

    def test_new_pregnancy_transition_creates_one_review_candidate(self):
        with TestClient(app):
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim=Record(save_id=save.id,kind="sim",label="Pregnancy Detection",data={"game_sim_id":"preg-detect","game_was_pregnant":False})
                session.add(sim);session.flush()
                snapshot={"is_pregnant":True,"babies_expected":2}
                first=reconcile_sim(session,save,sim,snapshot)
                second=reconcile_sim(session,save,sim,snapshot)
                self.assertEqual(len(first),1)
                self.assertEqual(first[0].data["action"],"pregnancy_discovered")
                self.assertEqual(len(second),0)
                self.assertTrue(sim.data["game_was_pregnant"])
                self.assertEqual(sim.data["game_pregnancy_sequence"],1)
                session.rollback()

    def test_clock_telemetry_normalizes_traits_skills_and_milestones(self):
        with TestClient(app):
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim=Record(save_id=save.id,kind="sim",label="Telemetry Test",data={"game_sim_id":"telemetry-test"})
                session.add(sim);session.flush()
                reconcile_sim(session,save,sim,{"telemetry_version":2,"traits":["Creative"],"skills":[{"name":"Painting","level":7}],"milestones":["First Steps"]})
                self.assertEqual(sim.data["game_traits"],["Creative"])
                self.assertEqual(sim.data["game_skills"],["Painting (level 7)"])
                self.assertEqual(sim.data["game_milestones"],["First Steps"])
                reconcile_sim(session,save,sim,{"telemetry_version":2,"traits":[],"skills":[],"milestones":[]})
                self.assertEqual(sim.data["game_traits"],[])
                self.assertEqual(sim.data["game_skills"],[])
                session.rollback()

    def test_clock_telemetry_retains_complete_current_and_future_fields(self):
        marker=uuid.uuid4().hex
        with TestClient(app):
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim=Record(save_id=save.id,kind="sim",label="Complete Telemetry",data={
                    "game_sim_id":"complete-"+marker,"game_was_dead":True,"death_confirmed":True,
                })
                partner=Record(save_id=save.id,kind="sim",label="Detected Partner",data={
                    "game_sim_id":"partner-"+marker,
                })
                session.add_all([sim,partner]);session.flush()
                snapshot={
                    "telemetry_version":4,
                    "source":"read-only Sims 4 save scan",
                    "age_progress":.5,
                    "pregnancy_progress":.72,
                    "pregnancy_scan_supported":True,
                    "significant_other_game_id":"partner-"+marker,
                    "is_dead":True,
                    "death_type":"Malaria",
                    "death_details":{"death_type":"Malaria","is_ghost":True,"place":"Capp Manor"},
                    "is_ghost":True,
                    "children":[{"game_sim_id":"child-1","name":"Child One"}],
                    "child_game_sim_ids":["child-1"],
                    "genealogy_scan_supported":True,
                    "future_life_history":{"chapter":7},
                    "portrait_image_base64":"stored-in-portrait-table-not-json",
                }
                made=reconcile_sim(session,save,sim,snapshot)
                self.assertEqual(sim.data["game_age_progress_percentage"],50.0)
                self.assertEqual(sim.data["game_pregnancy_progress_percentage"],72.0)
                self.assertEqual(sim.data["game_significant_other_game_sim_id"],"partner-"+marker)
                self.assertEqual(sim.data["game_death_details"]["place"],"Capp Manor")
                self.assertTrue(sim.data["game_is_ghost"])
                self.assertEqual(sim.data["game_children"][0]["name"],"Child One")
                self.assertEqual(sim.data["game_telemetry_extra"]["future_life_history"],{"chapter":7})
                self.assertNotIn("portrait_image_base64",sim.data["game_telemetry_extra"])
                relationship_candidates=[item for item in made if item.data.get("action")=="relationship_change"]
                self.assertEqual(len(relationship_candidates),1)
                self.assertEqual(relationship_candidates[0].data["payload"]["category"],"Romantic")
                self.assertEqual(reconcile_sim(session,save,sim,snapshot),[])
                session.rollback()

    def test_tracker_classifies_generic_game_relationships_from_bits_and_scores(self):
        friendship = classify_game_relationship({"category":"Relationship","friendship_score":72,"romance_score":0})
        romantic = classify_game_relationship({"category":"Relationship","friendship_score":80,"romance_score":12})
        family = classify_game_relationship({"category":"Relationship","relationship_bits":["RelationshipBit_Sibling"],"friendship_score":65})
        genealogy_family = classify_game_relationship({"category":"Relationship","genealogy_family":True,"friendship_score":5})
        acquaintance = classify_game_relationship({"category":"Relationship","friendship_score":12,"romance_score":0})
        self.assertEqual(friendship["category"],"Friendship")
        self.assertEqual(romantic["category"],"Romantic")
        self.assertEqual(set(romantic["relationship_tags"]),{"Friendship","Romantic"})
        self.assertEqual(family["category"],"Family")
        self.assertEqual(set(family["relationship_tags"]),{"Family","Friendship"})
        self.assertEqual(genealogy_family["category"],"Family")
        self.assertEqual(genealogy_family["relationship_classification_source"],"genealogy")
        self.assertEqual(acquaintance["category"],"Acquaintance")

    def test_relationship_inbox_repairs_generic_rows_and_suppresses_acquaintances(self):
        marker=uuid.uuid4().hex
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Relationship repair "+marker,global_day=10)
                session.add(save);session.flush()
                first=Record(save_id=save.id,kind="sim",label="First Friend",data={"game_sim_id":"first-"+marker})
                second=Record(save_id=save.id,kind="sim",label="Second Friend",data={"game_sim_id":"second-"+marker})
                session.add_all([first,second]);session.flush()
                first.data={**first.data,"game_relationships":[{
                    "other_game_sim_id":"second-"+marker,"category":"Relationship",
                    "friendship_score":77,"romance_score":0,
                }]}
                friend=Record(save_id=save.id,kind="game_candidate",label="Relationship detected",data={
                    "action":"relationship_change","sim_id":first.id,"status":"pending","source_key":"friend-"+marker,
                    "payload":{"other_game_sim_id":"second-"+marker,"category":"Relationship"},
                })
                acquaintance=Record(save_id=save.id,kind="game_candidate",label="Relationship detected",data={
                    "action":"relationship_change","sim_id":second.id,"status":"pending","source_key":"acquaintance-"+marker,
                    "payload":{"other_game_sim_id":"third-"+marker,"category":"Relationship","friendship_score":4,"romance_score":0},
                })
                session.add_all([friend,acquaintance]);session.flush()
                result=repair_relationship_inbox(session,save)
                self.assertEqual(result,{"classified":1,"dismissed":1})
                self.assertEqual(friend.data["payload"]["category"],"Friendship")
                self.assertEqual(friend.data["status"],"pending")
                self.assertEqual(acquaintance.data["status"],"dismissed")
                session.rollback()

    def test_clock_telemetry_does_not_erase_optional_data_when_scan_is_unavailable(self):
        with TestClient(app):
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim=Record(save_id=save.id,kind="sim",label="Guarded Telemetry",data={
                    "game_sim_id":"guarded-telemetry",
                    "game_skills":["Painting (level 7)"],
                    "game_milestones":["First Steps"],
                })
                session.add(sim);session.flush()
                reconcile_sim(session,save,sim,{
                    "telemetry_version":3,
                    "skills":[],"skills_scan_supported":False,
                    "milestones":[],"milestone_scan_supported":False,
                })
                self.assertEqual(sim.data["game_skills"],["Painting (level 7)"])
                self.assertEqual(sim.data["game_milestones"],["First Steps"])
                reconcile_sim(session,save,sim,{
                    "telemetry_version":3,
                    "skills":[],"skills_scan_supported":True,
                    "milestones":[],"milestone_scan_supported":True,
                })
                self.assertEqual(sim.data["game_skills"],[])
                self.assertEqual(sim.data["game_milestones"],[])
                session.rollback()

    def test_occult_detection_handles_explicit_hybrids_humans_and_trait_fallback(self):
        hybrid=occult_identity({"occult_types":["OccultType.VAMPIRE","WITCH"],"occult_scan_supported":True})
        self.assertEqual(hybrid["display"],"Hybrid (Vampire / Spellcaster)")
        self.assertEqual(hybrid["types"],["Vampire","Spellcaster"])
        self.assertEqual(occult_identity({"occult_types":[],"occult_scan_supported":True})["display"],"Human")
        self.assertEqual(occult_identity({"traits":["Trait_OccultWerewolf"]})["display"],"Werewolf")
        self.assertEqual(occult_identity({"traits":["Vampire Lore"]})["display"],"")

    def test_clock_occult_state_updates_profile_and_writes_history(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Occult telemetry",global_day=10)
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label="Occult Sim",data={"species_occult":"Human","game_sim_id":"occult-test"})
                session.add(sim);session.flush()
                reconcile_sim(session,save,sim,{"telemetry_version":2,"occult_types":["VAMPIRE","WITCH"],"occult_scan_supported":True,"traits":[]})
                self.assertEqual(sim.data["species_occult"],"Hybrid (Vampire / Spellcaster)")
                self.assertEqual(sim.data["game_occult_types"],["Vampire","Spellcaster"])
                history=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="game_history",Record.data["category"].as_string()=="occult"))
                self.assertIsNotNone(history)
                reconcile_sim(session,save,sim,{"telemetry_version":2,"occult_types":[],"occult_scan_supported":True,"traits":[]})
                self.assertEqual(sim.data["species_occult"],"Human")
                self.assertEqual(sim.data["game_occult_types"],[])
                session.rollback()

    def test_occult_scheduler_targets_detected_types_households_and_full_moons_once(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Occult scheduling",global_day=65,start_year=1500,days_per_year=4,settings={"automatic_occult_rolls":True,"occult_rolls_enabled_from_global_day":65,"full_moon_anchor_global_day":65,"full_moon_interval_days":8})
                session.add(save);session.flush()
                home=Record(save_id=save.id,kind="household",label="Occult House",data={})
                session.add(home);session.flush()
                sims=[
                    Record(save_id=save.id,kind="sim",label="Vampire One",data={"species_occult":"Vampire","game_occult_types":["Vampire"],"current_household_id":home.id}),
                    Record(save_id=save.id,kind="sim",label="Vampire Two",data={"species_occult":"Vampire","game_occult_types":["Vampire"],"current_household_id":home.id}),
                    Record(save_id=save.id,kind="sim",label="Alien One",data={"species_occult":"Alien","game_occult_types":["Alien"]}),
                    Record(save_id=save.id,kind="sim",label="Wolf One",data={"species_occult":"Werewolf","game_occult_types":["Werewolf"],"werewolf_confined":False}),
                    Record(save_id=save.id,kind="sim",label="Fairy One",data={"species_occult":"Fairy","game_occult_types":["Fairy"]}),
                    Record(save_id=save.id,kind="sim",label="Ghost One",data={"species_occult":"Ghost","game_occult_types":["Ghost"]}),
                    Record(save_id=save.id,kind="sim",label="Mermaid One",data={"species_occult":"Mermaid","game_occult_types":["Mermaid"],"occult_water_access":"Unknown"}),
                    Record(save_id=save.id,kind="sim",label="Spellcaster One",data={"species_occult":"Spellcaster","game_occult_types":["Spellcaster"],"current_household_id":home.id}),
                ]
                session.add_all(sims);session.flush()
                self.assertGreaterEqual(seed_occult_rules(session,save),60)
                self.assertEqual(schedule_occult_rolls(session,save,sims),9)
                rolls=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["occult_roll"].as_boolean().is_(True))))
                keys=[item.data["occult_rule_key"] for item in rolls]
                self.assertEqual(keys.count("vampire_hunt"),1)
                self.assertEqual(keys.count("werewolf_attack"),1)
                self.assertEqual(keys.count("werewolf_discovery"),1)
                self.assertNotIn("mermaid_sailor",keys);self.assertNotIn("mermaid_dehydration",keys)
                self.assertEqual(schedule_occult_rolls(session,save,sims),0)
                session.rollback()

    def test_werewolf_discovery_and_hunt_followups_schedule_automatically(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Automatic werewolf chain",global_day=65,start_year=1500,days_per_year=4,settings={"automatic_occult_rolls":True,"occult_rolls_enabled_from_global_day":65,"full_moon_anchor_global_day":65,"full_moon_interval_days":8})
                session.add(save);session.flush()
                wolf=Record(save_id=save.id,kind="sim",label="Discovered Wolf",global_day=1,data={"species_occult":"Werewolf","game_occult_types":["Werewolf"],"werewolf_confined":False,"birth_global_day":1})
                session.add(wolf);session.flush();seed_occult_rules(session,save);schedule_occult_rolls(session,save,[wolf])
                discovery=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["occult_rule_key"].as_string()=="werewolf_discovery"))
                self.assertIsNotNone(discovery)

                discovery_result=complete_roll(session,save,discovery,1)
                response=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["occult_rule_key"].as_string()=="werewolf_hunt_response"))
                self.assertEqual(discovery_result["automatic_followups"],1)
                self.assertIsNotNone(response)
                self.assertEqual((response.global_day,response.data["origin_roll_id"],response.data["automatic_followup"]),(65,discovery.id,True))
                self.assertTrue(discovery.data["rule_followup_reviewed"])

                response_result=complete_roll(session,save,response,5)
                hunt=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["occult_rule_key"].as_string()=="werewolf_hunt_death"))
                self.assertEqual(response_result["automatic_followups"],1)
                self.assertIsNotNone(hunt)
                self.assertEqual((hunt.data["die"],hunt.data["bad_results"],hunt.data["nonlethal"]),("d6","1-3",False))
                self.assertEqual(hunt.data["origin_roll_id"],response.id)

                death_result=complete_roll(session,save,hunt,1)
                self.assertTrue(death_result["death_changed"])
                self.assertIsNotNone(wolf.data.get("death_global_day"))
                session.rollback()

    def test_completed_werewolf_discovery_backfills_without_duplicate_followups(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Werewolf follow-up backfill",global_day=70,start_year=1500,days_per_year=4,settings={"automatic_occult_rolls":True,"occult_rolls_enabled_from_global_day":70})
                session.add(save);session.flush()
                wolf=Record(save_id=save.id,kind="sim",label="Historic Wolf",global_day=1,data={"species_occult":"Werewolf","game_occult_types":["Werewolf"],"werewolf_confined":False,"birth_global_day":1})
                session.add(wolf);session.flush();seed_occult_rules(session,save)
                discovery=Record(save_id=save.id,kind="roll",label="Historic Wolf — Werewolf discovery",global_day=60,data={
                    "sim_id":wolf.id,"occult_roll":True,"occult_rule_key":"werewolf_discovery",
                    "source_rule_key":"werewolf_discovery","completed":True,"triggered":True,
                    "actual":1,"outcome":"Discovered",
                })
                session.add(discovery);session.flush()

                self.assertGreaterEqual(schedule_occult_rolls(session,save,[wolf]),1)
                followups=list(session.scalars(select(Record).where(
                    Record.save_id==save.id,Record.kind=="roll",
                    Record.data["origin_roll_id"].as_string()==discovery.id,
                    Record.data["occult_rule_key"].as_string()=="werewolf_hunt_response",
                )))
                self.assertEqual(len(followups),1)
                self.assertTrue(discovery.data["rule_followup_reviewed"])
                schedule_occult_rolls(session,save,[wolf])
                followups=list(session.scalars(select(Record).where(
                    Record.save_id==save.id,Record.kind=="roll",
                    Record.data["origin_roll_id"].as_string()==discovery.id,
                    Record.data["occult_rule_key"].as_string()=="werewolf_hunt_response",
                )))
                self.assertEqual(len(followups),1)
                session.rollback()

    def test_required_occult_followups_schedule_multi_target_and_nontrigger_branches(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Complete automatic chains",global_day=65,start_year=1500,days_per_year=4,settings={"automatic_occult_rolls":True,"automatic_death_causes":True})
                session.add(save);session.flush()
                home=Record(save_id=save.id,kind="household",label="Chain House",data={});session.add(home);session.flush()
                vampire=Record(save_id=save.id,kind="sim",label="Vampire",global_day=1,data={"birth_global_day":1,"species_occult":"Vampire","current_household_id":home.id})
                spellcaster=Record(save_id=save.id,kind="sim",label="Spellcaster",global_day=1,data={"birth_global_day":1,"species_occult":"Spellcaster","current_household_id":home.id})
                human=Record(save_id=save.id,kind="sim",label="Human",global_day=1,data={"birth_global_day":1,"species_occult":"Human","current_household_id":home.id})
                session.add_all([vampire,spellcaster,human]);session.flush();seed_occult_rules(session,save)

                def resolve(rule_key, sim, actual):
                    rule=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="occult_rule",Record.data["rule_key"].as_string()==rule_key))
                    roll=Record(save_id=save.id,kind="roll",label=f"{sim.label} — {rule.label}",global_day=save.global_day,data={"sim_id":sim.id,"occult_roll":True,"occult_rule_key":rule_key,"source_rule_key":rule_key,"die":rule.data.get("die"),"trigger_results":rule.data.get("trigger_results"),"result_rules":rule.data.get("result_rules"),"bad_results":"","completed":False})
                    session.add(roll);session.flush();result=complete_roll(session,save,roll,actual)
                    children=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["origin_roll_id"].as_string()==roll.id)))
                    return result,roll,children

                result,hunt,hunt_children=resolve("vampire_hunt",vampire,1)
                self.assertEqual(result["automatic_followups"],2)
                self.assertEqual({item.data["occult_rule_key"] for item in hunt_children},{"vampire_accused","vampire_false_accusation"})
                false_accusation=next(item for item in hunt_children if item.data["occult_rule_key"]=="vampire_false_accusation")
                self.assertEqual(false_accusation.data["sim_id"],human.id)
                self.assertEqual(complete_roll(session,save,false_accusation,18)["automatic_followups"],1)

                result,trial,trial_children=resolve("spellcaster_witch_trial",spellcaster,3)
                self.assertEqual(result["automatic_followups"],2)
                false_trial=next(item for item in trial_children if item.data["occult_rule_key"]=="spellcaster_false_accusation")
                self.assertNotEqual(false_trial.data["sim_id"],spellcaster.id)
                self.assertEqual(complete_roll(session,save,false_trial,4)["automatic_followups"],1)
                verdict=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["origin_roll_id"].as_string()==false_trial.id))
                self.assertEqual(complete_roll(session,save,verdict,2)["automatic_followups"],1)
                drowning=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["origin_roll_id"].as_string()==verdict.id))
                self.assertEqual(drowning.data["occult_rule_key"],"spellcaster_innocent_drowning")
                session.rollback()

    def test_werewolf_attack_automatically_selects_victim_and_schedules_survivor_roll(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Werewolf victim chain",global_day=65,start_year=1500,days_per_year=4,settings={"automatic_occult_rolls":True})
                session.add(save);session.flush()
                home=Record(save_id=save.id,kind="household",label="Wolf House",data={});session.add(home);session.flush()
                wolf=Record(save_id=save.id,kind="sim",label="Wolf",global_day=1,data={"birth_global_day":1,"species_occult":"Werewolf","current_household_id":home.id})
                spouse=Record(save_id=save.id,kind="sim",label="Adult Spouse",global_day=1,data={"birth_global_day":1,"species_occult":"Human","current_household_id":home.id})
                session.add_all([wolf,spouse]);session.flush()
                marriage=Record(save_id=save.id,kind="relationship",label="Marriage",global_day=1,data={"partner1_id":wolf.id,"partner2_id":spouse.id,"type":"Marriage","legally_married":True,"status":"Active"})
                session.add(marriage);session.flush();seed_occult_rules(session,save)
                attack_rule=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="occult_rule",Record.data["rule_key"].as_string()=="werewolf_attack"))
                attack=Record(save_id=save.id,kind="roll",label="Werewolf attack",global_day=65,data={"sim_id":wolf.id,"occult_roll":True,"occult_rule_key":"werewolf_attack","source_rule_key":"werewolf_attack","die":"d6","trigger_results":"1","result_rules":attack_rule.data["result_rules"],"completed":False})
                session.add(attack);session.flush();self.assertEqual(complete_roll(session,save,attack,1)["automatic_followups"],1)
                relation_roll=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["origin_roll_id"].as_string()==attack.id))
                self.assertEqual((relation_roll.data["occult_rule_key"],relation_roll.data["sim_id"]),("werewolf_close_relation",spouse.id))
                self.assertEqual(complete_roll(session,save,relation_roll,1)["automatic_followups"],1)
                death_roll=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["origin_roll_id"].as_string()==relation_roll.id))
                self.assertEqual(death_roll.data["occult_rule_key"],"werewolf_attack_death")
                self.assertEqual(complete_roll(session,save,death_roll,2)["automatic_followups"],1)
                turn_roll=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["origin_roll_id"].as_string()==death_roll.id))
                self.assertEqual((turn_roll.data["occult_rule_key"],turn_roll.data["sim_id"]),("werewolf_turn_adult",spouse.id))
                session.rollback()

    def test_declared_future_changeling_ghost_and_servo_followups_are_automatic(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Remaining automatic chains",global_day=65,start_year=1900,days_per_year=4,settings={"automatic_occult_rolls":True})
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label="Rule Sim",global_day=60,data={"birth_global_day":60,"species_occult":"Human"})
                victim=Record(save_id=save.id,kind="sim",label="Living Victim",global_day=1,data={"birth_global_day":1,"species_occult":"Human"})
                session.add_all([sim,victim]);session.flush();seed_occult_rules(session,save)

                def complete_key(key, target, actual):
                    rule=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="occult_rule",Record.data["rule_key"].as_string()==key))
                    trigger_results="1" if key=="ghost_persistence" else rule.data.get("trigger_results")
                    roll=Record(save_id=save.id,kind="roll",label=rule.label,global_day=save.global_day,data={"sim_id":target.id,"occult_roll":True,"occult_rule_key":key,"source_rule_key":key,"die":rule.data.get("die"),"trigger_results":trigger_results,"result_rules":rule.data.get("result_rules"),"completed":False})
                    session.add(roll);session.flush();result=complete_roll(session,save,roll,actual)
                    return roll,result,list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["origin_roll_id"].as_string()==roll.id)))

                changeling,result,children=complete_key("fairy_changeling",sim,1)
                self.assertEqual(result["automatic_followups"],1);self.assertEqual(children[0].global_day,80)

                sim.data={**sim.data,"death_global_day":65};sim.version+=1
                ghost,result,children=complete_key("ghost_persistence",sim,1)
                self.assertEqual(result["automatic_followups"],2)
                self.assertEqual({item.data["occult_rule_key"] for item in children},{"ghost_haunting","ghost_move_on"})
                haunting=next(item for item in children if item.data["occult_rule_key"]=="ghost_haunting")
                self.assertEqual(complete_roll(session,save,haunting,6)["automatic_followups"],1)
                haunting_death=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["origin_roll_id"].as_string()==haunting.id))
                self.assertEqual(haunting_death.data["sim_id"],victim.id)

                servo,result,children=complete_key("servo_malfunction",victim,1)
                self.assertEqual(result["automatic_followups"],1)
                breakdown=children[0];self.assertEqual(breakdown.data["occult_rule_key"],"servo_breakdown")
                self.assertEqual(complete_roll(session,save,breakdown,6)["automatic_followups"],1)

                parent=Record(save_id=save.id,kind="future_rule",label="Declared parent",data={"rule_key":"declared-parent","die":"d6","trigger_results":"1","active":True})
                child=Record(save_id=save.id,kind="future_rule",label="Declared child",data={"rule_key":"declared-child","triggered_by":"declared-parent","die":"d4","trigger_results":"2","active":True})
                session.add_all([parent,child]);session.flush()
                origin=Record(save_id=save.id,kind="roll",label="Declared origin",global_day=65,data={"sim_id":victim.id,"rule_generated":True,"source_rule_key":"declared-parent","die":"d6","trigger_results":"1","completed":False})
                session.add(origin);session.flush();self.assertEqual(complete_roll(session,save,origin,1)["automatic_followups"],1)
                declared=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["origin_roll_id"].as_string()==origin.id))
                self.assertEqual(declared.data["source_rule_key"],"declared-child")
                session.rollback()

    def test_occult_inheritance_effect_and_every_completed_outcome_enter_storyline(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Occult story",global_day=20,start_year=1300,days_per_year=4,settings={"automatic_occult_rolls":True,"occult_rolls_enabled_from_global_day":20})
                session.add(save);session.flush()
                mother=Record(save_id=save.id,kind="sim",label="Vampire Mother",data={"species_occult":"Vampire","game_occult_types":["Vampire"],"birth_global_day":1})
                father=Record(save_id=save.id,kind="sim",label="Human Father",data={"species_occult":"Human","birth_global_day":1})
                session.add_all([mother,father]);session.flush()
                child=Record(save_id=save.id,kind="sim",label="Human Child",global_day=20,data={"species_occult":"Human","birth_global_day":20,"mother_id":mother.id,"father_id":father.id})
                session.add(child);session.flush();seed_occult_rules(session,save);schedule_occult_rolls(session,save,[mother,father,child])
                inheritance=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["occult_rule_key"].as_string()=="general_inheritance"))
                self.assertIsNotNone(inheritance);complete_roll(session,save,inheritance,1)
                self.assertEqual(child.data["dormant_occult_types"],["Vampire"])
                extra=Record(save_id=save.id,kind="roll",label="Resolved fairy secret",global_day=20,data={"occult_roll":True,"occult_type":"Fairy","roll_type":"Fairy discovery","die":"d20","actual":2,"outcome":"Hidden","completed":True,"completed_global_day":20})
                pending=Record(save_id=save.id,kind="roll",label="Pending occult secret",global_day=20,data={"occult_roll":True,"occult_type":"Alien","completed":False})
                session.add_all([extra,pending]);session.flush()
                story=build_storyline(session,save);outcome_ids={item.id for item in story["occult_outcomes"]}
                self.assertIn(inheritance.id,outcome_ids);self.assertIn(extra.id,outcome_ids);self.assertNotIn(pending.id,outcome_ids)
                session.rollback()

    def test_every_sim_who_dies_at_ten_or_older_gets_one_ghost_roll(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Ghost age threshold",global_day=100,start_year=1500,days_per_year=4,settings={"automatic_occult_rolls":True,"occult_rolls_enabled_from_global_day":100})
                session.add(save);session.flush()
                too_young=Record(save_id=save.id,kind="sim",label="Nine Year Old",data={"species_occult":"Human","birth_global_day":10,"death_global_day":49,"cause_of_death":"Illness"})
                exactly_ten=Record(save_id=save.id,kind="sim",label="Ten Year Old",data={"species_occult":"Human","birth_global_day":10,"death_global_day":50,"cause_of_death":"Accident"})
                older=Record(save_id=save.id,kind="sim",label="Older Vampire",data={"species_occult":"Vampire","game_occult_types":["Vampire"],"birth_global_day":1,"death_global_day":90,"cause_of_death":"Murder"})
                future=Record(save_id=save.id,kind="sim",label="Future Death",data={"species_occult":"Human","birth_global_day":1,"death_global_day":101,"cause_of_death":"Old age"})
                session.add_all([too_young,exactly_ten,older,future]);session.flush();seed_occult_rules(session,save)
                self.assertEqual(schedule_occult_rolls(session,save,[too_young,exactly_ten,older,future]),2)
                rolls=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="roll",Record.data["occult_rule_key"].as_string()=="ghost_persistence")))
                self.assertEqual({item.data["sim_id"] for item in rolls},{exactly_ten.id,older.id})
                self.assertEqual({item.data["age_at_death_years"] for item in rolls},{10,22})
                self.assertEqual(schedule_occult_rolls(session,save,[too_young,exactly_ten,older,future]),0)
                schedule_rolls(session,save)
                self.assertTrue(all(not item.deleted for item in rolls))
                session.rollback()

    def test_clock_changes_build_a_passive_personal_history_once(self):
        with TestClient(app):
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                marker=uuid.uuid4().hex
                sim=Record(save_id=save.id,kind="sim",label="History Test",data={
                    "game_sim_id":"history-"+marker,"game_age_stage":"Age.CHILD",
                    "game_career":"Student","game_education":"Grade School",
                    "game_skills":["Logic (level 1)"],"game_milestones":[],
                })
                session.add(sim);session.flush()
                snapshot={
                    "telemetry_version":2,"age_stage":"Age.TEEN","career":"Part-time Worker",
                    "education":"High School","skills":[{"name":"Logic","level":2}],
                    "milestones":["Became a Teen"],"detected_game_day":900,
                    "detected_game_hour":7,"detected_game_minute":15,
                }
                reconcile_sim(session,save,sim,snapshot)
                first=list(session.scalars(select(Record).where(
                    Record.save_id==save.id,Record.kind=="game_history",
                    Record.data["sim_id"].as_string()==sim.id,
                )))
                self.assertEqual({item.data["category"] for item in first},{"life_stage","career","education","milestone","skill"})
                self.assertTrue(all(item.data["detected_game_time"]=="07:15" for item in first))
                reconcile_sim(session,save,sim,snapshot)
                second=list(session.scalars(select(Record).where(
                    Record.save_id==save.id,Record.kind=="game_history",
                    Record.data["sim_id"].as_string()==sim.id,
                )))
                self.assertEqual(len(second),len(first))
                session.rollback()

    def test_pregnancy_progress_updates_dashboard_without_inbox_noise(self):
        with TestClient(app):
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                mother=Record(save_id=save.id,kind="sim",label="Progress Parent",data={"game_sim_id":"progress-"+uuid.uuid4().hex,"game_was_pregnant":True})
                session.add(mother);session.flush()
                pregnancy=Record(save_id=save.id,kind="pregnancy",label="Progress pregnancy",global_day=save.global_day,data={"mother_id":mother.id,"status":"Active","conception_global_day":save.global_day-2,"due_global_day":save.global_day+2})
                session.add(pregnancy);session.flush()
                reconcile_sim(session,save,mother,{"is_pregnant":True,"pregnancy_progress":0.72,"detected_game_day":88,"detected_game_hour":18,"detected_game_minute":5})
                session.flush()
                self.assertEqual(pregnancy.data["game_pregnancy_progress"],72.0)
                self.assertEqual(pregnancy.data["game_pregnancy_band"],"Late pregnancy")
                records=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False))))
                row=pregnancy_dashboard(records,save)["rows"][pregnancy.id]
                self.assertEqual(row["progress"],72)
                self.assertTrue(row["reported"])
                pending=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="game_candidate",Record.data["sim_id"].as_string()==mother.id))
                self.assertIsNone(pending)
                session.rollback()

    def test_finance_census_and_illness_statistics_use_existing_telemetry(self):
        with TestClient(app):
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                marker=uuid.uuid4().hex
                home=Record(save_id=save.id,kind="household",label="Ledger House",data={})
                session.add(home);session.flush()
                sim=Record(save_id=save.id,kind="sim",label="Ledger Sim",data={"game_sim_id":"ledger-"+marker,"current_household_id":home.id,"last_household_funds":1000})
                session.add(sim);session.flush()
                entries=telemetry.capture_household_finances(session,save,[{"game_sim_id":sim.data["game_sim_id"],"household_id":"game-home","household_name":"Ledger House","household_funds":1250}],{sim.data["game_sim_id"]:sim},{"detected_game_day":10,"detected_game_hour":9,"detected_game_minute":30})
                illness=Record(save_id=save.id,kind="illness",label="Ledger Sim — Fever",global_day=save.global_day-2,data={"sim_id":sim.id,"illness_name":"Fever","status":"Recovered","onset_global_day":save.global_day-2,"end_global_day":save.global_day,"source":"Clock Sync"})
                session.add(illness);session.flush()
                self.assertEqual(len(entries),1)
                self.assertIn("gained §250",entries[0])
                records=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.deleted.is_(False))))
                census=household_census(records,save)
                self.assertEqual(census["rows"][home.id]["balance"],1250)
                stats=illness_statistics(records,save)
                self.assertEqual(stats["total"],1)
                self.assertEqual(stats["recovered"],1)
                self.assertEqual(stats["average_duration"],2)
                session.rollback()

    def test_complete_statistics_dashboard_summarizes_every_challenge_system(self):
        save=ChronicleSave(id="stats-save",workspace_id="stats-workspace",name="Statistics",global_day=20,start_year=1600,days_per_year=4)
        home=Record(id="stats-home",save_id=save.id,kind="household",label="Census House",data={})
        parent=Record(id="stats-parent",save_id=save.id,kind="sim",label="Living Parent",global_day=1,data={"birth_global_day":1,"generation":1,"sex":"Female","current_household_id":home.id})
        child=Record(id="stats-child",save_id=save.id,kind="sim",label="Lost Child",global_day=5,data={"birth_global_day":5,"death_global_day":10,"cause_of_death":"Fever","generation":2,"sex":"Male","mother_id":parent.id,"current_household_id":home.id})
        records=[home,parent,child,
            Record(id="stats-relation",save_id=save.id,kind="relationship",label="Marriage",global_day=4,data={"type":"Marriage","status":"Active","legally_married":True,"start_global_day":4}),
            Record(id="stats-pregnancy",save_id=save.id,kind="pregnancy",label="Twin pregnancy",global_day=6,data={"mother_id":parent.id,"status":"Delivered","end_global_day":6,"babies_expected":2,"babies_delivered":2}),
            Record(id="stats-illness",save_id=save.id,kind="illness",label="Fever",global_day=7,data={"sim_id":child.id,"illness_name":"Fever","status":"Recovered","onset_global_day":7,"end_global_day":9,"source":"Clock Sync"}),
            Record(id="stats-event",save_id=save.id,kind="event",label="Famine",global_day=20,data={"start_global_day":20,"end_global_day":21,"category":"Famine","location":"Europe","roll_required":True}),
            Record(id="stats-roll",save_id=save.id,kind="roll",label="Famine roll",global_day=20,data={"roll_type":"Event — Famine","die":"d20","actual":1,"bad_results":"1","outcome":"Failed","completed":True,"completed_global_day":20,"event_id":"stats-event"}),
        ]
        result=challenge_statistics(records,save)
        self.assertEqual((result["living"],result["deceased"]),(1,1))
        self.assertEqual(result["challenge_net_growth"],1)
        self.assertEqual(result["pregnancy"]["delivered_babies"],2)
        self.assertEqual(result["relationship"]["active_marriages"],1)
        self.assertEqual(result["illness"]["recovered"],1)
        self.assertEqual(result["rolls"]["failed"],1)
        self.assertEqual(result["event_categories"][0],("Famine",1))
        self.assertTrue(result["yearly_activity"])

    def test_populated_telemetry_dashboards_and_profiles_render(self):
        marker=uuid.uuid4().hex
        created=[]
        with TestClient(app) as client:
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                home=Record(save_id=save.id,kind="household",label="Render House "+marker,data={"last_game_funds":1500})
                session.add(home);session.flush();created.append(home.id)
                sim=Record(save_id=save.id,kind="sim",label="Render Sim "+marker,data={"first_name":"Render","last_name":"Sim","current_household_id":home.id,"game_age_stage":"Age.YOUNGADULT"})
                session.add(sim);session.flush();created.append(sim.id)
                pregnancy=Record(save_id=save.id,kind="pregnancy",label="Render pregnancy",global_day=save.global_day,data={"mother_id":sim.id,"mother_name":sim.label,"status":"Active","conception_global_day":save.global_day-2,"due_global_day":save.global_day+2,"game_pregnancy_progress":50,"game_pregnancy_band":"Middle pregnancy"})
                illness=Record(save_id=save.id,kind="illness",label="Render Fever",global_day=save.global_day-1,data={"sim_id":sim.id,"sim_name":sim.label,"illness_name":"Fever","status":"Active","onset_global_day":save.global_day-1})
                history=Record(save_id=save.id,kind="game_history",label="Render House gained §500.",global_day=save.global_day,data={"category":"finance","sim_id":sim.id,"tracker_household_id":home.id,"balance":1500,"delta":500})
                digest=Record(save_id=save.id,kind="session_journal",label="Game report",global_day=save.global_day,data={"entries":["Render Sim entered young adult."],"game_time":"08:15"})
                session.add_all([pregnancy,illness,history,digest]);session.flush();created.extend([pregnancy.id,illness.id,history.id,digest.id]);session.commit()
            for path in ("/p/today","/p/pregnancies","/p/illnesses","/p/households","/p/storyline",f"/sims/{sim.id}",f"/households/{home.id}",f"/pregnancies/{pregnancy.id}"):
                response=client.get(path)
                self.assertEqual(response.status_code,200,path)
            with SessionLocal() as session:
                session.execute(delete(Record).where(Record.id.in_(created)));session.commit()

    def test_pregnancy_completion_preserves_twins_and_review_can_confirm_them(self):
        with TestClient(app) as client:
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                mother=Record(save_id=save.id,kind="sim",label="Twin Parent",data={"game_sim_id":"twin-parent","game_was_pregnant":True,"last_game_pregnancy_count":2})
                session.add(mother);session.flush()
                pregnancy=Record(save_id=save.id,kind="pregnancy",label="Twin pregnancy",global_day=save.global_day,data={"mother_id":mother.id,"mother_name":mother.label,"status":"Active","babies_expected":2,"babies_delivered":0})
                session.add(pregnancy);session.flush()
                made=reconcile_sim(session,save,mother,{"is_pregnant":False})
                self.assertEqual(made[0].data["payload"]["babies_delivered"],2)
                candidate_id=made[0].id;pregnancy_id=pregnancy.id;mother_id=mother.id
                session.commit()
            response=client.post(f"/automation/{candidate_id}/accept",data={"status":"Delivered","babies_delivered":"2","delivery_global_day":"42","outcome":"Healthy twins","complication":""},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            with SessionLocal() as session:
                pregnancy=session.get(Record,pregnancy_id);candidate=session.get(Record,candidate_id)
                self.assertEqual(pregnancy.data["babies_delivered"],2)
                self.assertEqual(pregnancy.data["actual_delivery_global_day"],42)
                self.assertEqual(candidate.data["status"],"accepted")
                session.execute(delete(Record).where(Record.id.in_([candidate_id,pregnancy_id,mother_id])));session.commit()

    def test_detected_game_details_render_on_sim_profile(self):
        with TestClient(app) as client:
            with SessionLocal() as session:
                save=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim=Record(save_id=save.id,kind="sim",label="Visible Telemetry",data={"first_name":"Visible","last_name":"Telemetry","game_traits":["Bookworm"],"game_skills":["Logic (level 5)"],"game_milestones":["Learned to Walk"]})
                session.add(sim);session.commit();sim_id=sim.id
            page=client.get(f"/sims/{sim_id}")
            self.assertEqual(page.status_code,200)
            self.assertIn("Bookworm",page.text);self.assertIn("Logic (level 5)",page.text);self.assertIn("Learned to Walk",page.text)
            self.assertIn("LIFE AT A GLANCE",page.text);self.assertIn("Family connections",page.text);self.assertIn("Health & family planning",page.text);self.assertIn("PROFILE EDITOR",page.text)
            for field in ("first_name","birth_global_day","mother_id","household_id","game_traits","game_skills","game_milestones","notes"):
                self.assertIn(f'name="{field}"',page.text)
            with SessionLocal() as session:
                session.execute(delete(Record).where(Record.id==sim_id));session.commit()

    def test_storyline_never_passes_current_year(self):
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                future = Record(save_id=save.id, kind="event", label="Future event", global_day=save.global_day + save.days_per_year * 50, data={"active": True})
                session.add(future); session.flush()
                story = build_storyline(session, save)
                self.assertTrue(all(chapter["year"] <= story["year"] for chapter in story["chapters"]))
                session.rollback()

    def test_storyline_writes_a_detailed_paragraph_for_every_year(self):
        marker=uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                original=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=original.workspace_id,name=f"Annual chronicle {marker}",global_day=13,start_year=1600,days_per_year=4)
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label="Anne Chronicle",global_day=1,data={"birth_global_day":1})
                event=Record(save_id=save.id,kind="event",label="Recorded Crop Failure",global_day=5,data={"start_global_day":5,"end_global_day":8,"active":True})
                departed=Record(save_id=save.id,kind="sim",label="Robert Chronicle",global_day=1,data={"birth_global_day":1,"death_global_day":13,"cause_of_death":"Fever"})
                session.add_all([sim,event,departed]);session.flush()
                death=Record(save_id=save.id,kind="death",label="Death of Robert Chronicle",global_day=13,data={"sim_id":departed.id,"death_global_day":13,"cause_of_death":"Fever"})
                session.add(death);session.commit();original_id,save_id=original.id,save.id
            with SessionLocal() as session:
                story=build_storyline(session,session.get(ChronicleSave,save_id))
                self.assertEqual([chapter["year"] for chapter in story["chapters"]],[1603,1602,1601,1600])
                self.assertTrue(all(chapter["paragraph"].count(".") >= 3 for chapter in story["chapters"]))
                self.assertIn("Recorded Crop Failure",next(chapter for chapter in story["chapters"] if chapter["year"]==1601)["paragraph"])
                self.assertIn("quiet interval",next(chapter for chapter in story["chapters"] if chapter["year"]==1602)["paragraph"])
                death_paragraph=next(chapter for chapter in story["chapters"] if chapter["year"]==1603)["paragraph"]
                self.assertEqual(death_paragraph.count("Robert Chronicle"),1)
            client.post("/saves/select",data={"save_id":save_id})
            page=client.get("/p/storyline")
            self.assertEqual(page.status_code,200);self.assertIn("A chapter for every year",page.text);self.assertIn("story-year-1602",page.text)
            client.post("/saves/select",data={"save_id":original_id})
            with SessionLocal() as session:
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_storyline_handles_mixed_timestamp_awareness(self):
        with TestClient(app):
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                naive = Record(save_id=save.id,kind="note",label="Naive timestamp",global_day=save.global_day,data={})
                aware = Record(save_id=save.id,kind="note",label="Aware timestamp",global_day=save.global_day,data={})
                session.add_all([naive,aware]);session.flush()
                naive.updated_at=datetime(2026,1,1,12,0,0)
                aware.updated_at=datetime(2026,1,1,12,0,1,tzinfo=timezone.utc)
                story=build_storyline(session,save)
                self.assertIn("recent",story)
                session.rollback()

    def test_relationship_removal_is_reviewed_once_and_preserves_marriage_date(self):
        with TestClient(app) as client:
            marker = uuid.uuid4().hex
            with SessionLocal() as session:
                template = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save = ChronicleSave(workspace_id=template.workspace_id, name="Relationship ending", global_day=50, start_year=1550, days_per_year=4)
                session.add(save); session.flush()
                first = Record(save_id=save.id, kind="sim", label="First Spouse", data={"game_sim_id":"a-"+marker,"game_relationship_keys":["b-"+marker+":marriage"]})
                second = Record(save_id=save.id, kind="sim", label="Second Spouse", data={"game_sim_id":"b-"+marker,"game_relationship_keys":["a-"+marker+":marriage"]})
                session.add_all([first,second]); session.flush()
                marriage = Record(save_id=save.id, kind="relationship", label="First & Second", global_day=20, data={"partner1_id":first.id,"partner2_id":second.id,"type":"Marriage","status":"Active","legally_married":True,"marriage_global_day":20,"historical_marriage_date":"January 1, 1555"})
                session.add(marriage); session.flush()
                made = reconcile_sim(session,save,first,{"game_sim_id":"a-"+marker,"relationships":[],"detected_tracker_global_day":50,"detected_game_hour":14,"detected_game_minute":30})
                duplicate = reconcile_sim(session,save,first,{"game_sim_id":"a-"+marker,"relationships":[],"detected_tracker_global_day":50,"detected_game_hour":14,"detected_game_minute":31})
                self.assertEqual(len(made),1); self.assertEqual(made[0].data["action"],"relationship_end"); self.assertEqual(duplicate,[])
                candidate_id=made[0].id; save_id=save.id; marriage_id=marriage.id
                session.commit()
            client.post("/saves/select",data={"save_id":save_id})
            response=client.post(f"/automation/{candidate_id}/accept",data={"other_sim_id":second.id,"relationship_type":"Marriage","relationship_status":"Divorced","end_global_day":"50","end_game_hour":"14","end_game_minute":"30"},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            with SessionLocal() as session:
                marriage=session.get(Record,marriage_id)
                self.assertEqual(marriage.data["status"],"Divorced")
                self.assertEqual(marriage.data["marriage_global_day"],20)
                self.assertEqual(marriage.data["end_time"],"14:30")
                self.assertFalse(marriage.data["legally_married"])
                session.execute(delete(Record).where(Record.save_id==save_id)); session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id)); session.commit()

    def test_reopening_failed_roll_reverses_unconfirmed_automatic_death(self):
        with TestClient(app) as client:
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Roll correction",global_day=40,start_year=1550,days_per_year=4)
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label="Corrected Sim",global_day=1,data={"birth_global_day":1})
                session.add(sim);session.flush()
                roll=Record(save_id=save.id,kind="roll",label="Corrected danger",global_day=40,data={"sim_id":sim.id,"roll_type":"Danger","die":"d20","bad_results":"1","death_window_start":40,"death_window_end":40})
                future=Record(save_id=save.id,kind="roll",label="Future restored",global_day=45,data={"sim_id":sim.id,"roll_type":"Later","die":"d20","bad_results":"1"})
                illness=Record(save_id=save.id,kind="illness",label="Corrected fever",global_day=39,data={"sim_id":sim.id,"status":"Active","onset_global_day":39})
                session.add_all([roll,future,illness]);session.flush();complete_roll(session,save,roll,1);session.commit();save_id,roll_id,sim_id,future_id,illness_id=save.id,roll.id,sim.id,future.id,illness.id
            client.post("/saves/select",data={"save_id":save_id})
            response=client.post(f"/api/rolls/{roll_id}/reopen",follow_redirects=False)
            self.assertEqual(response.status_code,303)
            with SessionLocal() as session:
                roll=session.get(Record,roll_id);sim=session.get(Record,sim_id);future=session.get(Record,future_id);illness=session.get(Record,illness_id)
                self.assertFalse(bool(roll.data.get("completed")));self.assertIsNone(sim.data.get("death_global_day"));self.assertFalse(future.deleted);self.assertEqual(illness.data["status"],"Active")
                death=session.scalar(select(Record).where(Record.save_id==save_id,Record.kind=="death",Record.data["source_roll_id"].as_string()==roll_id));self.assertTrue(death.deleted)
                session.execute(delete(Record).where(Record.save_id==save_id));session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id));session.commit()

    def test_permanent_sim_delete_archives_dependents_and_detaches_children(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Safe delete",global_day=20,start_year=1550,days_per_year=4)
                session.add(save);session.flush()
                parent=Record(save_id=save.id,kind="sim",label="Accidental Sim",data={})
                session.add(parent);session.flush()
                child=Record(save_id=save.id,kind="sim",label="Child",data={"mother_id":parent.id,"parent_ids":[parent.id]})
                roll=Record(save_id=save.id,kind="roll",label="Dependent roll",global_day=20,data={"sim_id":parent.id})
                home=Record(save_id=save.id,kind="household",label="Home",data={"head_id":parent.id,"member_ids":[parent.id]})
                session.add_all([child,roll,home]);session.flush();parent_id=parent.id;roll_id=roll.id;child_id=child.id;home_id=home.id
                result=purge_sim(session,save,parent);session.flush()
                self.assertEqual(result["archived"],1);self.assertIsNone(session.get(Record,parent_id))
                self.assertTrue(session.get(Record,roll_id).deleted)
                self.assertIsNone(session.get(Record,child_id).data["mother_id"]);self.assertEqual(session.get(Record,child_id).data["parent_ids"],[])
                self.assertIsNone(session.get(Record,home_id).data["head_id"]);self.assertEqual(session.get(Record,home_id).data["member_ids"],[])
                session.rollback()

    def test_approved_event_catalog_repairs_a_missing_row_without_duplicates(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Event integrity",start_year=1550,days_per_year=4)
                session.add(save);session.flush()
                made=__import__('app.domain',fromlist=['seed_event_catalog']).seed_event_catalog(session,save)
                self.assertEqual(made,655)
                events=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="event")))
                self.assertEqual(len(events),655)
                session.delete(events[0]);session.flush()
                repaired=__import__('app.domain',fromlist=['seed_event_catalog']).seed_event_catalog(session,save)
                self.assertEqual(repaired,1)
                self.assertEqual(session.scalar(select(func.count()).select_from(Record).where(Record.save_id==save.id,Record.kind=="event")),655)
                session.rollback()

    def test_backup_exports_notifications_and_story_generation_are_grounded(self):
        with TestClient(app):
            with SessionLocal() as session:
                template=session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                save=ChronicleSave(workspace_id=template.workspace_id,name="Portable Chronicle",global_day=9,start_year=1600,days_per_year=4)
                session.add(save);session.flush()
                sim=Record(save_id=save.id,kind="sim",label="Anne Test",global_day=1,data={"first_name":"Anne","last_name":"Test","birth_global_day":1})
                session.add(sim);session.flush();save.revision=1
                raw=backup_service.build_package(session,save)
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    self.assertIn("records.json",archive.namelist())
                restored=backup_service.restore_as_copy(session,save.workspace_id,raw)
                self.assertIn("Restored",restored.name)
                self.assertTrue(exports.csv_archive(session,save).startswith(b"PK"))
                self.assertIn("0 HEAD",exports.gedcom(session,save));self.assertIn("BEGIN:VCALENDAR",exports.calendar_ics(session,save))
                entry=__import__('app.storyline',fromlist=['generate_chapter']).generate_chapter(session,save,tone="formal")
                self.assertEqual(entry.data["source"],"offline story engine");self.assertTrue(entry.data["grounding_facts"])
                event=notifications.record(session,save,"baby","New baby detected!","Review the child",source_key="test-baby")
                self.assertEqual(notifications.record(session,save,"baby","Duplicate","",source_key="test-baby").id,event.id)
                session.rollback()

    def test_workspace_invitation_and_field_level_conflict_merge(self):
        with TestClient(app):
            with SessionLocal() as session:
                owner=session.scalar(select(User).where(User.email=="local@decades.invalid"));membership=session.scalar(select(Membership).where(Membership.user_id==owner.id))
                invite,raw=accounts.create_invite(session,membership.workspace_id,owner,f"guest-{uuid.uuid4().hex}@example.com")
                guest=User(email=invite.email,display_name="Guest");session.add(guest);session.flush()
                accepted=accounts.accept_invitation(session,guest,raw);self.assertEqual(accepted.role,"editor")
                conflict=Conflict(record_id="record",save_id="save",local_change={"kind":"sim","operation":"upsert","payload":{"label":"Desktop","global_day":7,"data":{"notes":"desktop","rank":"duke"}}},server_record={"kind":"sim","label":"Hosted","global_day":6,"data":{"notes":"hosted","rank":"earl"},"deleted":False})
                fields=sync.conflict_fields(conflict);self.assertTrue(any(row["path"]=="data.notes" and row["different"] for row in fields))
                merged=sync.merged_conflict_payload(conflict,{"label","data.notes"})
                self.assertEqual((merged["label"],merged["global_day"],merged["data"]["notes"],merged["data"]["rank"]),("Desktop",6,"desktop","earl"))
                session.rollback()

    def test_custom_illness_signature_matches_new_mod_traits(self):
        found=trait_illnesses({"traits":["Malaria fever marker"]},localizations={},signatures=[{"pattern":"malaria","match_type":"contains","illness_name":"Malaria","active":True}])
        self.assertEqual(found[0]["name"],"Malaria")


if __name__ == "__main__":
    unittest.main()
