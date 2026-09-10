"""Household-history features use disposable data, not installed saves."""
import copy
import re
import unittest
from uuid import uuid4
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app import main,heritage as h,heritage_ui as web,domain,insights,storyline,sync
from app.models import Record,ChronicleSave,ActionPreview,Change,User,Workspace,Membership,Portrait
from tests import test_infinite_decades as fixtures

class HeritageTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.InfiniteDecadesTests();self.f.setUp();self.save=self.f.save;self.s=self.f.session
        self.save.settings={**self.save.settings,'automation_enabled':False};self.s.commit()
        self.p=patch.object(main,'SessionLocal',self.f.sessions);self.p.start();self.client=TestClient(main.app)
        self.client.post('/saves/select',data={'save_id':self.save.id},follow_redirects=False)
    def tearDown(self):self.client.close();self.p.stop();self.f.tearDown()
    def add(self,kind,label,day=90,**data):
        row=Record(save_id=self.save.id,kind=kind,label=label,global_day=day,data=data);self.s.add(row);self.s.commit();return row
    def form(self,feature,row=None,**values):
        data={'save_id':self.save.id,'form_id':uuid4().hex,'label':'Test '+feature,'private':'on'}
        for f in web.SCHEMAS[feature][2]:data[f['key']]=(row.data.get(f['key'],f['default']) if row else f['default'])
        if row:data.update(record_id=row.id,version=row.version,label=row.label,notes=row.data.get('notes',''))
        for key in ('from_global_day','held_from_global_day'):
            if key in data and data[key]=='':data[key]=self.save.global_day
        if 'from_year' in data and feature=='routine' and data['from_year']=='':data['from_year']=1324
        data.update(values);return {key:value for key,value in data.items() if value is not None}
    def put(self,feature,row=None,expected=303,**values):
        data=self.form(feature,row,**values);response=self.client.post('/heritage/'+feature+'/save',data=data,follow_redirects=False)
        self.assertEqual(response.status_code,expected,response.text[:500])
        if expected!=303:return response
        rid=row.id if row else h.record_id(self.save.id,feature+':'+data['form_id']);self.s.expire_all();return self.s.get(Record,rid)
    def preview(self,rows,choice='historical',**extra):
        data={'save_id':self.save.id,'through':self.save.global_day-1,'reason':'Spreadsheet import'}
        for row in rows:data.update({'resolution_'+row.id:choice,'version_'+row.id:row.version})
        data.update(extra);response=self.client.post('/heritage/catch-up/preview',data=data)
        self.assertEqual(response.status_code,200,response.text[:500])
        return re.search(r'/heritage/catch-up/([a-f0-9]+)/confirm',response.text).group(1)
    def confirm(self,pid,status=200):
        response=self.client.post('/heritage/catch-up/'+pid+'/confirm',data={'save_id':self.save.id})
        self.assertEqual(response.status_code,status,response.text[:500]);self.s.expire_all();return response
    def export(self,**changes):
        data={'save_id':self.save.id,'sim_ids':[p.id for p in self.f.people],'from_year':1300,'through_year':1324,'download':'no',**changes}
        return self.client.post('/heritage/chronicle',data=data)
    def test_all_pages_render_without_mutating_user_records(self):
        before=self.s.scalar(select(func.count()).select_from(Record))
        for page in web.PAGES:
            with self.subTest(page=page):
                response=self.client.get('/p/'+page);self.assertEqual(response.status_code,200,response.text[:300])
        self.assertEqual(before,self.s.scalar(select(func.count()).select_from(Record)))
    def test_property_residence_and_evidence_do_not_move_the_sim(self):
        prop=self.put('property',label='Rose Cottage',address='1 Rose Lane')
        before=copy.deepcopy(self.f.people[0].data)
        stay=self.put('residence',property_id=prop.id,sim_id=self.f.people[0].id,from_global_day=50,until_global_day=80,status='Ended')
        self.add('migration','Journey while resident',60,sim_id=self.f.people[0].id)
        self.add('migration','Later journey',90,sim_id=self.f.people[0].id)
        response=self.client.get('/p/historical-addresses?property='+prop.id)
        self.assertEqual(response.status_code,200,response.text[:500]);self.assertIn('Journey while resident',response.text);self.assertNotIn('Later journey',response.text)
        self.assertIn('venue not confirmed',response.text);self.s.refresh(self.f.people[0]);self.assertEqual(before,self.f.people[0].data)
        self.assertIn(prop.kind,sync.SYNC_KINDS);self.assertIn(stay.kind,sync.SYNC_KINDS)
    def test_residence_date_validation_and_required_occupant(self):
        prop=self.put('property',address='Address')
        self.put('residence',expected=400,property_id=prop.id,from_global_day=1)
        self.put('residence',expected=400,property_id=prop.id,sim_id=self.f.people[0].id,from_global_day=90,until_global_day=80)
        self.put('residence',expected=400,property_id=prop.id,sim_id=self.f.people[0].id,from_global_day=101)
        self.put('residence',expected=400,property_id=prop.id,sim_id=self.f.people[0].id,from_global_day=1,status='Ended')
    def test_title_transfer_retains_history_and_claim_order(self):
        title=self.put('title',label='Baron of Rose',holder_id=self.f.people[0].id,held_from_global_day=20)
        title=self.put('title',title,holder_id=self.f.people[1].id,held_from_global_day=80,transfer_reason='Recorded inheritance')
        self.assertEqual(title.data['tenures'][0]['holder_id'],self.f.people[0].id)
        self.assertEqual(title.data['tenures'][0]['until_global_day'],79)
        self.put('claim',title_id=title.id,sim_id=self.f.people[2].id,rank=2,status='Contested')
        self.put('claim',title_id=title.id,sim_id=self.f.people[3].id,rank=1)
        response=self.client.get('/p/titles-estates');self.assertEqual(response.status_code,200,response.text[:500])
        self.assertIn('Previous holders',response.text);self.assertIn('Recorded inheritance',response.text);self.assertIn('Contested',response.text)
    def test_title_transfer_invalid_dates_rejected(self):
        title=self.put('title',holder_id=self.f.people[0].id,held_from_global_day=90)
        self.put('title',title,expected=400,holder_id=self.f.people[1].id,held_from_global_day=80)
        self.put('title',title,expected=400,held_from_global_day=101)
        self.put('title',title,expected=400,status='Vacant')
    def test_idempotent_create_and_stale_edit(self):
        data=self.form('property',address='Address')
        for _ in range(2):self.assertEqual(self.client.post('/heritage/property/save',data=data,follow_redirects=False).status_code,303)
        row=self.s.get(Record,h.record_id(self.save.id,'property:'+data['form_id']));edit=self.form('property',row,address='Updated')
        self.assertEqual(self.client.post('/heritage/property/save',data=edit,follow_redirects=False).status_code,303)
        self.assertEqual(self.client.post('/heritage/property/save',data=edit,follow_redirects=False).status_code,409)
        self.assertEqual(self.s.scalar(select(func.count()).select_from(Record).where(Record.data['feature'].as_string()=='heritage_property')),1)
    def test_references_and_save_are_owned(self):
        foreign=ChronicleSave(workspace_id=self.save.workspace_id,name='Other',settings={});self.s.add(foreign);self.s.flush()
        person=Record(save_id=foreign.id,kind='sim',label='Foreign',data={});self.s.add(person);self.s.commit()
        self.put('commitment',expected=400,sim_id=person.id,next_step='Promise')
        data=self.form('property',address='Address',save_id=foreign.id)
        self.assertEqual(self.client.post('/heritage/property/save',data=data).status_code,409)
    def test_frozen_record_cannot_be_edited(self):
        prop=self.put('property',address='Address');prop.data={**prop.data,'infinite_frozen':True};self.s.commit()
        self.put('property',prop,expected=400,address='Changed')
    def test_custom_name_uses_relative_parent_and_era(self):
        for label,kind in [('Beatrice','first'),('Arden','surname')]:self.add('name_entry',label,culture='Test source',sex='Any',name_kind=kind,name=label)
        self.f.people[0].data={**self.f.people[0].data,'first_name':'Ada','last_name':'Cooley'};self.s.commit()
        custom=self.put('custom',culture='Test source',surname_culture='Test source',given_mode='Relative',surname_mode='Patronymic',patronymic_pattern='child of {parent}',style_prefix='Lady',from_year=1300,until_year=1400)
        params={'generate':'1','custom_id':custom.id,'relative_id':self.f.people[0].id,'parent_id':self.f.people[1].id,'sex':'Any'}
        response=self.client.get('/p/naming-customs',params=params);self.assertEqual(response.status_code,200,response.text[:400]);self.assertIn('Lady Ada child of Ben',response.text)
        custom=self.put('custom',custom,until_year=1320)
        response=self.client.get('/p/naming-customs',params=params);self.assertIn('outside its configured era',response.text)
        response=self.client.get('/p/naming-customs',params={**params,'override_era':'on'});self.assertIn('Lady Ada child of Ben',response.text)
    def test_invalid_patronymic_pattern(self):
        self.add('name_entry','Ada',culture='Tiny',sex='Any',name_kind='first',name='Ada')
        self.put('custom',expected=400,culture='Tiny',surname_mode='Patronymic',patronymic_pattern='{other}')
    def test_seasonal_quarters_scale_and_tasks_are_idempotent(self):
        self.save.days_per_year=12;self.save.global_day=1;self.save.settings={**self.save.settings,'automation_enabled':True};self.s.commit()
        routine=self.put('routine',household_id=self.f.home.id,season='Autumn',instructions='Harvest the fields',from_year=1300)
        self.assertEqual(h.season_day(self.save,1300,'Autumn'),7)
        self.assertEqual(h.schedule_routines(self.s,self.save),0)
        self.save.global_day=7;self.s.commit();self.assertEqual(h.schedule_routines(self.s,self.save),1);self.s.commit()
        self.assertEqual(h.schedule_routines(self.s,self.save),0)
        task=self.s.get(Record,h.record_id(self.save.id,f'routine:{routine.id}:1300'))
        response=self.client.get('/api/ui/today/decisions');self.assertIn(task.label,response.text)
        task=self.put('routine_task',task,status='Completed',outcome_notes='Harvest safely stored')
        self.assertEqual(h.schedule_routines(self.s,self.save),0)
        self.save.global_day=19;self.s.commit();self.assertEqual(h.schedule_routines(self.s,self.save),1)
        self.s.commit();self.s.refresh(task);self.assertTrue(task.data['completed'])
    def test_routines_respect_pause_and_do_not_backfill(self):
        routine=self.put('routine',household_id=self.f.home.id,season='Spring',instructions='Plant seeds',from_year=1300)
        self.assertEqual(h.schedule_routines(self.s,self.save),0)
        self.save.settings={**self.save.settings,'automation_enabled':True};self.s.commit()
        self.assertEqual(h.schedule_routines(self.s,self.save),0) # GD 97 was before enablement on 100.
        self.save.global_day=101;self.s.commit();self.assertEqual(h.schedule_routines(self.s,self.save),1)
        self.s.commit();routine=self.put('routine',routine,status='Paused')
        self.save.global_day=105;self.s.commit();self.assertEqual(h.schedule_routines(self.s,self.save),0)
    def test_thread_deadline_and_exact_resolution(self):
        thread=self.put('thread',household_id=self.f.home.id,next_step='Find the missing letter',due_global_day=100)
        undated=self.put('thread',label='Long-term question',next_step='Think about it')
        html=self.client.get('/api/ui/today/decisions').text;self.assertIn(thread.label,html);self.assertNotIn('Long-term question',html)
        thread=self.put('thread',thread,status='Resolved',outcome_notes='Ada found the letter beneath the hearth.')
        journal=self.s.scalar(select(Record).where(Record.data['source_record_id'].as_string()==thread.id,Record.kind=='story_entry'))
        self.assertIn('beneath the hearth',journal.data['body']);self.assertTrue(journal.data['private'])
        self.assertIn('beneath the hearth',self.client.get('/p/storyline').text)
        self.assertNotIn(thread.label,self.client.get('/api/ui/today/decisions').text)
    def test_broken_commitment_carries_context_to_drama(self):
        promise=self.put('commitment',sim_id=self.f.people[0].id,promisee_id=self.f.people[1].id,next_step='Keep the inheritance secret',witness_ids=[self.f.people[2].id])
        promise=self.put('commitment',promise,status='Broken',outcome_notes='Ada revealed the will at the feast.')
        response=self.client.post('/heritage/commitment/'+promise.id+'/scene',data={'save_id':self.save.id},follow_redirects=False);self.assertEqual(response.status_code,303,response.text)
        html=self.client.get('/p/drama').text;self.assertIn('Keep the inheritance secret',html);self.assertIn('revealed the will at the feast',html);self.assertIn('A promise returned',html)
    def test_catchup_preview_is_read_only_then_confirm_and_undo(self):
        row=self.add('roll','Old check',50,die='d6',bad_results='1',sim_id=self.f.people[0].id,completed=False)
        original=copy.deepcopy(row.data);pid=self.preview([row]);self.s.refresh(row);self.assertEqual(row.data,original)
        with patch.object(domain,'complete_roll',side_effect=AssertionError('Must not execute dice consequences')):self.confirm(pid)
        self.s.refresh(row);self.assertTrue(row.data['completed']);self.assertNotIn('actual',row.data);self.assertEqual(row.data['completed_global_day'],50)
        self.confirm(pid,409)
        response=self.client.post('/heritage/catch-up/'+pid+'/undo',data={'save_id':self.save.id},follow_redirects=False);self.assertEqual(response.status_code,303,response.text)
        self.s.refresh(row)
        for key,value in original.items():self.assertEqual(row.data.get(key),value)
        self.assertNotIn('catch_up_resolution',row.data)
    def test_catchup_completed_and_today_are_ineligible(self):
        for day,completed in [(100,False),(101,False),(40,True)]:
            row=self.add('roll','Not eligible',day,completed=completed)
            self.assertFalse(h.catchup_eligible(row,self.save,999))
        html=self.client.get('/p/catch-up').text;self.assertNotIn('<h3>Not eligible</h3>',html)
    def test_catchup_atomic_on_stale_record_and_day(self):
        a=self.add('roll','A',50,die='d6');b=self.add('roll','B',60,die='d6');pid=self.preview([a,b])
        b.version+=1;b.data={**b.data,'die':'d10'};self.s.commit();self.confirm(pid,409);self.s.refresh(a);self.assertFalse(a.data.get('completed'))
        pid=self.preview([a]);self.save.global_day+=1;self.s.commit();self.confirm(pid,409)
    def test_expired_or_foreign_catchup_is_rejected(self):
        row=self.add('roll','Old',50,die='d6');pid=self.preview([row]);ticket=self.s.get(ActionPreview,pid)
        ticket.created_at=datetime.now(timezone.utc)-timedelta(hours=1);self.s.commit();self.confirm(pid,409)
        pid=self.preview([row]);ticket=self.s.get(ActionPreview,pid);other=User(email='other@testing.invalid');self.s.add(other);self.s.flush();ticket.user_id=other.id;self.s.commit();self.confirm(pid,404)
    def test_undo_does_not_overwrite_later_edits(self):
        row=self.add('roll','Old',50,die='d6');pid=self.preview([row]);self.confirm(pid);self.s.refresh(row);row.version+=1;row.data={**row.data,'notes':'Later correction'};self.s.commit()
        response=self.client.post('/heritage/catch-up/'+pid+'/undo',data={'save_id':self.save.id});self.assertEqual(response.status_code,409)
        self.s.refresh(row);self.assertEqual(row.data['notes'],'Later correction')
    def test_waived_rolls_never_count_as_passes_or_spawn_followups(self):
        row=self.add('roll','Old occult',50,die='d6',occult_rule_key='witch_trial_occurrence',triggered=True)
        pid=self.preview([row],'waive');self.confirm(pid);self.s.refresh(row)
        stats=insights.statistics([row],self.save)['rolls'];self.assertEqual(stats['passed'],0);self.assertEqual(stats['administrative'],1)
        self.assertEqual(domain._schedule_automatic_occult_followup(self.s,self.save,row),0)
        self.assertEqual(domain._schedule_event_followup(self.s,self.save,row,1),0)
    def test_export_default_is_selective_and_private(self):
        public=self.add('story_entry','Public fact',50,body='Body is separately opt-in')
        private=self.add('story_entry','Secret plot',50,body='SECRET CONTENT',private=True)
        self.add('note','Secret note',20,body='PRIVATE NOTE')
        self.save.settings={**self.save.settings,'api_key':'NEVER-EXPORT-KEY'};self.s.commit()
        response=self.export(fact_ids=[public.id,private.id]);self.assertEqual(response.status_code,200,response.text[:500])
        self.assertIn('Public fact',response.text)
        for value in ('Secret plot','SECRET CONTENT','PRIVATE NOTE','NEVER-EXPORT-KEY','Body is separately opt-in','<script','/p/sims','last_game_funds'):self.assertNotIn(value,response.text)
        self.assertIn("default-src 'none'",response.headers['content-security-policy'])
    def test_export_explicit_private_narrative_optin_escapes_html(self):
        fact=self.add('story_entry','Chosen private',30,body='<script>alert(1)</script>',private=True)
        response=self.export(fact_ids=[fact.id],private='on',narration='on',download='yes')
        self.assertEqual(response.status_code,200,response.text[:500]);self.assertIn('&lt;script&gt;',response.text);self.assertNotIn('<script>',response.text)
        self.assertIn('attachment;',response.headers['content-disposition'])
    def test_export_never_includes_future_facts_or_unselected_parents(self):
        future=self.add('story_entry','Future spoiler',105,body='Spoiler')
        response=self.export(sim_ids=[self.f.people[2].id],fact_ids=[future.id],tree='on',statistics='on')
        self.assertEqual(response.status_code,200);self.assertNotIn('Future spoiler',response.text);self.assertNotIn('>Ada<',response.text);self.assertIn('Cara',response.text)
        response=self.export(through_year=1400);self.assertEqual(response.status_code,400)
    def test_export_portraits_are_embedded_not_remote(self):
        response=self.export(sim_ids=[self.f.people[2].id],portraits='on')
        self.assertEqual(response.status_code,200,response.text[:500]);self.assertIn('data:image/png;base64,',response.text)
    def test_export_rejects_non_exportable_facts_and_cross_save_people(self):
        note=self.add('note','Private note',10,body='secret')
        self.assertEqual(self.export(fact_ids=[note.id]).status_code,400)
        self.assertEqual(self.export(sim_ids=['foreign']).status_code,400)
    def test_new_records_journal_only_supported_sync_kinds(self):
        self.put('property',address='Address');self.put('thread',next_step='Investigate')
        changes=list(self.s.scalars(select(Change).where(Change.save_id==self.save.id)))
        self.assertTrue(changes);self.assertTrue(all(c.kind in sync.SYNC_KINDS for c in changes))
    def test_catchup_paginates_and_filters_household_in_sql(self):
        for i in range(57):self.add('roll','Batch item '+str(i),50,sim_id=self.f.people[0].id)
        self.add('roll','Outside household',50,sim_id=self.f.people[4].id)
        a=self.client.get('/p/catch-up',params={'household':self.f.home.id}).text
        b=self.client.get('/p/catch-up',params={'household':self.f.home.id,'batch':2}).text
        self.assertEqual(a.count('name="resolution_'),50);self.assertEqual(b.count('name="resolution_'),7);self.assertNotIn('Outside household',a+b)
    def test_frozen_branch_write_rejected(self):
        with patch.object(web.infinite_decades,'frozen',return_value=True):self.put('property',expected=409,address='Address')
    def test_year_only_birth_export_does_not_invent_a_day(self):
        person=self.f.people[0];person.data={**person.data,'birth_global_day':21,'birth_year':1305,'birth_year_only':True};self.s.commit()
        response=self.export(sim_ids=[person.id]);self.assertEqual(response.status_code,200,response.text[:500])
        self.assertIn('Year 1305 (year-only)',response.text);self.assertIn('During 1305',response.text);self.assertNotIn('On Global Day 21',response.text)
    def test_scheduled_death_not_presented_as_confirmed(self):
        person=self.f.people[0];person.data={**person.data,'death_global_day':90};self.s.commit()
        response=self.export(sim_ids=[person.id]);self.assertEqual(response.status_code,200);self.assertNotIn('Death GD 90',response.text)
    def test_routine_calendar_change_corrects_pending_not_completed(self):
        self.save.global_day=1;self.save.settings={**self.save.settings,'automation_enabled':True};self.s.commit()
        routine=self.put('routine',household_id=self.f.home.id,season='Autumn',instructions='Harvest',from_year=1300)
        self.save.global_day=3;self.s.commit();h.schedule_routines(self.s,self.save);self.s.commit()
        row=self.s.get(Record,h.record_id(self.save.id,f'routine:{routine.id}:1300'));self.assertEqual(row.global_day,3)
        self.save.days_per_year=12;self.s.commit();h.schedule_routines(self.s,self.save);self.s.commit();self.s.refresh(row);self.assertEqual(row.global_day,7)
    def test_kept_promise_requires_exact_outcome(self):
        promise=self.put('commitment',sim_id=self.f.people[0].id,next_step='Protect the ward')
        self.put('commitment',promise,expected=400,status='Kept',outcome_notes='')
    def test_calendar_change_rescales_tasks_outside_the_new_current_year(self):
        self.save.settings={**self.save.settings,'automation_enabled':True};self.s.commit()
        row=self.add('task','Old Spring task',97,feature='heritage_routine_task',status='Open',year=1324,season='Spring',due_global_day=97)
        self.save.days_per_year=12;self.s.commit();h.schedule_routines(self.s,self.save);self.s.commit();self.s.refresh(row)
        self.assertEqual(row.global_day,289);self.assertEqual(row.data['due_global_day'],289)
    def test_reading_or_exporting_chronicle_does_not_publish_anything(self):
        before=self.s.scalar(select(func.count()).select_from(Change))
        self.assertEqual(self.export().status_code,200)
        self.assertEqual(before,self.s.scalar(select(func.count()).select_from(Change)))
    def test_later_privacy_changes_cover_derived_outcomes(self):
        row=self.put('thread',next_step='Find the letter',private='')
        row=self.put('thread',row,status='Resolved',outcome_notes='Found in the cellar',private='')
        journal=self.s.scalar(select(Record).where(Record.kind=='story_entry',Record.data['source_record_id'].as_string()==row.id));self.assertFalse(journal.data['private'])
        self.put('thread',row,private='on');self.s.refresh(journal);self.assertTrue(journal.data['private'])
        self.assertNotIn('Found in the cellar',self.export(fact_ids=[journal.id],narration='on').text)
    def test_corrected_outcome_is_labelled_in_history(self):
        row=self.put('thread',next_step='Find the letter')
        row=self.put('thread',row,status='Resolved',outcome_notes='Found in the cellar')
        self.put('thread',row,outcome_notes='Actually found behind the hearth')
        journals=list(self.s.scalars(select(Record).where(Record.kind=='story_entry',Record.data['source_record_id'].as_string()==row.id)))
        self.assertEqual(len(journals),2);self.assertTrue(any('Player correction:' in j.data['body'] and 'behind the hearth' in j.data['body'] for j in journals))

if __name__=='__main__':unittest.main()
