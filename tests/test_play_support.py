"""End-to-end play-support checks on an isolated, disposable dynasty."""
import copy
import unittest
from uuid import uuid4
from types import SimpleNamespace
from sqlalchemy import select
from app import play_support as play,play_support_ui,why,domain,infinite_decades,sync
from app.models import Record,ChronicleSave,ClockLink
from tests import test_workflow as fixtures


class PlaySupportTests(unittest.TestCase):
    def setUp(self):
        self.h=fixtures.WorkflowTests();self.h.setUp();self.f=self.h.fixture;self.client=self.h.client
    def tearDown(self): self.h.tearDown()
    def rows(self):
        self.f.session.expire_all()
        return list(self.f.session.scalars(select(Record).where(Record.save_id==self.f.save.id)))
    def create(self,kind,label,data,day=100):
        row=Record(save_id=self.f.save.id,kind=kind,label=label,data=data,global_day=day)
        self.f.session.add(row);self.f.session.commit();return row
    def post(self,path,**fields):
        return self.client.post(path,data={'save_id':self.f.save.id,'form_id':uuid4().hex,**fields})
    def project(self,feature='ambition',**fields):
        return self.post('/play-support/project/'+feature,
                         **{'label':'Restore fortune','household_id':self.f.home.id,'metric':'wealth','target':10000,'progress':0,'enabled':'on','status':'Active',**fields})
    def get_feature(self,feature):
        return next(r for r in self.rows() if r.data.get('feature')==feature)

    def test_all_new_pages_render_and_are_in_navigation(self):
        for page in play_support_ui.PAGES:
            response=self.client.get('/p/'+page)
            self.assertEqual(response.status_code,200,(page,response.text[:300]))
            self.assertIn(play_support_ui.PAGES[page][0],response.text)

    def test_session_priority_covers_births_marriages_rolls_and_storylines(self):
        self.create('pregnancy','Ada delivery',{'mother_id':self.f.people[0].id,'due_global_day':102,'status':'Active'})
        self.create('relationship','Wedding',{'partner1_id':self.f.people[0].id,'partner2_id':self.f.people[1].id,'type':'Marriage','planned_marriage_global_day':103})
        self.create('roll','Unresolved famine',{'sim_id':self.f.people[1].id,'completed':False,'due_global_day':99})
        self.create('task','Forgotten ambition',{'feature':'ambition','household_id':self.f.home.id,'status':'Active'})
        plan=play.session_plan(self.rows(),self.f.save,7)
        labels=[r['label'] for r in plan[0]['reasons']]
        self.assertTrue(any('Birth expected' in x for x in labels))
        self.assertTrue(any('Marriage' in x for x in labels))
        self.assertIn('Unresolved famine',labels)
        self.assertTrue(any('unfinished family storyline' in x for x in labels))

    def test_planner_uses_twelve_day_years_and_ignores_dead_sim_rolls(self):
        self.f.save.days_per_year=12;self.f.session.commit()
        dead=self.f.people[0];dead.data={**dead.data,'death_global_day':90};self.f.session.commit()
        self.create('roll','Dead Sim obligation',{'sim_id':dead.id,'completed':False,'due_global_day':100})
        reasons=play.session_plan(self.rows(),self.f.save,28)[0]['reasons']
        self.assertFalse(any(r['label']=='Dead Sim obligation' for r in reasons))
        self.assertTrue(any('becomes Preteen' in r['label'] and r['day']==121 for r in reasons))

    def test_handover_is_idempotent_and_reports_meaningful_changes_only(self):
        token=uuid4().hex
        for _ in range(2):
            response=self.post('/play-support/handover',form_id=token,household_id=self.f.home.id,notes='Finish the wedding preparations.')
            self.assertEqual(response.status_code,200,response.text[:300])
        notes=[r for r in self.rows() if r.data.get('feature')=='handover'];self.assertEqual(len(notes),1)
        sim=self.f.people[0];sim.data={**sim.data,'last_seen_game_hour':15};sim.version+=1;self.f.session.commit()
        plan=play.session_plan(self.rows(),self.f.save)
        self.assertFalse(any(r.id==sim.id for r in plan[0]['changes']))
        sim.data={**sim.data,'career':'Baker'};sim.version+=1;self.f.session.commit()
        plan=play.session_plan(self.rows(),self.f.save)
        self.assertTrue(any(r.id==sim.id for r in plan[0]['changes']))
        self.assertIn('Finish the wedding preparations.',self.client.get('/p/play-next').text)

    def test_ambition_unknown_wealth_and_descendants_across_households(self):
        self.assertEqual(self.project().status_code,200)
        project=play.project_views(self.rows(),self.f.save)['ambition'][0]
        self.assertIsNone(project['value']);self.assertFalse(project['met'])
        self.f.home.data={**self.f.home.data,'last_game_funds':12000};self.f.session.commit()
        self.assertTrue(play.project_views(self.rows(),self.f.save)['ambition'][0]['met'])
        child=self.f.people[2];child.data={**child.data,'current_household_id':'moved-away'};self.f.session.commit()
        value=play.metric_value({'metric':'descendants','founder_id':self.f.people[0].id},self.rows(),self.f.save)
        self.assertEqual(value,1)
        self.assertEqual(play.metric_value({'metric':'generations','founder_id':self.f.people[0].id},self.rows(),self.f.save),2)

    def test_project_edits_have_version_protection_and_disable_checks(self):
        self.assertEqual(self.project().status_code,200);row=self.get_feature('ambition');old=row.version
        self.assertEqual(self.project(record_id=row.id,version=old,enabled='',notes='Changed goal').status_code,200)
        self.assertEqual(self.project(record_id=row.id,version=old,notes='Stale overwrite').status_code,409)
        view=play.project_views(self.rows(),self.f.save)['ambition'][0]
        self.assertFalse(view['row'].data['enabled']);self.assertIsNone(view['value'])

    def test_secret_knowledge_is_exact_and_drama_uses_the_actual_discovery(self):
        mother,witness=self.f.people[:2]
        response=self.project('secret',label='Hidden parentage',sim_id=mother.id,knower_ids=[witness.id],notes='Ada is secretly the heir’s mother.',category='Hidden parentage')
        self.assertEqual(response.status_code,200,response.text[:300]);secret=self.get_feature('secret')
        self.assertEqual(secret.data['knower_ids'],[witness.id])
        self.assertEqual(secret.data['knowledge_history'][0]['learned'],[witness.id])
        self.assertEqual(self.post('/play-support/secret-scene',record_id=secret.id,witness_id=mother.id).status_code,400)
        response=self.post('/play-support/secret-scene',record_id=secret.id,witness_id=witness.id)
        self.assertEqual(response.status_code,200,response.text[:300])
        self.assertIn('Ada is secretly the heir’s mother.',response.text)
        self.assertIn('Keep the knowledge private',response.text)
        self.assertIn('Tell the people affected',response.text)
        other=ChronicleSave(workspace_id=self.f.workspace.id,name='Other',global_day=100,settings=copy.deepcopy(self.f.save.settings))
        self.f.session.add(other);self.f.session.commit();self.client.post('/saves/select',data={'save_id':other.id})
        self.assertEqual(self.client.post('/drama/branch',data={'branch_id':'protect'}).status_code,409)

    def test_recovery_is_optional_keeps_due_date_and_never_removes_money(self):
        event=self.create('event','Famine',{'end_global_day':100})
        before=copy.deepcopy(self.f.home.data)
        response=self.project('recovery',label='Recover from famine',event_id=event.id,due_global_day=107,debt=500,
                              consequences=['Debt','Displacement','Guardianship needs'],notes='Rebuild the barn.')
        self.assertEqual(response.status_code,200,response.text[:300]);row=self.get_feature('recovery')
        self.assertEqual(row.data['consequences'],['Debt','Displacement','Guardianship needs'])
        self.assertEqual(self.f.home.data,before)
        self.f.save.global_day=110;self.f.session.commit()
        response=self.project('recovery',record_id=row.id,version=row.version,label=row.label,event_id=event.id,
                              due_global_day=107,status='Completed',debt=0,completed_steps='Barn rebuilt.')
        self.assertEqual(response.status_code,200,response.text[:300])
        row=self.get_feature('recovery');self.assertEqual(row.data['start_global_day'],100);self.assertEqual(row.data['due_global_day'],107)

    def test_historical_checks_are_off_by_default_pack_scoped_and_overridable(self):
        self.f.people[0].data={**self.f.people[0].data,'career':'Astronaut'};self.f.session.commit()
        response=self.post('/play-support/accuracy',label='Space careers',category='occupation',match_text='Astronaut',
                           allowed_from_year=1950,source='My selected era rule',enabled='on',rule_pack='severaludo')
        self.assertEqual(response.status_code,200,response.text[:300])
        html=self.client.get('/p/historical-check').text
        self.assertNotIn('Matches needing review',html)
        response=self.post('/play-support/accuracy-toggle',enabled='on')
        self.assertEqual(response.status_code,200,response.text[:300]);self.assertIn('Ada · Space careers',response.text)
        warning=play.historical_checks(self.rows(),self.f.save)[0]
        response=self.post('/play-support/accuracy-override',warning_key=warning['key'],notes='Alternate history permitted.')
        self.assertEqual(response.status_code,200,response.text[:300])
        self.assertTrue(play.historical_checks(self.rows(),self.f.save)[0]['overridden'])
        rule=self.get_feature('accuracy');rule.data={**rule.data,'rule_pack':'harry_potter_decades'};self.f.session.commit()
        self.assertEqual(play.historical_checks(self.rows(),self.f.save),[])

    def test_letters_retain_selected_facts_separate_from_fiction_and_reject_future(self):
        fact=self.create('migration','Ada moved to York',{'sim_id':self.f.people[0].id},90)
        future=self.create('migration','Future move',{'sim_id':self.f.people[0].id},110)
        response=self.post('/play-support/writing',label='News from York',writing_kind='letter',perspective='parent',
                           author_id=self.f.people[0].id,recipient_id=self.f.people[1].id,fact_ids=[fact.id])
        self.assertEqual(response.status_code,200,response.text[:300]);letter=self.get_feature('perspective_writing')
        self.assertTrue(letter.data['fictional_narration'])
        self.assertEqual(letter.data['confirmed_facts'],['GD 90: Ada moved to York'])
        self.assertNotIn('Future move',letter.data['body'])
        response=self.post('/play-support/writing',label='Bad future',writing_kind='diary',perspective='heir',author_id=self.f.people[0].id,fact_ids=[future.id])
        self.assertEqual(response.status_code,400)

    def test_achievements_use_year_length_and_require_evidence(self):
        heirloom=self.create('heirloom','Founder’s ring',{'household_id':self.f.home.id,'acquired_global_day':1},1)
        self.f.save.days_per_year=12;self.f.save.global_day=1201;self.f.session.commit()
        self.assertEqual(play.metric_value({'metric':'heirloom_years','evidence_id':heirloom.id},self.rows(),self.f.save),100)
        response=self.project('achievement',label='A century of memory',metric='heirloom_years',target=100,evidence_id=heirloom.id)
        self.assertEqual(response.status_code,200,response.text[:300])
        self.assertTrue(play.project_views(self.rows(),self.f.save)['achievement'][0]['met'])

    def test_branch_comparison_does_not_restore_or_modify_checkpoints(self):
        root=self.f.enable();child=self.f.capture()
        before=copy.deepcopy(self.f.save.settings);snapshots={r.id:copy.deepcopy(r.data) for r in (root,child)}
        response=self.client.get(f'/p/branch-comparison?left={root.id}&right={child.id}')
        self.assertEqual(response.status_code,200,response.text[:300]);self.assertIn('Parent–child occult matches',response.text)
        self.f.session.expire_all();self.assertEqual(self.f.save.settings,before)
        for row in (root,child): self.assertEqual(row.data,snapshots[row.id])
        self.assertEqual(self.client.get('/p/branch-comparison?left=not-owned').status_code,404)

    def test_branch_metrics_keep_unknown_wealth_and_future_births_out_of_deaths(self):
        payload={'global_day':100,'member_sim_ids':['a','b'],'records':[
          {'id':'a','kind':'sim','label':'A','global_day':1,'data':{'birth_global_day':1},'deleted':False},
          {'id':'b','kind':'sim','label':'Future','global_day':120,'data':{'birth_global_day':120},'deleted':False}]}
        metrics=play.branch_metrics(payload)
        self.assertEqual(metrics['members'],1);self.assertEqual(metrics['dead'],0);self.assertIsNone(metrics['wealth'])

    def test_provenance_snapshots_rule_calculation_and_allowlisted_report(self):
        rule=self.create('roll_rule','Adult rule',{'die':'d20','bad_results':'1','min_age':72,'password':'never copy'})
        roll=self.create('roll','Future adult check',{'source_rule_id':rule.id,'sim_id':self.f.people[0].id,'due_global_day':73,'die':'d20'},73)
        self.f.session.info['play_trigger_report']={'save_id':self.f.save.id,'report_id':'report-17','report_sequence':17,'game_day':10}
        domain.journal(self.f.session,roll,'upsert',0);self.f.session.commit();snapshot=copy.deepcopy(roll.data['why_evidence'])
        self.assertEqual(snapshot['report']['report_id'],'report-17')
        self.assertNotIn('password',snapshot['sources'][0]['values'])
        self.assertTrue(any('72 days old' in text for text in why.explain(roll)['calculation']))
        rule.data={**rule.data,'die':'d6'};roll.global_day=80;self.f.session.info.pop('play_trigger_report');domain.journal(self.f.session,roll,'upsert',1);self.f.session.commit()
        self.assertEqual(roll.data['why_evidence'],snapshot)
        response=self.client.get('/records/'+roll.id+'/why')
        self.assertEqual(response.status_code,200,response.text[:300]);self.assertIn('report-17',response.text)
        self.assertNotIn('never copy',response.text)

    def test_legacy_explanations_do_not_invent_source_history(self):
        response=self.client.get('/records/'+self.f.roll.id+'/why')
        self.assertEqual(response.status_code,200,response.text[:300])
        self.assertIn('older record has no creation-time evidence',response.text)
        self.assertIn('No report identity was retained',response.text)
        self.assertEqual(self.client.get('/records/not-owned/why').status_code,404)

    def test_report_context_is_cleaned_even_on_errors(self):
        @why.with_report
        def broken(session,link,report):
            self.assertNotIn('token',session.info['play_trigger_report'])
            raise ValueError('test failure')
        with self.assertRaises(ValueError): broken(self.f.session,ClockLink(save_id=self.f.save.id),{'game_day':1,'token':'private'})
        self.assertNotIn('play_trigger_report',self.f.session.info)

    def test_cross_save_and_frozen_mutations_rejected(self):
        self.assertEqual(self.project(save_id='not-current').status_code,409)
        self.assertEqual(self.project(founder_id='not-owned',metric='generations').status_code,400)
        self.f.enable();self.f.finish_modern()
        self.client.headers['X-Dynasty-Epoch']=infinite_decades.state(self.f.save)['epoch']
        self.assertEqual(self.project().status_code,409)

    def test_old_edited_roll_does_not_gain_invented_creation_evidence(self):
        domain.journal(self.f.session,self.f.roll,'upsert',self.f.roll.version)
        self.assertNotIn('why_evidence',self.f.roll.data)

    def test_colon_aging_source_and_planner_rule_are_retained(self):
        rule=self.create('roll_rule','Young Adult',{'age_days':72,'standard_age_days':72,'die':'d20'})
        roll=self.create('roll','Age check',{'source':f'aging:{self.f.people[0].id}:{rule.id}','sim_id':self.f.people[0].id})
        domain.journal(self.f.session,roll,'upsert',0)
        self.assertEqual(roll.data['why_evidence']['sources'][0]['values']['age_days'],72)
        rule=self.create('planner_rule','Marriage eligibility',{'minimum_age':72})
        roll=self.create('roll','Marriage check',{'planner_rule_id':rule.id})
        domain.journal(self.f.session,roll,'upsert',0)
        self.assertEqual(roll.data['why_evidence']['sources'][0]['id'],rule.id)

    def test_reviewed_detection_links_to_original_report(self):
        candidate=self.create('game_candidate','Detected illness',{'action':'illness','status':'pending'})
        self.f.session.info['play_accepted_detection']={'save_id':self.f.save.id,'detection_id':candidate.id,'reviewed_at_utc':'2026-09-08T10:00:00Z','report':{'report_id':'source-20'}}
        illness=self.create('illness','Confirmed flu',{'sim_id':self.f.people[0].id})
        domain.journal(self.f.session,illness,'upsert',0);self.f.session.commit()
        response=self.client.get('/records/'+illness.id+'/why')
        self.assertEqual(response.status_code,200)
        self.assertIn('/records/'+candidate.id+'/why',response.text)
        self.assertIn('source-20',response.text)

    def test_game_career_and_school_fields_are_checked(self):
        person=self.f.people[0];person.data={**person.data,'game_career':'Astronaut','game_education':'University'}
        self.f.session.commit()
        for category,phrase in [('occupation','Astronaut'),('education','University')]:
            self.create('era_rule',phrase,{'feature':'accuracy','enabled':True,'category':category,'match_text':phrase,'allowed_from_year':1950,'source':'Selected fictional rule'})
        warnings=play.historical_checks(self.rows(),self.f.save)
        self.assertEqual({w['rule'].label for w in warnings},{'Astronaut','University'})

    def test_pending_plans_cannot_be_presented_as_confirmed_letters(self):
        planned=self.create('migration','Planned move',{'status':'Planned'},90)
        self.assertFalse(play.eligible_writing_fact(planned,self.f.save))
        response=self.post('/play-support/writing',label='Not yet happened',writing_kind='letter',perspective='spouse',author_id=self.f.people[0].id,fact_ids=[planned.id])
        self.assertEqual(response.status_code,400)

    def test_future_wealth_and_unborn_descendants_do_not_complete_goals(self):
        self.create('economy_entry','Future windfall',{'household_id':self.f.home.id,'balance':999999},200)
        self.assertIsNone(play.metric_value({'metric':'wealth','household_id':self.f.home.id},self.rows(),self.f.save))
        before=play.metric_value({'metric':'descendants','founder_id':self.f.people[0].id},self.rows(),self.f.save)
        self.create('sim','Future child',{'birth_global_day':200,'mother_id':self.f.people[0].id},200)
        self.assertEqual(play.metric_value({'metric':'descendants','founder_id':self.f.people[0].id},self.rows(),self.f.save),before)

    def test_only_existing_synced_record_kinds_are_used(self):
        self.assertTrue({kind for label,kind in play.FEATURES.values()} <= sync.SYNC_KINDS)
        self.assertTrue({'session_journal','correspondence','era_check','era_rule'} <= sync.SYNC_KINDS)


if __name__=='__main__':unittest.main()
