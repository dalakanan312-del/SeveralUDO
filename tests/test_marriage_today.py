"""Marriage checks stay actionable on the simplified Today board."""
import unittest
from app import usability_ui, domain, marriage_rules
from tests import test_play_clarity as fixtures


class MarriageTodayTests(unittest.TestCase):
    setUp=fixtures.PlayClarityTests.setUp
    tearDown=fixtures.PlayClarityTests.tearDown
    add=fixtures.PlayClarityTests.add

    def roll(self,name,day=100,**extra):
        return self.add('roll',name,day,die='d6',bad_results='2-6',nonlethal=True,
                        **{'sim_id':self.f.people[0].id,'roll_type':'MarriageEligibility',**extra})

    def test_first_remarriage_and_refusal_appear_with_roll_buttons(self):
        rows=[self.roll('First marriage'),self.roll('Remarriage check',roll_type='AnnualRemarriageEligibility',annual_marriage=True),
              self.roll('Refusal check',roll_type='MarriageRefusal',marriage_refusal=True)]
        html=self.client.get('/p/today').text
        for row in rows:
            self.assertIn('id="roll-'+row.id+'"',html)
            self.assertIn('/api/rolls/'+row.id+'/roll',html)
        self.assertIn('Marriage · To do',html);self.assertIn('Remarriage · To do',html)
        self.assertIn('Marriage refusal · To do',html)

    def test_overdue_marriage_stays_on_today_without_mixing_other_old_work(self):
        overdue=self.roll('Unresolved marriage',97)
        self.roll('Earlier unrelated aging',97,roll_type='Teen')
        future=self.roll('Later marriage',101)
        html=self.client.get('/p/today').text
        self.assertIn('id="roll-'+overdue.id+'"',html);self.assertIn('Overdue since GD 97',html)
        self.assertNotIn('Earlier unrelated aging',html);self.assertNotIn('id="roll-'+future.id+'"',html)
        self.assertIn(overdue.label,self.client.get('/p/today?window=overdue').text)
        future_html=self.client.get('/p/today?window=future').text
        self.assertIn(future.label,future_html);self.assertNotIn('/api/rolls/'+future.id+'/roll',future_html)

    def test_completed_dead_frozen_and_other_household_are_not_todos(self):
        done=self.roll('Completed marriage',97,completed=True,completed_global_day=100)
        frozen=self.roll('Frozen marriage',97,infinite_frozen=True)
        dead=self.f.people[1];dead.data={**dead.data,'death_confirmed':True,'death_global_day':98};self.f.session.commit()
        dead_roll=self.roll('Dead marriage',97,sim_id=dead.id)
        outsider=self.roll('Other household',97,sim_id=self.f.people[-1].id)
        board=usability_ui.board(self.f.session,self.f.save,{'household':self.f.home.id})
        ids={r.id for r in board['board_groups'][0]['rows']}
        self.assertTrue({done.id,frozen.id,dead_roll.id,outsider.id}.isdisjoint(ids))
        self.assertIn(done.id,{r.id for r in board['board_groups'][2]['rows']})

    def test_marriage_pagination_and_partial_refresh(self):
        for i in range(26):self.roll('Marriage '+str(i),99)
        groups=[usability_ui.board(self.f.session,self.f.save,{'decisions_page':str(p)},'decisions')['board_groups'][0] for p in (1,2)]
        self.assertEqual([len(g['rows']) for g in groups],[24,2])
        self.assertFalse({r.id for r in groups[0]['rows']} & {r.id for r in groups[1]['rows']})
        response=self.client.get('/api/ui/today/decisions?decisions_page=2')
        self.assertEqual(response.status_code,200);self.assertEqual(response.text.count('Marriage · To do'),2)

    def test_scheduled_annual_checks_reach_today_and_refusal_follows(self):
        self.f.save.settings={**self.f.save.settings,'marriage_roll_mode':'era_annual','automation_enabled':True}
        domain.schedule_marriage_rolls(self.f.session,self.f.save);self.f.session.commit()
        board=usability_ui.board(self.f.session,self.f.save,{},'decisions')['board_groups'][0]
        rolls=[r for r in board['rows'] if r.data.get('annual_marriage')]
        self.assertTrue(rolls)
        row=rolls[0];domain.complete_roll(self.f.session,self.f.save,row,1);self.f.session.commit()
        # Completed results leave the to-do list; a refusal is its own check.
        ids={r.id for r in usability_ui.board(self.f.session,self.f.save,{},'decisions')['board_groups'][0]['rows']}
        self.assertNotIn(row.id,ids)
        child=marriage_rules.request_refusal(self.f.session,self.f.save,row);self.f.session.commit()
        html=self.client.get('/p/today').text
        self.assertIn('id="roll-'+child.id+'"',html)
        self.assertIn('/api/rolls/'+child.id+'/roll',html)
