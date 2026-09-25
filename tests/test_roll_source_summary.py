import unittest
from app import insights
from tests import test_play_clarity as fixtures


class RollSourceSummaryTests(unittest.TestCase):
    setUp=fixtures.PlayClarityTests.setUp
    tearDown=fixtures.PlayClarityTests.tearDown
    add=fixtures.PlayClarityTests.add

    def test_internal_ids_collapse_into_categories_with_bounded_details(self):
        rows=[self.add('roll','Birth',source='aging:sim:'+str(i),roll_type='Infant',die='d20') for i in range(45)]
        rows+=[self.add('roll','HP',source='harry-potter:HP-05:some-id',die='d6'),
               self.add('roll','Occult',source='occult:alignment:some-id',die='d4'),
               self.add('roll','Marriage',source='planner:marriage:annual:id:1400',die='d8')]
        summary=insights.statistics(rows,self.f.save)['rolls']
        self.assertEqual(dict(summary['sources']),{'Aging':45,'Harry Potter':1,'Occult':1,'Marriage & remarriage':1})
        self.assertEqual(len(summary['source_details']),20);self.assertEqual(summary['source_details_total'],48)
        self.assertEqual(sum(n for _,n in summary['sources']),summary['total'])
        html=self.client.get('/p/statistics?tab=rolls').text
        self.assertIn('Where rolls come from',html)
        self.assertIn('<details class="roll-source-details">',html)
        self.assertNotIn('<details class="roll-source-details" open',html)

    def test_categories_handle_followups_and_missing_metadata(self):
        for data,label in [({},'Other tracker rolls'),({'source':'rule:auto-followup:abc'},'Other follow-up checks'),
                           ({'maternal_followup':True},'Childbirth & maternal checks'),
                           ({'event_id':'abc','origin_roll_id':'def'},'Historical events'),
                           ({'marriage_refusal':True},'Marriage & remarriage')]:
            self.assertEqual(insights.roll_source_category(data),label)
