"""Evidence-based planning views. Reading a view never applies game changes."""
from collections import Counter
from types import SimpleNamespace
import hashlib
import re
import json
from . import advanced, insights, occult_rules


def integer(value, default=None):
    try: return int(value)
    except (TypeError, ValueError): return default


def home_id(sim):
    return str((sim.data or {}).get('current_household_id') or (sim.data or {}).get('household_id') or '')


def active(rows):
    return [r for r in rows if not r.deleted and not (r.data or {}).get('infinite_frozen')]


def closed(data):
    return data.get('enabled') is False or bool(data.get('completed')) or str(data.get('status','')).casefold() in {
        'completed','resolved','cancelled','canceled','delivered','miscarriage','stillbirth','ended','dismissed','archived','paused'}


def linked(row, household, member_ids):
    data=row.data or {}
    if row.kind=='sim': return row.id in member_ids
    if row.kind=='household': return row.id==household.id
    return data.get('household_id')==household.id or any(data.get(key) in member_ids for key in
        ('sim_id','mother_id','father_id','partner1_id','partner2_id','author_id'))

def handover_value(row):
    if row.kind=='sim':
        fields=('birth_global_day','death_global_day','mother_id','father_id','current_household_id','household_id','career','education','skills','traits','game_career','game_education','game_skills','game_traits','game_milestones','species_occult','game_occult_types')
        return hashlib.sha256(json.dumps([row.label,{key:row.data.get(key) for key in fields}],sort_keys=True,default=str).encode()).hexdigest()
    return row.version


def session_plan(rows, save, horizon=7):
    rows=active(rows); day=save.global_day; year_days=max(1,save.days_per_year)
    sims=[r for r in rows if r.kind=='sim']; homes=[r for r in rows if r.kind=='household']
    people={s.id:s for s in sims}; result=[]
    for home in homes:
        members=[s for s in sims if home_id(s)==home.id]
        living=[s for s in members if advanced.living(s,day)]
        ids={s.id for s in members}; living_ids={s.id for s in living}
        owned=[r for r in rows if linked(r,home,ids)]
        rotations=[r for r in owned if r.kind in {'play_rotation','session_journal'} and
                   (r.kind=='play_rotation' or r.data.get('feature')=='handover') and
                   r.global_day is not None and r.global_day<=day]
        last=max(rotations,key=lambda r:(r.global_day,r.created_at),default=None)
        reasons=[]
        def add(label,due,weight,href):
            if due is not None and due<=day+horizon:
                reasons.append({'label':label,'day':due,'score':weight+(20 if due<=day else 0),'href':href})
        for row in owned:
            data=row.data or {}
            if row.kind=='pregnancy' and not closed(data) and data.get('mother_id') in living_ids:
                add('Birth expected: '+row.label,integer(data.get('due_global_day'),row.global_day),90,'/p/pregnancies')
            elif row.kind=='roll' and not closed(data) and (not data.get('sim_id') or data['sim_id'] in living_ids):
                due=integer(data.get('due_global_day'),row.global_day)
                if due is not None and due>=0: add(row.label,due,65,'/records/'+row.id+'/why')
            elif row.kind=='relationship' and not closed(data):
                due=next((integer(data.get(k)) for k in ('planned_marriage_global_day','suggested_marriage_global_day','marriage_global_day','start_global_day') if integer(data.get(k)) is not None),row.global_day)
                if due is not None and due>=day and ('marri' in str(data.get('type','')).casefold() or 'court' in str(data.get('type','')).casefold()):
                    add('Marriage: '+row.label,due,75,'/p/relationships#marriage-dates')
            elif row.kind=='game_candidate' and str(data.get('status'))=='pending':
                add('Review: '+row.label,integer(row.global_day,day),55,'/p/automation#candidate-'+row.id)
            elif row.kind=='task' and data.get('feature')=='recovery' and not closed(data):
                add('Recovery: '+row.label,integer(data.get('due_global_day'),day),40,'/p/family-projects#recovery')
            elif row.kind=='task' and data.get('feature') in {'heritage_routine_task','heritage_thread','heritage_commitment'} and not closed(data):
                if data.get('status') in {'Open','Active'}:
                    target='seasonal-routines' if data['feature']=='heritage_routine_task' else 'story-threads'
                    add(row.label,integer(data.get('due_global_day'),day),45 if data.get('priority')=='High' else 30,'/p/'+target+'#item-'+row.id)
        for sim in living:
            birth=integer(sim.data.get('birth_global_day'),sim.global_day)
            if birth is None: continue
            age=day-birth
            next_birthday=birth+(max(0,age)//year_days+1)*year_days
            if age>0 and age%year_days==0: next_birthday=day
            add('Birthday: '+sim.label,next_birthday,50,'/sims/'+sim.id)
            next_stage=next(((name,birth+threshold) for name,threshold in insights.life_stages(save) if threshold>0 and threshold>=age),None)
            if next_stage: add(sim.label+' becomes '+next_stage[0],next_stage[1],60,'/sims/'+sim.id)
        handover=next((r for r in sorted(rotations,key=lambda r:(r.global_day,r.created_at),reverse=True) if r.data.get('feature')=='handover'),None)
        elapsed=day-last.global_day if last else None
        open_stories=[r for r in owned if r.data.get('feature') in {'ambition','secret'} and not closed(r.data)]
        if open_stories and (elapsed is None or elapsed>=year_days):
            reasons.append({'label':f'{len(open_stories)} unfinished family storyline(s)','day':day,'score':30,'href':'/p/family-projects'})
        changes=[]
        if handover:
            baseline=handover.data.get('baseline',{})
            for row in owned:
                if row.kind in {'play_rotation','session_journal'}: continue
                if row.global_day is not None and row.global_day>day: continue
                if row.id not in baseline or baseline[row.id]!=handover_value(row): changes.append(row)
            changes.sort(key=lambda r:(r.global_day or 0,r.updated_at),reverse=True)
        reasons.sort(key=lambda r:(-r['score'],r['day'],r['label']))
        result.append({'home':home,'living':len(living),'last':last,'handover':handover,
                       'reasons':reasons,'changes':changes[:12], 'change_count':len(changes),
                       'score':sum(r['score'] for r in reasons)+min(elapsed or (15 if last is None else 0),30)})
    result.sort(key=lambda r:(-r['score'],r['last'].global_day if r['last'] else -1,r['home'].label.casefold()))
    return result


METRICS={'manual':'Player-confirmed progress','wealth':'Known household funds',
         'descendants':'Living descendants of a founder','occult':'Living occult descendants',
         'generations':'Generations descended from a founder','marriages':'Recorded family marriages',
         'heirloom_years':'Years a recorded heirloom has been kept','event_survivors':'Recorded event survivors'}
FEATURES={'ambition':('Household ambitions','task'),'secret':('Secrets & knowledge','note'),
          'recovery':('Event recovery','task'),'achievement':('Achievements','task')}
CONSEQUENCES=('Displacement','Debt','Reduced resources','Guardianship needs','Rebuilding')

def descendants(rows,founder):
    sims={r.id:r for r in rows if r.kind=='sim'}; found=set(); frontier={founder} if founder in sims else set()
    depth=0
    while frontier:
        children={s.id for s in sims.values() if s.id!=founder and s.id not in found and
                  (set(str(v) for v in (s.data.get('parent_ids') or [])) | {s.data.get('mother_id'),s.data.get('father_id')}) & frontier}
        if not children: break
        found.update(children);frontier=children;depth+=1
    return found,depth

def known_wealth(home,rows,day=None):
    entries=sorted((r for r in rows if r.kind=='economy_entry' and r.data.get('household_id')==home.id and
                    integer(r.data.get('balance')) is not None and (day is None or r.global_day is None or r.global_day<=day)),key=lambda r:(r.global_day or 0,r.updated_at),reverse=True)
    if entries: return integer(entries[0].data['balance'])
    for key in ('last_game_funds','household_funds','funds','wealth','balance'):
        if integer(home.data.get(key)) is not None: return integer(home.data[key])
    values={integer(s.data.get('last_household_funds')) for s in rows if s.kind=='sim' and home_id(s)==home.id}
    values.discard(None)
    return next(iter(values)) if len(values)==1 else None

def metric_value(data,rows,save):
    rows=[r for r in rows if r.kind!='sim' or integer(r.data.get('birth_global_day'),integer(r.global_day,save.global_day))<=save.global_day]
    metric=data.get('metric','manual'); founder=data.get('founder_id')
    family,depth=descendants(rows,founder)
    if metric=='manual': return max(0,integer(data.get('progress'),0))
    if metric in {'descendants','occult','generations'}:
        if not founder or not any(r.id==founder and r.kind=='sim' for r in rows): return None
        if metric=='generations': return depth+1
        living=[r for r in rows if r.id in family and advanced.living(r,save.global_day)]
        return len(living) if metric=='descendants' else sum(bool(occult_rules.sim_occult_types(r.data)) for r in living)
    home=next((r for r in rows if r.id==data.get('household_id') and r.kind=='household'),None)
    if metric=='wealth': return known_wealth(home,rows,save.global_day) if home else None
    members=family|{founder} if founder else {r.id for r in rows if r.kind=='sim' and (not data.get('household_id') or home_id(r)==data.get('household_id'))}
    if metric=='marriages':
        return sum(r.kind=='relationship' and r.global_day is not None and r.global_day<=save.global_day and
                   (bool(r.data.get('legally_married')) or 'marri' in str(r.data.get('type','')).casefold()) and
                   bool({r.data.get('partner1_id'),r.data.get('partner2_id')} & members) for r in rows)
    if metric=='heirloom_years':
        item=next((r for r in rows if r.id==data.get('evidence_id') and r.kind=='heirloom'),None)
        start=integer(item.data.get('acquired_global_day'),integer(item.global_day)) if item else None
        return max(0,save.global_day-start)//max(1,save.days_per_year) if start is not None else None
    if metric=='event_survivors':
        event=next((r for r in rows if r.id==data.get('evidence_id') and r.kind=='event'),None)
        end=integer(event.data.get('end_global_day')) if event else None
        if end is None or end>save.global_day: return None
        affected={r.data.get('sim_id') for r in rows if r.kind=='roll' and r.data.get('event_id')==event.id and r.data.get('completed')}
        if not affected: return None
        return sum(r.kind=='sim' and r.id in affected & members and advanced.living(r,end) for r in rows)
    return None

def project_views(rows,save):
    rows=active(rows);result={key:[] for key in FEATURES}
    for row in rows:
        feature=row.data.get('feature')
        if feature not in result: continue
        data=row.data;value=metric_value(data,rows,save) if feature in {'ambition','achievement'} and data.get('enabled',True) else None
        target=max(1,integer(data.get('target'),1))
        result[feature].append({'row':row,'value':value,'target':target,'met':value is not None and value>=target,
                                'percent':min(100,max(0,round(100*value/target))) if value is not None else None})
    for group in result.values(): group.sort(key=lambda p:(closed(p['row'].data),p['row'].label.casefold()))
    return result


ACCURACY_FIELDS={'occupation':('career','game_career','occupation','trade'), 'education':('education','game_education','game_school','school','institution','path','education_type'),
                 'marriage':('arrangement','marriage_arrangement','type'), 'practice':('practices','household_practices','notes')}

def historical_checks(rows,save):
    year=advanced.year_for(save,save.global_day); selected=set((save.settings or {}).get('selected_rule_packs') or [])
    selected.add((save.settings or {}).get('core_ruleset_id','severaludo'))
    constraints=[r for r in rows if r.data.get('feature')=='accuracy' and r.data.get('enabled',True) and
                 (not r.data.get('rule_pack') or r.data['rule_pack'] in selected)]
    overrides={r.data.get('warning_key') for r in rows if r.data.get('feature')=='accuracy_override' and not r.deleted}
    warnings=[]
    for rule in constraints:
        data=rule.data; before=integer(data.get('allowed_from_year'));after=integer(data.get('allowed_until_year'))
        if (before is None or year>=before) and (after is None or year<=after): continue
        category=data.get('category'); field_keys=ACCURACY_FIELDS.get(category,())
        kinds={'occupation':{'sim'},'education':{'sim','education_plan'},'marriage':{'relationship','dowry_plan'},'practice':{'household'}}.get(category,set())
        needle=str(data.get('match_text','')).strip().casefold()
        if not needle: continue
        for record in rows:
            if record.kind not in kinds: continue
            if record.kind in {'education_plan','dowry_plan','relationship'} and closed(record.data): continue
            if record.kind=='sim' and not advanced.living(record,save.global_day): continue
            if record.global_day is not None and record.global_day>save.global_day: continue
            matched='; '.join(str(record.data.get(key) or '') for key in field_keys)
            if not re.search(r'(?<!\w)'+re.escape(needle)+r'(?!\w)',matched.casefold()): continue
            key=hashlib.sha256(f'{record.id}:{rule.id}:{rule.version}:{matched}'.encode()).hexdigest()[:32]
            warnings.append({'key':key,'record':record,'rule':rule,'matched':matched,'overridden':key in overrides})
    return warnings


PERSPECTIVES={'spouse':'I find myself thinking about the life we share and what these changes ask of us.',
 'parent':'My thoughts turn to those I have raised, and to what I can still do for them.',
 'heir':'I wonder what I will inherit: not only possessions, but promises and unfinished duties.',
 'rival':'I cannot ignore how these changes may alter the balance between us.',
 'chronicler':'I want to preserve the shape of these days before memory softens their edges.'}

def eligible_writing_fact(row,save):
    if row.kind not in {'death','event_result','migration','relationship','pregnancy','game_history','drama_scene'}: return False
    if row.deleted or row.global_day is None or row.global_day>save.global_day: return False
    if str(row.data.get('status','')).casefold() in {'pending','planned','scheduled','proposed','dismissed','cancelled','canceled'}: return False
    if row.kind=='relationship' and integer(row.data.get('planned_marriage_global_day'),0)>save.global_day: return False
    return True


def correspondence(save,author,recipient,perspective,kind,subject,facts):
    # Fact lines are explicitly selected records. Emotions are labelled fiction.
    fact_lines=[f'GD {r.global_day}: {r.label}' for r in facts]
    opener=f'Dear {recipient.label if recipient else "reader"},' if kind=='letter' else 'From my private journal,'
    body=f'{advanced.year_for(save,save.global_day)}\n\n{opener}\n\n'+PERSPECTIVES[perspective]
    body+='\n\nThe recorded news is:\n'+'\n'.join('• '+line for line in fact_lines)
    themes={'death':'loss and the space someone leaves behind','pregnancy':'a family preparing for change',
            'migration':'leaving the familiar for another home','relationship':'the obligations and hopes between people',
            'event_result':'the aftermath of a turning point','game_history':'the small changes that become a life',
            'drama_scene':'the choices we make and the stories we tell about them'}
    reflections={'spouse':'I wish we could speak honestly about {theme}, without having to find every answer tonight.',
                 'parent':'When I think of {theme}, I wonder what the next generation will need from me.',
                 'heir':'The thought of {theme} makes the responsibilities ahead feel less distant.',
                 'rival':'Considering {theme}, I find it difficult to separate sympathy from my own interests.',
                 'chronicler':'Writing of {theme}, I am reminded that a date alone cannot hold every feeling.'}
    for kind_seen in dict.fromkeys(r.kind for r in facts):
        body+='\n\n'+reflections[perspective].format(theme=themes[kind_seen])
    body+='\n\nI do not yet know which of these moments will matter most. For now, I will carry them with me.'
    body+='\n\n— '+author.label
    return {'source':'Family correspondence','feature':'perspective_writing','fictional_narration':True,
            'author_id':author.id,'recipient_id':recipient.id if recipient else None,'perspective':perspective,
            'writing_kind':kind,'subject':subject,'body':body,'fact_record_ids':[r.id for r in facts],
            'confirmed_facts':fact_lines,'narration_label':'Fictional voice and feelings; selected titles and dates are confirmed tracker records. A recorded drama choice is not proof of an in-game event.'}


def branch_metrics(payload):
    rows=[SimpleNamespace(**{**r,'version':1,'updated_at':None}) for r in payload['records'] if not r.get('deleted')]
    day=payload['global_day'];sims=[r for r in rows if r.kind=='sim' and r.id in set(payload['member_sim_ids']) and integer(r.data.get('birth_global_day'),integer(r.global_day,day))<=day]
    ids={r.id for r in sims}; living=[r for r in sims if advanced.living(r,day)]
    homes=[r for r in rows if r.kind=='household' and r.id in {home_id(s) for s in sims}]
    balances=[known_wealth(h,rows,day) for h in homes]
    roots=[s.id for s in sims if not (({s.data.get('mother_id'),s.data.get('father_id')} | set(s.data.get('parent_ids') or [])) & ids)]
    desc=set().union(*(descendants(sims,root)[0] for root in roots)) if roots else set()
    inherited=[]
    for sim in sims:
        parents=[p for p in sims if p.id in ({sim.data.get('mother_id'),sim.data.get('father_id')} | set(sim.data.get('parent_ids') or []))]
        shared=set(occult_rules.sim_occult_types(sim.data)) & set().union(*(set(occult_rules.sim_occult_types(p.data)) for p in parents)) if parents else set()
        if shared: inherited.append(f'{sim.label}: '+', '.join(sorted(shared)))
    marriages=[r for r in rows if r.kind=='relationship' and r.global_day is not None and r.global_day<=day and
               (r.data.get('legally_married') or 'marri' in str(r.data.get('type','')).casefold()) and
               {r.data.get('partner1_id'),r.data.get('partner2_id')} & ids]
    turns=sorted((r for r in rows if r.kind in {'death','migration','drama_scene','story_entry','event_result','relationship'} and
                  r.global_day is not None and r.global_day<=day),key=lambda r:(r.global_day,r.label),reverse=True)[:12]
    inheritance=[r for r in rows if r.kind=='roll' and r.data.get('completed') and 'inherit' in str(r.data.get('roll_type') or r.label).casefold() and r.global_day is not None and r.global_day<=day]
    return {'day':day,'members':len(sims),'descendants':len(desc),'living':len(living),'dead':len(sims)-len(living),
            'survival':round(100*len(living)/len(sims),1) if sims else None,
            'wealth':sum(balances) if balances and all(v is not None for v in balances) else None,
            'known_balances':sum(v is not None for v in balances),'homes':len(homes),'marriages':len(marriages),
            'occults':Counter(t for s in sims for t in occult_rules.sim_occult_types(s.data)),
            'shared_occults':inherited,'inheritance_outcomes':inheritance,'turning_points':turns}
