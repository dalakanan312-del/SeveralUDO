"""Declining releases a throw; retries, old tabs and completed history stay safe."""
import copy
import unittest
from unittest.mock import patch
from sqlalchemy import select
from app import dice, infinite_decades
from app.models import Record, DiceAudit, ActionPreview, ChronicleSave
from tests.test_preview_confirmation import ConfirmationTests


class RollDeclineTests(unittest.TestCase):
    setUp=ConfirmationTests.setUp
    tearDown=ConfirmationTests.tearDown
    add=ConfirmationTests.add
    headers=ConfirmationTests.headers
    pending=ConfirmationTests.pending
    preview=ConfirmationTests.preview
    confirm=ConfirmationTests.confirm

    def decline(self,p,status=200,headers=None):
        r=self.client.post('/api/previews/'+p['token']+'/decline',headers=self.headers() if headers is None else headers,follow_redirects=False)
        self.assertEqual(r.status_code,status,r.text)
        return r

    def audits(self,row):
        self.f.session.expire_all()
        return list(self.f.session.scalars(select(DiceAudit).where(DiceAudit.context_id==row.id).order_by(DiceAudit.created_at)))

    def test_decline_throws_again_and_preserves_pending_roll_and_audit(self):
        row=self.pending(nonlethal=True)
        # Two valid, deterministic test seeds that produce visibly different faces.
        seeds=[next(str(i) for i in range(100) if dice.deterministic_faces(str(i),1,6)==[face]) for face in (1,6)]
        original=(copy.deepcopy(row.data),row.version,row.global_day)
        with patch('app.dice.secrets.token_hex',side_effect=seeds):
            p=self.preview(row,native=True);self.assertEqual(p['actual'],1)
            self.decline(p)
            fresh=self.preview(row,native=True);self.assertEqual(fresh['actual'],6)
        self.f.session.refresh(row)
        self.assertEqual((row.data,row.version,row.global_day),original)
        audits=self.audits(row);self.assertEqual(len(audits),2)
        self.assertTrue(audits[0].context.startswith('declined-preview-'))
        self.assertTrue(audits[1].context.startswith('pending-preview-'))
        self.assertTrue(all(dice.verify(a) for a in audits))
        shadow=self.f.session.scalar(select(Record).where(Record.kind=='dice_audit_record',Record.data['audit_id'].as_string()==audits[0].id))
        self.assertEqual(shadow.data['context'],audits[0].context)
        self.confirm(fresh);self.f.session.refresh(row);self.assertEqual(row.data['actual'],6)

    def test_reopen_without_decline_and_refresh_do_not_throw_again(self):
        row=self.pending(nonlethal=True);p=self.preview(row,native=True)
        with patch('app.dice.audited_roll',side_effect=AssertionError('Do not reroll retries')):
            self.assertEqual(self.preview(row,native=True)['actual'],p['actual'])
            r=self.client.post('/api/previews/'+p['token']+'/refresh',headers=self.headers())
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['preview']['actual'],p['actual'])
            self.confirm(r.json()['preview'])
        self.assertEqual(len(self.audits(row)),1)

    def test_decline_in_one_tab_invalidates_same_throw_in_other_tabs(self):
        row=self.pending(nonlethal=True);a=self.preview(row,native=True);b=self.preview(row,native=True)
        self.decline(a);self.confirm(a,409);self.confirm(b,409)
        self.assertEqual(self.client.post('/api/previews/'+b['token']+'/refresh',headers=self.headers()).status_code,409)
        fresh=self.preview(row,native=True)
        # Retrying the old decline must not clear the new reservation.
        self.decline(a);self.decline(b)
        self.assertEqual(self.preview(row,native=True)['actual'],fresh['actual'])
        self.assertEqual(len(self.audits(row)),2)
        self.confirm(fresh)

    def test_declining_refreshed_preview_releases_original_throw(self):
        row=self.pending(nonlethal=True);p=self.preview(row,native=True)
        r=self.client.post('/api/previews/'+p['token']+'/refresh',headers=self.headers());self.assertEqual(r.status_code,200,r.text)
        fresh=r.json()['preview'];self.decline(fresh);self.confirm(p,409)
        self.preview(row,native=True);self.assertEqual(len(self.audits(row)),2)

    def test_completed_roll_cannot_be_declined(self):
        row=self.pending(nonlethal=True);p=self.preview(row,native=True);self.confirm(p)
        self.f.session.refresh(row);before=copy.deepcopy(row.data)
        self.decline(p,409);self.f.session.refresh(row);self.assertEqual(row.data,before)
        self.assertTrue(self.audits(row)[0].context.startswith('pending-preview-'))

    def test_manual_decline_and_refreshed_aliases_do_not_apply_any_outcome(self):
        row=self.pending(bad_results='1');p=self.preview(row,1)
        q=self.client.post('/api/previews/'+p['token']+'/refresh',headers=self.headers()).json()['preview']
        r=self.client.post('/api/previews/'+p['token']+'/refresh',headers=self.headers()).json()['preview']
        self.decline(q);self.confirm(p,409);self.confirm(r,409)
        self.f.session.refresh(row);self.assertFalse(row.data.get('completed'))
        self.f.session.refresh(self.f.people[0]);self.assertIsNone(self.f.people[0].data.get('death_global_day'))
        self.assertEqual(len(self.audits(row)),0)

    def test_manual_entry_decline_also_releases_preexisting_native_throw(self):
        row=self.pending(nonlethal=True);native=self.preview(row,native=True)
        manual=self.preview(row,2);self.decline(manual);self.confirm(native,409)
        self.preview(row,native=True);self.assertEqual(len(self.audits(row)),2)

    def test_decline_does_not_release_other_roll(self):
        a=self.pending(nonlethal=True);b=self.pending(nonlethal=True)
        first=self.preview(a,native=True);other=self.preview(b,native=True)
        self.decline(first)
        with patch('app.dice.audited_roll',side_effect=AssertionError('Other roll stays reserved')):
            self.assertEqual(self.preview(b,native=True)['actual'],other['actual'])

    def test_legacy_preview_without_audit_id_can_be_declined(self):
        row=self.pending(nonlethal=True);p=self.preview(row,native=True)
        ticket=self.f.session.get(ActionPreview,p['token']);payload=copy.deepcopy(ticket.payload)
        payload.pop('audit_id');payload.pop('native');ticket.payload=payload;self.f.session.commit()
        self.decline(p);self.preview(row,native=True);self.assertEqual(len(self.audits(row)),2)

    def test_wrong_owner_active_save_and_branch_are_rejected(self):
        row=self.pending(nonlethal=True);p=self.preview(row,native=True)
        other=ChronicleSave(workspace_id=self.f.workspace.id,name='Other save',settings={});self.f.session.add(other);self.f.session.commit()
        self.client.post('/saves/select',data={'save_id':other.id},follow_redirects=False);self.decline(p,409)
        self.client.post('/saves/select',data={'save_id':self.f.save.id},follow_redirects=False)
        ticket=self.f.session.get(ActionPreview,p['token']);ticket.user_id='another-user';self.f.session.commit();self.decline(p,404)

    def test_decline_within_an_enabled_infinite_branch(self):
        infinite_decades.enable(self.f.session,self.f.save,[p.id for p in self.f.people],'Main line',2000,'Test checkpoint')
        self.f.session.commit()
        row=self.pending(nonlethal=True)
        headers={**self.headers(row),'X-Dynasty-Epoch':infinite_decades.state(self.f.save)['epoch']}
        r=self.client.post('/api/rolls/'+row.id+'/roll',headers=headers);self.assertEqual(r.status_code,200,r.text)
        p=r.json()['preview'];self.decline(p,headers=headers)
        self.decline(p,409,headers={**headers,'X-Dynasty-Epoch':'old-branch'})

    def test_plain_html_decline_returns_to_original_page(self):
        row=self.pending(nonlethal=True)
        r=self.client.post('/api/rolls/'+row.id+'/roll',headers={'referer':'http://testserver/p/today?household=test&window=overdue'})
        self.assertEqual(r.status_code,200,r.text[:300]);self.assertIn('Decline result',r.text)
        ticket=self.f.session.scalar(select(ActionPreview).where(ActionPreview.payload['roll_id'].as_string()==row.id))
        r=self.decline({'token':ticket.id},303,headers={})
        self.assertEqual(r.headers['location'],'/p/today?household=test&window=overdue')


if __name__=='__main__':unittest.main()
