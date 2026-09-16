"""All tests use disposable databases; never the installed Black HP save."""
import copy
import re
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import dynasty_tools as t, infinite_dynasty as d, clock, main
from app.models import Record,ClockLink,ActionPreview,ChronicleSave
from tests.test_infinite_decades import InfiniteDecadesTests as Fixture


class DynastyToolsTests(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.f.setUp();self.s=self.f.session;self.save=self.f.save
        self.root=d.enable(self.s,self.save,[r.id for r in self.f.people[:4]],'Main',1400,clock_mode='tracker')
        self.s.commit()

    def tearDown(self): self.f.tearDown()

    def child(self,day=104): return self.f.capture(day=day)

    def preview(self,kind,**args): return t.prepare(self.s,self.save,self.f.user.id,kind,args)

    def apply(self,kind,**args):
        ticket=self.preview(kind,**args);t.confirm(self.s,self.save,ticket);self.s.commit();return ticket

    def test_queue_sort_and_preferences_survive_switches(self):
        child=self.child()
        t.save_preferences(self.s,self.save,child.id,{'queue_order':'0','notes':'Meet the in-laws','goal_kind':'year','goal_target':'1350'})
        self.s.commit()
        self.assertEqual(t.queue(self.s,self.save,'custom')[0]['branch'].id,child.id)
        self.assertEqual(t.queue(self.s,self.save,'oldest')[0]['branch'].id,child.id)
        self.apply('switch',branch_id=child.id)
        self.assertEqual(t.resume_summary(self.s,self.save,child)['notes'],'Meet the in-laws')
        self.assertEqual(d.metadata(child)['play_goal']['target'],1350)

    def test_switch_preview_does_not_mutate_then_confirm_and_replay_is_blocked(self):
        child=self.child();old=d.state(self.save)['epoch'];day=self.save.global_day
        ticket=self.preview('switch',branch_id=child.id)
        self.assertEqual(d.state(self.save)['active_branch_id'],self.root.id)
        self.assertEqual(self.save.global_day,day)
        self.assertIn('game clock is unchanged',' '.join(ticket.payload['plan']['effects']))
        t.confirm(self.s,self.save,ticket);self.s.commit()
        self.assertEqual(d.state(self.save)['active_branch_id'],child.id)
        self.assertNotEqual(d.state(self.save)['epoch'],old)
        with self.assertRaises(ValueError):t.confirm(self.s,self.save,ticket)

    def test_switch_stale_and_expired_previews_rejected(self):
        child=self.child();ticket=self.preview('switch',branch_id=child.id)
        self.save.global_day+=1;self.s.commit()
        with self.assertRaisesRegex(ValueError,'affected data changed'):t.confirm(self.s,self.save,ticket)
        ticket=self.preview('switch',branch_id=child.id)
        ticket.created_at=datetime.now(timezone.utc)-timedelta(hours=1);self.s.commit()
        with self.assertRaisesRegex(ValueError,'expired'):t.confirm(self.s,self.save,ticket)

    def test_unrelated_telemetry_does_not_block_switch_confirmation(self):
        child=self.child();ticket=self.preview('switch',branch_id=child.id)
        sim=self.f.people[0];sim.data={**sim.data,'game_age_progress_percentage':25};sim.version+=1;self.s.commit()
        t.confirm(self.s,self.save,ticket);self.s.commit()
        preserved=d.unpack_snapshot(self.root.data['snapshot'])
        self.assertEqual(next(r for r in preserved['records'] if r['id']==sim.id)['data']['game_age_progress_percentage'],25)

    def test_unrelated_telemetry_does_not_block_identity_correction(self):
        sim=self.f.people[0];ticket=self.preview('correction',sim_id=sim.id,field='birthplace',value='England')
        sim.data={**sim.data,'game_age_progress_percentage':25};sim.version+=1;self.s.commit()
        t.confirm(self.s,self.save,ticket);self.s.commit()
        self.assertEqual(sim.data['birthplace'],'England');self.assertEqual(sim.data['game_age_progress_percentage'],25)

    def test_undo_switch_keeps_new_progress(self):
        child=self.child();self.apply('switch',branch_id=child.id)
        clean=t.make_plan(self.s,self.save,'undo_switch',{})
        self.assertFalse(clean['changed_since_switch'])
        note=Record(save_id=self.save.id,kind='story_entry',label='A new baby',global_day=self.save.global_day,data={'sim_id':self.f.people[2].id})
        self.s.add(note);self.save.global_day+=2;self.s.commit()
        preview=self.preview('undo_switch');self.assertTrue(preview.payload['plan']['changed_since_switch'])
        t.confirm(self.s,self.save,preview);self.s.commit()
        self.assertEqual(d.state(self.save)['active_branch_id'],self.root.id)
        preserved=d.unpack_snapshot(child.data['snapshot'])
        self.assertEqual(preserved['global_day'],106)
        self.assertIn(note.id,{r['id'] for r in preserved['records']})

    def test_resume_exposes_age_mismatch_no_birth_change(self):
        child=self.child();sim=self.f.people[2]
        with d.branch_operation(self.s,self.save):d._touch(self.s,sim,{**sim.data,'game_age_stage':'Elder'})
        birth=sim.data['birth_global_day'];summary=t.resume_summary(self.s,self.save,child)
        self.assertEqual(summary['mismatches'][0]['name'],'Cara')
        self.assertEqual(sim.data['birth_global_day'],birth)
        self.assertEqual(summary['pending'],1)

    def test_year_birth_marriage_generation_goals(self):
        payload=t.point(self.s,self.save,self.root)
        for mode,target in [('year',1300),('generation',2)]:
            t.save_preferences(self.s,self.save,self.root.id,{'goal_kind':mode,'goal_target':str(target)})
            if mode=='generation':next(r for r in payload['records'] if r['kind']=='sim')['data']['generation']=2
            self.assertTrue(t.goal_status(self.save,self.root,payload)['reached'])

    def test_stage_check_scales_twelve_day_years_and_uses_configured_rules(self):
        from types import SimpleNamespace
        save=SimpleNamespace(days_per_year=12,start_year=1300,settings={})
        person={'kind':'sim','data':{'birth_global_day':1}}
        payload={'global_day':100,'records':[]}
        self.assertEqual(t.expected_stage(save,payload,person),'Child')
        payload['global_day']=220
        self.assertEqual(t.expected_stage(save,payload,person),'Young Adult')
        payload['records'].append({'kind':'roll_rule','label':'Young Adult','data':{'age_days':300}})
        self.assertEqual(t.expected_stage(save,payload,person),'Teen')

    def test_next_birth_and_marriage_goals(self):
        payload=t.point(self.s,self.save,self.root)
        for mode,entry in [('birth',{'id':'baby','kind':'sim','label':'Baby','global_day':101,'data':{'birth_global_day':101}}),
                           ('marriage',{'id':'married','kind':'relationship','label':'Wedding','global_day':101,'data':{'type':'Marriage','partner1_id':self.f.people[0].id}})]:
            t.save_preferences(self.s,self.save,self.root.id,{'goal_kind':mode})
            self.assertFalse(t.goal_status(self.save,self.root,payload)['reached'])
            payload['records'].append(entry);payload['member_sim_ids'].append(entry['id']);payload['global_day']=101
            self.assertTrue(t.goal_status(self.save,self.root,payload)['reached'])

    def test_correction_all_copies_and_undo_preserves_rolls(self):
        child=self.child();sim=self.f.people[2];roll=copy.deepcopy(self.f.roll.data)
        self.apply('correction',sim_id=sim.id,field='birthplace',value='England')
        self.assertEqual(sim.data['birthplace'],'England')
        for br in d.branches(self.s,self.save):
            for entry in d.unpack_snapshot(br.data['snapshot'])['records']:
                if entry['id']==sim.id:self.assertEqual(entry['data']['birthplace'],'England')
        audit=next(r for r in t.records(self.s,self.save,{t.KIND}) if r.data['feature']=='correction')
        self.apply('undo_correction',audit_id=audit.id)
        self.assertNotIn('birthplace',sim.data);self.assertEqual(self.f.roll.data,roll)
        self.assertTrue(audit.data['undone'])

    def test_correction_undo_wont_overwrite_later_edit(self):
        sim=self.f.people[0]
        self.apply('correction',sim_id=sim.id,field='birthplace',value='England')
        audit=next(r for r in t.records(self.s,self.save,{t.KIND}) if r.data['feature']=='correction')
        sim.data={**sim.data,'birthplace':'Scotland'};self.s.commit()
        ticket=self.preview('undo_correction',audit_id=audit.id)
        with self.assertRaisesRegex(ValueError,'changed again'):t.confirm(self.s,self.save,ticket)
        self.assertEqual(sim.data['birthplace'],'Scotland');self.assertFalse(audit.data.get('undone'))

    def test_corrections_reject_cycles_wrong_save_and_protected_fields(self):
        with self.assertRaisesRegex(ValueError,'ancestor'):
            self.preview('correction',sim_id=self.f.people[0].id,field='mother_id',value=self.f.people[2].id)
        with self.assertRaises(ValueError):self.preview('correction',sim_id='foreign',field='birthplace',value='England')
        with self.assertRaises(ValueError):self.preview('correction',sim_id=self.f.people[0].id,field='birth_global_day',value='200')

    def test_transfer_and_marriage_same_ids_and_rolls(self):
        child=self.child();sim=self.f.people[3];daughter=self.f.people[2]
        existing={r.id for r in t.records(self.s,self.save,{'sim'})}
        self.apply('transfer',branch_id=child.id,sim_ids=[sim.id],marriage='yes',partner1=sim.id,partner2=daughter.id)
        self.assertEqual({r.id for r in t.records(self.s,self.save,{'sim'})},existing)
        self.assertTrue(sim.data['infinite_frozen']);self.assertEqual(sim.data['infinite_branch_id'],child.id)
        point=d.unpack_snapshot(child.data['snapshot'])
        self.assertIn(sim.id,point['member_sim_ids'])
        self.assertEqual(next(r for r in point['records'] if r['id']==daughter.id)['data']['father_id'],self.f.people[1].id)
        self.assertTrue(any(r['kind']=='relationship' for r in point['records']))
        self.apply('switch',branch_id=child.id)
        self.assertFalse(sim.deleted);self.assertFalse(self.f.roll.deleted)

    def test_transfer_rejects_different_dates_and_dead_sims(self):
        child=self.child();self.save.global_day+=1;self.s.commit()
        with self.assertRaisesRegex(ValueError,'same tracker date'):self.preview('transfer',branch_id=child.id,sim_ids=[self.f.people[1].id])
        self.save.global_day-=1;self.f.people[1].data={**self.f.people[1].data,'death_global_day':100};self.s.commit()
        with self.assertRaises(ValueError):self.preview('transfer',branch_id=child.id,sim_ids=[self.f.people[1].id])

    def test_transfer_stale_preview_cannot_move_new_unreviewed_data(self):
        child=self.child();ticket=self.preview('transfer',branch_id=child.id,sim_ids=[self.f.people[1].id])
        self.f.people[1].data={**self.f.people[1].data,'birthplace':'New fact'};self.f.people[1].version+=1;self.s.commit()
        with self.assertRaisesRegex(ValueError,'affected data changed'):t.confirm(self.s,self.save,ticket)
        self.assertFalse(self.f.people[1].deleted)

    def test_wrong_household_held_before_clock_or_pregnancy_changes(self):
        link=ClockLink(save_id=self.save.id,token_hash='test',enabled=True,game_anchor_day=500,tracker_anchor_day=100,last_game_day=500)
        self.s.add(link);self.s.commit();before=self.save.global_day
        report={'game_day':501,'hour':12,'household_name':'Unrelated Jones','household_members':[{'game_sim_id':'10','is_pregnant':True}]}
        result=clock.receive(self.s,link,report);self.s.commit()
        self.assertEqual(result['status'],'branch_review');self.assertEqual(self.save.global_day,before)
        self.assertEqual(link.last_game_day,500)
        held=next(r for r in t.records(self.s,self.save,{t.KIND}) if r.data['feature']=='held_report')
        self.assertTrue(d.unpack_snapshot(held.data['report'])['report']['household_members'][0]['is_pregnant'])
        self.assertFalse(t.records(self.s,self.save,{'pregnancy'}))
        t.review_held(self.s,self.save,held.id,False);self.s.commit()
        self.assertEqual(held.data['status'],'discarded');self.assertNotIn('report',held.data)

    def test_known_household_passes_unknown_evidence_does_not_invent_mismatch(self):
        self.assertIsNone(t.hold_mismatched_report(self.s,self.save,{'household_name':'Cooley','game_day':5}))
        self.assertIsNone(t.hold_mismatched_report(self.s,self.save,{'game_day':5}))

    def test_accept_held_report_applies_once_and_remembers_household(self):
        self.save.settings={**self.save.settings,'clock_game_day_high_watermark':500}
        link=ClockLink(save_id=self.save.id,token_hash='accepted-test',enabled=True,game_anchor_day=500,tracker_anchor_day=100,last_game_day=500)
        self.s.add(link);self.s.commit()
        report={'game_day':501,'hour':1,'minute':2,'household_name':'Renamed Cooley','household_members':[]}
        self.assertEqual(clock.receive(self.s,link,report)['status'],'branch_review');self.s.commit()
        held=next(r for r in t.records(self.s,self.save,{t.KIND}) if r.data['feature']=='held_report')
        result=t.review_held(self.s,self.save,held.id,True);self.s.commit()
        self.assertTrue(result['ok']);self.assertEqual(held.data['status'],'accepted')
        self.assertEqual(link.last_game_day,501)
        self.assertIsNone(t.hold_mismatched_report(self.s,self.save,{**report,'game_day':502}))
        with self.assertRaises(ValueError):t.review_held(self.s,self.save,held.id,True)

    def test_goal_detects_birth_on_same_tracker_day(self):
        t.save_preferences(self.s,self.save,self.root.id,{'goal_kind':'birth'})
        baby=Record(save_id=self.save.id,kind='sim',label='Baby',global_day=100,data={'birth_global_day':100})
        self.s.add(baby);self.s.commit()
        self.assertTrue(t.resume_summary(self.s,self.save,self.root)['goal']['reached'])

    def test_checkpoint_only_parent_cycle_is_blocked(self):
        child=self.child();payload=d.unpack_snapshot(child.data['snapshot'])
        payload['records'].append({'id':self.f.people[3].id,'kind':'sim','label':'Dara','global_day':1,'deleted':False,
                                  'data':{'mother_id':self.f.people[2].id}})
        with d.branch_operation(self.s,self.save):d._update_branch(self.s,child,payload)
        self.s.commit()
        with self.assertRaisesRegex(ValueError,'parent cycle'):
            self.preview('correction',sim_id=self.f.people[2].id,field='mother_id',value=self.f.people[3].id)

    def test_accept_held_only_on_matching_branch_and_not_older_than_applied_time(self):
        child=self.child();link=ClockLink(save_id=self.save.id,token_hash='test',enabled=True,last_game_day=600,last_game_hour=1,last_game_minute=0)
        self.s.add(link);self.s.commit()
        t.hold_mismatched_report(self.s,self.save,{'game_day':599,'household_name':'New Family'})
        self.s.commit();held=t.records(self.s,self.save,{t.KIND})[0]
        with self.assertRaisesRegex(ValueError,'newer game report'):t.review_held(self.s,self.save,held.id,True)
        self.apply('switch',branch_id=child.id)
        with self.assertRaisesRegex(ValueError,'Resume the branch'):t.review_held(self.s,self.save,held.id,True)

    def test_same_year_metrics_do_not_project_future_or_current_wealth(self):
        payload=t.point(self.s,self.save,self.root)
        future,note=t.historical_metrics(self.save,payload,1400)
        self.assertIsNone(future['members']);self.assertIn('not recorded',note)
        past,note=t.historical_metrics(self.save,payload,1310)
        self.assertEqual(past['day'],44);self.assertIsNone(past['wealth']);self.assertEqual(past['occults'],{})

    def test_snapshot_coverage_exact_missing_people_and_waiting_year(self):
        child=self.child();data=t.snapshot_coverage(self.s,self.save,1301,None)
        row=next(r for r in data if r['name']==child.label)
        self.assertIn('Cara',row['missing']);self.assertNotIn('Cara',row['no_photo'])
        future=t.snapshot_coverage(self.s,self.save,1390,None)
        self.assertTrue(all(not r['reached'] and not r['missing'] for r in future))

    def test_suggested_splits_include_spouse_and_dependent_child(self):
        self.f.people[2].data={**self.f.people[2].data,'birth_global_day':98}
        event=Record(save_id=self.save.id,kind='relationship',label='Ada and Ben married',global_day=99,
            data={'type':'Marriage','partner1_id':self.f.people[0].id,'partner2_id':self.f.people[1].id})
        self.s.add(event);self.s.commit()
        suggestion=t.split_suggestions(self.s,self.save)[0]
        self.assertEqual({r.id for r in suggestion['people']},{r.id for r in self.f.people[:3]})
        self.assertEqual(len(d.branches(self.s,self.save)),2)

    def test_management_logs_survive_branch_restore(self):
        child=self.child()
        with d.branch_operation(self.s,self.save):row=t.tool_record(self.s,self.save,'correction',changes=[],effects=[],field='birthplace')
        self.s.commit();self.apply('switch',branch_id=child.id)
        self.assertFalse(row.deleted);self.assertNotIn(row.id,{r['id'] for r in d.snapshot(self.s,self.save)['records']})

    def test_move_suggestion_finds_existing_spouse_from_older_marriage(self):
        self.s.add_all([Record(save_id=self.save.id,kind='relationship',label='Old marriage',global_day=10,
            data={'type':'Marriage','partner1_id':self.f.people[0].id,'partner2_id':self.f.people[1].id}),
            Record(save_id=self.save.id,kind='migration',label='Ada moves',global_day=99,data={'sim_id':self.f.people[0].id})])
        self.s.commit()
        suggestion=t.split_suggestions(self.s,self.save)[0]
        self.assertEqual({r.id for r in suggestion['people']},{self.f.people[0].id,self.f.people[1].id})

    def test_http_pages_and_preview_authorization(self):
        child=self.child()
        with patch.object(main,'SessionLocal',self.f.sessions),TestClient(main.app) as client:
            client.post('/saves/select',data={'save_id':self.save.id})
            for path,text in [('/p/infinite-decades','Branch queue'),('/p/decade-snapshots','Dynasty coverage'),('/p/branch-comparison?year=1310','Compare at end of year')]:
                response=client.get(path);self.assertEqual(response.status_code,200,response.text[:300]);self.assertIn(text,response.text)
            epoch=d.state(self.save)['epoch']
            response=client.post('/infinite/'+self.save.id+'/play',data={'branch_id':child.id},headers={'X-Dynasty-Epoch':epoch})
            self.assertIn('PREVIEW ONLY',response.text)
            url=re.search(r'action="([^"]+/tools/confirm/[^"]+)"',response.text).group(1)
            denied=client.post(url,headers={'X-Dynasty-Epoch':'wrong'},follow_redirects=False)
            self.assertEqual(denied.status_code,409)
            response=client.post(url,headers={'X-Dynasty-Epoch':epoch},follow_redirects=False)
            self.assertEqual(response.status_code,303,response.text[:300])


if __name__=='__main__':unittest.main()
