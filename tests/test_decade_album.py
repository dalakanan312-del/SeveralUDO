import copy
import io
import unittest
from unittest.mock import patch

from PIL import Image
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session

from app import decade_album as album, decade_portraits, infinite_dynasty as dynasty
from app.db import Base
from app.models import ChronicleSave,Record,Portrait,Workspace


class DecadeAlbumTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://');Base.metadata.create_all(self.engine)
        self.session=Session(self.engine)
        workspace=Workspace(name='Fixture');self.session.add(workspace);self.session.flush()
        self.save=ChronicleSave(workspace_id=workspace.id,name='Family',start_year=979,global_day=45,days_per_year=4)
        self.session.add(self.save);self.session.flush()
        self.home=Record(save_id=self.save.id,kind='household',label='Black',data={})
        self.session.add(self.home);self.session.flush()
        self.people=[]
        for i in range(3):
            person=Record(save_id=self.save.id,kind='sim',label=f'Person {i}',global_day=1,
                data={'birth_global_day':1,'current_household_id':self.home.id,'game_age_stage':'adult'})
            self.session.add(person);self.people.append(person)
        self.session.flush()
        self.raw=self.png('red')
        self.sources={p.id:(self.raw,'adult','Tray Library') for p in self.people}

    def tearDown(self): self.session.close();self.engine.dispose()

    def png(self,color):
        image=Image.new('RGBA',(100,240),(0,0,0,0));image.paste(color,(30,10,70,230))
        raw=io.BytesIO();image.save(raw,'PNG');return raw.getvalue()

    def add(self,ids=None,year=989,**kw):
        with patch.object(album,'_source_photos',return_value=(self.sources,3,0,0)), patch.object(album,'_legacy_photos',return_value=self.sources):
            return album.update(self.session,self.save,year,selected_ids=ids,**kw)

    def test_one_image_per_year_additive_idempotent_and_frozen(self):
        first=self.add([self.people[0].id]);self.session.flush();rid=first['snapshot'].id
        original=album._image(self.session,rid,'sim-'+self.people[0].id).image
        self.sources[self.people[0].id]=(self.png('blue'),'elder','Later photo')
        self.people[0].data={**self.people[0].data,'death_global_day':44,'game_was_dead':True}
        second=self.add([self.people[1].id]);third=self.add([self.people[1].id])
        self.assertEqual((second['snapshot'].id,second['added'],second['kept']),(rid,1,1))
        self.assertEqual((third['added'],third['snapshot'].data['member_count']),(0,2))
        self.assertEqual(album._image(self.session,rid,'sim-'+self.people[0].id).image,original)
        self.assertEqual(len(album.archives(self.session,self.save)),1)
        self.assertEqual(self.save.global_day,45)

    def test_newborn_after_selected_year_is_not_backdated_into_it(self):
        self.people[1].data={**self.people[1].data,'birth_global_day':45}
        with self.assertRaisesRegex(ValueError,'lived in that year'):self.add([self.people[1].id])
        self.assertFalse(album.eligible_in_year(self.people[1],self.save,989))

    def test_future_year_rejected(self):
        with self.assertRaisesRegex(ValueError,'current year'):self.add(year=1000)

    def test_missing_new_photo_does_not_remove_existing_people(self):
        first=self.add([self.people[0].id]);self.sources={}
        result=self.add([self.people[1].id])
        self.assertEqual(result['snapshot'].id,first['snapshot'].id)
        self.assertEqual(result['snapshot'].data['member_count'],1)
        self.assertEqual(result['missing'],[self.people[1].label])

    def test_profile_upload_fallback(self):
        self.session.add(Portrait(save_id=self.save.id,record_id=self.people[0].id,stage='default',source='upload',mime_type='image/png',image=self.raw));self.session.flush()
        with patch.object(album,'discover_portraits',return_value=[]):
            result=album.update(self.session,self.save,989,selected_ids=[self.people[0].id])
        self.assertEqual(result['snapshot'].data['members'][0]['photo_source'],'Imported/profile portrait')

    def test_branch_switch_does_not_rewind_shared_image(self):
        # Isolated real branch workflow, including an old checkpoint that carries
        # legacy album metadata. All effects stay in this memory-only database.
        with patch('app.backup_service.create_snapshot'):
            dynasty.enable(self.session,self.save,[p.id for p in self.people],'Main',1000,'Main game')
        self.session.flush()
        first=self.add([self.people[0].id]);rid=first['snapshot'].id
        child=dynasty.capture(self.session,self.save,[self.people[1].id],'Child branch','Child game');self.session.flush()
        legacy=copy.deepcopy(first['snapshot'].data)
        self.add([self.people[2].id]);before=album._image(self.session,rid).image
        point=dynasty.unpack_snapshot(child.data['snapshot'])
        self.assertNotIn(rid,{r['id'] for r in point['records']})
        point['records'].append({'id':rid,'kind':'decade_snapshot','label':'Old album','global_day':41,'data':legacy,'deleted':False})
        point['portraits'].append({'record_id':rid,'stage':'default','mime_type':'image/png','image':'b2xk'})
        with dynasty.branch_operation(self.session,self.save):dynasty._restore_working_view(self.session,self.save,child,point)
        self.session.flush()
        self.assertEqual(first['snapshot'].data['member_count'],2)
        self.assertEqual(album._image(self.session,rid).image,before)
        self.assertFalse(first['snapshot'].deleted)
        result=self.add([self.people[1].id])
        self.assertEqual(result['snapshot'].id,rid)
        self.assertEqual(result['snapshot'].data['member_count'],3)
        self.assertEqual(len(result['snapshot'].data['contributions']),2)

    def test_reminders_are_per_branch_and_dismissals_stick(self):
        with patch('app.backup_service.create_snapshot'):
            dynasty.enable(self.session,self.save,[p.id for p in self.people],'Main',1000,'Main game')
        self.assertEqual(decade_portraits.schedule_prompt(self.session,self.save),1)
        self.session.flush()
        reminder=self.session.scalar(select(Record).where(Record.kind=='game_candidate'));reminder.deleted=True;self.session.flush()
        self.assertEqual(decade_portraits.schedule_prompt(self.session,self.save),0)
        child=dynasty.capture(self.session,self.save,[self.people[1].id],'Child','Child game')
        with dynasty.branch_operation(self.session,self.save):dynasty._restore_working_view(self.session,self.save,child,dynasty.unpack_snapshot(child.data['snapshot']))
        self.assertEqual(decade_portraits.schedule_prompt(self.session,self.save),1)

    def test_legacy_conversion_keeps_original_and_every_member(self):
        plate=Record(save_id=self.save.id,kind='household_portrait',label='Original household',data={'member_ids':[p.id for p in self.people],'household_id':self.home.id,'household_name':self.home.label})
        self.session.add(plate);self.session.flush()
        old=Record(save_id=self.save.id,kind='decade_snapshot',label='Old',global_day=41,data={'portrait_year':989,'household_portrait_ids':[plate.id]})
        self.session.add(old);self.session.flush()
        self.session.add(Portrait(save_id=self.save.id,record_id=old.id,stage='default',image=self.raw,mime_type='image/png'));self.session.flush()
        result=self.add([])
        self.assertEqual(result['snapshot'].id,old.id)
        self.assertEqual((result['kept'],result['snapshot'].data['member_count'],old.global_day),(3,3,41))
        self.assertEqual(album._image(self.session,old.id,'original').image,self.raw)

    def test_legacy_conversion_refuses_to_lose_a_member(self):
        plate=Record(save_id=self.save.id,kind='household_portrait',label='Old',data={'member_ids':[self.people[0].id]})
        self.session.add(plate);self.session.flush()
        old=Record(save_id=self.save.id,kind='decade_snapshot',label='Old',data={'portrait_year':989,'household_portrait_ids':[plate.id]})
        self.session.add(old);self.session.flush();self.sources={}
        with self.assertRaisesRegex(ValueError,'Original snapshot preserved'):self.add([])
        self.assertNotIn('album_version',old.data)

    def test_group_layout_has_white_background_and_uses_all_people(self):
        entries=[({'sim_id':str(i),'name':str(i),'photo_age_stage':'child' if i%2 else 'adult'},self.raw) for i in range(125)]
        image=Image.open(io.BytesIO(album.compose(1040,entries)))
        self.assertEqual(image.getpixel((0,0)),(255,255,255))
        self.assertGreater(image.width,image.height)
        self.assertLess(image.width,16384)
        self.assertGreater(image.height,1000) # wraps, never truncates the crowd

    def test_legacy_crop_preserves_appearance_without_original_source(self):
        plate=Record(save_id=self.save.id,kind='household_portrait',label='Original',data={
            'member_ids':[self.people[0].id],'member_names':[self.people[0].label],'background_color':'#2b2118'})
        self.session.add(plate);self.session.flush()
        raw=decade_portraits._compose('Home',989,[(self.people[0].label,self.raw)],'#2b2118')
        self.session.add(Portrait(save_id=self.save.id,record_id=plate.id,stage='default',image=raw,mime_type='image/webp'))
        record=Record(save_id=self.save.id,kind='decade_snapshot',data={'household_portrait_ids':[plate.id]})
        self.session.add(record);self.session.flush()
        with patch.object(album,'discover_portraits',return_value=[]):found=album._legacy_photos(self.session,record)
        self.assertEqual(found[self.people[0].id][2],'Preserved original snapshot crop')
        expected=Image.open(io.BytesIO(raw)).convert('RGB').crop((24,94,274,424))
        self.assertEqual(Image.open(io.BytesIO(found[self.people[0].id][0])).tobytes(),expected.tobytes())
        from types import SimpleNamespace
        from unittest.mock import Mock
        candidates=[]
        for color,stage in [('blue','elder'),('red','child')]:
            path=Mock();path.read_bytes.return_value=self.png(color)
            candidates.append(SimpleNamespace(first_name='Person',last_name='0',image_path=path,age_stage=stage))
        with patch.object(album,'discover_portraits',return_value=candidates),patch.object(album,'decode_sgi',side_effect=lambda raw:raw):
            found=album._legacy_photos(self.session,record)
        self.assertEqual(found[self.people[0].id][0],self.raw)
        self.assertEqual(found[self.people[0].id][1],'child')
        self.assertEqual(found[self.people[0].id][2],'Original Tray photo matched to archived snapshot')


class DecadeAlbumPageTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app import main
        from tests.test_infinite_decades import InfiniteDecadesTests
        self.f=InfiniteDecadesTests();self.f.setUp()
        self.f.save.settings={**self.f.save.settings,'automation_enabled':False};self.f.session.commit()
        self.binding=patch.object(main,'SessionLocal',self.f.sessions);self.binding.start()
        self.client=TestClient(main.app)
        self.client.post('/saves/select',data={'save_id':self.f.save.id})

    def tearDown(self):
        self.client.close();self.binding.stop();self.f.tearDown()

    def test_page_and_post_use_same_album_and_preserve_members(self):
        raw=io.BytesIO();Image.new('RGBA',(70,170),'red').save(raw,'PNG')
        sources={p.id:(raw.getvalue(),'adult','Test') for p in self.f.people}
        response=self.client.get('/p/decade-snapshots?year=1320')
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('Add Sims from this branch',response.text)
        with patch.object(album,'_source_photos',return_value=(sources,5,0,0)):
            for person in self.f.people[:2]:
                response=self.client.post('/api/decade-snapshots',data={'year':1320,'member_ids':person.id})
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn('This year still has one snapshot',response.text)
        self.f.session.expire_all()
        rows=album.archives(self.f.session,self.f.save)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0].data['member_count'],2)
        self.assertEqual(self.client.get('/portraits/'+rows[0].id+'/default').status_code,200)

    def test_old_dismissed_reminder_stays_dismissed(self):
        year,_=decade_portraits._milestone(self.f.save)
        self.f.session.add(Record(save_id=self.f.save.id,kind='game_candidate',deleted=True,
            data={'source_key':f'save-portrait:{year}','status':'dismissed'}));self.f.session.commit()
        self.assertEqual(decade_portraits.schedule_prompt(self.f.session,self.f.save),0)


if __name__=='__main__':unittest.main()
