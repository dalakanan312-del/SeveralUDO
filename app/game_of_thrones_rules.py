from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChronicleSave, Record

PACK_ID="game_of_thrones_decades"
PACK_NAME="Game of Thrones Decades"

# code, title, category, default, die, trigger, concise editable rule
_ROWS=(
 ("GOT-01","House Identity","Family identity","Recommended","","Noble founding family","Record House, region, words, sigil, religion, rank, wealth, ancestral seat, ruler, and heir. Legitimate descendants continue the House unless another rule changes it."),
 ("GOT-02","Social Rank","Social class","Recommended","","Every household","Ranks: Smallfolk; wealthy commoner/merchant; Landed Knight; Minor Noble; Major Noble; Great House; Royal House. Rank and wealth remain separate."),
 ("GOT-03","House Wealth and Property","Inheritance and wealth","Recommended","","Noble House","The ancestral seat and major property belong to the House and pass with the title. Track House treasury separately from personal property and younger-child provisions."),
 ("GOT-04","Male-Preference Primogeniture","Inheritance","Recommended outside Dorne","","Most Westerosi noble Houses","Legitimate sons and their descendants precede daughters and theirs; representation applies, so an elder deceased son's descendants precede a younger son. Bastards need legitimization."),
 ("GOT-05","Dornish Absolute Primogeniture","Inheritance","Recommended in Dorne","","Rhoynish Dornish House","Oldest legitimate child and descendants inherit regardless of sex. Replaces GOT-04 for the selected House."),
 ("GOT-06","Bastards and Regional Names","Legitimacy","Recommended","","Illegitimate highborn child","Bastards do not automatically take House name, title, seat, or succession. Recognized highborn surnames: Waters, Sand, Pyke, Snow, Flowers, Rivers, Storm, Stone, or Hill by region."),
 ("GOT-07","Legitimization","Succession","Optional","","Royal or sovereign decree","A recognized bastard may gain the House name, property, succession, or a cadet branch, without automatically displacing existing legitimate descendants."),
 ("GOT-08","House Extinction","Dynasty management","Recommended","","No apparent heir","Check descendants through daughters, maternal-name preservation, legitimized lines, cadet branches, and collateral relatives before marking Extinct."),
 ("GOT-09","Cadet Branches","Dynasty management","Optional","","Landed younger or collateral descendant","Record branch name, sigil, words, seat, and senior-House relationship. A cadet branch may inherit when the senior line ends."),
 ("GOT-10","Noble Marriage","Marriage","Recommended","","Noble or royal marriage","Marriage may serve alliance, wealth, land, claims, peace, prestige, troops, or heirs. Ordinary age and fertility rules continue."),
 ("GOT-11","Marriage Negotiations","Marriage matching","Optional","d6","Acceptance uncertain","1-3 accepted; 4-5 better terms demanded; 6 refused. Move one step for a major advantage or disadvantage."),
 ("GOT-12","Marriage Settlements","Marriage and wealth","Optional","","Marriage arranged","Record money, land, jewelry, military or political promises, debt relief, office, wardship, or claim recognition and transfer it on the agreed date."),
 ("GOT-13","Betrothal and Broken Betrothal","Marriage event","Optional","d6","Betrothal broken","1 accepted; 2-3 relationship damaged; 4-5 hostility; 6 feud begins and may activate GOT-36."),
 ("GOT-14","Marriage House Name","Dynasty management","Recommended","","Children of marriage","Children normally follow the father's House. A ruling heiress may require her spouse and/or heirs to preserve her House; record the agreement before births."),
 ("GOT-15","Elopement","Marriage event","Optional","d6","Noble elopes","1 accepted; 2 anger; 3 support lost; 4 disinherited; 5 exiled; 6 annulment/forced separation attempted."),
 ("GOT-16","Widowhood and Mourning","Marriage and death","Optional","","Spouse dies","Choose husband's House, birth family, regency, separate property, remarriage, or religion. Suggested mourning: relative 3 months, close kin 6, spouse 1 year, monarch 3-12 months."),
 ("GOT-17","Annulment and Unproductive Marriage","Marriage","Optional","d6","Five years without living child","1-2 accepted; 3 Maester/healer; 4 pressure; 5 annulment; 6 replacement marriage or alternate heir. Use historically appropriate grounds."),
 ("GOT-18","Heir and Spare","Fertility","Optional","","Noble or royal family planning","Continue intentional pregnancies until one living legal heir and one additional eligible child; male-preference Houses may continue seeking a son."),
 ("GOT-19","Posthumous Children","Succession","Recommended","","Titled ruler dies with possibly pregnant widow","Delay final succession. A legitimate posthumous child keeps normal rights and may replace a temporary heir or trigger crisis."),
 ("GOT-20","Adultery and Discovery","Relationship event","Optional","d10","Affair","Ordinary affair discovered on 1-3; royalty, heir, or scrutiny on 1-5. Consequences depend on rank, politics, religion, parentage, and settlements."),
 ("GOT-21","Questioned Parentage","Legitimacy and succession","Optional","","Noble parentage disputed","Track biological, legal, and public parentage separately. Create opposing factions, a political consequence, and a succession crisis if the child is heir."),
 ("GOT-22","Fostering, Wards and Hostages","Childhood placement","Optional","","Highborn child placed elsewhere","Record foster, ward, page, squire, or hostage placement, host House, and purpose. Rebellion may endanger a political hostage."),
 ("GOT-23","Court and Offices","Careers and politics","Optional","","Major court","Track court, office, and appointing ruler for family, wards, hostages, knights, attendants, Maesters, clergy, advisers, guards, servants, entertainers, and political guests."),
 ("GOT-24","Court Intrigue","Annual court event","Optional","d12","Annual major or royal court","1 assassination; 2 affair; 3 embezzlement; 4 spy; 5 alliance; 6 marriage plot; 7 rivalry; 8 blackmail; 9-12 no scandal."),
 ("GOT-25","Royal Favorite","Court politics","Optional","d10","Once per reign","1-2 creates a powerful Favorite; 3-10 none. An extremely powerful Favorite faces annual backlash on 1 on d10."),
 ("GOT-26","Martial Ability","Combat","Optional","d6","Serious training begins","1 Poor; 2-3 Average; 4-5 Skilled; 6 Exceptional. Exceptional grants one final reroll on failed personal combat per event."),
 ("GOT-27","Knighthood","Career and status","Optional","","Page/squire progression or exceptional service","Page to Squire to Knight; commoners may earn it. Ser grants neither automatic land, nobility, wealth, nor loss of inheritance."),
 ("GOT-28","Tourneys","Public event","Optional","dynamic","Dangerous tourney contest","Each participant: 1 on d20 accident. Accident d6: 1 death; 2 disability; 3-4 serious injury; 5-6 recovery. Winners gain plausible rewards or rivalries."),
 ("GOT-29","Duels and Trial by Combat","Combat and law","Optional","d6","Defeated combatant","1 death; 2 disability; 3-4 serious injury; 5-6 no permanent injury. A recognized Trial by Combat legally decides the case."),
 ("GOT-30","Battle Outcomes","War","Optional","d6","Named participant after major battle","1-2 death; 3 captured; 4 seriously wounded; 5 missing; 6 returns. Wounded Sims use GOT-31."),
 ("GOT-31","Battle Injury","Injury","Optional","d10","Serious battle wound","1 eye; 2 hand/arm; 3 foot/leg; 4 mobility; 5 facial scarring; 6 other scarring; 7 chronic pain; 8-10 recovery. Disability does not remove succession."),
 ("GOT-32","Prisoners and Ransom","War aftermath","Optional","","Noble captured","Record captor, ransom, and disposition: ransom, exchange, hostage, release, execution, or escape. Suggested values scale from §1,000 Landed Knight to §20,000+ Royal."),
 ("GOT-33","Missing Sims","War aftermath","Optional","d6","Annual while missing","1 confirmed dead; 2 returns; 3 found prisoner; 4-6 remains missing. A returned heir may cause crisis."),
 ("GOT-34","Calling the Banners","Military alliance","Optional","d6","Liege calls bannerman","Normal: 1-5 answers, 6 refuses. Serious grievance: 1-3 answers, 4-5 refuses, 6 joins enemy."),
 ("GOT-35","Calling Allies","Military alliance","Optional","d6","Alliance called","Ordinary: 1-4 honors, 5 refuses, 6 neutral/enemy. Marriage: 1-5 honors, 6 refuses."),
 ("GOT-36","House Feud","Annual family conflict","Optional","d10","Annual active feud","1 your member murdered; 2 rival murdered; 3 property attack; 4 duel; 5 abduction; 6 retaliation; 7 reconciliation; 8-10 no escalation."),
 ("GOT-37","Blood Feud Response","Family conflict","Optional","d6","Family member murdered","1-2 legal resolution; 3-4 compensation; 5 exile/severe punishment; 6 demand blood and continue/begin feud."),
 ("GOT-38","Guest Right","Reputation and law","Recommended","","Bread and salt exchanged","Host and guest cannot intentionally harm one another while protected. Violation permanently damages reputation, alliance, and marriage prospects."),
 ("GOT-39","Kinslaying","Reputation","Recommended","","Kin publicly killed","Known kinslaying creates a major permanent penalty regardless of political usefulness; secret acts gain it when discovered."),
 ("GOT-40","Treason and Attainder","Postwar law","Optional","d6","Rebel House defeated","1 pardon; 2 fine/hostages; 3 land loss; 4 lord executed, heir retains remainder; 5 titles stripped; 6 House destroyed/dispossessed."),
 ("GOT-41","Claims","Succession","Recommended","","Title claim exists","Record title, strength (Strong, Weak, Disputed, Renounced, Extinguished), and source. A claim never automatically grants the title."),
 ("GOT-42","Succession Crisis","Political event","Optional","d6","Two plausible claimants","1-2 accepted; 3 dispute; 4 supporters gather; 5 House factions; 6 succession war. Modify for overwhelming claim or balanced military support."),
 ("GOT-43","Child Ruler and Regent","Succession","Optional","d6","Ruler below adulthood","Regent: 1 extremely loyal; 2-3 loyal; 4 self-interested; 5 corrupt; 6 seizes power. Regency does not transfer legal rule."),
 ("GOT-44","Great Council","Royal succession","Optional","d6","Exceptionally unclear royal succession","Houses weigh claim, marriage, alliance, religion, reputation, troops, fear, and advantage. Each losing claimant: 1-4 accepts; 5 maintains claim; 6 rebels."),
 ("GOT-45","Night's Watch","Vow and career","Optional","dynamic","Sworn membership","Vow removes marriage, legitimate children, land, titles, and inheritance. Annual d20: 1 dies. Desertion d6: 1-4 executed, 5 wanted escape, 6 disappears."),
 ("GOT-46","Kingsguard","Vow and career","Optional","d6","Protected monarch faces attack death","Traditional vows bar marriage, new legitimate children, independent land, and normal inheritance. 1-3 present guard takes death; 4-6 monarch keeps it."),
 ("GOT-47","Maesters","Career and household benefit","Optional","","Citadel-trained resident Maester","Final vows normally remove marriage/inheritance. Household gets one final illness-death reroll yearly. Track specialty and assigned House; only one Grand Maester."),
 ("GOT-48","Faith of the Seven","Religion and career","Optional","","Faith adherent or vocation","Track Septons, Septas, High Septon, Silent Sisters, teachers, and confessors. Vows normally remove succession; Silent Sisters prepare dead and do not marry."),
 ("GOT-49","Old Gods","Religion","Optional","","Northern/First Men worship","Godswood and Heart Tree host prayer, marriage, judgment, and oaths. No normal clergy; breaking a witnessed oath harms reputation."),
 ("GOT-50","Drowned God","Religion","Optional","d20","Serious ritual drowning","1 revival fails and Sim dies; 2-20 revived. Symbolic ceremonies need no roll."),
 ("GOT-51","R'hllor Visions","Supernatural event","Optional","d10","Red Priest interprets flames","1 true; 2 partly true/misread; 3-10 nothing useful. Record vision and interpretation before the event."),
 ("GOT-52","Kiss of Life","Resurrection","Optional","d20","Recent death","Priest's first success only on 1; after prior success 1-2. Every resurrection creates a permanent memory, emotion, lifespan, body, identity, obsession, or faith consequence."),
 ("GOT-53","Smallfolk War Impact","Civilian war event","Optional","d10","Once per war year in affected area","1 death; 2 home destroyed; 3 crops/livestock; 4 food stolen; 5 flight; 6 recruited; 7-10 avoids major direct harm."),
 ("GOT-54","Merchants and Ennoblement","Social mobility","Optional","","Merchant rises","Merchants may gain wealth, lend, buy land, marry nobles, or hold office, but need a ruler's formal grant to gain nobility/title."),
 ("GOT-55","Mistresses, Paramours and Brothels","Relationships","Optional","","Applicable adult relationship","Paramours are not spouses; children remain bastards unless legitimized. Dornish norms may reduce condemnation. Ordinary consent, pregnancy, disease, and legitimacy continue."),
 ("GOT-56","Education and Language","Education","Optional","","Child education","Highborn children normally study; smallfolk literacy depends on resources and access. Track tutor and languages such as Common, Valyrian, Dothraki, Old Tongue, and regional forms."),
 ("GOT-57","Dragon Egg Hatching","Supernatural birth event","Optional","d20","Extraordinary egg/rider attempt","1 hatches; 2-20 stays unhatched. Major magic/blood sacrifice may expand to 1-3. Failure need not destroy egg."),
 ("GOT-58","Dragon Bonding","Supernatural companion","Optional","d12","Once per eligible Sim and unclaimed dragon","1-2 bonds; 3-8 refusal/escape; 9-10 burn/injury; 11 disability; 12 death. Dragonrider ancestry expands success to 1-3; one living rider per dragon."),
 ("GOT-59","Dragons in War","War modifier","Optional","dynamic","Bonded dragonrider in major battle","Enemy morale d6: 1-2 holds, 3-4 retreats, 5 surrenders, 6 routs. Rider dies/disappears on 1 on d10; dragon d12: 1 killed, 2 disabled, 3-4 wounded, 5-12 survives."),
 ("GOT-60","Wildfire","Disaster","Optional","dynamic","Wildfire stored, moved, or used","Ignition: 1 on d20 ordinary, 1-2 large/wartime. Nearby d6: 1-3 death; 4 burns; 5 serious injury; 6 escape. Cannot be normally extinguished."),
 ("GOT-61","Greensight and Skinchanging","Supernatural birth trait","Optional","dynamic","Plausible First Men birth","1 on d20 grants gift; d4 assigns Greensight, Skinchanging, Both, or prophetic dreams. Record dreams beforehand; human mind entry is forbidden."),
 ("GOT-62","White Walkers and Wights","Supernatural war","Off","dynamic","Long Night or White Walker event","Unsecured corpse rises on 1-4 on d6. Attack d6: 1-2 dies/rises; 3 dies/body destroyed; 4 injury; 5 missing; 6 escape. Fire, dragonglass, and Valyrian steel destroy wights."),
 ("GOT-63","Valyrian Steel","Magical artifact","Optional","","Bearer fights supernatural enemy","Track named weapon owner and House. A trained bearer gets one final reroll against a supernatural enemy per event."),
 ("GOT-64","Irregular Seasons","World calendar","Optional","d12","End of year after season lasts at least two years","After 2 years change on 9-12; after 5 on 7-12; after 10 on 4-12. Canon mode may use fixed seasons."),
 ("GOT-65","Winter Hardship","Seasonal mortality","Optional","d10","Every winter year for poor/smallfolk household","1 cold/hunger death; 2 serious illness; 3 property/food loss; 4-10 endures. Stock, warm region, or patron grants one final reroll."),
 ("GOT-66","Annual Realm Event","World event","Optional","d20","Ordinary year without fixed major event","1 royal death; 2 rebellion; 3 invasion; 4 feud; 5 succession; 6 plague; 7 famine; 8 religion; 9 tourney; 10 progress; 11 trade; 12 title/marriage; 13 raiding; 14 scandal; 15 omen; 16 disaster; 17 claimant; 18 heir; 19-20 none."),
 ("GOT-67","House Reputation","Reputation","Optional","","House action changes standing","Track Honored, Respected, Ordinary, Distrusted, or Infamous. Oaths, loyalty, generosity, justice, cruelty, betrayal, ransom, and broken betrothals modify standing and uncertain diplomacy by one step."),
 ("GOT-68","Generational House Objective","Generation goal","Optional","d8","Once per generation","1 succession; 2 rise rank; 3 land; 4 powerful marriage; 5 royal office; 6 feud; 7 wealth/reputation; 8 claimant. Goal guides choices without cancelling dice."),
 ("GOT-69","Timeline Mode","Campaign setting","Optional","","Campaign setup","Choose Canon, Canon-Compatible, Alternate History, or Original Realm. Never force resurrection, age reset, or marriage merely to reproduce canon."),
)

DEPENDENCIES={"GOT-05":("GOT-04",),"GOT-07":("GOT-06",),"GOT-09":("GOT-01","GOT-08"),"GOT-13":("GOT-36",),"GOT-19":("GOT-04","GOT-05"),"GOT-21":("GOT-20",),"GOT-31":("GOT-30",),"GOT-32":("GOT-30",),"GOT-33":("GOT-30",),"GOT-34":("GOT-30",),"GOT-35":("GOT-30",),"GOT-42":("GOT-41",),"GOT-44":("GOT-41",),"GOT-52":("GOT-51",),"GOT-58":("GOT-57",),"GOT-59":("GOT-58",),"GOT-62":(),"GOT-65":("GOT-64",)}
MODULES=tuple({"code":c,"name":n,"category":cat,"default":d,"die":die,"trigger":tr,"rule_text":text,"dependencies":DEPENDENCIES.get(c,())} for c,n,cat,d,die,tr,text in _ROWS)

EVENT_TABLES=(
 {"code":"GOT-T01","name":"Annual House Event","category":"House event table","default":"Optional","die":"d20","trigger":"Actively played House in an ordinary year","rule_text":"1 ruler dies; 2 heir dies/disappears; 3 heir born; 4 proposal; 5 broken betrothal; 6 bastard recognized; 7 legitimization; 8 cadet branch; 9 land dispute; 10 office; 11 ward; 12 feud; 13 finance; 14 tourney; 15 religion; 16 military appointment; 17 inheritance; 18 reputation rises; 19 falls; 20 none. Avoid duplicates."},
 {"code":"GOT-T02","name":"Winter Household Event","category":"Season event table","default":"Optional","die":"d10","trigger":"Every affected household in each winter year","rule_text":"Stocked/noble: 1 death, 2 illness, 3 stores, 4 livestock, 5 refugees, 6 no travel, 7-10 endures. Poor/smallfolk: 1-2 death, 3 illness, 4 home, 5 crops/livestock, 6 food stolen, 7 relief, 8-10 endures. Maester/stock grants final reroll."},
 {"code":"GOT-T03","name":"Plague Exposure","category":"Plague procedure","default":"Optional","die":"d6","trigger":"Plague begins in area","rule_text":"Household exposure 1-4, avoidance 5-6; remote/quarantined avoids on 4-6. Each exposed Sim then d10: 1-2 death, 3-5 severe, 6-8 mild, 9-10 not ill. Maester grants one household illness-death reroll yearly."},
 {"code":"GOT-T04","name":"Rebellion Cause","category":"Rebellion procedure","default":"Optional","die":"d10","trigger":"Rebellion begins","rule_text":"1 succession; 2 tax; 3 religion; 4 murder/execution; 5 broken marriage; 6 foreign influence; 7 independence; 8 cruel/incapable ruler; 9 rival claimant; 10 accumulated grievances. Then allegiance, banners, allies, battles, aftermath, titles, and reputation."},
 {"code":"GOT-T05","name":"Great Tourney House Event","category":"Tourney event table","default":"Optional","die":"d12","trigger":"Important participating House","rule_text":"1 accident; 2 champion; 3 royal marriage; 4 affair; 5 insult; 6 rivalry; 7 knighted; 8 office; 9 debt; 10 alliance; 11 omen; 12 none. Participants also use GOT-28."},
 {"code":"GOT-T06","name":"Long Night Escalation","category":"Supernatural campaign track","default":"Off","die":"","trigger":"Major supernatural event or failed defense","rule_text":"Advance Rumors → Confirmed Wights → Mass Migration → Wall Crisis → Wall Breached → Long Night → Battle for the Dawn. A major victory may move back one stage; political denial does not."},
)
ALL_RULES=MODULES+EVENT_TABLES

TIMELINE_MODES=(("canon","Canon","Fixed events occur in established years."),("canon_compatible","Canon-Compatible","Events occur, but original Houses receive independently rolled outcomes."),("alternate","Alternate History","Events may be prevented, delayed, or changed."),("original","Original Realm","Use the mechanics without canon events."))

# Signed lore years: negative is BC, positive is AC. There is deliberately no zero.
TIMELINE=(
 (-99999,-12001,"Dawn Age","Children, giants, Old Gods, greenseers, skinchangers, spirits, and irregular seasons precede human kingdoms."),
 (-12000,-10001,"First Men Migration and War","Communities face destruction, displacement, battle, sacred-site loss, blending, trade, peace, or alliance until the Pact."),
 (-10000,-8001,"The Pact and Age of Heroes","Old Gods, protected Godswoods, legendary founders, castles, clans, magical builders, and mythic House origins."),
 (-8000,-6001,"The Long Night and First Men Kingdoms","Others, wights, winter hardship, refugees, supernatural war, the Battle for the Dawn, and Night's Watch founding."),
 (-6000,-2000,"Andal Invasions","Faith, knighthood, Andal nobility, male-preference inheritance, conquest, migration, conversion, and northern resistance."),
 (-5000,-701,"Valyrian Expansion","Dragonlords, bloodlines, steel, conquest, slavery, trade, colonies, political marriage, and blood magic rise in Essos."),
 (-700,-115,"Nymeria and the Rhoynar","Refugees settle Dorne, establish Rhoynish culture and absolute primogeniture, and unify under Martell leadership."),
 (-114,-103,"Targaryens on Dragonstone","Daenys's prophecy moves House Targaryen and its dragons away from Valyria."),
 (-102,-3,"Doom and Century of Blood","Valyria is destroyed; survivors, successor states, refugees, mercenaries, trade wars, artifacts, and claimants reshape Essos."),
 (-2,-1,"Aegon's Conquest","Banners, alliances, dragons, battles, attainder, grants, and allegiance rolls transform Westeros."),
 (1,36,"Aegon's Crown and Consolidation","Iron Throne, royal House, Small Council, Great Houses, post-Conquest politics, and the First Dornish War."),
 (37,100,"Faith Militant and Jaehaerys's Peace","Religious uprising gives way to roads, law, trade, tourneys, offices, cadet branches, and court politics."),
 (101,128,"Great Council and Rival Claims","The Council, competing heirs, court factions, dragon eggs, betrothals, and disputed promises prepare civil war."),
 (129,136,"Dance of the Dragons and Regency","Succession war, dragon combat, banners, civilians, prisoners, attainder, missing heirs, child rule, and reconstruction."),
 (137,187,"Declining Dragons and Dorne","Dragons decline and die; conquest, revolt, resistance, marriage, and incorporation reshape Dorne."),
 (172,208,"Aegon IV and First Blackfyre Rebellion","Mistresses, bastards, corruption, rival claims, legitimization, factions, and rebellion destabilize succession."),
 (209,257,"Spring Sickness and Blackfyre Conflicts","Plague, tourney, Trials, plots, rebellions, wars, and Great Councils repeatedly test Houses."),
 (258,260,"Ninepenny Kings and Summerhall","Expedition war overlaps a failed magical dragon-hatching disaster with death, burns, injury, prophecy, and dynasty change."),
 (261,281,"Aerys II and the False Spring","Court initially stabilizes, then captivity, paranoia, executions, wildfire, hostages, Harrenhal romance, scandal, and factions escalate."),
 (282,283,"Robert's Rebellion","Banners, alliances, battles, capture, ransom, attainder, succession, Sack impacts, dynasty fall, flight, and loyalist punishment."),
 (284,297,"Robert's Reign and Greyjoy Rebellion","Postwar Houses recover while naval rebellion, siege, hostages, surrender, and punishment recur."),
 (298,299,"Main Story and War of the Five Kings","Royal parentage, multiple claimants, court intrigue, banners, alliances, feuds, Guest Right, smallfolk harm, dragons, and returning Others."),
 (300,9999,"Unresolved and Original Future","Continue original, book-compatible, or chosen later events; do not force television outcomes in a book timeline."),
)

def lore_year(save:ChronicleSave)->int:
    start=int(save.start_year);elapsed=(max(1,int(save.global_day))-1)//max(1,int(save.days_per_year))
    value=start+elapsed
    if start<0 and value>=0:value+=1
    return value

def year_label(year:int)->str:
    year=int(year)
    if year==0:return "Transition (no Year 0)"
    return f"{abs(year):,} BC" if year<0 else f"{year:,} AC"

def range_label(start:int,end:int)->str:
    if start<=-99999:return f"Before {year_label(end)}"
    if end>=9999:return f"{year_label(start)} and later"
    return year_label(start) if start==end else f"{year_label(start)}–{year_label(end)}"

def _payload(rule,enabled,pack_enabled):return {**rule,"rule_key":rule["code"].lower().replace("-","_"),"rule_pack_id":PACK_ID,"rule_family":"Game of Thrones Decades","source":"Game of Thrones Decades optional rules","module_enabled":enabled,"pack_enabled":pack_enabled,"active":enabled and pack_enabled,"result_rules":rule["rule_text"],"auto_schedule":False}

def sync_pack(session:Session,save:ChronicleSave,selected:list[str])->int:
    from .domain import journal
    on=PACK_ID in selected;existing={str((r.data or {}).get("code") or ""):r for r in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=="addon_rule",Record.deleted.is_(False))) if (r.data or {}).get("rule_pack_id")==PACK_ID};touched=[]
    for rule in ALL_RULES:
        record=existing.get(rule["code"])
        if record is None:
            if not on:continue
            enabled=str(rule["default"]).startswith("Recommended");record=Record(save_id=save.id,kind="addon_rule",label=f'{rule["code"]} — {rule["name"]}',data=_payload(rule,enabled,True));session.add(record);touched.append((record,0));continue
        data=dict(record.data or {});enabled=bool(data.get("module_enabled",str(rule["default"]).startswith("Recommended")));desired={**data,"pack_enabled":on,"active":on and enabled}
        if desired!=data:base=record.version;record.data=desired;record.version+=1;touched.append((record,base))
    if touched:
        session.flush()
        for record,base in touched:journal(session,record,"upsert",base)
    return len(touched)

def set_module(session:Session,save:ChronicleSave,code:str,enabled:bool)->Record|None:
    record=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=="addon_rule",Record.deleted.is_(False),Record.data["code"].as_string()==code))
    if not record or (record.data or {}).get("rule_pack_id")!=PACK_ID:return None
    data=dict(record.data or {});data["module_enabled"]=enabled;data["active"]=enabled and bool(data.get("pack_enabled"));record.data=data;record.version+=1;return record
