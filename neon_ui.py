
from __future__ import annotations
from pathlib import Path
import storage, save_manager, cloud_schema

def render_connection_setup(st):
    st.title('☁️ Connect Decades Tracker to Neon'); st.write('Decades Tracker v2.0 stores its live saves in Neon PostgreSQL. Your connection string is saved only in this app folder and is never included in a shared save.')
    st.info('In the Neon dashboard, copy the pooled connection string for normal app use. If you also copy a direct (non-pooler) connection string, schema creation and migrations will use it.')
    with st.form('neon_connection_setup'):
        pooled=st.text_input('Neon connection string',type='password',placeholder='postgresql://user:password@...-pooler.../neondb?sslmode=require',help='The pooled connection string is recommended for normal application traffic.')
        direct=st.text_input('Direct connection string (optional but recommended for setup/migrations)',type='password',placeholder='postgresql://user:password@.../neondb?sslmode=require')
        submitted=st.form_submit_button('Test connection & use Neon',type='primary',use_container_width=True)
    if submitted:
        if not pooled.strip():st.error('Paste your Neon connection string.');return
        try:
            storage.test_connection(direct.strip() or pooled.strip()); storage.save_config(pooled.strip(),direct.strip() or pooled.strip(),None); save_manager.ensure_setup(); st.success('Connected to Neon successfully.'); st.rerun()
        except Exception as e: st.error(f'Could not connect to Neon: {e}')
    st.caption('Tip: the connection string contains your database password. Do not put a configured .neon_storage.json file into a public release ZIP.')

def render_first_save_setup(st):
    saves=save_manager.list_saves()
    if saves:return False
    st.title('☁️ Neon is connected'); st.subheader('Move an existing tracker into the cloud')
    local=[x for x in save_manager.discover_local_saves() if int(x.get('sims') or 0)>0]
    if local:
        st.write('I found existing SQLite save data beside this app. Migration copies it into Neon; the original database files are not changed or deleted.')
        labels=[f"{x['name']} — {x['sims']:,} Sims — {Path(x['path']).name}" for x in local]
        selected=st.multiselect('SQLite saves to migrate',labels,default=labels,key='neon_migration_selection')
        if st.button('Migrate selected saves to Neon',type='primary',use_container_width=True):
            if not selected:st.error('Choose at least one save.')
            else:
                progress=st.progress(0); migrated=[]
                try:
                    for i,label in enumerate(selected,1):
                        item=local[labels.index(label)]; rec=save_manager.migrate_sqlite_file(item['path'],item['name'],make_active=True,source_note=f"Migrated from {Path(item['path']).name}"); migrated.append(rec['name']); progress.progress(i/len(selected))
                    st.success('Migrated to Neon: '+', '.join(migrated)); st.rerun()
                except Exception as e:st.error(f'Migration stopped: {e}')
        st.divider()
    st.subheader('Or start a new cloud save'); a,b,c=st.columns(3); name=a.text_input('Save name',value='My First Save',key='first_cloud_name'); start=b.number_input('Calendar start year',-10000,10000,1200,key='first_cloud_start'); current=c.number_input('Initial historical year',-10000,10000,1200,key='first_cloud_year'); day=st.selectbox('Initial challenge day',[1,2,3,4],key='first_cloud_day')
    if st.button('Create blank Neon save',use_container_width=True,key='first_cloud_create'):
        if int(current)<int(start):st.error('The initial historical year cannot be earlier than the calendar start year.')
        else:save_manager.create_blank(name,int(start),int(current),int(day),source_save_id=None);st.success('Cloud save created.');st.rerun()
    return True

def connection_summary():
    cfg=storage.load_config(); url=cfg.get('pooled_url',''); host=''
    try:host=url.split('@',1)[1].split('/',1)[0]
    except Exception:pass
    return {'configured':storage.configured(),'host':host,'using_environment':cfg.get('source')=='environment'}
