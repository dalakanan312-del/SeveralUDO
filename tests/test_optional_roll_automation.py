"""Optional automation never touches installed saves; disabled is the default."""
import unittest
from sqlalchemy import select
from app import domain, roll_automation as auto, roll_automation_ui as web, avatar_rules, game_of_thrones_rules, harry_potter_rules, main, action_previews
from app.models import Record
from tests import test_play_clarity as fixtures

class OptionalRollAutomationTests(unittest.TestCase):
    setUp=fixtures.PlayClarityTests.setUp
    tearDown=fixtures.PlayClarityTests.tearDown
    add=fixtures.PlayClarityTests.add
    def enable(self,*keys,**config):
        self.f.save.settings={**self.f.save.settings,'automation_enabled':True,'roll_automation':{
            **auto.config(self.f.save),**{key:True for key in keys},'from':{key:100 for key in keys},**config}}
        self.f.session.commit()
    def pack(self,module):
        selected=list(self.f.save.settings.get('selected_rule_packs',[]))+[module.PACK_ID]
        self.f.save.settings={**self.f.save.settings,'selected_rule_packs':selected};module.sync_pack(self.f.session,self.f.save,selected)
        for rule in auto.rows(self.f.session,self.f.save,['addon_rule']):rule.data={**rule.data,'active':True,'module_enabled':True}
        self.f.session.commit()
    def generated(self,key=None):
        return [r for r in auto.rows(self.f.session,self.f.save,['roll']) if r.data.get('extra_automation') and (key is None or r.data['extra_automation']==key)]
    def completed(self,key,actual=1,**data):
        return self.add('roll','Source '+key,sim_id=self.f.people[0].id,occult_rule_key=key,die='d6',trigger_results='1',actual=actual,completed=True,completed_global_day=100,**data)
    def test_off_and_master_pause_create_nothing(self):
        self.pack(game_of_thrones_rules);auto.schedule(self.f.session,self.f.save);self.assertEqual(self.generated(),[])
        self.enable('got');self.f.save.settings={**self.f.save.settings,'automation_enabled':False}
        self.f.home.data={**self.f.home.data,'got_house_name':'Cooley'};auto.schedule(self.f.session,self.f.save)
        self.assertEqual(self.generated(),[])
    def test_pregnancy_eligibility_dedupe_year_and_paused_sim(self):
        self.enable('pregnancy',pregnancy_side_only=False)
        self.add('planner_rule','Side Household Pregnancy',die='d20',bad_results='1-14: Schedule that many pregnancies; 15-20: No pregnancy',active=True)
        a,b=self.f.people[:2];a.data={**a.data,'sex':'Female'};b.data={**b.data,'sex':'Female','infertile':True}
        self.add('relationship','Marriage',partner1_id=a.id,partner2_id=b.id,type='Marriage',status='Active')
        auto.schedule(self.f.session,self.f.save);auto.schedule(self.f.session,self.f.save)
        self.assertEqual(len(self.generated('pregnancy')),1)
        self.assertEqual(self.generated()[0].data['sim_id'],a.id)
        self.f.save.global_day+=4;auto.schedule(self.f.session,self.f.save);self.assertEqual(len(self.generated()),1)
        a.data={**a.data,'infinite_frozen':True};self.f.save.global_day+=4;auto.schedule(self.f.session,self.f.save);self.assertEqual(len(self.generated()),1)
    def test_side_pregnancy_does_not_guess_missing_main_house(self):
        self.enable('pregnancy',pregnancy_married_only=False)
        self.add('planner_rule','Side Household Pregnancy',die='d20',bad_results='1: No pregnancy',active=True)
        self.f.people[0].data={**self.f.people[0].data,'sex':'Female'}
        auto.schedule(self.f.session,self.f.save);self.assertEqual(self.generated(),[])
    def test_hp_consequences_household_scoped_and_once(self):
        self.enable('hp');self.pack(harry_potter_rules)
        origin=self.completed('spellcaster_witch_trial',occult_household_id=self.f.home.id)
        auto.followups(self.f.session,self.f.save,origin);auto.followups(self.f.session,self.f.save,origin)
        children=self.generated();self.assertEqual(len(children),1);self.assertEqual(children[0].data['extra_rule_code'],'HP-T02')
        self.assertEqual(children[0].data['roll_scope'],'household');self.assertNotIn('sim_id',children[0].data)
        self.f.save.start_year=1700
        ministry=self.completed('',hp_rule_code='HP-19',hp_household_id=self.f.home.id)
        auto.followups(self.f.session,self.f.save,ministry)
        self.assertEqual({r.data['extra_rule_code'] for r in self.generated()},{'HP-T02','HP-T03'})
    def test_hp_nontrigger_disabled_module_and_old_results_are_ignored(self):
        self.enable('hp');self.pack(harry_potter_rules)
        no=self.completed('spellcaster_witch_trial',2);auto.followups(self.f.session,self.f.save,no);self.assertEqual(self.generated(),[])
        old=self.completed('spellcaster_witch_trial');old.data={**old.data,'completed_global_day':99};auto.followups(self.f.session,self.f.save,old);self.assertEqual(self.generated(),[])
        rule=auto.rules_for(self.f.session,self.f.save)['HP-T02'];rule.data={**rule.data,'active':False}
        yes=self.completed('spellcaster_witch_trial');auto.followups(self.f.session,self.f.save,yes);self.assertEqual(self.generated(),[])
    def test_dehydration_two_checks_use_core_table(self):
        self.enable('dehydration')
        rule=self.add('roll_rule','Young Adult',age_days=72,die='d20',bad_results='3,7',active=True)
        self.add('roll_rule','Adult',age_days=160,die='d10',bad_results='1',active=True)
        origin=self.completed('mermaid_dehydration')
        auto.followups(self.f.session,self.f.save,origin);auto.followups(self.f.session,self.f.save,origin)
        checks=self.generated('dehydration');self.assertEqual(len(checks),2)
        self.assertEqual({r.data['dehydration_check'] for r in checks},{1,2})
        for r in checks:self.assertEqual((r.data['die'],r.data['bad_results'],r.data['source_rule_id']),('d20','3,7',rule.id))
    def test_changeling_requires_new_birth_and_fairy_overlap(self):
        self.enable('changeling');domain.seed_occult_rules(self.f.session,self.f.save)
        f=self.f.people[0];f.data={**f.data,'species_occult':'Fairy'}
        baby=self.add('sim','Baby',birth_global_day=100,current_household_id=self.f.home.id)
        self.add('sim','Remote baby',birth_global_day=100,country='Other country')
        auto.schedule(self.f.session,self.f.save);auto.schedule(self.f.session,self.f.save)
        self.assertEqual(len(self.generated('changeling')),1);self.assertEqual(self.generated()[0].data['sim_id'],baby.id)
    def test_avatar_birth_age_and_no_backlog(self):
        self.enable('avatar');self.pack(avatar_rules)
        parent=self.f.people[0];parent.data={**parent.data,'avatar_bender_status':'Bender','avatar_bending_element':'Water'}
        child=self.add('sim','New bender',birth_global_day=100,mother_id=parent.id,avatar_birth_nation='Water Tribe')
        self.add('sim','Old birth',birth_global_day=99,mother_id=parent.id)
        auto.schedule(self.f.session,self.f.save);auto.schedule(self.f.session,self.f.save)
        new=[r for r in self.generated('avatar') if r.data.get('sim_id')==child.id]
        self.assertEqual({r.data['extra_rule_code'] for r in new},{'ATLA-02','ATLA-30'})
        self.assertEqual(len(self.generated('avatar')),2)
        self.f.save.global_day=120;auto.schedule(self.f.session,self.f.save)
        self.assertTrue(any(r.data['extra_rule_code']=='ATLA-34' and r.data['sim_id']==child.id for r in self.generated()))
    def test_got_household_year_and_winter_alternatives(self):
        self.enable('got');self.pack(game_of_thrones_rules)
        self.f.save.settings={**self.f.save.settings,'got_current_season':'Winter'}
        self.f.home.data={**self.f.home.data,'got_house_name':'Cooley','got_rank':'Smallfolk','roll_context_active_feud':True,'roll_context_winter_affected':True}
        auto.schedule(self.f.session,self.f.save);auto.schedule(self.f.session,self.f.save)
        codes=[r.data['extra_rule_code'] for r in self.generated()]
        self.assertEqual(codes.count('GOT-T01'),1);self.assertEqual(codes.count('GOT-T02'),1);self.assertEqual(codes.count('GOT-36'),1);self.assertNotIn('GOT-65',codes)
        self.assertTrue(all('sim_id' not in r.data for r in self.generated()))
    def test_toggles_retire_pending_not_completed(self):
        self.enable('hp');self.pack(harry_potter_rules)
        first=self.completed('spellcaster_witch_trial');auto.followups(self.f.session,self.f.save,first)
        finished=self.generated()[0];finished.data={**finished.data,'completed':True}
        second=self.completed('spellcaster_witch_trial');auto.followups(self.f.session,self.f.save,second)
        pending=next(r for r in self.generated() if not r.data.get('completed'))
        self.f.save.settings={**self.f.save.settings,'roll_automation':{**auto.config(self.f.save),'hp':False}}
        self.assertEqual(auto.retire_paused(self.f.session,self.f.save),1)
        self.assertFalse(finished.deleted);self.assertTrue(pending.deleted)
    def test_per_rule_switch_and_pack_gate(self):
        self.enable('hp');self.pack(harry_potter_rules)
        rule=auto.rules_for(self.f.session,self.f.save)['HP-T02']
        cfg=auto.config(self.f.save);cfg['rules']={rule.id:False};self.f.save.settings={**self.f.save.settings,'roll_automation':cfg}
        origin=self.completed('spellcaster_witch_trial');auto.followups(self.f.session,self.f.save,origin);self.assertEqual(self.generated(),[])
        cfg['rules']={};self.f.save.settings={**self.f.save.settings,'roll_automation':cfg,'selected_rule_packs':[]}
        auto.followups(self.f.session,self.f.save,origin);self.assertEqual(self.generated(),[])
    def test_settings_render_and_reject_stale_or_wrong_save(self):
        response=self.client.get('/p/roll-automation');self.assertEqual(response.status_code,200,response.text[:500])
        payload={'save_id':self.f.save.id,'config_stamp':web.stamp(self.f.save),'pregnancy':'on','pregnancy_min_age':'18','pregnancy_max_age':'59'}
        r=self.client.post('/api/roll-automation/settings',data={**payload,'save_id':'other'});self.assertEqual(r.status_code,409)
        r=self.client.post('/api/roll-automation/settings',data=payload);self.assertEqual(r.status_code,200,r.text[:500])
        self.f.session.refresh(self.f.save);self.assertTrue(auto.config(self.f.save)['pregnancy'])
        self.assertEqual(self.client.post('/api/roll-automation/settings',data=payload).status_code,409)
    def test_confirmed_feeding_situation_is_idempotent_and_not_a_buff_guess(self):
        self.enable('feeding');domain.seed_occult_rules(self.f.session,self.f.save);self.f.session.commit()
        rule=auto.rules_for(self.f.session,self.f.save)['vampire_feeding_suspicion']
        self.f.people[0].data={**self.f.people[0].data,'game_buffs':['Vampire hungry']};auto.schedule(self.f.session,self.f.save);self.assertEqual(self.generated(),[])
        data={'save_id':self.f.save.id,'rule_id':rule.id,'target_id':self.f.people[0].id,'start_day':100,'evidence':'Confirmed unwilling feeding in game','confirmed':'on','nonce':'a'*32}
        self.f.session.commit()
        for _ in range(2):
            r=self.client.post('/api/roll-automation/situation',data=data);self.assertEqual(r.status_code,200,r.text[:500])
        self.f.session.expire_all();self.assertEqual(len(self.generated()),1)
    def test_preview_roll_consequences_and_changed_toggle(self):
        self.enable('dehydration');self.add('roll_rule','Young Adult',age_days=72,die='d20',bad_results='1',active=True)
        origin=self.add('roll','Dehydration',sim_id=self.f.people[0].id,occult_rule_key='mermaid_dehydration',die='d6',trigger_results='1',occult_roll=True,nonlethal=True)
        plan=action_previews.simulate(self.f.session,self.f.save,lambda:domain.complete_roll(self.f.session,self.f.save,origin,1))
        self.assertEqual(len([c for c in plan['records'] if c['after']['data'].get('dehydration_check')]),2)
        self.assertEqual(self.generated(),[])
        dependencies=action_previews.roll_dependencies(self.f.session,self.f.save,origin)
        self.assertIn('roll_automation',dependencies['rules'])

    def test_reenable_resumes_but_manual_dismissal_stays_dismissed(self):
        self.enable('hp');self.pack(harry_potter_rules)
        origin=self.completed('spellcaster_witch_trial');auto.followups(self.f.session,self.f.save,origin)
        child=self.generated()[0]
        cfg=auto.config(self.f.save);cfg['hp']=False;self.f.save.settings={**self.f.save.settings,'roll_automation':cfg}
        auto.retire_paused(self.f.session,self.f.save);self.assertTrue(child.deleted)
        cfg['hp']=True;self.f.save.settings={**self.f.save.settings,'roll_automation':cfg}
        auto.followups(self.f.session,self.f.save,origin);self.assertFalse(child.deleted)
        child.deleted=True;child.data={**child.data,'retired_reason':'Player dismissed','extra_auto_paused':False};self.f.session.flush()
        auto.followups(self.f.session,self.f.save,origin);self.assertTrue(child.deleted)

    def test_only_explicit_accepted_feeding_actor_report_triggers(self):
        self.enable('feeding');domain.seed_occult_rules(self.f.session,self.f.save)
        sim=self.f.people[0];sim.data={**sim.data,'species_occult':'Vampire'}
        data={'type':'vampire_unwilling_feeding','confirmed':True,'actor_sim_id':sim.id,'detected_tracker_global_day':100}
        pending=self.add('game_candidate','Unreviewed feeding',status='pending',sim_id=sim.id,payload=data)
        self.add('game_candidate','Dismissed feeding',status='dismissed',sim_id=sim.id,payload=data)
        self.add('game_candidate','Wrong actor',status='accepted',sim_id=self.f.people[1].id,payload=data)
        auto.schedule(self.f.session,self.f.save);self.assertEqual(self.generated(),[])
        pending.data={**pending.data,'status':'accepted'};auto.schedule(self.f.session,self.f.save);auto.schedule(self.f.session,self.f.save)
        self.assertEqual(len(self.generated()),1)

    def test_monitored_annual_rule_repeats_and_stop_retires_pending_only(self):
        self.enable('got');self.pack(game_of_thrones_rules)
        rule=auto.rules_for(self.f.session,self.f.save)['GOT-24']
        situation=self.add('task','Court',feature=auto.FEATURE,option='got',rule_id=rule.id,target_id=self.f.home.id,start_day=100,cadence='annual',enabled=True)
        auto.schedule(self.f.session,self.f.save);auto.schedule(self.f.session,self.f.save);self.assertEqual(len(self.generated()),1)
        first=self.generated()[0];first.data={**first.data,'completed':True,'actual':9,'completed_global_day':100}
        self.f.save.global_day=104;auto.schedule(self.f.session,self.f.save);self.f.session.commit()
        self.assertEqual(len(self.generated()),2)
        result=self.client.post('/api/roll-automation/stop',data={'save_id':self.f.save.id,'record_id':situation.id,'record_version':situation.version})
        self.assertEqual(result.status_code,200,result.text[:500]);self.f.session.expire_all()
        self.assertEqual(len(self.generated()),1);self.assertTrue(self.generated()[0].data.get('completed'))

    def test_dynamic_situations_require_source_step_and_never_default_d20(self):
        self.enable('got');self.pack(game_of_thrones_rules)
        rule=auto.rules_for(self.f.session,self.f.save)['GOT-28']
        data={'save_id':self.f.save.id,'rule_id':rule.id,'target_id':self.f.people[0].id,'start_day':100,'confirmed':'on','nonce':'b'*32,'evidence':'Confirmed tourney participant'}
        self.assertEqual(self.client.post('/api/roll-automation/situation',data=data).status_code,400)
        data.update(die='d20',result_rules='1: Accident; 2-20: No accident')
        self.assertEqual(self.client.post('/api/roll-automation/situation',data=data).status_code,200)
        self.f.session.expire_all();root=self.generated()[0]
        domain.complete_roll(self.f.session,self.f.save,root,1)
        child=next(r for r in self.generated() if r.id!=root.id)
        self.assertEqual((child.data['die'],child.data['bad_results'],child.data['origin_roll_id']),('d6','1',root.id))

    def test_five_year_marriage_is_once_per_couple_and_living_child_exempts(self):
        self.enable('got');self.pack(game_of_thrones_rules)
        a,b=self.f.people[0],self.f.people[1];a.data={**a.data,'got_house':'Cooley'}
        # Cara is a living child of this couple, so they must not be rolled.
        rel=self.add('relationship','Long marriage',type='Marriage',status='Active',partner1_id=a.id,partner2_id=b.id,marriage_global_day=80)
        auto.schedule(self.f.session,self.f.save);self.assertFalse(any(r.data['extra_rule_code']=='GOT-17' for r in self.generated()))
        self.f.people[2].data={**self.f.people[2].data,'death_global_day':90,'death_confirmed':True}
        auto.schedule(self.f.session,self.f.save);auto.schedule(self.f.session,self.f.save)
        self.assertEqual(len([r for r in self.generated() if r.data['extra_rule_code']=='GOT-17']),1)

    def test_full_scheduler_and_today_run_optional_checks(self):
        self.enable('hp');self.pack(harry_potter_rules)
        self.completed('spellcaster_witch_trial');main._TODAY_SCHEDULE_CHECKED.pop(self.f.save.id,None)
        html=self.client.get('/p/today').text
        self.assertIn('Witch-Hunt Consequence',html)
