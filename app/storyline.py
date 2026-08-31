from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timezone
import hashlib
import json
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import event_is_ignored
from .models import ChronicleSave, Record


def year_for(save: ChronicleSave, day: int | None) -> int | None:
    return save.start_year + (int(day) - 1) // save.days_per_year if day is not None else None


def updated_timestamp(item: Record) -> float:
    value = item.updated_at
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _natural_list(values: list[str], limit: int = 4) -> str:
    names = list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
    if len(names) > limit:
        names = names[:limit] + [f"{len(names) - limit} others"]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _story_day(item: Record) -> int | None:
    data = item.data or {}
    candidates = {
        "sim": ("birth_global_day",),
        "relationship": ("marriage_global_day", "start_global_day"),
        "pregnancy": ("conception_global_day", "start_global_day"),
        "illness": ("start_global_day",),
        "roll": ("completed_global_day",),
        "event": ("start_global_day",),
        "death": ("death_global_day",),
        "university_enrollment": ("start_global_day",),
        "university_term": ("end_global_day", "start_global_day"),
    }.get(item.kind, ())
    for key in candidates:
        value = data.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                pass
    return int(item.global_day) if item.global_day is not None else None


def _annual_paragraph(save: ChronicleSave, year: int, entries: list[Record], sims: list[Record],
                      events: list[Record]) -> tuple[str, str]:
    year_start = (year - save.start_year) * save.days_per_year + 1
    year_end = min(save.global_day, year_start + save.days_per_year - 1)
    births = [sim for sim in sims if year_for(save, (sim.data or {}).get("birth_global_day") or sim.global_day) == year]
    sim_names = {sim.id: sim.label for sim in sims}
    deaths_by_name: dict[str, str] = {}
    for sim in sims:
        data = sim.data or {}
        if year_for(save, data.get("death_global_day")) == year:
            deaths_by_name[sim.label] = str(data.get("cause_of_death") or "an unrecorded cause")
    for item in entries:
        if item.kind == "death":
            data = item.data or {}
            name = str(data.get("sim_name") or sim_names.get(str(data.get("sim_id") or "")) or item.label)
            if name.casefold().startswith("death of "):
                name = name[9:].strip()
            deaths_by_name[name] = str(data.get("cause_of_death") or data.get("cause") or "an unrecorded cause")
    relationships = [item for item in entries if item.kind == "relationship"]
    migrations = [item for item in entries if item.kind == "migration"]
    pregnancies = [item for item in entries if item.kind == "pregnancy"]
    illnesses = [item for item in entries if item.kind == "illness"]
    completed_rolls = [item for item in entries if item.kind == "roll" and bool((item.data or {}).get("completed"))]
    histories = [item for item in entries if item.kind == "game_history"]
    university_enrollments = [item for item in entries if item.kind == "university_enrollment"]
    university_terms = [item for item in entries if item.kind == "university_term" and str((item.data or {}).get("status") or "").casefold() in {"completed", "passed"}]
    authored = [item for item in entries if item.kind == "story_entry"]
    overlapping_events = []
    for event in events:
        data = event.data or {}
        start = data.get("start_global_day", event.global_day)
        end = data.get("end_global_day", start)
        try:
            start = int(start) if start is not None else None
            end = int(end) if end is not None else start
        except (TypeError, ValueError):
            continue
        if start is not None and start <= year_end and (end is None or end >= year_start) and bool(data.get("active", True)):
            overlapping_events.append(event)

    headline_bits = []
    if births: headline_bits.append(f"{len(births)} birth{'s' if len(births) != 1 else ''}")
    if deaths_by_name: headline_bits.append(f"{len(deaths_by_name)} death{'s' if len(deaths_by_name) != 1 else ''}")
    if relationships: headline_bits.append(f"{len(relationships)} relationship change{'s' if len(relationships) != 1 else ''}")
    if overlapping_events: headline_bits.append(f"{len(overlapping_events)} historical event{'s' if len(overlapping_events) != 1 else ''}")
    if university_enrollments or university_terms: headline_bits.append(f"{len(university_enrollments) + len(university_terms)} university milestone{'s' if len(university_enrollments) + len(university_terms) != 1 else ''}")
    headline = ", ".join(headline_bits).capitalize() if headline_bits else "A quiet year in the surviving record"

    sentences = []
    if overlapping_events:
        sentences.append(f"The year {year} unfolded under {_natural_list([item.label for item in overlapping_events])}, placing the household within a wider historical moment.")
    else:
        sentences.append(f"The surviving family record for {year} contains {len(entries)} dated entr{'y' if len(entries) == 1 else 'ies'}.")
    if births:
        sentences.append(f"New lives entered the chronicle with {_natural_list([item.label for item in births])}, extending the family into another generation.")
    if deaths_by_name:
        losses = [f"{name} ({cause})" for name, cause in deaths_by_name.items()]
        sentences.append(f"The year also carried loss: {_natural_list(losses)} {'was' if len(losses) == 1 else 'were'} recorded among the dead.")
    if relationships:
        sentences.append(f"Family bonds shifted through {_natural_list([item.label for item in relationships])}; these unions, separations, or commitments changed the shape of the household record.")
    if migrations:
        routes=[f"{(item.data or {}).get('sim_name') or item.label} from {(item.data or {}).get('from_country') or 'an earlier home'} to {(item.data or {}).get('to_country') or 'a new country'}" for item in migrations]
        sentences.append(f"Movement reshaped the family's world as {_natural_list(routes)} entered the migration ledger.")
    if university_enrollments:
        studies=[f"{(item.data or {}).get('sim_name') or item.label} began {(item.data or {}).get('degree') or 'university study'}" for item in university_enrollments]
        sentences.append(f"Higher education entered the record when {_natural_list(studies)}.")
    if university_terms:
        outcomes=[]
        for item in university_terms:
            data=item.data or {};grade=f" with {data.get('grade')}" if data.get("grade") else ""
            outcomes.append(f"{data.get('sim_name') or item.label} completed term {data.get('term_number') or '?'}{grade}")
        sentences.append(f"Academic progress continued as {_natural_list(outcomes)}.")
    if pregnancies:
        details = []
        for item in pregnancies:
            data = item.data or {}
            subject = str(data.get("mother_name") or item.label)
            status = str(data.get("status") or "pregnancy recorded").replace("_", " ")
            babies = data.get("babies_born") or data.get("babies_expected")
            details.append(f"{subject} — {status}" + (f", {babies} child{'ren' if str(babies) != '1' else ''}" if babies not in (None, "") else ""))
        sentences.append(f"The household's succession and care were shaped by {_natural_list(details)}.")
    if illnesses:
        illness_details = []
        for item in illnesses:
            data = item.data or {}
            illness_details.append(f"{data.get('sim_name') or item.label} with {data.get('illness_name') or item.label}")
        sentences.append(f"Health entered the year's account through {_natural_list(illness_details)}, adding danger and uncertainty to daily life.")
    if completed_rolls:
        roll_details = []
        for item in completed_rolls:
            data = item.data or {}
            roll_details.append(f"{data.get('sim_name') or item.label}: {data.get('outcome') or 'resolved'}" + (f" on {data.get('actual')}" if data.get("actual") is not None else ""))
        sentences.append(f"The resolved dice record preserves {_natural_list(roll_details)}, so the year's consequences remain tied to their actual rolls.")
    if histories:
        history_counts = Counter(str((item.data or {}).get("category") or "life event").replace("_", " ") for item in histories)
        history_text = _natural_list([f"{count} {name}{'' if count == 1 else ' updates'}" for name, count in history_counts.most_common(4)])
        sentences.append(f"Clock Sync and personal records added {history_text}, capturing changes that might otherwise have gone unremarked.")
    if authored:
        sentences.append(f"The people of the save also left their own words in {_natural_list([item.label for item in authored])}.")

    known_alive = 0
    for sim in sims:
        data = sim.data or {}
        birth = data.get("birth_global_day", sim.global_day)
        death = data.get("death_global_day")
        try:
            born = birth is not None and int(birth) <= year_end
            still_alive = death is None or int(death) > year_end
        except (TypeError, ValueError):
            continue
        known_alive += int(born and still_alive)
    if len(sentences) == 1:
        sentences.append("No dated birth, death, relationship, pregnancy, illness, completed roll, personal change, or historical event is attached to this year yet.")
        sentences.append("It remains in the chronicle so that a quiet interval is visible rather than mistaken for missing coverage.")
    sentences.append(f"By the close of the recorded year, {known_alive} Sim{'s were' if known_alive != 1 else ' was'} known to be living from the dates currently preserved in the tracker.")
    return headline, " ".join(sentences)


def build(session: Session, save: ChronicleSave) -> dict:
    story_kinds={"sim","household","relationship","pregnancy","illness","roll","event","death","migration","game_history","story_entry","session_journal","university_enrollment","university_term","university_performance"}
    records = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind.in_(story_kinds), Record.deleted.is_(False))))
    ignored_event_ids={item.id for item in records if item.kind=="event" and event_is_ignored(item)}
    records=[item for item in records if item.id not in ignored_event_ids and str((item.data or {}).get("event_id") or "") not in ignored_event_ids]
    by_kind = defaultdict(list)
    for item in records: by_kind[item.kind].append(item)
    sims = by_kind["sim"]
    living = [sim for sim in sims if not sim.data.get("death_global_day") or int(sim.data["death_global_day"]) > save.global_day]
    deceased = [sim for sim in sims if sim not in living]
    households = {item.id: item for item in by_kind["household"]}
    household_members = defaultdict(list)
    for sim in living:
        household_members[sim.data.get("current_household_id")].append(sim)
    active_events = [event for event in by_kind["event"] if event.global_day is not None and event.global_day <= save.global_day and (event.data.get("end_global_day") is None or int(event.data["end_global_day"]) >= save.global_day) and event.data.get("active", 1)]
    pregnancies = [item for item in by_kind["pregnancy"] if str(item.data.get("status", "active")).casefold() == "active"]
    illnesses = [item for item in by_kind["illness"] if str(item.data.get("status", "active")).casefold() not in {"recovered", "resolved", "deceased", "ended"}]
    failed_rolls = [item for item in by_kind["roll"] if str(item.data.get("outcome", "")).casefold() in {"failed", "bad", "failure"}]
    occult_outcomes = sorted(
        [item for item in by_kind["roll"] if bool((item.data or {}).get("occult_roll")) and bool((item.data or {}).get("completed"))],
        key=lambda item:(int((item.data or {}).get("completed_global_day") or item.global_day or 0), updated_timestamp(item)), reverse=True,
    )
    game_history = sorted(by_kind["game_history"], key=lambda item: (item.global_day or 0, updated_timestamp(item)), reverse=True)
    recent = sorted([item for item in records if item.global_day is not None and item.global_day <= save.global_day], key=lambda item: (item.global_day, updated_timestamp(item)), reverse=True)[:12]
    hooks = []
    for event in active_events[:4]: hooks.append({"tone": "world", "title": event.label, "text": f"The household must respond while {event.label} shapes the year {year_for(save, save.global_day)}."})
    for pregnancy in pregnancies[:4]: hooks.append({"tone": "family", "title": f"An heir may be coming", "text": f"{pregnancy.data.get('mother_name') or pregnancy.label} is expecting {pregnancy.data.get('babies_expected', 1)} child or children around year {year_for(save, pregnancy.data.get('due_global_day'))}."})
    for illness in illnesses[:4]: hooks.append({"tone": "danger", "title": illness.data.get("illness_name") or illness.label, "text": f"{illness.data.get('sim_name') or illness.label} remains affected; treatment and household consequences are unresolved."})
    for roll in failed_rolls[-4:]: hooks.append({"tone": "danger", "title": "The dice have spoken", "text": f"{roll.label} failed, leaving consequences that should echo through the family chronicle."})
    for roll in occult_outcomes[:6]:
        data=roll.data or {};hooks.append({"tone":"danger" if data.get("triggered") else "world","title":roll.label,
            "text":f"The {data.get('die') or 'die'} showed {data.get('actual')}: {data.get('outcome') or 'the occult check was resolved'}."})
    for entry in game_history[:4]:
        category = str((entry.data or {}).get("category") or "life event").replace("_", " ").title()
        hooks.append({"tone": "family" if category in {"Life Stage", "Milestone", "Pregnancy Progress"} else "world",
                      "title": category, "text": entry.label})
    if not hooks:
        largest = max(household_members.items(), key=lambda pair: len(pair[1]), default=(None, []))
        if largest[1]: hooks.append({"tone": "family", "title": "A household at the center", "text": f"{households.get(largest[0]).label if largest[0] in households else 'The household'} holds {len(largest[1])} living Sims and offers the strongest place to continue the story."})
    chapters = []
    grouped = defaultdict(list)
    for item in records:
        # Only facts that have reached the current tracker day enter the prose.
        # Pending rolls remain choices for Today rather than completed history.
        if item.kind == "roll" and not bool((item.data or {}).get("completed")):
            continue
        day = _story_day(item)
        if day is not None and day <= save.global_day:
            grouped[year_for(save, day)].append(item)
    current_year = year_for(save, save.global_day)
    for year in range(current_year, save.start_year - 1, -1):
        entries = sorted(grouped.get(year, []), key=lambda item: (_story_day(item) or 0, updated_timestamp(item)))
        headline, paragraph = _annual_paragraph(save, year, entries, sims, by_kind["event"])
        chapters.append({"year": year, "headline": headline, "summary": paragraph,
                         "paragraph": paragraph, "entries": entries[:12], "entry_count": len(entries)})
    surname_counts = Counter((sim.data.get("last_name") or sim.label.split()[-1] if sim.label else "Unknown") for sim in living)
    history_by_sim = defaultdict(list)
    for entry in game_history:
        if (entry.data or {}).get("sim_id"):
            history_by_sim[str(entry.data["sim_id"])].append(entry)
    personal_threads = []
    for sim in living:
        entries = history_by_sim.get(sim.id, [])
        if not entries:
            continue
        data = sim.data or {}
        facts = [value for value in (data.get("game_age_stage"), data.get("game_career"), data.get("game_education")) if value]
        personal_threads.append({"sim": sim, "entries": entries[:4], "current": " · ".join(str(value).replace("Age.", "").replace("_", " ") for value in facts)})
    personal_threads.sort(key=lambda item: updated_timestamp(item["entries"][0]), reverse=True)
    authored = sorted(by_kind["story_entry"], key=lambda item:(item.global_day or 0,updated_timestamp(item)), reverse=True)
    narrator_id=str((save.settings or {}).get("current_heir_id") or "")
    narrator=next((sim for sim in sims if sim.id==narrator_id),None) or (living[0] if living else None)
    present=[]
    if active_events: present.append(f"the shadow of {active_events[0].label} lies across our days")
    if pregnancies: present.append(f"we await {len(pregnancies)} birth{'s' if len(pregnancies)!=1 else ''}")
    if illnesses: present.append(f"{len(illnesses)} illness{'es' if len(illnesses)!=1 else ''} trouble the household")
    if not present: present.append("the household passes through a quieter season")
    opening=f"I, {narrator.label if narrator else 'the keeper of this chronicle'}, write in the year {year_for(save,save.global_day)}: " + "; ".join(present) + "."
    arcs=[]
    for pregnancy in pregnancies[:4]:
        arcs.append({"kind":"legacy","title":f"The expected child of {pregnancy.data.get('mother_name') or pregnancy.label}","status":"unresolved","stakes":"succession, household resources, and maternal safety","record_id":pregnancy.id})
    for illness in illnesses[:4]:
        arcs.append({"kind":"health","title":f"{illness.data.get('sim_name') or illness.label} against {illness.data.get('illness_name') or illness.label}","status":"danger","stakes":"recovery, contagion, and the stability of the household","record_id":illness.id})
    for event in active_events[:4]:
        arcs.append({"kind":"world","title":event.label,"status":"in motion","stakes":"the choices and survival of every eligible household member","record_id":event.id})
    for roll in failed_rolls[-4:]:
        arcs.append({"kind":"consequence","title":roll.label,"status":"consequence due","stakes":"whether the family can absorb what the failed roll set in motion","record_id":roll.id})
    return {
        "revision": save.revision, "year": year_for(save, save.global_day),
        "living": len(living), "deceased": len(deceased), "households": len(households),
        "active_events": len(active_events), "pregnancies": len(pregnancies), "illnesses": len(illnesses),
        "dominant_lines": surname_counts.most_common(5), "hooks": hooks[:10],
        "chapters": chapters, "recent": recent, "recent_game_history": game_history[:16],
        "personal_threads": personal_threads[:8],
        "occult_outcomes":occult_outcomes,
        "authored_entries":authored, "opening":opening, "all_sims":sims,
        "arcs":arcs[:12],
    }


def _story_facts(session: Session, save: ChronicleSave) -> tuple[list[Record], list[str]]:
    allowed={"sim","relationship","pregnancy","illness","roll","event","death","migration","game_history","session_journal","university_enrollment","university_term","university_performance"}
    rows=list(session.scalars(select(Record).where(
        Record.save_id==save.id,Record.kind.in_(allowed),Record.deleted.is_(False)
    ).order_by(Record.global_day.desc(),Record.updated_at.desc()).limit(80)))
    current=[item for item in rows if (item.global_day is None or int(item.global_day)<=save.global_day)
             and not (item.kind=="roll" and bool((item.data or {}).get("occult_roll")) and not bool((item.data or {}).get("completed")))]
    facts=[]
    for item in current[:30]:
        data=item.data or {}
        detail=[]
        if item.kind=="illness": detail.append(str(data.get("illness_name") or "illness"))
        if item.kind=="roll": detail.extend([str(data.get("roll_type") or "roll"),str(data.get("outcome") or "pending")])
        if item.kind=="relationship": detail.append(str(data.get("type") or "relationship"))
        if item.kind=="pregnancy": detail.append(str(data.get("status") or "active"))
        facts.append(f"GD {item.global_day}: {item.kind} — {item.label}"+(f" ({', '.join(detail)})" if detail else ""))
    return current,facts


def _offline_chapter(session: Session, save: ChronicleSave, narrator: Record | None,
                     tone: str) -> tuple[str,str,list[str]]:
    rows,facts=_story_facts(session,save)
    story=build(session,save)
    seed=int(hashlib.sha256(f"{save.id}:{save.revision}:{tone}:{narrator.id if narrator else ''}".encode()).hexdigest()[:16],16)
    rng=random.Random(seed)
    voice=narrator.label if narrator else "the household chronicler"
    year=year_for(save,save.global_day)
    opening_choices={
        "intimate":[f"I, {voice}, set down what this household dared not say aloud in the year {year}.",f"In the year {year}, I, {voice}, began this account by candlelight."],
        "dramatic":[f"The year {year} did not arrive quietly, and I, {voice}, bear witness.",f"By the year {year}, every promise in the household had acquired a price."],
        "formal":[f"Let it be recorded by {voice} that the household entered the year {year} under changing circumstances.",f"This account, kept by {voice}, records the principal matters of the year {year}."],
        "hopeful":[f"Though the year {year} tested us, I, {voice}, found reasons to believe the line would endure.",f"I, {voice}, write in the year {year} with the future still open before us."],
    }
    first=rng.choice(opening_choices.get(tone,opening_choices["intimate"]))
    hooks=story.get("hooks") or []
    middle="The days passed in uneasy balance."
    if hooks:
        chosen=hooks[:3]
        middle=" ".join(hook["text"] for hook in chosen)
    living=[item for item in rows if item.kind=="sim" and not (item.data or {}).get("death_global_day")]
    names=[item.label for item in living[:4]]
    close=(f"For now, the next choice belongs to {rng.choice(names)}; what follows will be measured against the family already recorded here."
           if names else "For now, the chronicle waits for the next decision that will give this quiet interval its meaning.")
    title=rng.choice((f"The Household in {year}",f"A Turning of the Year",f"What Was Owed to the Future",f"The Chronicle at Global Day {save.global_day}"))
    return title,"\n\n".join((first,middle,close)),facts


def generate_chapter(session: Session, save: ChronicleSave, narrator_id: str = "",
                     tone: str = "intimate", use_ai: bool = False) -> Record:
    narrator=session.get(Record,narrator_id) if narrator_id else None
    if narrator and (narrator.save_id!=save.id or narrator.kind!="sim" or narrator.deleted): narrator=None
    title,body,facts=_offline_chapter(session,save,narrator,tone)
    source="offline story engine"
    if use_ai:
        try:
            from .portraits import effective_config
            config=effective_config()
            if config.get("openai_api_key"):
                from openai import OpenAI
                client=OpenAI(api_key=config["openai_api_key"])
                instructions=("Write a concise three-paragraph historical family chronicle in first person. "
                              "Use only the supplied facts; do not invent births, deaths, illnesses, marriages, dates, titles, or outcomes. "
                              f"Narrator: {narrator.label if narrator else 'household chronicler'}. Tone: {tone}.")
                response=client.responses.create(model="gpt-5-mini",input=instructions+"\n\nFACTS\n"+"\n".join(facts))
                generated=str(response.output_text or "").strip()
                if generated:
                    body=generated;source="OpenAI story engine"
        except Exception:
            source="offline story engine (AI fallback)"
    entry=Record(save_id=save.id,kind="story_entry",label=title,global_day=save.global_day,data={
        "body":body,"narrator_sim_id":narrator.id if narrator else None,
        "narrator_name":narrator.label if narrator else "Household chronicler",
        "automated":True,"tone":tone,"source":source,"source_revision":save.revision,
        "grounding_facts":facts,
    })
    session.add(entry);session.flush()
    from .domain import journal
    journal(session,entry,"upsert",0);save.revision+=1
    return entry
