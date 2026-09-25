"""Free-play dice must never alter a challenge save."""
import copy
import unittest
from unittest.mock import patch
from sqlalchemy import select, func
from app import quick_dice, main
from app.models import Record, Change, DiceAudit
from tests import test_play_clarity as fixtures


class QuickDiceTests(unittest.TestCase):
    setUp=fixtures.PlayClarityTests.setUp
    tearDown=fixtures.PlayClarityTests.tearDown

    def test_all_presets_and_custom_notation(self):
        for sides,label in quick_dice.PRESETS:
            result=quick_dice.throw('d'+str(sides))
            self.assertTrue(1<=result['total']<=sides)
            self.assertEqual(len(result['faces']),1)
            self.assertEqual(bool(result['coin']),sides==2)
        result=quick_dice.throw('3d10+2','Who hosts?')
        self.assertEqual(result['total'],sum(result['faces'])+2)
        self.assertEqual(result['question'],'Who hosts?')
        self.assertEqual(len(result['faces']),3)
        self.assertEqual(quick_dice.throw('d6-10000')['modifier'],-10000)

    def test_invalid_or_unbounded_inputs_are_rejected(self):
        for notation in ['d1','0d6','101d6','d1001','d6+10001','2d6;alert(1)','d6'*40]:
            with self.subTest(notation=notation),self.assertRaises(ValueError):quick_dice.throw(notation)
        with self.assertRaises(ValueError):quick_dice.throw('d6','x'*161)
        self.assertEqual(self.client.post('/api/quick-dice',data={'notation':'d1'},headers={'Accept':'application/json'}).status_code,400)

    def snapshot(self):
        self.f.session.expire_all()
        return (self.f.save.global_day,self.f.save.revision,copy.deepcopy(self.f.save.settings),
                [(r.id,r.version,copy.deepcopy(r.data)) for r in self.f.session.scalars(select(Record).order_by(Record.id))],
                *[self.f.session.scalar(select(func.count()).select_from(model)) for model in (Change,DiceAudit)])

    def test_json_roll_is_fresh_and_does_not_touch_any_save(self):
        before=self.snapshot()
        results=[self.client.post('/api/quick-dice',data={'notation':'2d6','save_id':self.f.save.id,'context_id':self.f.roll.id},headers={'Accept':'application/json'}) for _ in range(2)]
        self.assertTrue(all(r.status_code==200 for r in results))
        self.assertNotEqual(results[0].json()['id'],results[1].json()['id'])
        self.assertEqual(self.snapshot(),before)

    def test_page_and_non_javascript_form_work(self):
        page=self.client.get('/p/quick-dice');self.assertEqual(page.status_code,200,page.text)
        self.assertIn('quick_dice.js',page.text)
        for sides,label in quick_dice.PRESETS:self.assertIn('value="d'+str(sides)+'"',page.text)
        r=self.client.post('/api/quick-dice',data={'notation':'d5','question':'<script>danger()</script>'},follow_redirects=False)
        self.assertEqual(r.status_code,303)
        page=self.client.get(r.headers['location']);self.assertEqual(page.status_code,200)
        self.assertIn('&lt;script&gt;danger()',page.text);self.assertNotIn('<script>danger()',page.text)
        self.client.post('/api/quick-dice',data={'notation':'d1'})
        self.assertIn('between',self.client.get('/p/quick-dice').text.lower())

    def test_requires_signed_in_user(self):
        with patch.object(main,'signed_in',return_value=None):
            self.assertEqual(self.client.post('/api/quick-dice',data={'notation':'d6'}).status_code,401)
