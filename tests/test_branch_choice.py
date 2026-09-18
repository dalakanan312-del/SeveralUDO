"""Branch choices use disposable data; never switch the player's real branch."""
import copy
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app import infinite_dynasty as dynasty,main,automation,clock
from app.models import Record,Portrait,ClockLink,BackupSnapshot,ChronicleSave
from tests import test_infinite_decades as fixtures


class BranchChoiceTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.InfiniteDecadesTests();self.f.setUp()
        self.parent=self.f.enable();self.older=self.f.capture();self.newer=self.f.capture(3,108)
        self.s=self.f.session;self.save=self.f.save
    def tearDown(self):self.f.tearDown()
    def switch(self,target,name='Current family checkpoint'):
        row=dynasty.activate_branch(self.s,self.save,target.id,name);self.s.commit();return row

    def test_choose_older_waiting_branch_after_finishing(self):
        self.f.finish_modern();finished=copy.deepcopy(self.parent.data)
        self.assertEqual(self.switch(self.older).id,self.older.id)
        self.assertEqual(self.save.global_day,104)
        self.assertEqual(dynasty.metadata(self.newer)['status'],'waiting')
        self.assertEqual(self.parent.data,finished)
        self.assertFalse(dynasty.import_allowed(self.save))

    def test_pause_resume_preserves_progress_inbox_clock_and_shared_album(self):
        self.save.global_day=120
        mother=self.f.people[0]
        mother.data={**mother.data,'notes':'Do not lose the latest edits'}
        pregnancy=Record(save_id=self.save.id,kind='pregnancy',label='Pending birth',global_day=118,data={'mother_id':mother.id,'status':'Active'})
        candidate=Record(save_id=self.save.id,kind='game_candidate',label='Pending review',data={'status':'pending','sim_id':mother.id})
        photo=Portrait(save_id=self.save.id,record_id=mother.id,stage='default',image=b'parent photo',mime_type='image/png')
        album=Record(save_id=self.save.id,kind='decade_snapshot',label='Shared album',data={'portrait_year':1320})
        link=ClockLink(save_id=self.save.id,token_hash='choice-clock',enabled=True,game_anchor_day=3,tracker_anchor_day=120,last_game_day=3)
        self.s.add_all([pregnancy,candidate,photo,album,link]);self.s.flush()
        shared_photo=Portrait(save_id=self.save.id,record_id=album.id,stage='default',image=b'shared photo',mime_type='image/png')
        self.s.add(shared_photo);self.s.commit()
        old_epoch=dynasty.state(self.save)['epoch']
        self.switch(self.older,'Main family day 120')
        self.assertEqual(dynasty.metadata(self.parent)['status'],'paused')
        self.assertEqual(dynasty.metadata(self.parent)['game_save_name'],'Main family day 120')
        self.assertEqual(dynasty.metadata(self.parent)['split_game_save_name'],'Main checkpoint')
        self.assertTrue(candidate.deleted);self.assertTrue(mother.deleted)
        self.assertFalse(link.enabled);self.assertIsNone(link.game_anchor_day)
        self.assertNotEqual(dynasty.state(self.save)['epoch'],old_epoch)
        self.assertFalse(album.deleted);self.assertEqual(shared_photo.image,b'shared photo')
        self.save.global_day=111;self.f.roll.data={**self.f.roll.data,'completed':True,'actual':3}
        other_review=Record(save_id=self.save.id,kind='game_candidate',label='Child review',data={'status':'pending','sim_id':self.f.people[2].id})
        self.s.add(other_review);self.s.commit()
        self.switch(self.parent,'Cara day 111')
        self.assertEqual(self.save.global_day,120);self.assertFalse(mother.deleted)
        self.assertEqual(mother.data['notes'],'Do not lose the latest edits')
        self.assertFalse(pregnancy.deleted);self.assertFalse(candidate.deleted);self.assertTrue(other_review.deleted)
        self.assertEqual(photo.image,b'parent photo');self.assertEqual(shared_photo.image,b'shared photo')
        self.switch(self.older,'Main family day 120 again')
        self.assertEqual(self.save.global_day,111);self.assertTrue(self.f.roll.data['completed'])
        self.assertEqual(self.f.roll.data['actual'],3);self.assertFalse(other_review.deleted);self.assertTrue(candidate.deleted)

    def test_invalid_or_foreign_choices_and_missing_checkpoint_do_not_mutate(self):
        original=copy.deepcopy(self.save.settings)
        foreign_save=ChronicleSave(workspace_id=self.f.workspace.id,name='Other save');self.s.add(foreign_save);self.s.flush()
        foreign=Record(save_id=foreign_save.id,kind=dynasty.KIND,data={'meta':{'status':'waiting'}});self.s.add(foreign);self.s.commit()
        for target_id in [self.parent.id,dynasty.state(self.save)['starting_branch_id'],self.f.people[0].id,foreign.id,'missing']:
            with self.assertRaisesRegex(ValueError,'Choose a waiting or paused'):
                dynasty.activate_branch(self.s,self.save,target_id,'Current')
            self.assertEqual(self.save.settings,original)
        with self.assertRaisesRegex(ValueError,'checkpoint name'):
            dynasty.activate_branch(self.s,self.save,self.older.id)
        self.assertEqual(dynasty.metadata(self.parent)['status'],'active')
        self.assertEqual(dynasty.metadata(self.older)['status'],'waiting')
        dynasty.set_enabled(self.s,self.save,False);self.s.commit()
        with self.assertRaisesRegex(ValueError,'Turn Infinite Decades on'):
            dynasty.activate_branch(self.s,self.save,self.older.id,'Current')

    def test_failed_restore_rolls_back_pause_and_backup(self):
        before=copy.deepcopy(self.save.settings);parent=copy.deepcopy(self.parent.data)
        count=self.s.scalar(select(func.count()).select_from(BackupSnapshot))
        original=dynasty._restore_working_view
        def broken(*args):original(*args);raise ValueError('Simulated failed restore')
        with patch.object(dynasty,'_restore_working_view',side_effect=broken):
            with self.assertRaisesRegex(ValueError,'failed restore'):
                dynasty.activate_branch(self.s,self.save,self.older.id,'Current')
        self.assertEqual(self.save.settings,before);self.assertEqual(self.save.global_day,108)
        self.assertEqual(self.parent.data,parent);self.assertFalse(self.f.people[0].deleted)
        self.assertEqual(self.s.scalar(select(func.count()).select_from(BackupSnapshot)),count)

    def test_completed_branch_cannot_be_reopened(self):
        self.f.finish_modern();self.switch(self.older)
        with self.assertRaisesRegex(ValueError,'Completed branches remain read-only'):
            dynasty.activate_branch(self.s,self.save,self.parent.id,'Child game')

    def test_clock_same_day_keeps_paused_branch_journal_unchanged(self):
        old=automation.session_journal(self.s,self.save,['The parent family moved.'],24,6,0)
        self.s.commit();old_data=copy.deepcopy(old.data);old_version=old.version
        self.switch(self.newer)
        self.assertTrue(old.data['infinite_frozen'])
        frozen_data=copy.deepcopy(old.data);frozen_version=old.version
        dynasty.confirm_game(self.s,self.save)
        link=ClockLink(save_id=self.save.id,token_hash='branch-journal',enabled=True)
        self.s.add(link);self.s.commit()
        report={'protocol_version':2,'report_sequence':1,'game_day':24,'game_hour':6,'game_minute':46,
                'household_sims':[{'game_sim_id':'99','first_name':'New','last_name':'Neighbor','household_id':'77'}]}
        report['report_checksum']=clock.report_checksum(report)
        result=clock.receive(self.s,link,report);self.s.commit()
        self.assertTrue(result['ok']);self.assertTrue(result['journal_updated'])
        self.assertEqual(link.last_game_day,24);self.assertEqual(self.save.global_day,108)
        self.assertEqual(old.data,frozen_data);self.assertEqual(old.version,frozen_version)
        self.assertEqual(old.data['entries'],old_data['entries']);self.assertGreaterEqual(old.version,old_version)
        current=self.s.scalar(select(Record).where(Record.kind=='session_journal',Record.id!=old.id,
            Record.data['infinite_branch_id'].as_string()==self.newer.id))
        self.assertIsNotNone(current);self.assertEqual(current.data['source'],old.data['source'])
        self.assertNotIn('The parent family moved.',current.data['entries'])
        self.assertEqual(current.data['narrator_sim_id'],self.f.people[3].id)
        merged=automation.session_journal(self.s,self.save,['Another new-branch change.'],24,7,0)
        self.s.commit();self.assertEqual(merged.id,current.id)
        self.assertEqual(old.data,frozen_data)
        self.assertTrue(clock.receive(self.s,link,report)['duplicate'])

    def test_ordinary_journal_reuses_live_entry_but_not_archived_entry(self):
        ordinary=ChronicleSave(workspace_id=self.f.workspace.id,name='Ordinary',global_day=5)
        self.s.add(ordinary);self.s.commit()
        first=automation.session_journal(self.s,ordinary,['First change.'],3,8,0)
        self.s.commit()
        self.assertEqual(automation.session_journal(self.s,ordinary,['Next change.'],3,8,5).id,first.id)
        first.deleted=True;self.s.commit();archived=copy.deepcopy(first.data)
        fresh=automation.session_journal(self.s,ordinary,['Fresh change.'],3,9,0)
        self.s.commit()
        self.assertNotEqual(fresh.id,first.id);self.assertEqual(first.data,archived)
        self.assertEqual(fresh.data['entries'],['Fresh change.'])

    def test_http_picker_confirmation_and_stale_tab(self):
        with patch.object(main,'SessionLocal',self.f.sessions):
            client=TestClient(main.app);client.post('/saves/select',data={'save_id':self.save.id})
            page=client.get('/p/infinite-decades');self.assertEqual(page.status_code,200)
            choices=page.text.split('id="choose-branch"',1)[1].split('</section>',1)[0]
            for branch in [self.older,self.newer]:self.assertIn('value="'+branch.id+'"',choices)
            self.assertNotIn('value="'+self.parent.id+'"',choices)
            self.assertIn('/infinite/'+self.save.id+'/play',choices);self.assertIn('Play…',choices)
            url='/infinite/'+self.save.id+'/play';epoch=dynasty.state(self.save)['epoch']
            data={'branch_id':self.older.id,'load_confirmed':'yes','current_game_save_name':'Parent day 108'}
            headers={'X-Dynasty-Epoch':epoch}
            response=client.post(url,data=data,headers=headers);self.assertEqual(response.status_code,200)
            import re
            confirm_url=re.search(r'action="([^"]+/tools/confirm/[^"]+)"',response.text).group(1)
            self.assertEqual(client.post(confirm_url,data=data,headers=headers).status_code,409)
            data['checkpoint_confirmed']='yes'
            response=client.post(confirm_url,data=data,headers=headers);self.assertEqual(response.status_code,200,response.text[:500])
            self.assertIn('Reviewed dynasty changes applied',response.text)
            self.s.expire_all();self.assertEqual(dynasty.state(self.save)['active_branch_id'],self.older.id)
            self.assertEqual(client.post(url,data=data,headers=headers).status_code,409)
            client.close()


if __name__=='__main__':unittest.main()
