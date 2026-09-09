"""Small, allowlisted evidence snapshots, not copies of private game reports."""
from functools import wraps
from datetime import datetime,timezone
import re
from .models import Record,ChronicleSave

RULE_FIELDS=('die','bad_results','result_rules','trigger_results','roll_type','rule_key','source','source_url',
             'age_min','age_max','min_age','max_age','minimum_age','maximum_age','sex','country','location',
             'occult','frequency','repeat_interval','start_year','end_year','notes','conditions','eligibility',
             'age_days','standard_age_days','age_days_calendar_days_per_year','rule_text','core_ruleset_id','rule_pack_id')
VALUE_FIELDS=('roll_type','source','source_key','due_global_day','die','bad_results','result_rules','trigger_results',
              'actual','outcome','completed','rule_context','action','status','automatic_detection','conception_global_day',
              'due_global_day','delivery_global_day','death_global_day','birth_global_day','lifecycle_age_days',
              'aging_chart','occult_aging_mode','occult_types_at_scheduling','age_calendar_days_per_year',
              'maternal_baby_index','maternal_babies_delivered','failure_is_lethal','nonlethal',
              'country','location','scope','event_phase','eligibility','trigger_reason')

def simple(data,fields):
    result={}
    for key in fields:
        value=(data or {}).get(key)
        if value is None: continue
        if isinstance(value,(str,int,float,bool)):
            result[key]=value[:2000] if isinstance(value,str) else value
        elif isinstance(value,list) and all(isinstance(v,(str,int,float,bool)) for v in value): result[key]=value[:40]
    return result

def with_report(function):
    @wraps(function)
    def wrapped(session,link,report,*args,**kwargs):
        previous=session.info.get('play_trigger_report')
        session.info['play_trigger_report']={'save_id':link.save_id,
            **simple(report,('report_id','report_sequence','game_day','hour','minute','second','report_kind','clock_sync_version')),
            'received_at_utc':datetime.now(timezone.utc).isoformat()}
        try: return function(session,link,report,*args,**kwargs)
        finally:
            if previous is None: session.info.pop('play_trigger_report',None)
            else: session.info['play_trigger_report']=previous
    return wrapped

def capture(session,record,operation,base_version=0):
    if operation!='upsert' or record.deleted or (record.data or {}).get('infinite_frozen'): return
    report=session.info.get('play_trigger_report')
    if report and report.get('save_id')!=record.save_id: report=None
    reviewed=session.info.get('play_accepted_detection')
    if reviewed and reviewed.get('save_id')!=record.save_id: reviewed=None
    if not report and reviewed: report=reviewed.get('report')
    if record.kind not in {'roll','game_candidate'} and (not (report or reviewed) or record.kind not in {'sim','pregnancy','illness','relationship','death','game_history','household'}): return
    data=dict(record.data or {})
    # Never relabel an old record's first edit as original creation evidence.
    if not data.get('why_evidence') and base_version==0:
        save=session.get(ChronicleSave,record.save_id)
        if not save: return
        evidence={'captured_at_utc':datetime.now(timezone.utc).isoformat(),'tracker_global_day':save.global_day,
                  'record_global_day':record.global_day,
                  'days_per_year':save.days_per_year,'start_year':save.start_year,
                  'core_ruleset_id':(save.settings or {}).get('core_ruleset_id','severaludo'),
                  'selected_rule_packs':list((save.settings or {}).get('selected_rule_packs') or []),
                  'values':simple(data,VALUE_FIELDS),'report':report}
        sid=data.get('sim_id') or data.get('mother_id')
        sim=session.get(Record,sid) if sid else record if record.kind=='sim' else None
        if sim and sim.save_id==record.save_id and sim.kind=='sim':
            evidence['sim']={'id':sim.id,'name':sim.label,**simple(sim.data,('birth_global_day','death_global_day','sex','species_occult','game_occult_types','country','current_household_id'))}
        references=[data.get(field) for field in ('source_rule_id','source_id','occult_rule_id','event_id','event_rule_id','planner_rule_id','origin_roll_id')]
        references+=re.findall(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])',str(data.get('source') or ''))[:6]
        for rid in dict.fromkeys(value for value in references if isinstance(value,str) and value):
            rule=session.get(Record,rid)
            if rule and rule.save_id==record.save_id and rule.kind in {'roll_rule','planner_rule','occult_rule','event','event_rule','roll','addon_rule'}:
                evidence.setdefault('sources',[]).append({'id':rule.id,'kind':rule.kind,'name':rule.label,'version':rule.version,'values':simple(rule.data,RULE_FIELDS+('actual','outcome'))})
        data['why_evidence']=evidence
    if report: data['last_report_evidence']=dict(report)
    if reviewed: data['last_review_evidence']=dict(reviewed)
    record.data=data

def explain(record):
    data=record.data or {};evidence=data.get('why_evidence') or {};payload=data.get('payload') or {}
    report=evidence.get('report') or simple(payload,('report_id','report_sequence','detected_game_day','detected_game_hour','detected_game_minute'))
    calculation=[];values=evidence.get('values') or {};sim=evidence.get('sim') or {}
    birth=sim.get('birth_global_day');due=values.get('due_global_day',evidence.get('record_global_day',record.global_day));dpy=evidence.get('days_per_year')
    if isinstance(birth,int) and isinstance(due,int) and isinstance(dpy,int) and dpy>0:
        age=max(0,due-birth);years,days=divmod(age,dpy)
        calculation.append(f'At the scheduled day: GD {due} − birth GD {birth} = {age} days old ({years} years + {days} days at {dpy} days/year).')
    if dpy: calculation.append(f'This evidence used {dpy}-day years. The four-day baseline scale was {dpy/4:g}×; individual rule exceptions still apply.')
    if values.get('lifecycle_age_days') is not None:
        calculation.append(f"Configured lifecycle threshold: {values['lifecycle_age_days']} days. Chart: {values.get('aging_chart','not retained')}; occult adjustment: {values.get('occult_aging_mode','not retained')}.")
    return {'record':record,'evidence':evidence,'values':simple(data,VALUE_FIELDS),'report':report,
            'latest_report':data.get('last_report_evidence'),'calculation':calculation,
            'review':data.get('last_review_evidence'),
            'payload':simple(payload,VALUE_FIELDS+('detected_game_day','detected_game_hour','detected_game_minute','reason','provider'))}
