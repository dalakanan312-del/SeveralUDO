from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ChronicleSave, Record
from .domain import end_illnesses_for_death, event_is_ignored, journal
from . import game_metadata, telemetry


def _key(action: str, sim_id: str, value: str) -> str:
    return f"{action}:{sim_id}:{value}".casefold()


def _detected_list(value) -> list[str]:
    """Normalize optional game telemetry into stable, readable labels."""
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        value = [{"name": key, "value": item} for key, item in value.items()]
    elif not isinstance(value, (list, tuple, set)):
        value = [value]
    result = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("display_name") or item.get("title") or item.get("trait") or item.get("skill") or "").strip()
            level = item.get("level", item.get("value"))
            label = f"{name} (level {level})" if name and level not in (None, "") else name
        else:
            label = str(item).strip()
        if label and label not in result:
            result.append(label)
    return result


def _signal_labels(snapshot: dict) -> list[str]:
    """Collect human-readable state labels without treating arbitrary telemetry as fact."""
    result: list[str] = []

    def visit(value, depth: int = 0):
        if depth > 3 or value in (None, ""):
            return
        if isinstance(value, str):
            label = value.strip()
            if label and not label.isdigit() and label not in result:
                result.append(label)
            return
        if isinstance(value, dict):
            preferred = next((value.get(key) for key in (
                "display_name", "resolved_name", "name", "label", "title", "description",
                "trait", "buff", "moodlet", "state",
            ) if value.get(key) not in (None, "")), None)
            if preferred is not None:
                visit(preferred, depth + 1)
            else:
                for item in value.values():
                    visit(item, depth + 1)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item, depth + 1)

    for field in (
        "traits", "trait_details", "health_buffs", "symptoms", "fears", "lifestyles",
        "buffs", "moodlets", "status_effects", "legal_states", "grief_states",
    ):
        if field in snapshot:
            visit(snapshot.get(field))
    return result


_LEGAL_SIGNAL_WORDS = (
    "arrested", "under arrest", "arrest warrant", "probation", "parole", "sentenced",
    "prisoner", "inmate", "incarcerat", "community service", "criminal record", "wanted",
    "charged with", "convicted", "court summons", "law and disorder",
)
_GRIEF_SIGNAL_WORDS = (
    "grief", "grieving", "mourning", "bereaved", "bereavement", "lost a loved one",
    "death of a loved one", "widow grief",
)


def _positive_count(*values) -> int | None:
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def _percentage(*values) -> float | None:
    """Return the first reported fraction/percentage as a 0–100 value."""
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        return round(max(0.0, min(100.0, number * 100 if number <= 1 else number)), 2)
    return None


# Fields with a dedicated tracker destination. Any future Clock Sync field not
# listed here is retained in ``game_telemetry_extra`` instead of disappearing.
_CONSUMED_SIM_TELEMETRY = {
    "game_sim_id", "first_name", "last_name", "name", "sex", "gender",
    "gender_value", "age_value", "age_stage", "age_days", "current_age_days",
    "sim_age_days", "age_progress", "age_progress_percentage",
    "age_progress_percent", "life_stage_progress", "days_until_age_up",
    "age_transition_ready", "is_baby", "birth_global_day", "game_birth_global_day",
    "household_id", "household_name", "household_funds", "is_household_head",
    "household_member_game_ids", "household_last_played_game_sim_id",
    "household_is_unplayed", "household_is_player", "world_name", "lot_name",
    "career", "education", "careers", "degrees", "school", "traits", "skills",
    "milestones", "skill_details", "milestone_details", "trait_details",
    "degree_details", "aspiration_details", "stable_tuning_ids",
    "parents", "parent_game_sim_ids", "children",
    "child_game_sim_ids", "siblings", "sibling_game_sim_ids", "grandparents",
    "grandparent_game_sim_ids", "grandchildren", "grandchild_game_sim_ids",
    "relationships", "significant_other_game_id", "is_pregnant",
    "inventory_items",
    "pregnancy_stage", "pregnancy_progress", "pregnancy_progress_percentage",
    "pregnancy_hours_remaining", "is_in_labor", "babies_expected",
    "pregnancy_offspring_count", "offspring_count", "baby_count",
    "babies_delivered", "pregnancy_outcome", "pregnancy_partner_game_sim_id",
    "other_parent_game_sim_id", "responsible_pregnancy_states",
    "responsible_pregnancy_detected", "illnesses", "health_buffs", "symptoms",
    "unknown_health_traits", "occult_types", "species_occult", "occult_progress",
    "aspirations", "active_aspiration", "completed_aspirations", "lifestyles",
    "fears", "character_values", "preferences", "is_dead", "death_type",
    "death_details", "is_ghost", "game_portrait", "portrait_image_base64",
    "portrait_mime_type", "portrait_source", "portrait_resource_instance",
    "has_embedded_portrait", "portrait_data_uri", "clock_sync_version", "game_build", "installed_packs",
    "detected_optional_mods", "telemetry_capabilities", "clock_sync_diagnostics",
    "telemetry_version", "detected_game_day", "detected_game_hour",
    "detected_game_minute", "detected_game_second", "detected_tracker_global_day", "source",
    "inferred_tracker_household_id", "detected_newborn_count", "detected_newborns",
}


def _unmapped_telemetry(snapshot: dict) -> dict:
    """Keep the latest unknown values so protocol additions are not discarded."""
    return {
        str(key): value for key, value in snapshot.items()
        if not str(key).startswith("_")
        and key not in _CONSUMED_SIM_TELEMETRY
        and not str(key).endswith("_scan_supported")
    }


def _game_sim(session: Session, save: ChronicleSave, game_sim_id: str) -> Record | None:
    game_sim_id = str(game_sim_id or "").strip()
    if not game_sim_id:
        return None
    return session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
        Record.data["game_sim_id"].as_string() == game_sim_id,
    ))


def parent_suggestions(session: Session, save: ChronicleSave, snapshot: dict,
                       exclude_sim_id: str | None = None) -> dict:
    """Resolve game genealogy to tracker Sims without inventing unknown parents."""
    parent_rows = [row for row in (snapshot.get("parents") or []) if isinstance(row, dict)]
    rows_by_id = {
        str(row.get("game_sim_id") or "").strip(): row for row in parent_rows
        if str(row.get("game_sim_id") or "").strip()
    }
    game_ids = []
    for value in list(snapshot.get("parent_game_sim_ids") or []) + list(rows_by_id):
        game_id = str(value or "").strip()
        if game_id and game_id not in game_ids:
            game_ids.append(game_id)
    matched: list[tuple[Record, dict]] = []
    for game_id in game_ids:
        parent = _game_sim(session, save, game_id)
        if parent and parent.id != exclude_sim_id and all(item[0].id != parent.id for item in matched):
            matched.append((parent, rows_by_id.get(game_id, {})))
    result = {
        "parent_ids": [parent.id for parent, _ in matched],
        "inferred_mother_id": None, "inferred_mother_name": "",
        "inferred_father_id": None, "inferred_father_name": "",
    }
    unassigned = []
    for parent, row in matched:
        sex = str(row.get("sex") or (parent.data or {}).get("sex") or "").casefold()
        if "female" in sex and not result["inferred_mother_id"]:
            result.update(inferred_mother_id=parent.id, inferred_mother_name=parent.label)
        elif "male" in sex and "female" not in sex and not result["inferred_father_id"]:
            result.update(inferred_father_id=parent.id, inferred_father_name=parent.label)
        else:
            unassigned.append(parent)
    for field, name_field in (("inferred_mother_id", "inferred_mother_name"),
                              ("inferred_father_id", "inferred_father_name")):
        if not result[field] and unassigned:
            parent = unassigned.pop(0)
            result[field], result[name_field] = parent.id, parent.label
    return result


def resolve_parent_links(session: Session, save: ChronicleSave) -> int:
    """Retry unresolved genealogy after every report as newly detected parents are linked."""
    changed = 0
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    by_game_id = {
        str((sim.data or {}).get("game_sim_id") or "").strip(): sim for sim in sims
        if str((sim.data or {}).get("game_sim_id") or "").strip()
    }
    for sim in sims:
        data = dict(sim.data or {})
        if not data.get("parent_game_sim_ids"):
            continue
        hints = parent_suggestions(session, save, {
            "parent_game_sim_ids": data.get("parent_game_sim_ids") or [],
            "parents": data.get("game_parents") or [],
        }, sim.id)
        updates = {"parent_ids": hints["parent_ids"]} if hints["parent_ids"] or data.get("parent_ids") else {}
        if not data.get("mother_id") and hints.get("inferred_mother_id"):
            updates["mother_id"] = hints["inferred_mother_id"]
        if not data.get("father_id") and hints.get("inferred_father_id"):
            updates["father_id"] = hints["inferred_father_id"]
        if any(data.get(key) != value for key, value in updates.items()):
            base = sim.version
            sim.data = {**data, **updates}
            sim.version += 1
            journal(session, sim, "upsert", base)
            changed += 1
    # Some game builds expose a parent's children more reliably than the
    # child's parents. Use that inverse edge to fill missing factual links.
    for parent in sims:
        parent_data = dict(parent.data or {})
        parent_game_id = str(parent_data.get("game_sim_id") or "").strip()
        if not parent_game_id:
            continue
        for child_game_id in parent_data.get("child_game_sim_ids") or ():
            child = by_game_id.get(str(child_game_id or "").strip())
            if not child or child.id == parent.id:
                continue
            child_data = dict(child.data or {})
            parent_ids = [str(value) for value in (child_data.get("parent_ids") or []) if value]
            game_parent_ids = [str(value) for value in (child_data.get("parent_game_sim_ids") or []) if value]
            if parent.id not in parent_ids:
                parent_ids.append(parent.id)
            if parent_game_id not in game_parent_ids:
                game_parent_ids.append(parent_game_id)
            updates = {"parent_ids": parent_ids, "parent_game_sim_ids": game_parent_ids}
            sex = str(parent_data.get("sex") or parent_data.get("game_sex") or "").casefold()
            if "female" in sex and not child_data.get("mother_id"):
                updates["mother_id"] = parent.id
            elif "male" in sex and "female" not in sex and not child_data.get("father_id"):
                updates["father_id"] = parent.id
            if any(child_data.get(key) != value for key, value in updates.items()):
                base = child.version
                child.data = {**child_data, **updates}
                child.version += 1
                journal(session, child, "upsert", base)
                changed += 1
    return changed


def _significant_relationship(category: str) -> bool:
    value = str(category or "").casefold()
    return any(marker in value for marker in ("marriage", "married", "spouse", "fianc", "engag"))


_GENERIC_RELATIONSHIP_CATEGORIES = {"", "relationship", "unknown", "other", "unspecified"}


def _relationship_score(value) -> float | None:
    try:
        return round(float(value), 1) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _family_game_ids(value: dict) -> set[str]:
    result = set()
    for key in (
        "parent_game_sim_ids", "child_game_sim_ids", "sibling_game_sim_ids",
        "grandparent_game_sim_ids", "grandchild_game_sim_ids",
    ):
        result.update(str(item).strip() for item in (value.get(key) or []) if str(item).strip())
    return result


def classify_game_relationship(relationship: dict) -> dict:
    """Classify Clock Sync relationships using only already-received data.

    Relationship bits are authoritative. Scores are a fallback for game builds
    or optional mods whose tuning names are unavailable to the script mod.
    Multiple natures are retained as tags because relatives and romantic
    partners can also be friends.
    """
    result = dict(relationship or {})
    raw_category = str(result.get("category") or "Relationship").strip()
    folded_category = raw_category.casefold()
    bit_labels = game_metadata.readable_named_labels(
        result.get("relationship_bits"), result.get("relationship_bit_details"), kind="relationship bit",
    )
    bit_text = " ".join(bit_labels).casefold().replace("_", " ").replace("-", " ")
    friendship = _relationship_score(result.get("friendship_score"))
    romance = _relationship_score(result.get("romance_score"))
    bit_words = set(bit_text.split())

    def contains(*markers: str) -> bool:
        return any(marker in bit_text for marker in markers)

    def has_word(*markers: str) -> bool:
        return any(marker in bit_words for marker in markers)

    reported_marriage = any(marker in folded_category for marker in ("marriage", "married", "spouse", "husband", "wife"))
    reported_engagement = any(marker in folded_category for marker in ("fianc", "engag", "betroth"))
    reported_former = any(marker in folded_category for marker in ("widow", "divorc", "former marriage", "ex spouse"))
    reported_family = any(marker in folded_category for marker in ("family", "relative", "parent", "child", "sibling"))
    reported_romantic = any(marker in folded_category for marker in ("romantic", "romance", "love interest", "lover", "sweetheart"))
    reported_friendship = any(marker in folded_category for marker in ("friend", "bestie", "acquaintance"))
    marriage = reported_marriage or contains("married", "marriage", "spouse", "husband", "wife")
    engagement = reported_engagement or contains("fiance", "fiancé", "engaged", "betroth")
    former = reported_former or contains("widow", "widower", "divorc", "ex spouse")
    family = bool(result.get("genealogy_family")) or reported_family or has_word(
        "family", "relative", "parent", "mother", "father", "child", "son", "daughter",
        "sibling", "brother", "sister", "grandparent", "grandchild", "aunt", "uncle",
        "niece", "nephew", "cousin",
    )
    romantic_bit = contains("romance", "romantic", "lover", "lovebirds", "sweetheart", "partner")
    friendship_bit = contains("friend", "besties", "acquaintance")

    # Preserve a useful explicit category, but replace the generic Clock Sync
    # fallback with evidence from bits and scores.
    source = "reported category"
    if former:
        category = "Widowed" if contains("widow", "widower") or "widow" in folded_category else "Divorced"
        source = "reported category" if reported_former else "relationship bits"
    elif marriage:
        category, source = "Marriage", "reported category" if reported_marriage else "relationship bits"
    elif engagement:
        category, source = "Engagement", "reported category" if reported_engagement else "relationship bits"
    elif family:
        category, source = "Family", "genealogy" if result.get("genealogy_family") else "reported category" if reported_family else "relationship bits"
    elif reported_romantic or romantic_bit or (romance is not None and abs(romance) >= 1):
        category, source = "Romantic", "reported category" if reported_romantic else "relationship bits" if romantic_bit else "romance score"
    elif reported_friendship or friendship_bit or (friendship is not None and friendship >= 35):
        category, source = "Friendship", "reported category" if reported_friendship else "relationship bits" if friendship_bit else "friendship score"
    elif folded_category not in _GENERIC_RELATIONSHIP_CATEGORIES:
        category = raw_category.title()
    elif friendship is not None or romance is not None:
        category, source = "Acquaintance", "relationship scores"
    else:
        category, source = "Relationship", "insufficient game detail"

    tags = []
    if family or category == "Family":
        tags.append("Family")
    # Genealogy is authoritative for family display. Broad game labels and
    # romance scores must not turn a factual parent/child/sibling pair into a
    # partner; explicit marriages and engagements were handled above.
    if category != "Family" and (marriage or engagement or romantic_bit or category in {"Marriage", "Engagement", "Romantic", "Divorced", "Widowed"} or (romance is not None and abs(romance) >= 1)):
        tags.append("Romantic")
    if friendship_bit or category == "Friendship" or (friendship is not None and friendship >= 35):
        tags.append("Friendship")
    if category == "Acquaintance" and not tags:
        tags.append("Acquaintance")
    result.update(
        category=category,
        relationship_bits=bit_labels,
        relationship_tags=tags,
        relationship_classification_source=source,
        friendship_score=friendship,
        romance_score=romance,
    )
    return result


def _relationship_candidate_worthy(relationship: dict) -> bool:
    return str((relationship or {}).get("category") or "Relationship").casefold() not in {
        "", "relationship", "unknown", "other", "unspecified", "acquaintance",
    }


def repair_relationship_inbox(session: Session, save: ChronicleSave) -> dict[str, int]:
    """Repair old generic relationship reviews and silence acquaintances.

    This is deliberately tracker-side: it uses candidate payloads and the most
    recent game relationship snapshot already stored on each Sim.
    """
    rows = list(session.scalars(select(Record).where(
        Record.save_id == save.id,
        Record.kind == "game_candidate",
        Record.deleted.is_(False),
        Record.data["status"].as_string() == "pending",
        Record.data["action"].as_string().in_(("relationship_change", "relationship_end")),
    )))
    result = {"classified": 0, "dismissed": 0}
    for item in rows:
        item_data = dict(item.data or {})
        if item_data.get("action") not in {"relationship_change", "relationship_end"}:
            continue
        payload = dict(item_data.get("payload") or {})
        sim = session.get(Record, item_data.get("sim_id")) if item_data.get("sim_id") else None
        other_game_id = str(payload.get("other_game_sim_id") or "").strip()
        stored = {}
        for relationship in ((sim.data or {}).get("game_relationships") or []) if sim else []:
            if isinstance(relationship, dict) and str(relationship.get("other_game_sim_id") or "").strip() == other_game_id:
                stored = relationship
                break
        evidence = {**stored, **{key: value for key, value in payload.items() if value not in (None, "", [])}}
        if sim and other_game_id in _family_game_ids(sim.data or {}):
            evidence["genealogy_family"] = True
        classified = classify_game_relationship(evidence)
        if classified == payload:
            continue
        base = item.version
        if _relationship_candidate_worthy(classified):
            item_data["payload"] = classified
            item.label = f"{classified['category']} detected for {sim.label if sim else item.label}"
            result["classified"] += 1
        else:
            item_data.update(
                status="dismissed",
                auto_resolution="Generic acquaintance suppressed by relationship classifier",
            )
            item_data["payload"] = classified
            result["dismissed"] += 1
        item.data = item_data
        item.version += 1
        journal(session, item, "upsert", base)
    return result


_RELATIONSHIP_CLASSIFICATION_REPAIR_VERSION = 2


def repair_relationship_classifications(session: Session, save: ChronicleSave) -> dict[str, int]:
    """Repair accepted family records that were previously stored as romance.

    The repair uses factual genealogy already present on either Sim. Marriage,
    engagement and betrothal records are preserved, including uncommon family
    marriages, while generic and love-interest records for a parent, child or
    sibling pair become Family. The operation is journaled and runs once per
    save.
    """
    settings = dict(save.settings or {})
    if int(settings.get("relationship_classification_repair_version") or 0) >= _RELATIONSHIP_CLASSIFICATION_REPAIR_VERSION:
        return {"records": 0}
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    by_id = {sim.id: sim for sim in sims}
    family_pairs: set[frozenset[str]] = set()
    for sim in sims:
        data = sim.data or {}
        for relative_id in (data.get("mother_id"), data.get("father_id")):
            if relative_id in by_id and relative_id != sim.id:
                family_pairs.add(frozenset((sim.id, str(relative_id))))
    # Shared recorded parents are factual sibling evidence even when an older
    # Clock Sync payload did not include explicit sibling IDs.
    for index, sim in enumerate(sims):
        sim_parents = {
            str(relative_id) for relative_id in ((sim.data or {}).get("mother_id"), (sim.data or {}).get("father_id"))
            if relative_id in by_id
        }
        if not sim_parents:
            continue
        for sibling in sims[index + 1:]:
            sibling_parents = {
                str(relative_id) for relative_id in ((sibling.data or {}).get("mother_id"), (sibling.data or {}).get("father_id"))
                if relative_id in by_id
            }
            if sim_parents.intersection(sibling_parents):
                family_pairs.add(frozenset((sim.id, sibling.id)))
    game_to_tracker = {
        str((sim.data or {}).get("game_sim_id") or "").strip(): sim.id for sim in sims
        if str((sim.data or {}).get("game_sim_id") or "").strip()
    }
    for sim in sims:
        game_id = str((sim.data or {}).get("game_sim_id") or "").strip()
        if not game_id:
            continue
        for relative_game_id in _family_game_ids(sim.data or {}):
            relative_id = game_to_tracker.get(relative_game_id)
            if relative_id and relative_id != sim.id:
                family_pairs.add(frozenset((sim.id, relative_id)))

    changed = 0
    for relationship in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False),
    )):
        data = dict(relationship.data or {})
        pair = frozenset((str(data.get("partner1_id") or ""), str(data.get("partner2_id") or "")))
        if pair not in family_pairs:
            continue
        relationship_type = str(data.get("type") or "Relationship")
        folded = relationship_type.casefold()
        protected_partner = bool(data.get("legally_married")) or any(
            marker in folded for marker in ("marriage", "married", "spouse", "engag", "fianc", "betroth")
        )
        if protected_partner or folded == "family":
            continue
        tags = [str(tag) for tag in (data.get("relationship_tags") or []) if str(tag).casefold() != "family"]
        tags = [tag for tag in tags if tag.casefold() != "romantic"]
        tags.insert(0, "Family")
        base = relationship.version
        data.update(
            type="Family",
            relationship_tags=list(dict.fromkeys(tags)),
            relationship_classification_source="genealogy repair",
            previous_automatic_relationship_type=relationship_type,
        )
        relationship.data = data
        relationship.version += 1
        journal(session, relationship, "upsert", base)
        changed += 1
    settings["relationship_classification_repair_version"] = _RELATIONSHIP_CLASSIFICATION_REPAIR_VERSION
    save.settings = settings
    save.revision += changed + 1
    return {"records": changed}


_HASH_NAME_REPAIR_VERSION = 3
_HASHED_SIM_COLLECTIONS = (
    ("game_traits", "game_trait_details", "trait"),
    ("game_skills", "game_skill_details", "skill"),
    ("game_milestones", "game_milestone_details", "milestone"),
    ("game_degrees", "game_degree_details", "degree"),
    ("game_aspirations", "game_aspiration_details", "aspiration"),
    ("game_completed_aspirations", "game_aspiration_details", "aspiration"),
    ("game_lifestyles", "game_trait_details", "lifestyle"),
    ("game_fears", "game_trait_details", "fear"),
    ("game_character_values", "game_trait_details", "trait"),
    ("game_preferences", None, "preference"),
)


def repair_hashed_sim_metadata(session: Session, save: ChronicleSave) -> dict[str, int]:
    """Replace legacy hash labels using local STBL and Clock Sync details once."""
    settings = dict(save.settings or {})
    if int(settings.get("hash_name_repair_version") or 0) >= _HASH_NAME_REPAIR_VERSION:
        return {"sims": 0, "labels": 0}
    sims = list(session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
    )))
    family_game_pairs = {
        frozenset((game_id, relative_id))
        for sim in sims
        for game_id in (str((sim.data or {}).get("game_sim_id") or "").strip(),)
        for relative_id in _family_game_ids(sim.data or {})
        if game_id and relative_id and game_id != relative_id
    }
    aliases: dict[int, str] = {}
    for sim in sims:
        data = sim.data or {}
        for field, detail_field, kind in _HASHED_SIM_COLLECTIONS:
            aliases.update(game_metadata.localization_aliases(
                data.get(field), data.get(detail_field) if detail_field else None, kind,
            ))
    changed_sims = changed_labels = 0
    def values_of(value):
        return value if isinstance(value, (list, tuple, set)) else ([] if value in (None, "") else [value])
    for sim in sims:
        data = dict(sim.data or {})
        updates = {}
        raw_values = dict(data.get("game_raw_localization_values") or {})
        for field, detail_field, kind in _HASHED_SIM_COLLECTIONS:
            if field not in data:
                continue
            resolved = game_metadata.readable_named_labels(
                data.get(field), data.get(detail_field) if detail_field else None,
                kind=kind, aliases=aliases,
            )
            if resolved != data.get(field):
                updates[field] = resolved
                if any(game_metadata.localization_hash(value) is not None for value in values_of(data.get(field))):
                    raw_values[field] = data.get(field)
                changed_labels += sum(
                    game_metadata.localization_hash(value) is not None
                    for value in values_of(data.get(field))
                )
        relationships = []
        relationship_changed = False
        for relationship in data.get("game_relationships") or []:
            if not isinstance(relationship, dict):
                relationships.append(relationship)
                continue
            other_game_id = str(relationship.get("other_game_sim_id") or "").strip()
            game_id = str(data.get("game_sim_id") or "").strip()
            normalized = classify_game_relationship({
                **relationship,
                "genealogy_family": bool(relationship.get("genealogy_family"))
                or (bool(game_id and other_game_id) and frozenset((game_id, other_game_id)) in family_game_pairs),
            })
            relationships.append(normalized)
            relationship_changed = relationship_changed or normalized != relationship
        if relationship_changed:
            updates["game_relationships"] = relationships
        active_aspiration = data.get("game_active_aspiration")
        if active_aspiration not in (None, ""):
            resolved_active = game_metadata.readable_named_labels(
                active_aspiration, kind="aspiration", aliases=aliases,
            )
            if resolved_active and resolved_active[0] != active_aspiration:
                updates["game_active_aspiration"] = resolved_active[0]
                if game_metadata.localization_hash(active_aspiration) is not None:
                    raw_values["game_active_aspiration"] = active_aspiration
                    changed_labels += 1
        if raw_values != (data.get("game_raw_localization_values") or {}):
            updates["game_raw_localization_values"] = raw_values
        if not updates:
            continue
        base = sim.version
        data.update(updates)
        if aliases:
            data["game_localization_names"] = {str(key): value for key, value in sorted(aliases.items())}
        sim.data = data
        sim.version += 1
        journal(session, sim, "upsert", base)
        changed_sims += 1
    settings["hash_name_repair_version"] = _HASH_NAME_REPAIR_VERSION
    settings["hash_name_repair_unresolved"] = sum(
        1 for sim in sims for field, _, _ in _HASHED_SIM_COLLECTIONS
        for value in values_of((sim.data or {}).get(field))
        if game_metadata.localization_hash(value) is not None
    )
    save.settings = settings
    save.revision += changed_sims + 1
    return {"sims": changed_sims, "labels": changed_labels}


def _existing_relationship(session: Session, save: ChronicleSave, first: Record,
                           second: Record | None, category: str) -> bool:
    if not second:
        return False
    for relationship in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False),
    )):
        data = relationship.data or {}
        if {str(data.get("partner1_id") or ""), str(data.get("partner2_id") or "")} != {first.id, second.id}:
            continue
        existing_type = str(data.get("type") or "relationship").casefold()
        if (_significant_relationship(category) and (bool(data.get("legally_married")) or _significant_relationship(existing_type))) or existing_type == str(category or "relationship").casefold():
            return True
    return False


def _existing_relationship_pair(session: Session, save: ChronicleSave, first: Record,
                                second: Record | None) -> bool:
    """Return true for any current relationship between two tracker Sims."""
    if not second:
        return False
    closed = {"ended", "divorced", "annulled", "separated", "inactive", "closed"}
    for relationship in session.scalars(select(Record).where(
        Record.save_id == save.id, Record.kind == "relationship", Record.deleted.is_(False),
    )):
        data = relationship.data or {}
        if {str(data.get("partner1_id") or ""), str(data.get("partner2_id") or "")} != {first.id, second.id}:
            continue
        if str(data.get("status") or "active").casefold() not in closed:
            return True
    return False


def candidate(session: Session, save: ChronicleSave, action: str, sim: Record | None, label: str, payload: dict, identity: str) -> Record | None:
    source_key = _key(action, sim.id if sim else "new", identity)
    exists = session.scalar(select(Record.id).where(
        Record.save_id == save.id, Record.kind == "game_candidate",
        Record.deleted.is_(False),
        Record.data["source_key"].as_string() == source_key,
    ).limit(1))
    if exists:
        return None
    item = Record(save_id=save.id, kind="game_candidate", label=label, global_day=save.global_day,
                  data={"action": action, "sim_id": sim.id if sim else None, "status": "pending", "source_key": source_key, "payload": payload})
    session.add(item); session.flush(); journal(session, item, "upsert", 0)
    return item


def reconcile_sim(session: Session, save: ChronicleSave, sim: Record, snapshot: dict) -> list[Record]:
    """Turn one guarded game snapshot into safe telemetry and confirmable changes."""
    named_collections = (
        ("traits", "trait_details", "trait"),
        ("skills", "skill_details", "skill"),
        ("milestones", "milestone_details", "milestone"),
        ("degrees", "degree_details", "degree"),
        ("aspirations", "aspiration_details", "aspiration"),
        ("completed_aspirations", "aspiration_details", "aspiration"),
        ("lifestyles", "trait_details", "lifestyle"),
        ("fears", "trait_details", "fear"),
        ("character_values", "trait_details", "trait"),
        ("preferences", None, "preference"),
    )
    if any(source in snapshot for source, _, _ in named_collections):
        snapshot = dict(snapshot)
        for source, detail_source, kind in named_collections:
            if source in snapshot:
                snapshot[source] = game_metadata.readable_named_labels(
                    snapshot.get(source), snapshot.get(detail_source), kind=kind,
                )
        if snapshot.get("active_aspiration") not in (None, ""):
            active = game_metadata.readable_named_labels(
                snapshot.get("active_aspiration"), kind="aspiration",
            )
            snapshot["active_aspiration"] = active[0] if active else None
    if "relationships" in snapshot:
        family_game_ids = _family_game_ids(snapshot)
        snapshot = {
            **snapshot,
            "relationships": [
                classify_game_relationship({
                    **value,
                    "genealogy_family": str(value.get("other_game_sim_id") or "").strip() in family_game_ids,
                })
                for value in (snapshot.get("relationships") or []) if isinstance(value, dict)
            ],
        }
    if "responsible_pregnancy_states" in snapshot:
        snapshot = {
            **snapshot,
            "responsible_pregnancy_states": game_metadata.responsible_pregnancy_states(
                snapshot.get("responsible_pregnancy_states")
            ),
        }
    made = []
    data = dict(sim.data or {})
    history_entries = telemetry.capture_sim_changes(session, save, sim, snapshot, data)
    previously_observed_dead = data.get("game_was_dead")
    incoming_first = str(snapshot.get("first_name") or "").strip()
    incoming_last = str(snapshot.get("last_name") or "").strip()
    incoming_sex = str(snapshot.get("sex") or "").strip()
    old_first = str(data.get("first_name") or "").strip()
    old_last = str(data.get("last_name") or "").strip()
    old_sex = str(data.get("sex") or "").strip()
    previous_significant_other = str(data.get("game_significant_other_game_sim_id") or "").strip()
    name_changed = bool(incoming_first and (incoming_first.casefold(), incoming_last.casefold()) != (old_first.casefold(), old_last.casefold()))
    sex_changed = bool(incoming_sex and old_sex and incoming_sex.casefold() != old_sex.casefold())
    if name_changed or sex_changed:
        identity_payload = {
            **snapshot,
            "previous_first_name": old_first, "previous_last_name": old_last,
            "previous_sex": old_sex,
        }
        item = candidate(
            session, save, "sim_identity_change", sim,
            f"Profile change detected: {sim.label}", identity_payload,
            f"{incoming_first}:{incoming_last}:{incoming_sex}",
        )
        if item:
            made.append(item)
    has_pregnancy_state = "is_pregnant" in snapshot
    is_snapshot_pregnant = bool(snapshot.get("is_pregnant")) if has_pregnancy_state else bool(data.get("game_was_pregnant"))
    partner_game_id = str(snapshot.get("pregnancy_partner_game_sim_id") or snapshot.get("other_parent_game_sim_id") or "").strip()
    partner = session.scalar(select(Record).where(
        Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
        Record.data["game_sim_id"].as_string() == partner_game_id,
    )) if partner_game_id else None
    reported_count = _positive_count(
        snapshot.get("babies_expected"), snapshot.get("pregnancy_offspring_count"),
        snapshot.get("offspring_count"), snapshot.get("baby_count"),
    )
    occult = game_metadata.occult_identity(snapshot)
    age_progress = _percentage(
        snapshot.get("age_progress_percentage"), snapshot.get("age_progress"),
        snapshot.get("age_progress_percent"), snapshot.get("life_stage_progress"),
    )
    pregnancy_progress = _percentage(
        snapshot.get("pregnancy_progress_percentage"), snapshot.get("pregnancy_progress"),
    )
    scan_support = {
        str(key).removesuffix("_scan_supported"): bool(value)
        for key, value in snapshot.items() if str(key).endswith("_scan_supported")
    }
    prior_extra = dict(data.get("game_telemetry_extra") or {})
    extra = _unmapped_telemetry(snapshot)
    if extra:
        prior_extra.update(extra)
    telemetry_values = {
        "game_reported_first_name": incoming_first or None,
        "game_reported_last_name": incoming_last or None,
        "game_age_stage": snapshot.get("age_stage"), "game_sex": incoming_sex or None,
        "game_raw_gender_value": snapshot.get("gender_value"),
        "game_raw_age_value": snapshot.get("age_value"),
        "game_is_baby": snapshot.get("is_baby") if "is_baby" in snapshot else None,
        "game_age_days": snapshot.get("age_days"),
        "game_age_progress_percentage": age_progress,
        "game_days_until_age_up": snapshot.get("days_until_age_up"),
        "game_age_transition_ready": snapshot.get("age_transition_ready"),
        "game_career": snapshot.get("career"),
        "game_education": snapshot.get("education"), "game_traits": game_metadata.readable_trait_labels(snapshot.get("traits")),
        "game_careers": [row for row in (snapshot.get("careers") or []) if isinstance(row, dict)],
        "game_degrees": _detected_list(snapshot.get("degrees")), "game_school": snapshot.get("school"),
        "game_skills": _detected_list(snapshot.get("skills")),
        "game_milestones": _detected_list(snapshot.get("milestones")), "last_household_funds": snapshot.get("household_funds"),
        "game_skill_details": [row for row in (snapshot.get("skill_details") or []) if isinstance(row, dict)],
        "game_milestone_details": [row for row in (snapshot.get("milestone_details") or []) if isinstance(row, dict)],
        "game_trait_details": [row for row in (snapshot.get("trait_details") or []) if isinstance(row, dict)],
        "game_degree_details": [row for row in (snapshot.get("degree_details") or []) if isinstance(row, dict)],
        "game_aspiration_details": [row for row in (snapshot.get("aspiration_details") or []) if isinstance(row, dict)],
        "game_stable_tuning_ids": snapshot.get("stable_tuning_ids") or {},
        "last_game_world": snapshot.get("world_name"), "last_game_lot": snapshot.get("lot_name"),
        "game_is_household_head": snapshot.get("is_household_head") if "is_household_head" in snapshot else None,
        "game_household_member_game_ids": [str(value) for value in (snapshot.get("household_member_game_ids") or []) if value],
        "game_household_last_played_game_sim_id": snapshot.get("household_last_played_game_sim_id"),
        "game_household_is_unplayed": snapshot.get("household_is_unplayed") if "household_is_unplayed" in snapshot else None,
        "game_household_is_player": snapshot.get("household_is_player") if "household_is_player" in snapshot else None,
        "game_is_pregnant": snapshot.get("is_pregnant") if "is_pregnant" in snapshot else None,
        "game_pregnancy_progress_percentage": pregnancy_progress,
        "last_game_pregnancy_count": reported_count if is_snapshot_pregnant else None,
        "last_game_pregnancy_partner_game_sim_id": partner_game_id if is_snapshot_pregnant and partner_game_id else None,
        "game_significant_other_game_sim_id": snapshot.get("significant_other_game_id"),
        "parent_game_sim_ids": [str(value) for value in (snapshot.get("parent_game_sim_ids") or []) if value],
        "game_parents": [row for row in (snapshot.get("parents") or []) if isinstance(row, dict)],
        "game_children": [row for row in (snapshot.get("children") or []) if isinstance(row, dict)],
        "child_game_sim_ids": [str(value) for value in (snapshot.get("child_game_sim_ids") or []) if value],
        "game_siblings": [row for row in (snapshot.get("siblings") or []) if isinstance(row, dict)],
        "sibling_game_sim_ids": [str(value) for value in (snapshot.get("sibling_game_sim_ids") or []) if value],
        "game_grandparents": [row for row in (snapshot.get("grandparents") or []) if isinstance(row, dict)],
        "grandparent_game_sim_ids": [str(value) for value in (snapshot.get("grandparent_game_sim_ids") or []) if value],
        "game_grandchildren": [row for row in (snapshot.get("grandchildren") or []) if isinstance(row, dict)],
        "grandchild_game_sim_ids": [str(value) for value in (snapshot.get("grandchild_game_sim_ids") or []) if value],
        "game_relationships": [row for row in (snapshot.get("relationships") or []) if isinstance(row, dict)],
        "game_inventory_items": [row for row in (snapshot.get("inventory_items") or []) if isinstance(row, dict)],
        "game_inventory_scan_supported": snapshot.get("inventory_scan_supported") if "inventory_scan_supported" in snapshot else None,
        "game_health_buffs": [row for row in (snapshot.get("health_buffs") or []) if isinstance(row, dict)],
        "game_symptoms": _detected_list(snapshot.get("symptoms")),
        "game_pregnancy_stage": snapshot.get("pregnancy_stage"),
        "game_pregnancy_hours_remaining": snapshot.get("pregnancy_hours_remaining"),
        "game_in_labor": snapshot.get("is_in_labor"),
        "game_responsible_pregnancy_states": [
            row for row in (snapshot.get("responsible_pregnancy_states") or []) if isinstance(row, dict)
        ],
        "game_responsible_pregnancy_detected": snapshot.get("responsible_pregnancy_detected")
        if "responsible_pregnancy_detected" in snapshot else None,
        "game_occult_progress": snapshot.get("occult_progress") or {},
        "game_aspirations": _detected_list(snapshot.get("aspirations")),
        "game_active_aspiration": snapshot.get("active_aspiration"),
        "game_completed_aspirations": _detected_list(snapshot.get("completed_aspirations")),
        "game_lifestyles": _detected_list(snapshot.get("lifestyles")),
        "game_fears": _detected_list(snapshot.get("fears")),
        "game_character_values": _detected_list(snapshot.get("character_values")),
        "game_preferences": _detected_list(snapshot.get("preferences")),
        "game_is_dead": snapshot.get("is_dead") if "is_dead" in snapshot else None,
        "game_death_type": snapshot.get("death_type"),
        "game_death_details": snapshot.get("death_details") if isinstance(snapshot.get("death_details"), dict) else None,
        "game_is_ghost": snapshot.get("is_ghost") if "is_ghost" in snapshot else None,
        "game_portrait": snapshot.get("game_portrait") or {},
        "clock_sync_version": snapshot.get("clock_sync_version"),
        "game_build": snapshot.get("game_build"),
        "game_installed_packs": _detected_list(snapshot.get("installed_packs")),
        "game_detected_optional_mods": _detected_list(snapshot.get("detected_optional_mods")),
        "game_telemetry_capabilities": snapshot.get("telemetry_capabilities") or {},
        "game_clock_diagnostics": snapshot.get("clock_sync_diagnostics") or {},
        "game_scan_support": scan_support,
        "game_latest_telemetry_version": int(snapshot.get("telemetry_version") or 0),
        "game_latest_telemetry_source": snapshot.get("source") or "Clock Sync",
        "game_telemetry_extra": prior_extra,
    }
    if occult["display"]:
        telemetry_values.update({
            "species_occult": occult["display"], "game_occult_types": occult["types"],
            "game_occult_source": occult["source"], "game_occult_scan_supported": occult["authoritative"],
        })
    telemetry_version = int(snapshot.get("telemetry_version") or 0)
    clearable = {"game_traits", "game_career", "game_education"} if telemetry_version >= 2 else set()
    # Clock Sync 2.1.0 reports whether the game actually exposed each optional
    # tracker.  An empty supported scan is authoritative; an unavailable scan
    # must not erase skill or milestone data captured by an earlier report.
    if telemetry_version == 2 or snapshot.get("skills_scan_supported") is True:
        clearable.add("game_skills")
    if telemetry_version == 2 or snapshot.get("milestone_scan_supported") is True:
        clearable.add("game_milestones")
    if occult["authoritative"]:
        clearable.add("game_occult_types")
    if telemetry_version >= 4:
        supported_fields = {
            "pregnancy_scan_supported": {
                "game_pregnancy_stage", "game_pregnancy_progress_percentage",
                "game_pregnancy_hours_remaining", "last_game_pregnancy_count",
                "last_game_pregnancy_partner_game_sim_id",
            },
            "genealogy_scan_supported": {
                "game_children", "child_game_sim_ids", "game_siblings",
                "sibling_game_sim_ids", "game_grandparents",
                "grandparent_game_sim_ids", "game_grandchildren",
                "grandchild_game_sim_ids",
            },
            "relationship_scan_supported": {"game_relationships"},
            "health_scan_supported": {"game_health_buffs", "game_symptoms"},
            "responsible_pregnancy_scan_supported": {
                "game_responsible_pregnancy_states", "game_responsible_pregnancy_detected",
            },
            "career_scan_supported": {"game_careers"},
            "education_scan_supported": {"game_degrees", "game_school"},
            "personal_development_scan_supported": {"game_aspirations", "game_active_aspiration", "game_completed_aspirations", "game_lifestyles", "game_fears", "game_character_values", "game_preferences"},
            "occult_progress_scan_supported": {"game_occult_progress"},
            "death_scan_supported": {"game_death_type", "game_death_details"},
            "inventory_scan_supported": {"game_inventory_items"},
        }
        for supported_key, fields in supported_fields.items():
            if snapshot.get(supported_key) is True:
                clearable.update(fields)
    if telemetry_version >= 5:
        if snapshot.get("skills_scan_supported") is True:
            clearable.add("game_skill_details")
        if snapshot.get("milestone_scan_supported") is True:
            clearable.add("game_milestone_details")
        clearable.update({"game_trait_details", "game_degree_details", "game_aspiration_details", "game_stable_tuning_ids"})
    if telemetry_version >= 6 and snapshot.get("inventory_scan_supported") is True:
        clearable.update({"game_inventory_items", "game_inventory_scan_supported"})
    updates = {key: value for key, value in telemetry_values.items() if value not in (None, "", []) or key in clearable}
    changed_telemetry = any(data.get(key) != value for key, value in updates.items())
    if changed_telemetry:
        base = sim.version; data.update(updates); sim.data = data; sim.version += 1; journal(session, sim, "upsert", base)
    data = dict(sim.data or {})

    # Genealogy is factual save data, so fill only missing links and retry later
    # when a referenced parent has not yet been accepted into the tracker.
    hints = parent_suggestions(session, save, snapshot, sim.id)
    if hints["parent_ids"]:
        parent_updates = {"parent_ids": hints["parent_ids"]}
        if not data.get("mother_id") and hints.get("inferred_mother_id"):
            parent_updates["mother_id"] = hints["inferred_mother_id"]
        if not data.get("father_id") and hints.get("inferred_father_id"):
            parent_updates["father_id"] = hints["inferred_father_id"]
        if any(data.get(key) != value for key, value in parent_updates.items()):
            base = sim.version; sim.data = {**sim.data, **parent_updates}; sim.version += 1; journal(session, sim, "upsert", base)
            data = dict(sim.data or {})

    has_death_state = "is_dead" in snapshot
    currently_dead = bool(snapshot.get("is_dead")) if has_death_state else bool(previously_observed_dead)
    if has_death_state and currently_dead and previously_observed_dead is not True and not data.get("death_confirmed"):
        item = candidate(session, save, "sim_death", sim, f"Death detected: {sim.label}", snapshot, str(save.global_day))
        if item: made.append(item)
    elif has_death_state and not currently_dead and previously_observed_dead is True and data.get("death_confirmed"):
        item = candidate(session, save, "sim_resurrection", sim, f"Resurrection detected: {sim.label}", snapshot, str(save.global_day))
        if item: made.append(item)
    old_household = str(data.get("game_household_id") or "")
    new_household = str(snapshot.get("household_id") or "")
    if old_household and new_household and old_household != new_household:
        item = candidate(session, save, "household_change", sim, f"Household change: {sim.label}", snapshot, new_household)
        if item: made.append(item)
    elif new_household and not old_household:
        base = sim.version; sim.data = {**sim.data, "game_household_id": new_household, "game_household_name": snapshot.get("household_name")}; sim.version += 1; journal(session, sim, "upsert", base)

    has_relationship_state = "relationships" in snapshot
    relationships = [rel for rel in (snapshot.get("relationships") or []) if isinstance(rel, dict)]
    relationship_keys = sorted({
        f"{str(rel.get('other_game_sim_id') or '')}:{str(rel.get('category') or 'relationship').casefold()}"
        for rel in relationships if rel.get("other_game_sim_id")
    })
    prior_relationship_keys = set(data.get("game_relationship_keys") or [])
    has_relationship_baseline = "game_relationship_keys" in data
    current_game_id = str(data.get("game_sim_id") or snapshot.get("game_sim_id") or "")
    for rel in relationships:
        other = str(rel.get("other_game_sim_id") or "")
        category = str(rel.get("category") or "relationship")
        relationship_key = f"{other}:{category.casefold()}"
        other_sim = _game_sim(session, save, other)
        prior_categories_for_other = {
            key.partition(":")[2] for key in prior_relationship_keys
            if key.partition(":")[0] == other
        }
        is_classifier_upgrade = (
            relationship_key not in prior_relationship_keys
            and prior_categories_for_other
            and prior_categories_for_other.issubset(_GENERIC_RELATIONSHIP_CATEGORIES | {"acquaintance"})
        )
        is_new_transition = has_relationship_baseline and relationship_key not in prior_relationship_keys and not is_classifier_upgrade
        is_initial_significant = not has_relationship_baseline and _significant_relationship(category)
        is_canonical_endpoint = not (other_sim and current_game_id and other and current_game_id > other)
        if (other and (is_new_transition or is_initial_significant) and is_canonical_endpoint
                and _relationship_candidate_worthy(rel)
                and not _existing_relationship(session, save, sim, other_sim, category)):
            rel_payload = {
                **rel,
                "other_sim_id": other_sim.id if other_sim else None,
                "other_sim_name": other_sim.label if other_sim else "",
                "detected_game_day": snapshot.get("detected_game_day"),
                "detected_game_hour": snapshot.get("detected_game_hour"),
                "detected_game_minute": snapshot.get("detected_game_minute"),
                "detected_game_second": snapshot.get("detected_game_second"),
                "detected_tracker_global_day": snapshot.get("detected_tracker_global_day", save.global_day),
            }
            item = candidate(session, save, "relationship_change", sim, f"{category.title()} detected for {sim.label}", rel_payload, f"{other}:{category}")
            if item: made.append(item)
    prior_scandal_keys = set(data.get("game_scandal_signal_keys") or [])
    scandal_keys = set()
    for rel in relationships:
        other = str(rel.get("other_game_sim_id") or "")
        other_sim = _game_sim(session, save, other)
        if other_sim and current_game_id and other and current_game_id > other:
            continue
        for signal in rel.get("scandal_signals") or []:
            if not isinstance(signal, dict):
                continue
            signal_type = str(signal.get("type") or "possible_scandal").strip().casefold()
            signal_key = f"{other}:{signal_type}"
            scandal_keys.add(signal_key)
            if signal_key in prior_scandal_keys:
                continue
            payload = {
                **signal,
                "other_game_sim_id": other,
                "other_sim_id": other_sim.id if other_sim else None,
                "other_sim_name": other_sim.label if other_sim else "",
                "relationship_bits": rel.get("relationship_bits") or [],
                "friendship_score": rel.get("friendship_score"),
                "romance_score": rel.get("romance_score"),
                "detected_tracker_global_day": snapshot.get("detected_tracker_global_day", save.global_day),
                "detected_game_hour": snapshot.get("detected_game_hour"),
                "detected_game_minute": snapshot.get("detected_game_minute"),
            }
            item = candidate(
                session, save, "scandal_detected", sim,
                f"{str(signal.get('label') or 'Possible scandal')}: {sim.label}",
                payload, signal_key,
            )
            if item:
                made.append(item)
    signal_fields = {
        "traits", "trait_details", "health_buffs", "symptoms", "fears", "lifestyles",
        "buffs", "moodlets", "status_effects", "legal_states", "grief_states",
    }
    has_signal_state = any(field in snapshot for field in signal_fields)
    legal_signal_keys: set[str] = set()
    grief_signal_keys: set[str] = set()
    prior_legal_signal_keys = set(data.get("game_legal_signal_keys") or [])
    prior_grief_signal_keys = set(data.get("game_grief_signal_keys") or [])
    detected_mods = " ".join(str(value) for value in (snapshot.get("detected_optional_mods") or []))
    law_and_disorder = "law and disorder" in detected_mods.casefold()
    for label in _signal_labels(snapshot):
        normalized = " ".join(label.casefold().split())
        if any(word in normalized for word in _LEGAL_SIGNAL_WORDS):
            signal_key = normalized[:240]
            legal_signal_keys.add(signal_key)
            if signal_key not in prior_legal_signal_keys:
                payload = {
                    "signal_label": label,
                    "source_mod": "Law and Disorder" if law_and_disorder else "Game telemetry",
                    "detected_tracker_global_day": snapshot.get("detected_tracker_global_day", save.global_day),
                    "detected_game_hour": snapshot.get("detected_game_hour"),
                    "detected_game_minute": snapshot.get("detected_game_minute"),
                }
                item = candidate(
                    session, save, "legal_signal", sim,
                    f"Legal status needs review: {sim.label}", payload, signal_key,
                )
                if item:
                    made.append(item)
        if any(word in normalized for word in _GRIEF_SIGNAL_WORDS):
            signal_key = normalized[:240]
            grief_signal_keys.add(signal_key)
            if signal_key not in prior_grief_signal_keys:
                payload = {
                    "signal_label": label,
                    "start_global_day": snapshot.get("detected_tracker_global_day", save.global_day),
                    "suggested_end_global_day": save.global_day + max(1, save.days_per_year // 2),
                    "detected_game_hour": snapshot.get("detected_game_hour"),
                    "detected_game_minute": snapshot.get("detected_game_minute"),
                }
                item = candidate(
                    session, save, "grief_detected", sim,
                    f"Grief needs review: {sim.label}", payload, signal_key,
                )
                if item:
                    made.append(item)
    # Save files expose one significant-other ID even when their compact
    # summary does not contain the full relationship collection. Preserve the
    # ID and offer a review instead of guessing that it means marriage.
    significant_other = str(snapshot.get("significant_other_game_id") or "").strip()
    if significant_other and significant_other != current_game_id and significant_other != previous_significant_other:
        other_sim = _game_sim(session, save, significant_other)
        already_reported = any(
            str(rel.get("other_game_sim_id") or "") == significant_other
            and _significant_relationship(str(rel.get("category") or ""))
            for rel in relationships
        )
        already_made = any(
            (item.data or {}).get("action") == "relationship_change"
            and str(((item.data or {}).get("payload") or {}).get("other_game_sim_id") or "") == significant_other
            for item in made
        )
        if not already_reported and not already_made and not _existing_relationship_pair(session, save, sim, other_sim):
            payload = {
                "other_game_sim_id": significant_other,
                "other_sim_id": other_sim.id if other_sim else None,
                "other_sim_name": other_sim.label if other_sim else "",
                "category": "Romantic",
                "source": snapshot.get("source") or "Sims 4 save summary",
                "detected_game_day": snapshot.get("detected_game_day"),
                "detected_game_hour": snapshot.get("detected_game_hour"),
                "detected_game_minute": snapshot.get("detected_game_minute"),
                "detected_game_second": snapshot.get("detected_game_second"),
                "detected_tracker_global_day": snapshot.get("detected_tracker_global_day", save.global_day),
            }
            item = candidate(
                session, save, "relationship_change", sim,
                f"Significant other detected for {sim.label}", payload,
                f"significant-other:{significant_other}",
            )
            if item:
                made.append(item)
    # A disappeared relationship is also meaningful.  Use a per-relationship
    # sequence so a later reconciliation of the same pair can be reviewed again,
    # while ordinary repeat reports remain silent.  When both Sims are tracked,
    # only the lower game id creates the shared review item.
    end_sequences = dict(data.get("game_relationship_end_sequences") or {})
    current_relationship_others = {key.partition(":")[0] for key in relationship_keys}
    for old_key in sorted(prior_relationship_keys - set(relationship_keys)) if has_relationship_baseline and has_relationship_state else ():
        other, _, category = old_key.partition(":")
        if not other or other in current_relationship_others or not _significant_relationship(category):
            continue
        other_sim = session.scalar(select(Record).where(
            Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
            Record.data["game_sim_id"].as_string() == other,
        ))
        if other_sim and current_game_id and current_game_id > other:
            continue
        sequence = int(end_sequences.get(old_key) or 0) + 1
        end_sequences[old_key] = sequence
        end_payload = {
            "other_game_sim_id": other,
            "other_sim_id": other_sim.id if other_sim else None,
            "other_sim_name": other_sim.label if other_sim else "",
            "category": category or "relationship",
            "detected_game_day": snapshot.get("detected_game_day"),
            "detected_game_hour": snapshot.get("detected_game_hour"),
            "detected_game_minute": snapshot.get("detected_game_minute"),
            "detected_game_second": snapshot.get("detected_game_second"),
            "detected_tracker_global_day": snapshot.get("detected_tracker_global_day", save.global_day),
        }
        item = candidate(
            session, save, "relationship_end", sim,
            f"Possible {category or 'relationship'} ending: {sim.label}",
            end_payload, f"{other}:{category}:{sequence}",
        )
        if item:
            made.append(item)
    state_updates = {"game_was_dead": currently_dead} if has_death_state else {}
    if has_relationship_state:
        state_updates.update(game_relationship_keys=relationship_keys, game_relationship_end_sequences=end_sequences,
                             game_scandal_signal_keys=sorted(scandal_keys))
    if has_signal_state:
        state_updates.update(game_legal_signal_keys=sorted(legal_signal_keys),
                             game_grief_signal_keys=sorted(grief_signal_keys))
    if any(data.get(key) != value for key, value in state_updates.items()):
        base = sim.version; sim.data = {**sim.data, **state_updates}; sim.version += 1; journal(session, sim, "upsert", base)
        data = dict(sim.data or {})
    was_pregnant = bool(data.get("game_was_pregnant"))
    is_pregnant = is_snapshot_pregnant
    if has_pregnancy_state and is_pregnant and reported_count:
        data["last_game_pregnancy_count"] = reported_count
    if has_pregnancy_state and is_pregnant and not was_pregnant:
        sequence = int(data.get("game_pregnancy_sequence") or 0) + 1
        conception_estimate = save.global_day
        due_estimate = save.global_day + save.pregnancy_days
        try:
            progress = max(0.0, min(100.0, float(snapshot.get("pregnancy_progress_percentage"))))
            elapsed = round(save.pregnancy_days * progress / 100.0)
            conception_estimate = max(1, save.global_day - elapsed)
            due_estimate = conception_estimate + save.pregnancy_days
        except (TypeError, ValueError):
            try:
                remaining_days = max(0, round(float(snapshot.get("pregnancy_hours_remaining")) / 24.0))
                due_estimate = save.global_day + remaining_days
                conception_estimate = max(1, due_estimate - save.pregnancy_days)
            except (TypeError, ValueError):
                pass
        pregnancy_payload = {
            **snapshot, "babies_expected": reported_count or 1,
            "conception_global_day": conception_estimate,
            "due_global_day": due_estimate,
            "pregnancy_date_estimate": True,
            "inferred_other_parent_id": partner.id if partner else None,
            "inferred_other_parent_name": partner.label if partner else "",
        }
        item = candidate(session, save, "pregnancy_discovered", sim, f"New pregnancy detected: {sim.label}", pregnancy_payload, str(sequence))
        if item: made.append(item)
        data["game_pregnancy_sequence"] = sequence
    if has_pregnancy_state and was_pregnant and not is_pregnant:
        active = next((record for record in session.scalars(select(Record).where(
            Record.save_id == save.id, Record.kind == "pregnancy", Record.deleted.is_(False),
            Record.data["mother_id"].as_string() == sim.id,
        ).order_by(Record.global_day.desc())) if str((record.data or {}).get("status") or "active").casefold() not in {"delivered", "miscarriage", "stillbirth", "cancelled", "canceled", "ended", "closed"}), None)
        active_expected = (active.data or {}).get("babies_expected") if active else None
        explicit_delivery = None
        explicit_source = ""
        for field, label in (("babies_delivered", "game report"), ("detected_newborn_count", "newborn detection")):
            if field not in snapshot:
                continue
            try:
                explicit_delivery = max(0, int(snapshot.get(field)))
                explicit_source = label
                break
            except (TypeError, ValueError):
                pass
        delivered = explicit_delivery if explicit_delivery is not None else (_positive_count(
            data.get("last_game_pregnancy_count"), active_expected,
        ) or 1)
        source = explicit_source or (
                "pregnancy scan" if data.get("last_game_pregnancy_count") else "pregnancy record"
        )
        suggested_status = str(snapshot.get("pregnancy_outcome") or ("Delivered" if delivered > 0 else "Miscarriage"))
        outcome_payload = {**snapshot, "babies_delivered": delivered, "babies_delivered_source": source,
                           "suggested_status": suggested_status,
                           "pregnancy_id": active.id if active else None}
        item = candidate(session, save, "pregnancy_outcome", sim, f"Pregnancy outcome detected: {sim.label}", outcome_payload, str(save.global_day))
        if item: made.append(item)
    if has_pregnancy_state and was_pregnant != is_pregnant:
        base = sim.version; sim.data = {**sim.data, **data, "game_was_pregnant": is_pregnant}; sim.version += 1; journal(session, sim, "upsert", base)
    history_entries.extend(telemetry.capture_pregnancy_progress(session, save, sim, snapshot))
    history_entries.extend(telemetry.capture_responsible_pregnancy(session, save, sim, snapshot))
    snapshot["_history_entries"] = history_entries
    return made


def ensure_event_participation(session: Session, save: ChronicleSave) -> int:
    events = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "event", Record.deleted.is_(False), Record.global_day <= save.global_day)))
    sims = list(session.scalars(select(Record).where(Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False))))
    made = 0
    for event in events:
        if event_is_ignored(event): continue
        end = event.data.get("end_global_day")
        if end is not None and int(end) < save.global_day: continue
        country = str(event.data.get("country") or event.data.get("location") or "global").casefold()
        for sim in sims:
            death = sim.data.get("death_global_day")
            if death is not None and int(death) <= save.global_day: continue
            location = str(sim.data.get("last_game_world") or sim.data.get("country") or "").casefold()
            if country not in {"", "global", "worldwide", "all"} and country not in location: continue
            source = f"event:{event.id}:{sim.id}"
            exists = session.scalar(select(Record.id).where(Record.save_id == save.id, Record.kind == "event_result", Record.data["source"].as_string() == source).limit(1))
            if exists: continue
            result = Record(save_id=save.id, kind="event_result", label=f"{event.label} — {sim.label}", global_day=save.global_day,
                            data={"event_id": event.id, "sim_id": sim.id, "source": source, "status": "pending", "die": event.data.get("die"), "bad_results": event.data.get("bad_results")})
            session.add(result); session.flush(); journal(session, result, "upsert", 0); made += 1
    return made


def session_journal(session: Session, save: ChronicleSave, changes: list[str], game_day: int,
                    game_hour: int | None = None, game_minute: int | None = None) -> Record | None:
    if not changes: return None
    current_heir = str((save.settings or {}).get("current_heir_id") or "")
    narrator = session.get(Record, current_heir) if current_heir else None
    if not narrator or narrator.kind != "sim" or narrator.deleted:
        narrator = session.scalar(select(Record).where(
            Record.save_id == save.id, Record.kind == "sim", Record.deleted.is_(False),
        ).order_by(Record.global_day.asc(), Record.created_at.asc()).limit(1))
    narrator_name = narrator.label if narrator else "the household chronicler"
    narrative = f"I, {narrator_name}, set down what changed: " + " ".join(changes)
    source = f"game-session:{game_day}:{save.global_day}"
    existing = session.scalar(select(Record).where(Record.save_id == save.id, Record.kind == "session_journal", Record.data["source"].as_string() == source))
    if existing:
        merged = list(dict.fromkeys((existing.data.get("entries") or []) + changes)); base = existing.version
        existing.data = {**existing.data, "entries": merged, "notes": " ".join(merged),
                         "narrative": f"I, {narrator_name}, set down what changed: " + " ".join(merged),
                         "narrator_sim_id": narrator.id if narrator else None, "narrator_name": narrator_name,
                         "game_hour": game_hour, "game_minute": game_minute,
                         "game_time": f"{game_hour:02d}:{game_minute:02d}" if game_hour is not None and game_minute is not None else None}; existing.version += 1; journal(session, existing, "upsert", base); return existing
    item = Record(save_id=save.id, kind="session_journal", label=f"Game report — Global Day {save.global_day}", global_day=save.global_day,
                  data={"source": source, "entries": changes, "notes": " ".join(changes), "narrative":narrative,
                        "narrator_sim_id": narrator.id if narrator else None, "narrator_name": narrator_name,
                        "game_day": game_day, "game_hour": game_hour, "game_minute": game_minute,
                        "game_time": f"{game_hour:02d}:{game_minute:02d}" if game_hour is not None and game_minute is not None else None})
    session.add(item); session.flush(); journal(session, item, "upsert", 0); return item
