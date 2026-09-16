import copy
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import birth_multiples as births, main
from app.models import Record
from tests import test_infinite_decades as fixtures


class BirthMultiplesTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.InfiniteDecadesTests(); self.f.setUp()
        self.session = self.f.session; self.save = self.f.save
        self.mother = self.f.people[0]
        self.p = Record(save_id=self.save.id, kind='pregnancy', label='Birth', global_day=100,
            data={'mother_id':self.mother.id,'conception_global_day':96,'due_global_day':100,
                  'status':'Active','babies_expected':1,'babies_delivered':0,
                  'maternal_rolls_required':False,'birth_newborn_rolls_required':False})
        self.session.add(self.p); self.session.commit()

    def tearDown(self):self.f.tearDown()

    def baby(self, name, linked=True, **extra):
        tags=['Live birth','Singleton birth','Born at Home']
        row=Record(save_id=self.save.id,kind='sim',label=name,global_day=100,data={
            'mother_id':self.mother.id,'birth_global_day':100,
            'pregnancy_id':self.p.id if linked else None,
            'multiple_birth_status':'Singleton','birth_circumstances_source':'Reviewed Clock Sync suggestion',
            'birth_circumstances':'; '.join(tags)+'.','birth_circumstance_tags':tags,
            'notes':'Keep this note', **extra})
        self.session.add(row);self.session.flush();return row

    def delivered(self,count):
        self.p.data={**self.p.data,'status':'Delivered','babies_delivered':count,'actual_delivery_global_day':100}
        self.session.flush()

    def test_actual_count_corrects_unlinked_legacy_siblings(self):
        self.delivered(3)
        children=[self.baby(str(i),linked=False) for i in range(3)]
        self.assertEqual(births.reconcile(self.session,self.save),3)
        for child in children:
            self.assertEqual(child.data['multiple_birth_status'],'Triplet')
            self.assertIn('Triplet birth',child.data['birth_circumstances'])
            self.assertNotIn('Singleton',child.data['birth_circumstances'])
            self.assertEqual(child.data['notes'],'Keep this note')
        self.assertEqual(births.reconcile(self.session,self.save),0)

    def test_partial_delivery_updates_earlier_siblings(self):
        first=self.baby('First');births.reconcile(self.session,self.save)
        second=self.baby('Second');births.reconcile(self.session,self.save)
        self.assertEqual(first.data['multiple_birth_status'],'Twin')
        self.assertEqual(second.data['multiple_birth_status'],'Twin')
        third=self.baby('Third');self.delivered(3);births.reconcile(self.session,self.save)
        self.assertTrue(all(s.data['multiple_birth_status']=='Triplet' for s in [first,second,third]))

    def test_frozen_history_readable_but_never_rewritten(self):
        self.delivered(6)
        child=self.baby('Frozen',linked=False,infinite_frozen=True);child.deleted=True
        self.session.commit();before=copy.deepcopy(child.data);version=child.version
        view=births.load(self.session,self.save).corrected_data(child)
        self.assertEqual(view['multiple_birth_status'],'Sextuplet')
        self.assertEqual(births.reconcile(self.session,self.save),0)
        self.assertEqual(child.data,before);self.assertEqual(child.version,version)
        self.assertFalse(self.session.dirty)

    def test_manual_status_and_custom_summary_survive(self):
        self.delivered(3)
        manual=self.baby('Manual',multiple_birth_status='Player choice',multiple_birth_status_source='manual')
        custom=self.baby('Custom',birth_circumstances='My personal account of the birth.')
        births.reconcile(self.session,self.save)
        self.assertEqual(manual.data['multiple_birth_status'],'Player choice')
        self.assertEqual(custom.data['multiple_birth_status'],'Triplet')
        self.assertEqual(custom.data['birth_circumstances'],'My personal account of the birth.')
        changed={**custom.data,'multiple_birth_status':'Unknown','birth_circumstances':'Changed note'}
        births.retain_edit_provenance(custom.data,changed)
        self.assertEqual(changed['multiple_birth_status_source'],'manual')
        self.assertEqual(changed['birth_circumstance_tags'],[])

    def test_no_guess_from_shared_day_or_estimated_birth(self):
        self.delivered(2)
        children=[self.baby('Unrelated',linked=False,mother_id='someone-else'),
                  self.baby('Estimated',linked=False,birth_year_only=True),
                  self.baby('Adopted',linked=False,legitimacy='Adopted')]
        groups=births.load(self.session,self.save)
        self.assertTrue(all(s.id not in groups.for_sim for s in children))
        ambiguous=self.baby('Ambiguous',linked=False)
        self.session.add(Record(save_id=self.save.id,kind='pregnancy',label='Another',data=copy.deepcopy(self.p.data)))
        self.session.flush()
        self.assertNotIn(ambiguous.id,births.load(self.session,self.save).for_sim)

    def test_actual_counts_override_forecast_and_zero_is_not_singleton(self):
        self.p.data={**self.p.data,'babies_expected':3,'babies_delivered':1}
        self.assertEqual(births.count_for(self.p),3) # partial, not yet complete
        self.delivered(2)
        self.assertEqual(births.count_for(self.p),2)
        self.delivered(0)
        self.assertIsNone(births.count_for(self.p))
        self.assertEqual(births.label(None),'')
        self.assertEqual(births.label(8),'Octuplet')
        self.assertEqual(births.label(9),'9-baby multiple')
        result=main.birth_circumstance_suggestion(self.session,self.save,None,self.mother,100,events=[])
        self.assertEqual(result['multiple_birth_status'],'')
        self.assertNotIn('Singleton',result['summary'])

    def test_automation_off_does_not_repair(self):
        self.delivered(2);child=self.baby('First')
        self.save.settings={**self.save.settings,'automation_enabled':False}
        self.assertEqual(births.reconcile(self.session,self.save),0)
        self.assertEqual(child.data['multiple_birth_status'],'Singleton')

    def test_manual_newborn_route_refreshes_entire_delivery(self):
        self.session.commit()
        with patch.object(main,'SessionLocal',self.f.sessions), TestClient(main.app) as client:
            client.post('/saves/select',data={'save_id':self.save.id})
            for name in ('First','Second','Third'):
                response=client.post(f'/pregnancies/{self.p.id}/newborns',data={'first_name':name,'birth_global_day':100},follow_redirects=False)
                self.assertEqual(response.status_code,303,response.text)
        self.session.expire_all()
        children=list(self.session.scalars(select(Record).where(Record.save_id==self.save.id,Record.kind=='sim',Record.data['pregnancy_id'].as_string()==self.p.id)))
        self.assertEqual(len(children),3)
        self.assertTrue(all(s.data['multiple_birth_status']=='Triplet' for s in children))
        self.assertTrue(all('Triplet birth' in s.data['birth_circumstances'] for s in children))

    def test_clock_babies_before_delivery_get_corrected_after_acceptance(self):
        candidates=[]
        for name in ('Twin One','Twin Two'):
            r=Record(save_id=self.save.id,kind='game_candidate',label=name,global_day=100,
                data={'action':'new_baby','status':'pending','source_key':name,'payload':{
                    'game_sim_id':name,'first_name':name,'inferred_mother_id':self.mother.id}})
            self.session.add(r);candidates.append(r)
        outcome=Record(save_id=self.save.id,kind='game_candidate',label='Delivery',global_day=100,
            data={'action':'pregnancy_outcome','status':'pending','sim_id':self.mother.id,'source_key':'delivery',
                  'payload':{'pregnancy_id':self.p.id,'babies_delivered':2}})
        self.session.add(outcome);self.session.commit()
        with patch.object(main,'SessionLocal',self.f.sessions), TestClient(main.app) as client:
            client.post('/saves/select',data={'save_id':self.save.id})
            for candidate in candidates:
                response=client.post(f'/automation/{candidate.id}/accept',data={'birth_global_day':100},follow_redirects=False)
                self.assertEqual(response.status_code,303,response.text)
            response=client.post(f'/automation/{outcome.id}/accept',data={'status':'Delivered','delivery_global_day':100,'babies_delivered':2},follow_redirects=False)
            self.assertEqual(response.status_code,303,response.text)
        self.session.expire_all()
        children=list(self.session.scalars(select(Record).where(Record.save_id==self.save.id,Record.kind=='sim',Record.label.in_(('Twin One','Twin Two')))))
        self.assertEqual(len(children),2)
        for child in children:
            self.assertEqual(child.data['multiple_birth_status'],'Twin')
            self.assertIn('Twin birth',child.data['birth_circumstances'])


if __name__=='__main__':unittest.main()
