"""UI contracts against disposable data, never the installed save."""
import unittest
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import main,usability as ui,usability_ui as web
from app.models import Record,ClockLink,UiPreference,User
from tests import test_infinite_decades as fixtures

class UsabilityTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.InfiniteDecadesTests();self.f.setUp();self.f.save.settings={**self.f.save.settings,'automation_enabled':False};self.f.session.commit()
        self.patch=patch.object(main,'SessionLocal',self.f.sessions);self.patch.start();self.client=TestClient(main.app)
        self.client.post('/saves/select',data={'save_id':self.f.save.id},follow_redirects=False)
    def tearDown(self):self.client.close();self.patch.stop();self.f.tearDown()
    def add(self,kind,label,day=100,**data):
        row=Record(save_id=self.f.save.id,kind=kind,label=label,global_day=day,data=data);self.f.session.add(row);self.f.session.commit();return row
    def headers(self,row):return {'X-Decades-Fragment':'1','X-UI-Save':self.f.save.id,'X-Record-Version':str(row.version)}
    def test_four_groups_and_disabled_addons(self):
        self.assertEqual([g['label'] for g in ui.visible_navigation(self.f.save)],['Play','People','History','Settings'])
        self.assertNotIn('harry-potter',[p for g in ui.visible_navigation(self.f.save) for p in g['pages']])
        self.assertCountEqual([p for g in ui.NAVIGATION_GROUPS for p in g['pages']],main.FEATURES)
        self.f.save.settings={**self.f.save.settings,'selected_rule_packs':['harry_potter_decades']}
        self.assertIn('harry-potter',[p for g in ui.visible_navigation(self.f.save) for p in g['pages']])
    def test_today_separates_windows_and_keeps_completed_deceased(self):
        person=self.f.people[0]
        self.add('roll','Due today',sim_id=person.id,die='d6',bad_results='1')
        self.add('roll','Older debt',99,sim_id=person.id,die='d6')
        self.add('roll','Tomorrow roll',101,sim_id=person.id,die='d6')
        dead=self.f.people[1];dead.data={**dead.data,'death_global_day':80,'death_confirmed':True};self.f.session.commit()
        self.add('roll','Must not show',sim_id=dead.id,die='d6')
        self.add('roll','Past result still shown',80,sim_id=dead.id,die='d6',completed=True,completed_global_day=100,actual=1,outcome='Failed')
        html=self.client.get('/p/today').text
        for value in ['Needs your decision','Happening today','Completed','Due today','Past result still shown','All Today tools']:self.assertIn(value,html)
        for value in ['Older debt','Tomorrow roll','Must not show']:self.assertNotIn(value,html)
        self.assertIn('Older debt',self.client.get('/p/today?window=overdue').text)
        self.assertIn('Tomorrow roll',self.client.get('/p/today?window=future').text)
    def test_batches_do_not_load_all_results(self):
        for n in range(55):self.add('roll','Batch '+str(n),sim_id=self.f.people[0].id,die='d6')
        first=web.board(self.f.session,self.f.save,{},'decisions')['board_groups'][0]
        second=web.board(self.f.session,self.f.save,{'decisions_page':'2'},'decisions')['board_groups'][0]
        self.assertEqual((first['count'],len(first['rows']),first['pages']),(55,24,3))
        self.assertFalse({r.id for r in first['rows']} & {r.id for r in second['rows']})
    def test_post_death_ghost_roll_is_preserved(self):
        person=self.f.people[0];person.data={**person.data,'death_confirmed':True};self.f.session.commit()
        self.add('roll','Ghost decision',sim_id=person.id,die='d6',occult_roll=True,occult_rule_key='ghost_persistence')
        self.assertIn('Ghost decision',self.client.get('/p/today').text)
    def test_current_birth_and_active_illness_appear(self):
        self.add('illness','Active fever',97,sim_id=self.f.people[0].id,status='Active',onset_global_day=97)
        self.add('sim','Newborn today',birth_global_day=100)
        html=self.client.get('/api/ui/today/happening').text
        self.assertIn('Active fever',html);self.assertIn('Newborn today',html)
    def test_roll_partial_and_stale_version(self):
        row=self.add('roll','A harmless roll',sim_id=self.f.people[0].id,die='d6',bad_results='',completed=False,nonlethal=True)
        r=self.client.post('/api/rolls/'+row.id+'/complete',data={'actual':4},headers=self.headers(row))
        self.assertEqual(r.status_code,200,r.text)
        token=r.json()['preview']['token']
        r=self.client.post('/api/previews/'+token+'/confirm',headers=self.headers(row))
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['kind'],'roll')
        self.assertEqual(self.client.post('/api/rolls/'+row.id+'/complete',data={'actual':2},headers=self.headers(row)).status_code,409)
        self.assertIn('4',self.client.get('/api/ui/rolls/'+row.id).text)
        other=self.add('roll','Changed',die='d6')
        headers=self.headers(other);headers['X-Record-Version']='999'
        self.assertEqual(self.client.post('/api/rolls/'+other.id+'/roll',headers=headers).status_code,409)
    def test_wrong_save_rejected(self):
        row=self.add('roll','Wrong tab',die='d6');headers=self.headers(row);headers['X-UI-Save']='wrong'
        self.assertEqual(self.client.post('/api/rolls/'+row.id+'/roll',headers=headers).status_code,409)
        self.assertEqual(self.client.get('/api/ui/today/decisions',headers={'X-UI-Save':'wrong'}).status_code,409)
    def test_dismissal_partial_is_persistent(self):
        row=self.add('game_candidate','Possible fever',action='unknown_illness',status='pending',sim_id=self.f.people[0].id,source_key='unknown:test',payload={'illness_name':'Fever'})
        html=self.client.get('/p/automation').text
        self.assertIn('Current value',html);self.assertIn('Proposed value',html);self.assertIn('Uncertain suggestion',html)
        r=self.client.post('/automation/'+row.id+'/dismiss',headers=self.headers(row));self.assertEqual(r.status_code,200,r.text)
        self.f.session.refresh(row);self.assertEqual(row.data['status'],'dismissed')
        self.assertEqual(row.data['dismissed_global_day'],100)
        self.assertNotIn('Possible fever',self.client.get('/p/automation').text)
    def test_evidence_filter_does_not_call_missing_confidence_confirmed(self):
        self.add('game_candidate','Certain pregnancy',action='pregnancy_started',status='pending',payload={'is_pregnant':True})
        self.add('game_candidate','Uncertain name',action='sim_update',status='pending',payload={'first_name':'Maybe'})
        html=self.client.get('/p/automation?review_quality=confirmed').text
        self.assertIn('Certain pregnancy',html);self.assertNotIn('<h3>Uncertain name</h3>',html)
        html=self.client.get('/p/automation?review_quality=suggestion').text
        self.assertIn('Uncertain name',html);self.assertNotIn('<h3>Certain pregnancy</h3>',html)
    def test_preferences_persist_and_are_personal(self):
        r=self.client.post('/api/ui/preferences',json={'save_id':self.f.save.id,'page':'sims','filters':{'record_status':'dead','sort':'birth-newest','password':'not stored'},'density':'compact','thumbnail':'small'})
        self.assertEqual(r.status_code,200,r.text)
        row=self.f.session.get(UiPreference,self.f.user.id)
        self.assertNotIn('password',row.values['pages'][self.f.save.id+':sims'])
        html=self.client.get('/p/sims').text;self.assertIn('value="dead" selected',html);self.assertIn('data-density="compact"',html)
        self.assertIn('value="living" selected',self.client.get('/p/sims?record_status=living').text)
        other=User(email='other@example.test');self.f.session.add(other);self.f.session.commit();self.assertEqual(ui.preferences(self.f.session,other),{})
        self.assertEqual(self.client.post('/api/ui/preferences',json={'save_id':'foreign','page':'sims','density':'compact'}).status_code,404)
    def test_living_default_and_history_available(self):
        dead=self.f.people[0];dead.data={**dead.data,'death_global_day':80,'death_confirmed':True};self.f.session.commit()
        html=self.client.get('/p/sims').text
        self.assertNotIn('href="/sims/'+dead.id+'"',html)
        self.assertIn('href="/sims/'+dead.id+'"',self.client.get('/p/sims?record_status=dead').text)
    def test_date_certainty_is_honest(self):
        self.assertEqual(ui.date_certainty({'birth_time':'12:30','birth_time_randomized':True,'birth_date_precision':'exact'})['key'],'random')
        self.assertEqual(ui.date_certainty({'birth_time':'12:30','birth_time_source':'Manually recorded birth time'})['key'],'manual')
        self.assertEqual(ui.date_certainty({'birth_year_only':True})['key'],'estimated')
        self.assertEqual(ui.date_certainty({'birth_time':'12:30','birth_time_source':'Clock Sync exact game observation'})['key'],'exact')
        self.assertEqual(ui.date_certainty({'birth_time':'12:30'})['key'],'unknown')
    def test_profile_identity_dates_and_tab_source_sections(self):
        person=self.f.people[0];person.data={**person.data,'birth_time':'08:42','birth_time_randomized':True};self.f.session.commit()
        html=self.client.get('/sims/'+person.id).text
        for value in ['u-essentials','Parents','Spouse / partner','data-certainty="random"','id="education"','id="occult"','id="game-record"','id="health"']:self.assertIn(value,html)
    def test_clock_waiting_is_not_claimed_paused(self):
        now=datetime.now(timezone.utc);link=ClockLink(save_id=self.f.save.id,enabled=True,token_hash='test',last_game_day=29,last_game_hour=7,last_game_minute=50,last_seen_at=now-timedelta(hours=3))
        state=ui.clock_status(self.f.save,link,now=now);self.assertEqual(state['state'],'waiting');self.assertIn('silence alone',state['detail'])
        prefs={'paused_clocks':{self.f.save.id:[29,7,50]}};self.assertEqual(ui.clock_status(self.f.save,link,prefs,now)['state'],'paused')
        link.last_game_minute=51;self.assertEqual(ui.clock_status(self.f.save,link,prefs,now)['state'],'waiting')
    def test_paused_marker_and_status_endpoint(self):
        link=ClockLink(save_id=self.f.save.id,enabled=True,token_hash='test',last_game_day=29,last_game_hour=7,last_game_minute=50,last_seen_at=datetime.now(timezone.utc));self.f.session.add(link);self.f.session.commit()
        self.assertEqual(self.client.post('/api/ui/preferences',json={'save_id':self.f.save.id,'paused':True}).status_code,200)
        data=self.client.get('/api/live-status').json()['ui_clock'];self.assertEqual(data['state'],'paused');self.assertEqual(data['save_name'],self.f.save.name);self.assertEqual(data['global_day'],100)
    def test_shared_card_and_followups(self):
        parent=self.add('roll','Parent roll',die='d6',bad_results='1',modifiers='Adjusted for recorded condition')
        self.add('roll','Child roll',102,origin_roll_id=parent.id,die='d10')
        html=self.client.get('/api/ui/rolls/'+parent.id).text
        for value in ['Due','Bad results','Modifiers','Outcome','Scheduled follow-ups','Why this roll']:self.assertIn(value,html)
        result=self.client.get('/api/ui/rolls/'+parent.id+'/followups').json();self.assertEqual(result['items'][0]['label'],'Child roll')
    def test_detailed_tools_are_still_accessible(self):
        r=self.client.get('/p/today?view=tools');self.assertEqual(r.status_code,200,r.text[:300]);self.assertIn('age',r.text.lower())
        self.assertEqual(self.client.get('/p/relationships').status_code,200)

if __name__=='__main__':unittest.main()
