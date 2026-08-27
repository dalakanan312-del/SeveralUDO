from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChronicleSave, Record


PACK_ID = "harry_potter_decades"
PACK_NAME = "Harry Potter Decades"

# code, title, category, default, die, trigger, rule text
_ROWS = (
    ("HP-01","Pureblood Family Size","Fertility and family planning","Optional","","Pureblood couple considers intentional pregnancy","Stop intentional attempts after at least one living grandchild; resume if the only grandchild dies. Accidental and existing pregnancies remain possible. Weasley families are exempt."),
    ("HP-02","Magical Marriage Ages","Marriage eligibility","Optional","","Marriage rolls begin","Witches become eligible at 17 and Wizards at 18, replacing ordinary minimum ages only for eligible magical Sims. A Wizard's Muggle wife may use normal cultural rules."),
    ("HP-03","Magical Marriage Age Gaps","Marriage matching","Optional","","Two magical Sims are matched","0-5 years is standard; 6-10 uncommon but allowed; 11+ requires a recorded reason. A Wizard-Muggle marriage may follow ordinary Muggle patterns."),
    ("HP-04","Blood Status","Magical identity","Recommended","","Birth or ancestry discovery","Assign Pureblood from fully magical recent ancestry, Half-Blood from mixed magical/Muggle ancestry, Muggle-Born to a magical child of two Muggles, and Muggle to a non-magical child. Squibs retain magical ancestry."),
    ("HP-05","Magical and Squib Birth Rolls","Birth outcome","Recommended","d20","Every birth","Two Muggle parents: 1 Muggle-Born magical child, 2-20 Muggle. At least one magical parent: 1 Squib, 2-20 Witch or Wizard. Roll each baby separately; first result is final."),
    ("HP-06","Squib Discovery","Childhood development","Recommended","","Recorded Squib reaches age 7","Keep Squib status hidden until 7, then make it public, apply inheritance and education rules, and stop accidental-magic rolls. Ordinary family, property, work, marriage, and parenting rights remain."),
    ("HP-07","Squib Inheritance","Inheritance","Optional","","Heirs change","A Squib normally yields magical-family succession to the next eligible magical child, but may inherit money, ordinary property, businesses, gifts, or the estate when no eligible magical descendant exists."),
    ("HP-08","Magical Family Heirs","Inheritance","Optional","","Birth, death, discovery, marriage, or disinheritance","Default to the oldest eligible magical child without sex preference. Black, Malfoy, Gaunt, and explicitly traditional houses may prefer magical sons and their descendants."),
    ("HP-09","Pureblood Spouse Preference","Marriage matching","Optional","","Pureblood family evaluates spouse","Preference order: Pureblood; established-family Half-Blood; Half-Blood; Muggle-Born; Muggle. Strict purists may refuse, withdraw support, alter succession, disinherit, or sever contact—record one chosen consequence."),
    ("HP-10","Pureblood Clothing Delay","Clothing and culture","Optional","","1490 onward at each decade","Pureblood ordinary clothing generally references at least two decades earlier than Muggle fashion, with exceptions for children, Muggle exposure, Muggle-facing work, or deliberate blending."),
    ("HP-11","Magical Higher-Order Multiples","Pregnancy and birth","Optional","d20","Eligible magical-family Witch rolls triplets","After triplets, 1 upgrades to quadruplets; repeat after each upgrade for quintuplets then sextuplets, stopping there. Muggle-born, Squib, and Muggle pregnant Sims are excluded."),
    ("HP-12","Hogwarts Attendance","Education","Recommended","","Magical child in Britain or Ireland reaches 11","Eligible magical students may attend Hogwarts from 11 to about 17. Squibs cannot enroll as regular magical students; students at another magical school receive no Hogwarts House."),
    ("HP-13","Hogwarts House Assignment","Education","Optional","d4","Eligible Sim begins Hogwarts","1 Hufflepuff; 2 Ravenclaw; 3 Slytherin; 4 Gryffindor. First result is final and applies only to Hogwarts students."),
    ("HP-14","Accidental Magic","Childhood event","Optional","d6","Annually ages 3-10","1 causes noticeable accidental magic; 2-6 no major event. A Muggle witness during persecution or secrecy triggers the applicable exposure roll. Squibs are excluded."),
    ("HP-15","American Muggle-Born Obscurial Risk","Childhood danger","Optional","dynamic","Magic appears in an American Muggle-born child","On d6, supportive succeeds on 1, fearful on 1-2, anti-magic on 1-3, violent suppression on 1-4. Each Obscurial year, 1 on d4 dies. Safe magical placement may end mortality. Squib-parent homes are excluded."),
    ("HP-16","Early-Generation Remarriage Restriction","Marriage","Optional","","Spouse dies","Generations 1-4 cannot enter another recognized marriage; Generation 5 onward uses normal remarriage rules. Other household and permitted relationship arrangements remain possible."),
    ("HP-17","Professional Quidditch Income","Career and income","Optional","","Annually while active professional adult","Add §5,000 yearly to ordinary income. School, informal, recreational, and retired players are excluded unless retirement leads to another paid Quidditch role."),
    ("HP-18","Witch Hunt Period","Historical exposure","Optional","dynamic","1300-1691 magical household among Muggles","Use ordinary SeveralUDO annual Witch Hunt rules. Repeated public exposure causes two accusation rolls; either success discovers the household. Before 1300 use only story-supported individual persecution."),
    ("HP-19","International Statute of Wizarding Secrecy","Historical exposure","Recommended","d6","1692 onward after exposure to unrelated Muggles","1-2 brings authority involvement; 3-6 no formal action. Escalate warnings, fines, confiscation, memory work, relocation, restrictions, or imprisonment after repeated violations. Replaces annual Witch Hunts for concealed magical households."),
)

DEPENDENCIES = {"HP-06":("HP-05",),"HP-07":("HP-05","HP-06"),"HP-13":("HP-12",),"HP-15":("HP-05",),"HP-19":("HP-18",)}
MODULES = tuple({"code":code,"name":name,"category":category,"default":default,"die":die,"trigger":trigger,"rule_text":text,"dependencies":DEPENDENCIES.get(code,())} for code,name,category,default,die,trigger,text in _ROWS)

EVENT_TABLES = (
    {"code":"HP-T01","name":"Annual Wizarding Household Event","category":"Annual event table","default":"Optional","die":"d20","trigger":"Once per year for each active magical household","rule_text":"1 accidental magic witnessed; 2 magical illness; 3 dangerous object; 4 creature incident; 5 Ministry investigation; 6 blood-status dispute; 7 school event; 8 Quidditch; 9 unusual birth; 10 family secret; 11 forbidden experiment; 12 Muggle relative discovers magic; 13 business; 14 visitor; 15 heir dispute; 16 Dark Wizard; 17 helpful discovery; 18 friendship/courtship/betrothal; 19-20 no major event."},
    {"code":"HP-T02","name":"Witch-Hunt Consequence","category":"1300-1691 event table","default":"Optional","die":"d8","trigger":"Household fails a Witch Hunt roll","rule_text":"1 public accusation; 2 magical object found; 3 accidental magic witnessed; 4 healer accused; 5 Muggle relative accused; 6 household flees; 7 arrest/confiscation; 8 magical concealment. Resolve injury and death normally."},
    {"code":"HP-T03","name":"Secrecy Violation Consequence","category":"1692 onward event table","default":"Optional","die":"d8","trigger":"Magical authorities intervene","rule_text":"1 warning; 2 fine; 3 memory modification; 4 object confiscated; 5 job/school privilege lost; 6 observation; 7 forced relocation; 8 trial/imprisonment. Increase severity for repeat violations."},
    {"code":"HP-T04","name":"Wizarding-War Household Event","category":"1970-1981 or 1995-1998","default":"Optional","die":"d12","trigger":"Annually during an active Wizarding War","rule_text":"1 death; 2 imprisonment/capture; 3 disappearance; 4 property attack; 5 ordered collaboration; 6 resistance; 7 hiding; 8 betrayal; 9 protects target; 10 intelligence; 11 avoids major harm; 12 gains influence with possible later consequences."},
    {"code":"HP-T05","name":"Hogwarts Annual Event","category":"School event table","default":"Optional","die":"d12","trigger":"Each enrolled Story Sim annually","rule_text":"1 serious accident; 2 discipline; 3 House rivalry; 4 Forbidden Forest; 5 secret passage/object; 6 Quidditch; 7 academic distinction; 8 friendship/romance; 9 professor conflict; 10 creature encounter; 11 family interruption; 12 uneventful. Major canon school events replace it when appropriate."},
)
ALL_RULES = MODULES + EVENT_TABLES

TIMELINE_MODES = (
    ("canon","Canon Timeline","Fixed Wizarding events occur in their established years."),
    ("canon_compatible","Canon-Compatible Timeline","Major dates remain fixed, but original families determine their own outcomes."),
    ("alternate","Alternate Timeline","Canon events are prompts and may be prevented, delayed, or changed; record the new date and outcome."),
)

TIMELINE = (
    (-9999,-501,"Prehistoric Magic","Instinctive magic, rare wands, visible creatures, no blood-status categories, and no Hogwarts rules."),
    (-500,1,"Ancient Wandmaking and Classical Magic","Early wands, apprenticeships, magical families, scholars, governments, and ingredient trade."),
    (1,499,"Roman and Post-Roman Magic","Migration, regional law, artifacts, healers, and increasingly local government."),
    (500,989,"Early Medieval Wizarding Communities","Family education, apprenticeships, variable wand access, and folk interpretations of accidental magic."),
    (990,990,"Hogwarts Founded","Enable Hogwarts attendance, Houses, formal education, and school-based relationships."),
    (991,1049,"First Hogwarts Generations","Founders teach directly; attendance and traditions remain uneven while blood-status conflict develops."),
    (1050,1149,"Earliest Quidditch","Informal broom games, injuries, regional traditions, and growing intercommunity play."),
    (1150,1268,"Quidditch Spreads and Institutions Expand","Organized teams, travel, courts, hereditary trades, and stronger Hogwarts traditions."),
    (1269,1299,"Snidget Quidditch and Rising Suspicion","Snidget matches continue while magical families increasingly conceal public practices."),
    (1300,1399,"Early Witch-Hunt Period","Enable annual Witch Hunts, exposure consequences, relocation, and concealment."),
    (1400,1490,"Major Witch-Hunt Era","Public spells, flying, creatures, healing, accidental magic, and objects raise exposure risk."),
    (1490,1691,"Peak Witch-Hunt Pressure","Pureblood clothing delay begins; persecution, blood-purism, confiscation, and flight intensify."),
    (1689,1691,"Statute Negotiations","Wizarding representatives debate concealment, separation, and hidden settlements."),
    (1692,1692,"International Statute and Salem Crisis","Annual Witch Hunts give way to actual Secrecy violations; American magical households face additional danger."),
    (1693,1706,"MACUSA and Early Secrecy Society","American magical government and formal secrecy enforcement expand."),
    (1707,1749,"British Ministry Era","Enable Ministry, Auror, department, trial, creature, artifact, and political careers/events."),
    (1750,1799,"Standardized Quidditch and Mature Hidden Society","Professional careers, leagues, regulation, political influence, smuggling, and creature-rights conflict."),
    (1800,1880,"Industrial-Age Wizarding World","Rapid Muggle change widens the cultural divide and creates invention, inspection, and enchanted-object events."),
    (1881,1898,"Late Victorian Wizarding Era","International government, news, blood politics, professional Quidditch, schools, surveillance, and artifacts."),
    (1899,1913,"Dumbledore-Grindelwald Crisis and Rise","Enable Greater Good ideology, Hallows research, followers, radicalism, and Dark Wizard tracking."),
    (1914,1925,"World War and Interwar Instability","Real war, secrecy, healing, refugees, intervention debates, radical groups, Aurors, and Obscurial risk overlap."),
    (1926,1929,"New York Obscurial Crisis and Paris Rally","Enable American Obscurial risk, MACUSA emergency, Grindelwald infiltration, recruitment, propaganda, and division."),
    (1930,1945,"Grindelwald Expansion and Global War","Families choose resistance, collaboration, neutrality, or flight; 1945 brings Grindelwald's defeat and lasting reputation outcomes."),
    (1942,1945,"First Chamber Opening and Riddle's Departure","School attacks, investigation, a student death, a Horcrux in canon mode, and Riddle's graduation."),
    (1946,1969,"Riddle's Research and Voldemort's Emergence","Forbidden research, artifacts, disappearances, followers, recruitment, and intimidation grow."),
    (1970,1981,"First Wizarding War","Death Eaters, Order members, Aurors, persecution, capture, concealment, refugees, betrayal, prophecy children, and Voldemort's first defeat."),
    (1982,1990,"Uneasy Peace","Survivors hide collaboration, convictions are disputed, artifacts resurface, and war orphans discover their histories."),
    (1991,1994,"Early Harry Potter School Years","Stone, Chamber, Sirius, Dementors, werewolf discovery, Triwizard selection, World Cup, and Voldemort's return."),
    (1995,1996,"Hidden Second Wizarding War","Ministry denial, propaganda, inspection, secret defense, Azkaban escape, and the Ministry battle."),
    (1996,1998,"Open Second Wizarding War","Attacks, disappearances, occupation, registration, confiscation, resistance, refugees, and the Battle of Hogwarts."),
    (1998,2000,"Immediate Reconstruction","Trials, releases, reform, restored records, guardianship, property disputes, repairs, and delayed education."),
    (2001,2017,"Next Generation","Reputation consequences, institutional reform, more inter-status marriage, Hogwarts continuity, and accelerating Muggle technology."),
    (2018,9999,"Modern Wizarding Era","Original history may explore technology, secrecy, Azkaban reform, Squib inclusion, cooperation, politics, sport, war crimes, and artifacts."),
)


def year_label(year: int) -> str:
    year=int(year)
    return f"{abs(year):,} BCE" if year<0 else f"{year:,} CE"


def range_label(start: int, end: int) -> str:
    if start<=-9999:return f"Before {year_label(end)}"
    if end>=9999:return f"{year_label(start)} and later"
    return year_label(start) if start==end else f"{year_label(start)}–{year_label(end)}"


def _payload(rule: dict, enabled: bool, pack_enabled: bool) -> dict:
    return {**rule,"rule_key":rule["code"].lower().replace("-","_"),"rule_pack_id":PACK_ID,"rule_family":"Harry Potter Decades","source":"Harry Potter Decades optional rules","module_enabled":enabled,"pack_enabled":pack_enabled,"active":enabled and pack_enabled,"result_rules":rule["rule_text"],"auto_schedule":False}


def sync_pack(session: Session, save: ChronicleSave, selected: list[str]) -> int:
    from .domain import journal
    pack_enabled=PACK_ID in selected
    existing={str((r.data or {}).get("code") or ""):r for r in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="addon_rule",Record.deleted.is_(False))) if (r.data or {}).get("rule_pack_id")==PACK_ID}
    touched=[]
    for rule in ALL_RULES:
        record=existing.get(rule["code"])
        if record is None:
            if not pack_enabled: continue
            enabled=rule["default"]=="Recommended";record=Record(save_id=save.id,kind="addon_rule",label=f'{rule["code"]} — {rule["name"]}',data=_payload(rule,enabled,True));session.add(record);touched.append((record,0));continue
        data=dict(record.data or {});enabled=bool(data.get("module_enabled",rule["default"]=="Recommended"));desired={**data,"pack_enabled":pack_enabled,"active":pack_enabled and enabled}
        if desired!=data:
            base=record.version;record.data=desired;record.version+=1;touched.append((record,base))
    if touched:
        session.flush()
        for record,base in touched: journal(session,record,"upsert",base)
    return len(touched)


def set_module(session: Session, save: ChronicleSave, code: str, enabled: bool) -> Record|None:
    record=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="addon_rule",Record.deleted.is_(False),Record.data["code"].as_string()==code))
    if not record or (record.data or {}).get("rule_pack_id")!=PACK_ID:return None
    data=dict(record.data or {});data["module_enabled"]=enabled;data["active"]=enabled and bool(data.get("pack_enabled"));record.data=data;record.version+=1;return record
