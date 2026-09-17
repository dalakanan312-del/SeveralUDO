"""Disposable-database checks for dated cross-branch ledgers, never user saves."""
import copy
import re
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import dynasty_history as h, dynasty_tools as t, infinite_dynasty as d, main, storyline
from app.models import Record, ActionPreview, ChronicleSave, Portrait
from tests.test_infinite_decades import InfiniteDecadesTests as Fixture


class DynastyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.f=Fixture();self.f.setUp();self.s=self.f.session;self.save=self.f.save
        self.root=d.enable(self.s,self.save,[r.id for r in self.f.people[:4]],'Main',1400,clock_mode='tracker');self.s.commit()
        self.child=self.f.capture(day=104)

    def tearDown(self):self.f.tearDown()

    def settings(self,**values):
        with d.branch_operation(self.s,self.save):d._set_state(self.save,**values)
        self.s.commit()

    def preview(self,kind,**args):return t.prepare(self.s,self.save,self.f.user.id,'history_'+kind,args)

    def apply(self,kind,**args):
        ticket=self.preview(kind,**args);t.confirm(self.s,self.save,ticket);self.s.commit();return ticket

    def switch(self,br):
        ticket=t.prepare(self.s,self.save,self.f.user.id,'switch',{'branch_id':br.id});t.confirm(self.s,self.save,ticket);self.s.commit()

    def event(self):
        row=Record(save_id=self.save.id,kind='event',label='War of the Roses',global_day=100,data={'start_global_day':100,'end_global_day':150})
        self.s.add(row);self.s.commit();return row

    def test_shared_world_reused_only_at_date_never_completes_survival(self):
        event=self.event();self.save.global_day=110;self.s.commit();before=copy.deepcopy(self.f.roll.data)
        self.apply('world',event_id=event.id,day='110',outcome='The northern kingdom wins.')
        self.assertEqual(len(h.world_context(self.s,self.save,event.id)),1)
        self.assertEqual(self.f.roll.data,before)
        self.switch(self.child)
        self.assertEqual(h.world_context(self.s,self.save),[])
        self.save.global_day=110;self.s.commit()
        self.assertEqual(h.world_context(self.s,self.save)[0].data['outcome'],'The northern kingdom wins.')
        with self.assertRaisesRegex(ValueError,'already exists'):self.preview('world',event_id=event.id,day='110',outcome='Other')

    def test_world_requires_real_owned_event_and_valid_date(self):
        for args in [dict(event_id=self.f.people[0].id,day='100'),dict(event_id=self.event().id,day='90'),dict(event_id='foreign',day='100')]:
            with self.assertRaises(ValueError):self.preview('world',outcome='Winner',**args)

    def test_park_excludes_queue_and_next_without_changing_status(self):
        before=copy.deepcopy(self.child.data['snapshot'])
        self.apply('park',branch_id=self.child.id,parked='yes',reason='Play cousins later',reminder_year='1320')
        self.assertNotIn(self.child.id,[x['branch'].id for x in t.queue(self.s,self.save)])
        self.assertNotIn(self.child,d.playable_branches(d.branches(self.s,self.save)))
        self.assertIsNone(d.next_branch(d.branches(self.s,self.save)))
        self.assertEqual(self.child.data['snapshot'],before);self.assertEqual(d.metadata(self.child)['status'],'waiting')
        with self.assertRaises(ValueError):self.switch(self.child)
        self.apply('park',branch_id=self.child.id,parked='no')
        self.assertIn(self.child.id,[x['branch'].id for x in t.queue(self.s,self.save)])

    def test_cannot_park_current_branch(self):
        with self.assertRaises(ValueError):self.preview('park',branch_id=self.root.id,parked='yes')

    def test_visits_do_not_move_or_unfreeze_sim_and_end_once(self):
        sim=self.f.people[2];before=copy.deepcopy(sim.data);checkpoint=self.child.data['snapshot'];gd=self.save.global_day
        self.apply('visit',branch_id=self.child.id,sim_id=sim.id,day='104',label='Wedding visit',notes='Brings a letter')
        visit=h.logs(self.s,self.save,'visit')[0]
        self.assertEqual(sim.data,before);self.assertEqual(self.child.data['snapshot'],checkpoint);self.assertEqual(self.save.global_day,gd)
        self.apply('end_visit',row_id=visit.id,day='104')
        with self.assertRaises(ValueError):self.preview('end_visit',row_id=visit.id,day='104')

    def test_visit_rejects_wrong_member_unborn_and_dead(self):
        with self.assertRaises(ValueError):self.preview('visit',branch_id=self.child.id,sim_id=self.f.people[0].id,day=104,label='Visit')
        payload=d.unpack_snapshot(self.child.data['snapshot']);person=next(r for r in payload['records'] if r['id']==self.f.people[2].id)
        person['data']['death_global_day']=103
        with d.branch_operation(self.s,self.save):d._update_branch(self.s,self.child,payload)
        self.s.commit()
        with self.assertRaises(ValueError):self.preview('visit',branch_id=self.child.id,sim_id=self.f.people[2].id,day=104,label='Visit')

    def test_dated_relations_and_visits_supply_opt_in_drama_only(self):
        self.apply('relation',branch_id=self.child.id,day='104',relationship='Allied',notes='A trade agreement')
        self.assertEqual(h.drama_prompts(self.s,self.save),[])
        self.settings(branch_drama=True)
        self.assertIn('Allied',h.drama_prompts(self.s,self.save)[0]['label'])
        self.save.global_day=110;self.s.commit()
        self.apply('relation',branch_id=self.child.id,day='110',relationship='Neutral')
        self.assertEqual(h.drama_prompts(self.s,self.save),[])
        self.switch(self.child)
        self.assertIn('Allied',h.drama_prompts(self.s,self.save)[0]['label'])
        self.assertFalse(self.f.roll.data['completed'])

    def test_heirloom_chain_survives_switch_and_future_owner_hidden(self):
        self.apply('heirloom',branch_id=self.root.id,sim_id=self.f.people[0].id,day=104,label='Wedding ring')
        row=h.logs(self.s,self.save,'heirloom_history')[0]
        self.save.global_day=110;self.s.commit()
        self.apply('heirloom',row_id=row.id,branch_id=self.child.id,sim_id=self.f.people[2].id,day=110,notes='Wedding gift')
        self.assertEqual(len(row.data['history']),2)
        self.switch(self.child);self.assertEqual(len(row.data['history']),2)
        with self.assertRaises(ValueError):self.preview('heirloom',row_id=row.id,branch_id=self.child.id,sim_id=self.f.people[2].id,day=104)

    def test_link_existing_heirloom_without_duplicate_history(self):
        item=Record(save_id=self.save.id,kind='heirloom',label='Ancestral sword',data={});self.s.add(item);self.s.commit()
        self.apply('heirloom',heirloom_id=item.id,branch_id=self.root.id,sim_id=self.f.people[0].id,day=104)
        self.assertEqual(h.logs(self.s,self.save,'heirloom_history')[0].label,'Ancestral sword')
        with self.assertRaises(ValueError):self.preview('heirloom',heirloom_id=item.id,branch_id=self.root.id,sim_id=self.f.people[0].id,day=104)

    def send_person(self):
        self.save.global_day=110;self.s.commit()
        self.apply('send',branch_id=self.child.id,mode='people',sim_ids=[self.f.people[3].id],label='Dara moves')
        return h.logs(self.s,self.save,'parcel')[0]

    def test_queued_people_preserve_ids_ancestry_portraits_and_pending_rolls(self):
        sim=self.f.people[3];sim.data={**sim.data,'mother_id':self.f.people[0].id}
        roll=Record(save_id=self.save.id,kind='roll',label='Dara birthday',global_day=115,data={'sim_id':sim.id,'completed':False})
        self.s.add(roll);self.s.add(Portrait(save_id=self.save.id,record_id=sim.id,stage='default',image=b'Dara',mime_type='image/png'));self.s.commit()
        ids={r.id for r in t.records(self.s,self.save,{'sim'})};row=self.send_person()
        self.assertTrue(sim.deleted);self.assertTrue(sim.data['infinite_frozen']);self.assertEqual(sim.data['infinite_branch_id'],self.child.id)
        self.assertNotIn(sim.id,d.unpack_snapshot(self.child.data['snapshot'])['member_sim_ids'])
        self.switch(self.child)
        with self.assertRaisesRegex(ValueError,'unlocks'):self.preview('receive',row_id=row.id)
        self.save.global_day=110;self.s.commit();ticket=self.apply('receive',row_id=row.id)
        self.assertFalse(sim.deleted);self.assertEqual(sim.data['mother_id'],self.f.people[0].id)
        self.assertEqual({r.id for r in t.records(self.s,self.save,{'sim'})},ids)
        self.assertFalse(roll.deleted);self.assertFalse(roll.data['completed']);self.assertEqual(roll.global_day,115)
        self.assertEqual(row.data['status'],'received')
        with self.assertRaises(ValueError):self.preview('receive',row_id=row.id)
        with self.assertRaises(ValueError):t.confirm(self.s,self.save,ticket)
        self.assertEqual(self.s.scalar(select(Portrait.image).where(Portrait.record_id==sim.id)),b'Dara')

    def test_queued_inheritance_expense_and_income_once_no_game_funds_change(self):
        self.f.home.data={**self.f.home.data,'last_game_funds':10000};self.save.global_day=110;self.s.commit()
        self.apply('send',branch_id=self.child.id,mode='inheritance',label='Inheritance',amount=250,household_id=self.f.home.id)
        parcel=h.logs(self.s,self.save,'parcel')[0];self.assertEqual(self.f.home.data['last_game_funds'],10000)
        outgoing=[r for r in t.records(self.s,self.save,{'economy_entry'}) if r.data.get('dynasty_parcel_id')==parcel.id]
        self.assertEqual(len(outgoing),1);self.assertEqual(outgoing[0].data['entry_type'],'expense')
        self.switch(self.child);self.save.global_day=110;self.s.commit()
        self.apply('receive',row_id=parcel.id,household_id=self.f.home.id)
        incoming=[r for r in t.records(self.s,self.save,{'economy_entry'}) if r.data.get('dynasty_parcel_id')==parcel.id and r.data['entry_type']=='income']
        self.assertEqual(len(incoming),1);self.assertEqual(incoming[0].data['amount'],250)
        with self.assertRaises(ValueError):self.preview('receive',row_id=parcel.id,household_id=self.f.home.id)

    def test_transfer_invalid_target_all_living_and_wrong_receiving_branch(self):
        with self.assertRaises(ValueError):self.preview('send',branch_id=self.root.id,mode='people',sim_ids=[self.f.people[3].id],label='Move')
        with self.assertRaises(ValueError):self.preview('send',branch_id=self.child.id,mode='people',sim_ids=[r.id for r in self.f.people if not r.deleted],label='Move')
        row=self.send_person()
        with self.assertRaises(ValueError):self.preview('receive',row_id=row.id)

    def test_transfer_preview_stale_person_edit_rejected_without_departure(self):
        sim=self.f.people[3];ticket=self.preview('send',branch_id=self.child.id,mode='people',sim_ids=[sim.id],label='Move')
        sim.data={**sim.data,'birthplace':'England'};self.s.commit()
        with self.assertRaises(ValueError):t.confirm(self.s,self.save,ticket)
        self.assertFalse(sim.deleted);self.assertEqual(h.logs(self.s,self.save,'parcel'),[])

    def test_contradictions_parent_birth_and_marriage_after_death_no_writes(self):
        person=self.f.people[2];payload=d.unpack_snapshot(self.root.data['snapshot'])
        entry=next(r for r in payload['records'] if r['id']==person.id)
        entry['data'].update(birth_global_day=2,death_global_day=50)
        payload['records'].append({'id':'wedding','kind':'relationship','label':'Wedding','global_day':70,'data':{'type':'Marriage','partner1_id':person.id,'partner2_id':self.f.people[3].id}})
        # Explicit views compare preserved records independently of the current working copy.
        views=[(self.root,payload),(self.child,d.unpack_snapshot(self.child.data['snapshot']))]
        before=copy.deepcopy(person.data);issues=h.contradictions(self.s,self.save,views)
        self.assertTrue(any('birth global day' in r['message'] for r in issues))
        self.assertTrue(any('marriage on GD 70' in r['message'] for r in issues));self.assertEqual(person.data,before)

    def test_spoiler_history_sanitizes_without_mutating_save(self):
        sim=self.f.people[0];sim.data={**sim.data,'death_global_day':200,'death_cause':'Starvation','death_confirmed':True};self.s.commit()
        future=Record(save_id=self.save.id,kind='story_entry',label='Secret future',global_day=200,data={'body':'Spoiler'});self.s.add(future);self.s.commit()
        self.settings(spoiler_free=True)
        projected=h.history_records(self.save,[sim,future]);self.assertEqual(len(projected),1)
        self.assertNotIn('death_global_day',projected[0].data);self.assertEqual(sim.data['death_global_day'],200)
        story=storyline.build(self.s,self.save);self.assertNotIn(future.id,[r.id for r in story['authored_entries']])
        self.switch(self.child);self.assertTrue(h.spoiler_free(self.save))

    def test_journey_has_split_and_dated_transfer_not_duplicate_sim(self):
        row=self.send_person();journey=h.journey(self.s,self.save,self.f.people[3].id)
        self.assertTrue(any('in transit' in r['text'] for r in journey))
        self.assertTrue(any('Started dynasty' in r['text'] for r in journey))
        self.settings(spoiler_free=True);self.switch(self.child)
        self.assertFalse(any('in transit' in r['text'] for r in h.journey(self.s,self.save,self.f.people[3].id)))

    def test_checklist_reports_unplayed_due_rolls_births_and_missing_photos(self):
        pregnancy=Record(save_id=self.save.id,kind='pregnancy',label='Due',global_day=100,data={'mother_id':self.f.people[0].id,'due_global_day':103,'status':'Active'})
        self.s.add(pregnancy);self.s.commit();result=h.checklist(self.s,self.save,1325)
        root=next(r for r in result if r['branch'].id==self.root.id)
        self.assertEqual(root['births'],1);self.assertTrue(root['reached'])
        self.assertGreater(len(root['coverage'][0]['missing']),0)
        self.assertTrue(all(not r['reached'] for r in h.checklist(self.s,self.save,1330)))

    def test_pages_forms_preferences_and_foreign_save_guard(self):
        with patch.object(main,'SessionLocal',self.f.sessions):
            client=TestClient(main.app);client.post('/saves/select',data={'save_id':self.save.id})
            for section in ('world','checks','visits','deliveries','parked','relations','heirlooms','decade','journey'):
                response=client.get(f'/infinite/{self.save.id}/history?section={section}')
                self.assertEqual(response.status_code,200,(section,response.text[:200]));self.assertIn('Dynasty History',response.text)
            epoch=d.state(self.save)['epoch']
            response=client.post(f'/infinite/{self.save.id}/history/preferences',data={'spoiler_free':'yes','branch_drama':'yes'},headers={'X-Dynasty-Epoch':epoch},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            response=client.post(f'/infinite/{self.save.id}/tools/preview',data={'operation':'history_relation','branch_id':self.child.id,'relationship':'Rival','day':104},headers={'X-Dynasty-Epoch':epoch})
            self.assertEqual(response.status_code,200);self.assertIn('source-rule odds',response.text)
            token=re.search(r'/tools/confirm/([a-z0-9]+)',response.text).group(1)
            response=client.post(f'/infinite/{self.save.id}/tools/confirm/{token}',headers={'X-Dynasty-Epoch':epoch},follow_redirects=False)
            self.assertEqual(response.status_code,303);self.assertEqual(response.headers['location'],f'/infinite/{self.save.id}/history')
            self.assertEqual(client.get('/infinite/not-owned/history').status_code,404)

    def test_wrong_save_records_and_stale_epoch_rejected(self):
        with self.assertRaises(ValueError):self.preview('relation',branch_id='foreign',day=104,relationship='Rival')
        ticket=self.preview('relation',branch_id=self.child.id,day=104,relationship='Rival')
        self.switch(self.child)
        with self.assertRaisesRegex(ValueError,'active branch changed'):t.confirm(self.s,self.save,ticket)

    def test_populated_pages_and_spoiler_heirloom_owner(self):
        self.apply('world',event_id=self.event().id,day=104,outcome='A northern victory')
        self.apply('visit',branch_id=self.child.id,sim_id=self.f.people[2].id,day=104,label='Cousins return')
        self.apply('relation',branch_id=self.child.id,day=104,relationship='Rival',notes='Compete for the same title')
        self.apply('heirloom',branch_id=self.root.id,sim_id=self.f.people[0].id,day=104,label='Silver chalice')
        item=h.logs(self.s,self.save,'heirloom_history')[0]
        self.save.global_day=110;self.s.commit()
        self.apply('heirloom',row_id=item.id,branch_id=self.child.id,sim_id=self.f.people[2].id,day=110,notes='FUTURE OWNER SECRET')
        self.apply('send',branch_id=self.child.id,mode='inheritance',amount=100,household_id=self.f.home.id,label='A bequest')
        self.settings(branch_drama=True)
        with patch.object(main,'SessionLocal',self.f.sessions):
            client=TestClient(main.app);client.post('/saves/select',data={'save_id':self.save.id})
            for section in ('world','checks','visits','deliveries','parked','relations','heirlooms','decade','journey'):
                response=client.get(f'/infinite/{self.save.id}/history?section={section}')
                self.assertEqual(response.status_code,200,(section,response.text[:300]))
            for page in ('timeline','storyline','drama','infinite-decades'):
                self.assertEqual(client.get('/p/'+page).status_code,200,page)
            self.settings(spoiler_free=True);self.switch(self.child)
            response=client.get(f'/infinite/{self.save.id}/history?section=heirlooms')
            self.assertNotIn('FUTURE OWNER SECRET',response.text)

    def test_unrelated_telemetry_does_not_block_departure(self):
        sim=self.f.people[3];ticket=self.preview('send',branch_id=self.child.id,mode='people',sim_ids=[sim.id],label='Move')
        sim.data={**sim.data,'game_age_progress_percentage':42};self.s.commit()
        t.confirm(self.s,self.save,ticket);self.s.commit()
        payload=d.unpack_snapshot(h.logs(self.s,self.save,'parcel')[0].data['payload'])
        self.assertEqual(next(r for r in payload['records'] if r['id']==sim.id)['data']['game_age_progress_percentage'],42)

    def test_shared_record_changes_block_receipt_without_overwrite(self):
        relation=Record(save_id=self.save.id,kind='relationship',label='Siblings',global_day=100,data={'type':'Sibling','partner1_id':self.f.people[3].id,'partner2_id':self.f.people[2].id})
        self.s.add(relation);self.s.commit()
        # Include the original shared relationship in the receiving checkpoint.
        payload=d.unpack_snapshot(self.child.data['snapshot']);payload['records'].append({'id':relation.id,'kind':relation.kind,'label':relation.label,'global_day':100,'data':copy.deepcopy(relation.data),'deleted':False})
        with d.branch_operation(self.s,self.save):d._update_branch(self.s,self.child,payload)
        self.s.commit();parcel=self.send_person();self.switch(self.child)
        relation.data={**relation.data,'notes':'Changed after departure'};self.save.global_day=110;self.s.commit()
        with self.assertRaisesRegex(ValueError,'Shared records changed'):self.preview('receive',row_id=parcel.id)
        self.assertEqual(parcel.data['status'],'pending');self.assertTrue(self.f.people[3].deleted)

    def test_future_terminal_result_not_exposed_as_past_history(self):
        self.settings(spoiler_free=True)
        entry={'id':'old','kind':'pregnancy','global_day':80,'label':'Delivered later','data':{'delivery_global_day':200,'status':'Delivered'}}
        self.assertEqual(h.visible_history(self.save,[entry]),[])

    def test_decade_default_follows_challenge_start_not_civil_decade(self):
        with d.branch_operation(self.s,self.save):self.save.start_year=979
        self.s.commit()
        with patch.object(main,'SessionLocal',self.f.sessions):
            client=TestClient(main.app);client.post('/saves/select',data={'save_id':self.save.id})
            response=client.get(f'/infinite/{self.save.id}/history?section=decade')
            self.assertEqual(response.status_code,200);self.assertIn('value="999"',response.text)

    def test_ownership_and_visits_not_before_branch_foundation(self):
        with self.assertRaisesRegex(ValueError,'founded'):self.preview('visit',branch_id=self.child.id,sim_id=self.f.people[2].id,day=103,label='Visit')
        with self.assertRaisesRegex(ValueError,'founded'):self.preview('heirloom',branch_id=self.child.id,sim_id=self.f.people[2].id,day=103,label='Ring')


if __name__=='__main__':unittest.main()
