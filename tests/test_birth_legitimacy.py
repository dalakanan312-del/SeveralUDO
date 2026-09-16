"""Legitimacy defaults use the actual parents and birth date, not current spouses."""
import copy
import unittest
from sqlalchemy import select
from app import birth_legitimacy as legitimacy
from app.models import Record, ChronicleSave
from tests import test_usability as fixtures


class BirthLegitimacyTests(unittest.TestCase):
    setUp = fixtures.UsabilityTests.setUp
    tearDown = fixtures.UsabilityTests.tearDown
    add = fixtures.UsabilityTests.add

    def marriage(self, **overrides):
        data = {'partner1_id':self.f.people[0].id, 'partner2_id':self.f.people[1].id,
                'type':'Marriage', 'status':'Active', 'start_global_day':20}
        data.update(overrides)
        return self.add('relationship','Parents marriage',20,**data)

    def suggested(self, day=100):
        return legitimacy.suggestion(self.f.session,self.f.save,self.f.people[0].id,self.f.people[1].id,day)

    def child_data(self, **overrides):
        return {'mother_id':self.f.people[0].id,'father_id':self.f.people[1].id,
                'birth_global_day':100,'legitimacy':'',**overrides}

    def test_married_parents_default_and_preserve_provenance(self):
        marriage=self.marriage()
        original=self.child_data()
        data=legitimacy.apply_default(self.f.session,self.f.save,original)
        self.assertEqual(original['legitimacy'],'')
        self.assertEqual(data['legitimacy'],'Legitimate')
        self.assertEqual(data['legitimacy_marriage_id'],marriage.id)
        self.assertIn('GD 100',data['legitimacy_source'])
        self.assertEqual(self.suggested(20)['value'],'Legitimate')
        self.assertFalse(self.suggested(19))

    def test_wrong_spouse_courtship_and_unknown_parents_do_not_qualify(self):
        row=self.marriage(partner2_id=self.f.people[2].id)
        self.assertFalse(self.suggested())
        row.data={**row.data,'partner2_id':self.f.people[1].id,'type':'Courtship'}
        self.f.session.commit()
        self.assertFalse(self.suggested())
        self.assertFalse(legitimacy.suggestion(self.f.session,self.f.save,None,self.f.people[1].id,100))
        self.assertFalse(legitimacy.suggestion(self.f.session,self.f.save,self.f.people[0].id,self.f.people[0].id,100))
        self.assertFalse(self.suggested(None))

    def test_historical_marriage_can_have_ended_after_birth(self):
        self.marriage(status='Divorced',end_global_day=110)
        self.assertEqual(self.suggested(100)['value'],'Legitimate')
        self.assertFalse(self.suggested(110))
        self.assertFalse(self.suggested(120))

    def test_planned_or_undated_ended_marriage_is_not_proof(self):
        row=self.marriage(status='Planned')
        self.assertFalse(self.suggested())
        for status in ('Divorced','Annulled','Separated','Widowed','Ended'):
            row.data={**row.data,'status':status}
            self.f.session.commit()
            self.assertFalse(self.suggested(),status)

    def test_recorded_deaths_limit_marriage_and_same_day_widowhood_is_allowed(self):
        self.marriage(status='Widowed',end_global_day=100)
        person=self.f.people[0]
        person.data={**person.data,'death_global_day':100}
        self.f.session.commit()
        self.assertEqual(self.suggested(100)['value'],'Legitimate')
        self.assertFalse(self.suggested(101))

    def test_explicit_status_and_uncertain_dates_are_not_overwritten(self):
        self.marriage()
        for value in ('Illegitimate','Adopted','Unknown','Not applicable','Legitimate','Custom status'):
            data=self.child_data(legitimacy=value)
            self.assertEqual(legitimacy.apply_default(self.f.session,self.f.save,data),data)
        for marker in ('birth_year_only','birth_global_day_estimated'):
            data=self.child_data(**{marker:True})
            self.assertEqual(legitimacy.apply_default(self.f.session,self.f.save,data),data)

    def test_archived_or_foreign_marriages_are_not_used(self):
        row=self.marriage()
        row.deleted=True
        self.f.session.commit()
        self.assertFalse(self.suggested())
        other=ChronicleSave(workspace_id=self.f.workspace.id,name='Other save')
        self.f.session.add(other);self.f.session.flush()
        row.deleted=False;row.save_id=other.id
        self.f.session.commit()
        self.assertFalse(self.suggested())

    def test_frozen_history_is_read_only_and_not_extrapolated(self):
        row=self.marriage()
        row.deleted=True
        row.data={**row.data,'infinite_frozen':True,'infinite_frozen_global_day':100}
        self.f.session.commit()
        before=copy.deepcopy(row.data)
        self.assertEqual(self.suggested(100)['value'],'Legitimate')
        self.assertFalse(self.suggested(101))
        self.assertEqual(before,row.data)

    def pregnancy(self):
        return self.add('pregnancy','Expected child',mother_id=self.f.people[0].id,
            father_id=self.f.people[1].id,due_global_day=100,babies_expected=5,status='Active',maternal_rolls_required=False)

    def test_pregnancy_form_defaults_and_manual_override(self):
        self.marriage()
        pregnancy=self.pregnancy()
        page=self.client.get('/pregnancies/'+pregnancy.id)
        self.assertEqual(page.status_code,200)
        self.assertIn('selected>Automatic — Legitimate</option>',page.text)
        self.assertIn('value="Unknown"',page.text)
        for name,value,expected in [('Automatic baby','','Legitimate'),('Override baby','Illegitimate','Illegitimate'),('Unknown baby','Unknown','Unknown')]:
            response=self.client.post('/pregnancies/'+pregnancy.id+'/newborns',data={
                'first_name':name,'birth_global_day':'100','legitimacy':value},follow_redirects=False)
            self.assertEqual(response.status_code,303,response.text)
            child=self.f.session.scalar(select(Record).where(Record.save_id==self.f.save.id,Record.kind=='sim',Record.label==name))
            self.assertEqual(child.data['legitimacy'],expected)

    def test_edited_birth_date_recalculates_automatic_instead_of_using_displayed_default(self):
        self.marriage(start_global_day=90)
        pregnancy=self.pregnancy()
        self.assertIn('Automatic — Legitimate',self.client.get('/pregnancies/'+pregnancy.id).text)
        response=self.client.post('/pregnancies/'+pregnancy.id+'/newborns',data={
            'first_name':'Earlier baby','birth_global_day':'80','legitimacy':''},follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text)
        child=self.f.session.scalar(select(Record).where(Record.kind=='sim',Record.label=='Earlier baby'))
        self.assertEqual(child.data['legitimacy'],'')

    def test_new_baby_inbox_and_acceptance_default(self):
        self.marriage()
        candidate=self.add('game_candidate','Detected baby',action='new_baby',status='pending',payload={
            'game_sim_id':'legitimacy-baby','first_name':'Detected','last_name':'Baby',
            'inferred_mother_id':self.f.people[0].id,'inferred_father_id':self.f.people[1].id})
        page=self.client.get('/p/automation')
        self.assertEqual(page.status_code,200)
        self.assertIn('Automatic — Legitimate',page.text)
        response=self.client.post('/automation/'+candidate.id+'/accept',data={},follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text)
        child=self.f.session.scalar(select(Record).where(Record.kind=='sim',Record.data['game_sim_id'].as_string()=='legitimacy-baby'))
        self.assertEqual(child.data['legitimacy'],'Legitimate')

    def test_manual_sim_creation_defaults_without_rewriting_existing_children(self):
        self.marriage()
        previous=copy.deepcopy(self.f.people[2].data)
        response=self.client.post('/sims',data={'first_name':'Manual child','birth_global_day':'100',
            'mother_id':self.f.people[0].id,'father_id':self.f.people[1].id},follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text)
        child=self.f.session.scalar(select(Record).where(Record.kind=='sim',Record.label=='Manual child'))
        self.assertEqual(child.data['legitimacy'],'Legitimate')
        self.f.session.refresh(self.f.people[2])
        self.assertEqual(self.f.people[2].data.get('legitimacy'),previous.get('legitimacy'))

    def test_editor_can_default_blank_or_keep_explicit_unknown(self):
        self.marriage()
        child=self.f.people[2]
        for value,expected in [('', 'Legitimate'),('Unknown','Unknown')]:
            response=self.client.post('/sims/'+child.id,data={'first_name':'Cara','birth_global_day':'100',
                'mother_id':self.f.people[0].id,'father_id':self.f.people[1].id,'legitimacy':value},follow_redirects=False)
            self.assertEqual(response.status_code,303,response.text)
            self.f.session.refresh(child)
            self.assertEqual(child.data['legitimacy'],expected)
            if value=='Unknown':
                self.assertNotIn('legitimacy_marriage_id',child.data)
                self.assertNotIn('legitimacy_source',child.data)


if __name__=='__main__':unittest.main()
