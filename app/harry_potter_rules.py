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
    ("HP-13","Hogwarts House Assignment","Education","Optional","d4","Spellcaster reaches age 11, from 990 onward","1: Hufflepuff; 2: Ravenclaw; 3: Slytherin; 4: Gryffindor. When this module is enabled, eligible spellcasters are automatically scheduled on their eleventh birthday. The first result is final and applies only to Hogwarts students."),
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

# code, start year, end year, title, region, eligible Sims, default, die,
# trigger, exact result table, source grouping.  These stay as discrete rule
# records rather than being flattened into the general event tables: canon
# dates are fixed, but the player chooses which eligible household members take
# part in scenario-only scenes such as the Triwizard Tournament or a Horcrux
# search.
_CANON_EVENT_ROWS = (
    ("HP-E01",1890,1945,"Grindelwald's rise and defeat","Europe","Adult wizarding Sims","Recommended","d6","Once per adult during the conflict","1: Arrested or injured; cannot work for 7 days; 2: Must relocate; 3-4: Remains uninvolved; 5: Gains a resistance contact; 6: Gains a protected mentor or §200.","Foundational canon"),
    ("HP-E02",1970,1981,"First Wizarding War","Britain","Wizarding households","Recommended","d6","Once per household for each year of the war","1: One household member dies; 2: The home is compromised; relocate; 3: Lose D4 stored goods; 4-5: Survive quietly; 6: Gain an Order, Ministry, or community contact.","Foundational canon"),
    ("HP-E03",1980,1980,"The prophecy is made","Britain","One newborn or child in a wizarding family","Recommended","d20","Once for an eligible child","1: Gains the Chosen, Scholar, or Brave trait; 2-20: No special effect. Never force a child into danger solely because of this result.","Foundational canon"),
    ("HP-E04",1981,1981,"Voldemort falls; Harry survives","Britain","Wizarding households","Recommended","d6","Once per household","1: A lost relative is confirmed dead; 2-4: The family reunites safely; 5: Recover §200 in property or savings; 6: Gain a lasting friendship with another survivor household.","Foundational canon"),
    ("HP-E05",1991,1992,"The Philosopher's Stone — Hogwarts sorting","Britain","First-year Hogwarts Sims","Recommended","d4","Once for each first-year Sim","1: Gryffindor; 2: Hufflepuff; 3: Ravenclaw; 4: Slytherin.","Main-series canon"),
    ("HP-E06",1991,1992,"The Philosopher's Stone — school year","Britain","First-year Hogwarts Sims","Recommended","d6","At the end of the school year","1: Detention; lose one weekend of free time; 2-4: Ordinary school year; 5: Gain a school friend; 6: Earn a House-point reward or useful magical item.","Main-series canon"),
    ("HP-E07",1992,1992,"The Chamber of Secrets is opened","Britain","Muggle-born and vulnerable Hogwarts Sims","Recommended","d6","Once per eligible student","1: Petrified; pause school progress until cured; 2: Targeted by rumors; lose one friendship; 3-5: Avoid direct harm; 6: Helps protect another student and gains a close friend.","Main-series canon"),
    ("HP-E08",1993,1993,"Sirius Black escapes Azkaban","Britain","Child and teen wizarding Sims","Recommended","d6","Once per eligible Sim","1: Fearful mood for 7 days; 2: Cannot travel outside the home for 3 days; 3-5: Follows ordinary routines; 6: Learns a useful defensive spell or confidence trait.","Main-series canon"),
    ("HP-E09",1993,1994,"Dementors and the truth about Pettigrew","Britain","Sims facing a Dementor or severe fear","Recommended","d6","When the scene applies","1: Overwhelmed; cannot attend school or work for 3 days; 2-4: Recovers with support; 5: Learns Patronus practice; 6: Successfully casts a Patronus; gain a Brave or Confident trait.","Main-series canon"),
    ("HP-E10",1994,1995,"The Triwizard Tournament","Britain","Player-approved young-adult competitors","Recommended","d6","For each approved task","1: Serious injury; cannot work for 7 days; 2: Minor injury; 3-4: Completes the task with no prize; 5: Gains §250 or a valuable item; 6: Wins the task and gains a lasting rival or ally.","Main-series canon"),
    ("HP-E11",1995,1995,"Voldemort returns","Britain","Adult wizarding Sims","Recommended","d6","Once per eligible adult","1: A loved one is injured or lost; 2: Household goes into hiding; 3: Lose D4 goods to an attack; 4-5: Safe but fearful; 6: Gains an Order contact and may learn defensive magic.","Main-series canon"),
    ("HP-E12",1995,1996,"Ministry denial and Dumbledore's Army","Britain","School-age Sims training in defensive magic","Recommended","d6","At the end of the school year","1: Training is discovered; detention or punishment; 2-4: Learns one defensive skill; 5: Gains a close friend in the group; 6: Becomes a capable leader and may train others.","Main-series canon"),
    ("HP-E13",1996,1996,"Battle of the Department of Mysteries","Britain","Participating teen and adult Sims","Recommended","d8","For each participating Sim","1: Dies; 2: Seriously injured; cannot work for 14 days; 3-5: Escapes safely; 6: Saves another Sim; 7-8: Gains a key clue, mentor, or resistance contact.","Main-series canon"),
    ("HP-E14",1996,1997,"Horcruxes and Draco's mission","Britain","Trusted adults with a clear lead","Recommended","d6","For each dangerous-object search","1: Curse or injury; pause the search for 7 days; 2: The clue is false; 3-4: Find useful information; 5: Locate the object; 6: Locate it and destroy it safely.","Main-series canon"),
    ("HP-E15",1997,1997,"Dumbledore dies; Hogwarts falls under control","Britain","Hogwarts students","Recommended","d6","Once per eligible student","1: Expelled, arrested, or forced into hiding; 2: Loses one friendship through fear or betrayal; 3-4: Endures the year quietly; 5: Joins a resistance network; 6: Protects another student and gains a loyal ally.","Main-series canon"),
    ("HP-E16",1997,1998,"The Ministry falls","Britain","Wizarding households","Recommended","d6","Once per household","1: Home is seized; relocate; 2: One Sim is detained for 7 days; 3: Lose D4 goods; 4-5: Remain hidden successfully; 6: Safe house or ally provides §200 in aid.","Main-series canon"),
    ("HP-E17",1997,1998,"The Horcrux hunt","Britain","Resistance households pursuing one clue","Recommended","d6","For each expedition","1: Group is attacked; one Sim is injured; 2: No progress; 3-4: Gain a true clue; 5: Find a Horcrux; 6: Find and destroy a Horcrux.","Main-series canon"),
    ("HP-E18",1998,1998,"Escape from Malfoy Manor and Gringotts","Britain","Households attempting a high-risk rescue or break-in","Recommended","d8","For each approved attempt","1: One Sim dies; 2: One Sim is captured; 3: Group escapes but loses all carried goods; 4-5: Escape safely; 6: Rescue another Sim; 7-8: Escape with a crucial item or clue.","Main-series canon"),
    ("HP-E19",1998,1998,"Battle of Hogwarts","Britain","Participating teen and adult Sims","Recommended","d8","For each participating Sim","1: Dies; 2: Seriously injured; cannot work for 14 days; 3: Minor injury; cannot work for 3 days; 4-5: Survives without injury; 6: Saves another Sim; 7: Gains a heroic reputation; 8: Gains a lifelong ally or §300 in recovered aid.","Main-series canon"),
    ("HP-E20",1998,1998,"Voldemort is defeated","Britain","Wizarding households","Recommended","d6","Once per household","1: Family grieves a permanent loss; 2-4: Rebuild slowly with §200 aid; 5: Recover a lost home or heirloom; 6: Found a household, business, or community project.","Main-series canon"),
    ("HP-E21",1998,2000,"Rebuilding Wizarding Britain","Britain","Wizarding households","Recommended","d6","Once per household","1: Trauma delays work for 7 days; 2-4: Ordinary rebuilding; 5: Gain §200 through a grant or recovered property; 6: Form a lasting community alliance.","Main-series canon"),
    ("HP-E22",2017,2017,"The next generation begins Hogwarts","Britain","Postwar children starting first year","Recommended","d6","At the start of first year","1: Feels pressure from family history; 2-4: Ordinary beginning; 5: Gains a close school friend; 6: Finds a personal calling, talent, or mentor.","Main-series canon"),
    ("HP-E23",-382,-382,"Ollivanders is founded","Britain","Wizarding Sims obtaining a specialist wand","Optional","d6","When obtaining a personal wand","1: Poor fit; 1-day negative mood when casting; 2-4: Ordinary compatible wand; 5: Wand aids one school or work task; 6: Exceptional bond; gain a Confident or Inspired trait.","Expanded historical canon"),
    ("HP-E24",990,990,"Hogwarts is founded","Britain","British wizarding children enrolling from age 11","Optional","d6","At enrolment","1: Struggles to settle in; 2-4: Ordinary start; 5: Gains a close friend; 6: Gains a mentor or special talent.","Expanded historical canon"),
    ("HP-E25",1294,1294,"The Triwizard Tournament begins","Europe","Player-approved competitors","Optional","","Use the Triwizard Tournament table","Three-school competitions may occur only with player approval. Each task uses the Triwizard D6 event; child Sims never have to compete.","Expanded historical canon"),
    ("HP-E26",1612,1612,"Goblin rebellion at Hogsmeade","Britain","Wizarding households near Hogsmeade","Optional","d6","Once per household","1: One Sim is injured; 2: Household loses D4 goods; 3-4: Household remains safe; 5: Gains a goblin or wizarding ally; 6: Learns a useful trade or craft skill.","Expanded historical canon"),
    ("HP-E27",1637,1637,"Werewolf Code of Conduct","Britain","Werewolf Sims","Optional","d6","When registration, concealment, or trusted housing is chosen","1: Exposure causes a 7-day work or school penalty; 2-4: Secret is kept; 5: Gain a supportive ally; 6: Secure safe employment or housing.","Expanded historical canon"),
    ("HP-E28",1689,1692,"International Statute of Secrecy","Global","Magical households after accidental Muggle exposure","Optional","d6","After every accidental Muggle exposure","1: Ministry fine of §200; 2: Memory altered and lose a friendship; 3-4: Warning only; 5: Incident resolved quietly; 6: Ministry contact helps conceal it.","Expanded historical canon"),
    ("HP-E29",1707,1707,"British Ministry of Magic is established","Britain","Adult wizarding Sims seeking Ministry help","Optional","d6","When seeking Ministry help","1: Bureaucratic delay of 7 days; 2-4: Request handled normally; 5: Helpful official contact; 6: Gain a job, permit, or §150 grant.","Expanded historical canon"),
    ("HP-E30",1717,1717,"Dragon breeding is banned","Britain","Households illegally keeping a dragon","Optional","d6","When the restriction is breached","1: Dragon escapes; pay §500 damage; 2: Ministry confiscation; 3-4: Creature is rehomed safely; 5-6: Reserve accepts the creature and grants a favor.","Expanded historical canon"),
    ("HP-E31",1750,1750,"Department of Magical Games and Sports is formed","Britain","Wizarding Sims in a formal competition","Optional","d6","For each formal event","1: Disqualified; 2-4: Ordinary result; 5: Gain a rival or ally; 6: Win §150 or a reputation reward.","Expanded historical canon"),
    ("HP-E32",1792,1792,"The Triwizard Tournament is discontinued","Europe","Wizarding schools","Optional","","Applies to future competitions","Stop regular Triwizard events. A rare revival is one high-risk, player-approved scenario using the Triwizard event table.","Expanded historical canon"),
    ("HP-E33",1811,1811,"Golden Snidget protections","Britain","Magical-creature households violating protections","Optional","d6","For a protected-creature violation","1-2: Fine of §200; 3: Creature is confiscated; 4-6: Warning and possible community service.","Expanded historical canon"),
    ("HP-E34",1865,1865,"International Confederation standardizes cooperation","Global","Wizarding Sims requesting overseas support","Optional","d6","For an overseas request","1: Refused; 2-4: Ordinary approval; 5: Gain a foreign ally; 6: Gain travel support or §200 in trade.","Expanded historical canon"),
    ("HP-E35",1926,1926,"New York magical conflict","North America","North American wizarding households","Optional","d6","Once per household","1: Exposure causes a Ministry penalty; 2: Household member relocates; 3-4: Household remains hidden; 5: Gains a trusted ally; 6: Gains a clue about a dangerous magical threat.","Expanded historical canon"),
    ("HP-E36",1932,1932,"International wizarding leadership crisis","Global","Wizarding adults in political conflict","Optional","d6","Once per participating adult","1: Targeted by extremists; lose D4 goods; 2: One Sim hides for 3 days; 3-4: Remains uninvolved; 5: Gain a political ally; 6: Protect another Sim and gain a lasting favor.","Expanded historical canon"),
    ("HP-E37",1945,1945,"Dumbledore defeats Grindelwald","Europe","European wizarding households","Optional","d6","Once per household","1: Wartime loss is confirmed; 2-4: Rebuild normally; 5: Recover property or §200; 6: Gain a resistance mentor or legacy item.","Expanded historical canon"),
    ("HP-E38",1965,1965,"Nimbus 1000 revolutionizes broom travel","Global","Sims using a modern racing broom","Optional","d6","On first use","1: Crash; cannot use broom for 3 days; 2-4: Ordinary travel; 5: Quidditch or travel advantage; 6: Prized broom or §200 sponsorship.","Expanded historical canon"),
    ("HP-E39",1620,1634,"Isolt Sayre founds her North American family","North America","North American wizarding households","Optional","d6","Once per household","1: Dangerous relative or enemy finds household; relocate; 2-4: Household remains hidden; 5: Gain a trusted non-magical or magical ally; 6: Establish a lasting school, community, or sanctuary connection.","Wiki-supplemented canon"),
    ("HP-E40",1733,1752,"Britain establishes the Auror Office","Britain","Eligible adult Auror applicants","Optional","d6","For each application","1: Rejected; try again after one year; 2-4: Accepted as a trainee; 5: Gain a skilled mentor; 6: Begin active service and gain §150.","Wiki-supplemented canon"),
    ("HP-E41",1750,1750,"Clause 73 strengthens creature secrecy","Global","Magical-creature households after a public incident","Optional","d6","After a public creature incident","1: International fine of §200; 2: Creature relocated; 3-4: Warning and concealment work; 5: Incident resolved quietly; 6: Gain a creature-care contact.","Wiki-supplemented canon"),
    ("HP-E42",1783,1783,"Dark wizard Flannery's rampage","Britain","British wizarding households","Optional","d6","Once per household","1: One Sim dies; 2: One Sim needs Ministry protection for 7 days; 3: Household relocates; 4-5: Avoids direct harm; 6: Gains an Auror contact or defensive-magic lesson.","Wiki-supplemented canon"),
    ("HP-E43",1790,1790,"Rappaport's Law begins","North America","North American wizarding and No-Maj households","Optional","d6","When the rule is breached","1: Ministry investigation and §200 fine; 2: One friendship ends; 3-4: Secret is repaired; 5: Trusted ally helps; 6: Household avoids discovery.","Wiki-supplemented canon"),
    ("HP-E44",1926,1926,"New York magical-beast incident","North America","North American wizarding households","Optional","d6","After a public magical-beast escape","1: Detained for 3 days; 2: Loses a magical creature; 3-4: Helps contain incident; 5: Gains a magizoologist contact; 6: Gains a rare creature-care skill.","Wiki-supplemented canon"),
    ("HP-E45",1927,1927,"Grindelwald's Paris rally","Europe","European wizarding adults","Optional","d6","Once per participating adult","1: Targeted by extremists; lose D4 goods; 2: Hides for 7 days; 3-4: Remains uninvolved; 5: Gains resistance contact; 6: Protects another Sim and gains a lasting ally.","Wiki-supplemented canon"),
    ("HP-E46",1932,1932,"The Ilfracombe Incident","Britain","British coastal households","Optional","d6","Once per household","1: One Sim injured; 2: Property damage costs §150; 3: Household shelters for one day; 4-5: Assists cleanup; 6: Earns a Ministry or creature-care favor.","Wiki-supplemented canon"),
    ("HP-E47",1965,1965,"Rappaport's Law is repealed","North America","North American wizarding and No-Maj relationships","Optional","d6","For a new cross-community relationship","1: Secrecy conflict; pause relationship for 7 days; 2-4: Develops normally; 5: Gain supportive family ally; 6: Establish a lasting shared household or community bond.","Wiki-supplemented canon"),
    ("HP-E48",1880,1891,"Ranrok's goblin rebellion","Britain","British wizarding households","Optional","d6","Once per household","1: One Sim is injured in conflict; 2: Household loses D4 goods; 3-4: Remains uninvolved; 5: Gains a goblin, wizard, or creature ally; 6: Finds a safe clue to ancient magic.","Game-sourced canon supplement"),
    ("HP-E49",1891,1891,"Battle for the final repository","Britain","Households with a clear ancient-magic connection","Optional","d6","Once per household","1: Curse; one Sim cannot work for 14 days; 2: Expedition fails and loses §200; 3-4: Finds a clue only; 5: Secures a useful magical artifact; 6: Resolves threat and gains a lasting ally.","Game-sourced canon supplement"),
    ("HP-E50",1984,1991,"Hogwarts vaults and cursed school years","Britain","Hogwarts-age Sims","Optional","d6","Once per approved school-year investigation","1: Detention and 3-day negative mood; 2: Friendship strained; 3-4: Finds no answer; 5: Gains true clue; 6: Solves part of mystery and gains a loyal friend.","Game-sourced canon supplement"),
    ("HP-E51",1989,1989,"The Cursed Vaults are opened and sealed","Britain","Households completing the vault storyline","Optional","d6","Once on completion","1: Curse injures a Sim for 7 days; 2-4: Vault sealed safely; 5: Recover lost family item; 6: Gain school mentor, club leadership, or rare magical reward.","Game-sourced canon supplement"),
    ("HP-E52",2010,2011,"Siege of Hogwarts","Britain","Participating teen and adult Sims","Optional","d8","For each participant","1: Dies; 2: Seriously injured for 14 days; 3-4: Escapes safely; 5: Saves another Sim; 6: Gains a resistance contact; 7-8: Recovers key magical object or earns §300.","Game-sourced canon supplement"),
    ("HP-E53",2010,2011,"Defeat of NOTME","Britain","Wizarding households","Optional","d6","Once per household","1: Publicly implicated; pay §200 fine; 2: One Sim hides for 7 days; 3-4: Assists without recognition; 5: Gains a Ministry contact; 6: Prevents a major breach and gains lasting reputation reward.","Game-sourced canon supplement"),
)

_DIRECT_PARTICIPANT_LETHAL_RESULTS = {"HP-E13":"1", "HP-E19":"1", "HP-E52":"1"}

CANON_EVENTS = tuple({
    "code":code, "start_year":start, "end_year":end, "name":name,
    "category":source, "location":location, "eligibility":eligibility,
    "default":default, "die":die, "trigger":trigger, "rule_text":rule_text,
    "source_group":source, "lethal_results":_DIRECT_PARTICIPANT_LETHAL_RESULTS.get(code, ""),
} for code,start,end,name,location,eligibility,default,die,trigger,rule_text,source in _CANON_EVENT_ROWS)

ALL_RULES = MODULES + EVENT_TABLES + CANON_EVENTS

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
    event = rule["code"].startswith("HP-E")
    return {
        **rule,
        "rule_key":rule["code"].lower().replace("-","_"),
        "rule_pack_id":PACK_ID,
        "rule_family":"Harry Potter Decades",
        "source":"Harry Potter Canon Timeline Challenge Rules" if event else "Harry Potter Decades optional rules",
        "rule_kind":"canon_event" if event else "event_table" if rule["code"].startswith("HP-T") else "module",
        "module_enabled":enabled,
        "pack_enabled":pack_enabled,
        "active":enabled and pack_enabled,
        "result_rules":rule["rule_text"],
        "auto_schedule":False,
    }


def section_for(code: str) -> str:
    code = str(code or "").upper()
    return "canon-events" if code.startswith("HP-E") else "event-tables" if code.startswith("HP-T") else "modules"


def _event_global_day(save: ChronicleSave, year: int) -> int:
    return max(1, (int(year) - int(save.start_year)) * max(1, int(save.days_per_year)) + 1)


def sync_canon_events(session: Session, save: ChronicleSave, selected: list[str]) -> int:
    """Mirror relevant dated canon entries into the native historical calendar.

    The calendar entries are intentionally reference-only.  Many source rules
    say *one household member* or require player approval, so automatic event
    scheduling would otherwise select the wrong person.  The event library's
    workbench carries the exact die to Today once the player selects the proper
    participant or household representative.
    """
    from .domain import journal

    pack_enabled = PACK_ID in selected
    event_rules = {
        str((item.data or {}).get("code") or ""): item
        for item in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "addon_rule", Record.deleted.is_(False),
        ))
        if (item.data or {}).get("rule_pack_id") == PACK_ID
    }
    existing = {
        str((item.data or {}).get("catalog_id") or ""): item
        for item in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "event", Record.deleted.is_(False),
        ))
        if str((item.data or {}).get("catalog_id") or "").startswith("hp-canon:")
    }
    touched: list[tuple[Record, int]] = []
    for event in CANON_EVENTS:
        # A save never needs a calendar entry that ended before the challenge
        # began; the full historical library remains available on its own page.
        if int(event["end_year"]) < int(save.start_year):
            continue
        code = str(event["code"])
        rule = event_rules.get(code)
        active = bool(pack_enabled and rule and (rule.data or {}).get("active"))
        catalog_id = f"hp-canon:{code}"
        start_year = max(int(save.start_year), int(event["start_year"]))
        start_day = _event_global_day(save, start_year)
        end_day = _event_global_day(save, int(event["end_year"])) + max(1, int(save.days_per_year)) - 1
        note = f"{event['eligibility']}. {event['trigger']}\n{event['rule_text']}"
        desired = {
            "catalog_id": catalog_id,
            "rule_pack_id": PACK_ID,
            "source_rule_code": code,
            "source_group": event["source_group"],
            "canonical_event": True,
            "start_global_day": start_day,
            "end_global_day": end_day,
            "source_start_year": int(event["start_year"]),
            "source_end_year": int(event["end_year"]),
            "scope": "Global" if str(event["location"]).casefold() == "global" else "Wizarding world",
            "location": event["location"],
            "affected_class": event["eligibility"],
            "notes": note,
            "roll_required": False,
            "configured_die": event["die"],
            "configured_result_rules": event["rule_text"],
            "active": active,
        }
        record = existing.get(catalog_id)
        if record is None:
            if not pack_enabled:
                continue
            record = Record(
                save_id=save.id, kind="event", label=event["name"], global_day=start_day,
                data=desired,
            )
            session.add(record)
            touched.append((record, 0))
            continue
        data = dict(record.data or {})
        # Canonical identity, dates, source text and add-on activation are
        # authoritative.  Keep a player's manual event controls intact.
        updates = {
            key: value for key, value in desired.items()
            if key not in {"notes", "roll_required", "configured_die", "configured_result_rules"}
            or key not in data
        }
        merged = {**data, **updates, "active": active}
        if merged != data:
            base = record.version
            record.data = merged
            record.global_day = start_day
            record.version += 1
            touched.append((record, base))
    if touched:
        session.flush()
        for record, base in touched:
            journal(session, record, "upsert", base)
    return len(touched)


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
        # Upgrade only the shipped HP-13 wording/result table. Player edits are
        # retained, while existing installs learn that sorting is scheduled at
        # age 11 and get a result table the normal roll renderer can display.
        if rule["code"]=="HP-13" and str(data.get("rule_text") or "").strip()=="1 Hufflepuff; 2 Ravenclaw; 3 Slytherin; 4 Gryffindor. First result is final and applies only to Hogwarts students.":
            desired.update({"trigger":rule["trigger"],"rule_text":rule["rule_text"],"result_rules":rule["rule_text"]})
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
