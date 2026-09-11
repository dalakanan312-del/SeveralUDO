"""Pregnancy allowance controls on simplified Today, using disposable saves."""
import re
import unittest
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch

from sqlalchemy import select
from app import main, usability_ui as web
from app.models import Record
from tests import test_usability as fixtures


class TodayPregnancyRollTests(unittest.TestCase):
    setUp = fixtures.UsabilityTests.setUp
    tearDown = fixtures.UsabilityTests.tearDown
    add = fixtures.UsabilityTests.add
    headers = fixtures.UsabilityTests.headers

    def rule(self):
        return self.add('planner_rule','Pregnancy allowance',rule_key='side_pregnancy',
                        start_year=1300,end_year=1399,die='d12',
                        bad_results='1-10: Schedule that many pregnancies; 11-12: No pregnancy',active=True)

    def prepare(self,person=None,**kwargs):
        return self.client.post('/api/today/pregnancy-count-rolls',data={
            'sim_id':(person or self.f.people[0]).id,'view':'workboard',
            'save_id':self.f.save.id,'household':self.f.home.id,**kwargs},follow_redirects=False)

    def selected_roll(self,response):
        self.assertEqual(response.status_code,303,response.text)
        address=response.headers['location']
        self.assertNotIn('view=tools',address)
        return self.f.session.get(Record,parse_qs(urlsplit(address).query)['pregnancy_roll'][0])

    def test_form_is_directly_available_on_simple_today(self):
        html=self.client.get('/p/today').text
        self.assertIn('＋ Roll for pregnancies',html)
        self.assertIn('action="/api/today/pregnancy-count-rolls"',html)
        self.assertIn('name="view" value="workboard"',html)
        self.assertIn('data-workboard',html)

    def test_picker_limits_to_born_living_household_members(self):
        self.f.people[1].data={**self.f.people[1].data,'death_confirmed':True}
        self.f.people[2].data={**self.f.people[2].data,'birth_global_day':101}
        self.f.people[3].deleted=True
        self.f.session.commit()
        ids={row.id for row in web.pregnancy_people(self.f.session,self.f.save,self.f.home.id)}
        self.assertEqual(ids,{self.f.people[0].id})
        self.assertIn(self.f.people[4].id,{row.id for row in web.pregnancy_people(self.f.session,self.f.save,'all')})

    def test_new_roll_keeps_household_and_appears_before_large_backlog(self):
        self.rule()
        for number in range(30):
            self.add('roll',f'Other work {number}',sim_id=self.f.people[0].id,die='d6')
        response=self.prepare()
        roll=self.selected_roll(response)
        self.assertEqual(roll.data['die'],'d12')
        self.assertFalse(roll.data['completed'])
        self.assertIn('household='+self.f.home.id,response.headers['location'])
        page=self.client.get(response.headers['location'])
        self.assertEqual(page.status_code,200,page.text[:500])
        self.assertIn('data-workboard',page.text)
        self.assertIn(f'/api/rolls/{roll.id}/roll',page.text)
        cards=re.findall(r'data-roll-id="([^"]+)"',page.text)
        self.assertEqual(cards[0],roll.id)
        self.assertEqual(len(cards),24)
        params={k:v[0] for k,v in parse_qs(urlsplit(response.headers['location']).query).items()}
        first=web.board(self.f.session,self.f.save,params,'decisions')['board_groups'][0]
        second=web.board(self.f.session,self.f.save,{**params,'decisions_page':'2'},'decisions')['board_groups'][0]
        self.assertFalse({r.id for r in first['rows']} & {r.id for r in second['rows']})

    def test_existing_pending_roll_is_found_in_overdue_without_duplication(self):
        self.rule()
        roll=self.selected_roll(self.prepare())
        roll.global_day=99;roll.data={**roll.data,'due_global_day':99};self.f.session.commit()
        result=self.prepare()
        self.assertEqual(self.selected_roll(result).id,roll.id)
        self.assertIn('window=overdue',result.headers['location'])
        self.assertIn('#work-decisions',result.headers['location'])
        self.assertIn(f'/api/rolls/{roll.id}/roll',self.client.get(result.headers['location']).text)
        rows=list(self.f.session.scalars(select(Record).where(Record.data['pregnancy_count_roll'].as_boolean().is_(True))))
        self.assertEqual(len(rows),1)

    def test_existing_completed_allowance_is_not_reopened(self):
        self.rule()
        roll=self.selected_roll(self.prepare())
        roll.data={**roll.data,'completed':True,'actual':7,'pregnancy_count':7,'outcome':'7 pregnancies','completed_global_day':99}
        self.f.session.commit()
        response=self.prepare()
        self.assertEqual(self.selected_roll(response).id,roll.id)
        self.assertIn('window=overdue',response.headers['location'])
        self.assertIn('#work-completed',response.headers['location'])
        html=self.client.get(response.headers['location']).text
        self.assertIn('7 pregnancies',html)
        self.assertNotIn(f'/api/rolls/{roll.id}/roll',html)
        self.f.session.refresh(roll)
        self.assertTrue(roll.data['completed']);self.assertEqual(roll.data['actual'],7)

    def test_native_roll_preview_confirms_exact_pregnancy_allowance(self):
        self.rule()
        person=self.f.people[0]
        roll=self.selected_roll(self.prepare())
        self.f.save.settings={**self.f.save.settings,'automation_enabled':True}
        self.f.session.commit()
        with patch('app.dice.deterministic_faces',return_value=[7]):
            response=self.client.post(f'/api/rolls/{roll.id}/roll',headers=self.headers(roll))
        self.assertEqual(response.status_code,200,response.text)
        preview=response.json()['preview']
        confirmed=self.client.post('/api/previews/'+preview['token']+'/confirm',headers=self.headers(roll))
        self.assertEqual(confirmed.status_code,200,confirmed.text)
        self.f.session.refresh(roll);self.f.session.refresh(person)
        self.assertTrue(roll.data['completed'])
        self.assertEqual(roll.data['pregnancy_count'],7)
        self.assertEqual(person.data['pregnancy_allowance_count'],7)
        self.assertFalse(person.data.get('death_global_day'))
        self.assertIn('7 pregnancies',self.client.get('/p/today').text)

    def test_missing_era_rule_returns_help_on_today(self):
        result=self.prepare()
        self.assertNotIn('pregnancy_roll=',result.headers['location'])
        html=self.client.get(result.headers['location']).text
        self.assertIn('No active pregnancy-count rule covers 1324',html)
        self.assertIn('Pregnancy roll rules',html)

    def test_stale_save_and_dead_sim_cannot_create_roll(self):
        self.rule()
        self.assertEqual(self.prepare(save_id='different-save').status_code,409)
        person=self.f.people[0];person.data={**person.data,'death_confirmed':True};self.f.session.commit()
        response=self.prepare()
        self.assertNotIn('pregnancy_roll=',response.headers['location'])
        self.assertIn('only available for living Sims',self.client.get(response.headers['location']).text)

    def test_detailed_today_form_keeps_legacy_destination(self):
        self.rule()
        response=self.client.post('/api/today/pregnancy-count-rolls',data={'sim_id':self.f.people[0].id},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertEqual(response.headers['location'],'/p/today?task=rolls&roll_kind=pregnancy-count')


if __name__ == '__main__':
    unittest.main()
