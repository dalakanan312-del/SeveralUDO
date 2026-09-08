import copy
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import birth_dates, domain, main, insights
from app.models import Change, Record
from tests import test_infinite_decades as fixtures

class BirthDateTests(unittest.TestCase):
    def setUp(self):
        self.save=SimpleNamespace(start_year=1550,days_per_year=4)

    def test_stable_random_time_and_date_in_recorded_quarter(self):
        data={'birth_global_day':5,'notes':'Preserve'};before=copy.deepcopy(data)
        fields=birth_dates.fields_for(self.save,data,'stable-id')
        self.assertEqual(fields,birth_dates.fields_for(self.save,data,'stable-id'))
        self.assertEqual(data,before);self.assertTrue(fields['birth_time_randomized'])
        self.assertEqual(fields['birth_date_precision'],birth_dates.MARKER)
        self.assertIn('1551',fields['historical_birth_date'])
        self.assertEqual(fields['historical_birth_date'],birth_dates.historical_date(self.save,5,fields['birth_game_hour'],fields['birth_game_minute']))
        self.assertEqual(birth_dates.fields_for(self.save,{**data,**fields},'different-id'),{})

    def test_many_values_stay_inside_day_and_are_not_all_identical(self):
        values=set()
        for n in range(1000):
            result=birth_dates.fields_for(self.save,{'birth_global_day':4},str(n))
            self.assertGreaterEqual(result['birth_game_hour'],0);self.assertLess(result['birth_game_hour'],24)
            self.assertGreaterEqual(result['birth_game_minute'],0);self.assertLess(result['birth_game_minute'],60)
            self.assertEqual(result['birth_randomized_global_day'],4);values.add(result['birth_time'])
        self.assertGreater(len(values),500)

    def test_known_times_dates_and_uncertain_days_are_untouched(self):
        for data in [
            {'birth_time':'00:00'}, {'birth_time':'07:12:03'}, {'birth_game_hour':0,'birth_game_minute':0},
            {'historical_birth_date':'January 5, 1550'}, {'birth_year_only':True},
            {'infinite_frozen':True}, {'estimated_birth_global_day_range_start':1,'estimated_birth_global_day_range_end':8}]:
            self.assertEqual(birth_dates.fields_for(self.save,{'birth_global_day':1,**data},'a'),{})
        self.assertEqual(birth_dates.fields_for(self.save,{},'a'),{})

    def test_partial_known_time_is_preserved(self):
        self.assertEqual(birth_dates.fields_for(self.save,{'birth_global_day':1,'birth_game_hour':6},'a')['birth_game_hour'],6)
        self.assertEqual(birth_dates.fields_for(self.save,{'birth_global_day':1,'birth_game_minute':42},'a')['birth_game_minute'],42)

    def test_leap_year_custom_year_lengths_and_negative_global_days(self):
        self.save.start_year=1600
        self.assertEqual(birth_dates.historical_date(self.save,1,23,59),'March 31, 1600')
        self.assertEqual(birth_dates.historical_date(self.save,0,23,59),'December 31, 1599')
        for dpy in (1,12,28,365):
            self.save.days_per_year=dpy
            self.assertEqual(birth_dates.historical_date(self.save,1,0,0),'January 1, 1600')
            self.assertEqual(birth_dates.historical_date(self.save,dpy,23,59),'December 31, 1600')
        self.save.start_year=0
        self.assertEqual(birth_dates.historical_date(self.save,1,0,0),'')

    def test_edit_keeps_estimate_label_until_time_is_changed(self):
        old={'birth_global_day':1,**birth_dates.fields_for(self.save,{'birth_global_day':1},'a')}
        same={**old,**main.birth_calendar_fields(self.save,1,old['birth_game_hour'],old['birth_game_minute'])}
        birth_dates.retain_edit_provenance(old,same,self.save)
        self.assertEqual(same['birth_date_precision'],birth_dates.MARKER)
        changed={**old,**main.birth_calendar_fields(self.save,1,(old['birth_game_hour']+1)%24,old['birth_game_minute'])}
        birth_dates.retain_edit_provenance(old,changed,self.save)
        self.assertNotIn('birth_time_randomized',changed);self.assertEqual(changed['birth_date_precision'],'exact')

class BirthDatePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.InfiniteDecadesTests();self.f.setUp()
    def tearDown(self):
        self.f.tearDown()

    def test_backfill_is_journalled_once_and_retains_original_global_days(self):
        f=self.f;before=[s.data['birth_global_day'] for s in f.people]
        self.assertEqual(birth_dates.fill_missing(f.session,f.save),5);f.session.commit()
        data=[copy.deepcopy(s.data) for s in f.people];revision=f.save.revision
        self.assertEqual(birth_dates.fill_missing(f.session,f.save),0);f.session.commit()
        self.assertEqual(data,[s.data for s in f.people]);self.assertEqual(revision,f.save.revision)
        self.assertEqual(before,[s.data['birth_global_day'] for s in f.people])
        changes=list(f.session.scalars(select(Change).where(Change.kind=='sim')))
        self.assertEqual(len(changes),5);self.assertTrue(all(c.payload['data']['birth_time_randomized'] for c in changes))

    def test_new_records_are_filled_before_sync_serialization(self):
        f=self.f;sim=Record(save_id=f.save.id,kind='sim',label='New baby',global_day=100,data={'birth_global_day':100})
        f.session.add(sim);f.session.flush();domain.journal(f.session,sim,'upsert',0);f.session.commit()
        self.assertTrue(sim.data['birth_time_randomized'])
        change=f.session.scalar(select(Change).where(Change.record_id==sim.id));self.assertEqual(change.payload['data']['birth_time'],sim.data['birth_time'])

    def test_automation_off_and_frozen_records_are_untouched(self):
        f=self.f;f.save.settings={**f.save.settings,'automation_enabled':False};f.session.commit()
        self.assertEqual(birth_dates.fill_missing(f.session,f.save),0)
        f.save.settings={**f.save.settings,'automation_enabled':True}
        f.people[0].data={**f.people[0].data,'infinite_frozen':True,'infinite_frozen_global_day':100};f.people[0].deleted=True
        self.assertFalse(birth_dates.apply_to_record(f.people[0],f.save))

    def test_later_recorded_time_replaces_the_randomized_provenance(self):
        f=self.f;sim=f.people[0]
        birth_dates.apply_to_record(sim,f.save)
        known='00:00' if sim.data['birth_time']!='00:00' else '23:59'
        sim.data={**sim.data,'birth_time':known}
        self.assertTrue(birth_dates.apply_to_record(sim,f.save))
        self.assertNotIn('birth_time_randomized',sim.data)
        self.assertEqual(sim.data['birth_date_precision'],'exact')
        hour,minute=map(int,known.split(':'))
        self.assertEqual(sim.data['historical_birth_date'],birth_dates.historical_date(f.save,1,hour,minute))

    def test_editing_profile_without_changing_time_keeps_the_estimate(self):
        f=self.f;birth_dates.fill_missing(f.session,f.save);f.session.commit()
        sim=f.people[0];original=copy.deepcopy(sim.data)
        with patch.object(main,'SessionLocal',f.sessions):
            client=TestClient(main.app);client.post('/saves/select',data={'save_id':f.save.id})
            response=client.post('/sims/'+sim.id,data={'first_name':'Ada','birth_global_day':1,
                'birth_game_hour':original['birth_game_hour'],'birth_game_minute':original['birth_game_minute']})
            self.assertEqual(response.status_code,200)
        f.session.expire_all()
        self.assertEqual(sim.data['birth_time'],original['birth_time'])
        self.assertTrue(sim.data['birth_time_randomized'])
        self.assertEqual(sim.data['birth_date_precision'],birth_dates.MARKER)

    def test_profile_and_timeline_show_generated_provenance(self):
        f=self.f
        with patch.object(main,'SessionLocal',f.sessions):
            client=TestClient(main.app);client.post('/saves/select',data={'save_id':f.save.id})
            page=client.get('/sims/'+f.people[0].id)
            self.assertEqual(page.status_code,200);self.assertIn('(randomized)',page.text)
            self.assertIn('Birth time was randomized',page.text)
        f.session.expire_all()
        timeline=insights.timeline(f.people,f.save)
        self.assertTrue(any('(randomized)' in (row['historical_date'] or '') for row in timeline))

if __name__=='__main__':unittest.main()
