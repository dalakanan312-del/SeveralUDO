from __future__ import annotations


def sim_dependency_summary(connection, sim_id):
    queries = {
        "Children linked to this Sim": (
            "SELECT COUNT(*) FROM sims WHERE mother_id=? OR father_id=?", (sim_id, sim_id)
        ),
        "Relationships": (
            "SELECT COUNT(*) FROM relationships WHERE partner1_id=? OR partner2_id=?", (sim_id, sim_id)
        ),
        "Pregnancies as mother": (
            "SELECT COUNT(*) FROM pregnancies WHERE mother_id=?", (sim_id,)
        ),
        "Pregnancies as father": (
            "SELECT COUNT(*) FROM pregnancies WHERE father_id=?", (sim_id,)
        ),
        "Rolls": (
            "SELECT COUNT(*) FROM rolls WHERE sim_id=? OR source_id=?", (sim_id, sim_id)
        ),
        "Event results": (
            "SELECT COUNT(*) FROM event_results WHERE sim_id=?", (sim_id,)
        ),
        "Households headed": (
            "SELECT COUNT(*) FROM households WHERE head_sim_id=?", (sim_id,)
        ),
        "Portraits": (
            "SELECT COUNT(*) FROM sim_photos WHERE sim_id=?", (sim_id,)
        ),
    }
    return {
        label: connection.execute(statement, parameters).fetchone()[0]
        for label, (statement, parameters) in queries.items()
    }


def delete_sim(connection, sim_id, commit=True):
    """Delete a Sim and remove or detach all references to that Sim."""
    exists = connection.execute("SELECT 1 FROM sims WHERE sim_id=?", (sim_id,)).fetchone()
    if not exists:
        raise ValueError("Sim not found.")

    partner_ids = [
        row[0]
        for row in connection.execute(
            """SELECT CASE WHEN partner1_id=? THEN partner2_id ELSE partner1_id END
               FROM relationships WHERE partner1_id=? OR partner2_id=?""",
            (sim_id, sim_id, sim_id),
        ).fetchall()
        if row[0]
    ]
    pregnancy_ids = [
        row[0]
        for row in connection.execute(
            "SELECT pregnancy_id FROM pregnancies WHERE mother_id=?", (sim_id,)
        ).fetchall()
    ]

    for pregnancy_id in pregnancy_ids:
        connection.execute("DELETE FROM rolls WHERE source_id=?", (pregnancy_id,))
    connection.execute("DELETE FROM pregnancies WHERE mother_id=?", (sim_id,))
    connection.execute(
        "UPDATE pregnancies SET father_id=NULL WHERE father_id=?", (sim_id,)
    )
    connection.execute("DELETE FROM rolls WHERE sim_id=? OR source_id=?", (sim_id, sim_id))
    connection.execute(
        "DELETE FROM relationships WHERE partner1_id=? OR partner2_id=?", (sim_id, sim_id)
    )
    connection.execute("DELETE FROM sim_photos WHERE sim_id=?", (sim_id,))
    connection.execute("UPDATE sims SET mother_id=NULL WHERE mother_id=?", (sim_id,))
    connection.execute("UPDATE sims SET father_id=NULL WHERE father_id=?", (sim_id,))
    connection.execute("UPDATE event_results SET sim_id=NULL WHERE sim_id=?", (sim_id,))
    connection.execute("UPDATE households SET head_sim_id=NULL WHERE head_sim_id=?", (sim_id,))
    connection.execute(
        "UPDATE settings SET value='' WHERE key='current_heir_id' AND value=?", (sim_id,)
    )
    connection.execute("DELETE FROM sims WHERE sim_id=?", (sim_id,))

    if partner_ids:
        import profiles

        profiles.sync_spouse_ids(connection, partner_ids, commit=False)
    if commit:
        connection.commit()


def delete_pregnancy(connection, pregnancy_id, commit=True):
    if not connection.execute("SELECT 1 FROM pregnancies WHERE pregnancy_id=?", (pregnancy_id,)).fetchone():
        raise ValueError("Pregnancy not found.")
    connection.execute("DELETE FROM rolls WHERE source_id=?", (pregnancy_id,))
    connection.execute("DELETE FROM pregnancies WHERE pregnancy_id=?", (pregnancy_id,))
    if commit:
        connection.commit()


def delete_relationship(connection, relationship_id, commit=True):
    row = connection.execute(
        "SELECT partner1_id,partner2_id FROM relationships WHERE relationship_id=?", (relationship_id,)
    ).fetchone()
    if not row:
        raise ValueError("Relationship not found.")
    partner_ids = [identifier for identifier in row if identifier]
    connection.execute("DELETE FROM relationship_photos WHERE relationship_id=?", (relationship_id,))
    connection.execute("DELETE FROM relationships WHERE relationship_id=?", (relationship_id,))
    if partner_ids:
        import profiles
        profiles.sync_spouse_ids(connection, partner_ids, commit=False)
    if commit:
        connection.commit()


def delete_household(connection, household_id, commit=True):
    if not connection.execute("SELECT 1 FROM households WHERE household_id=?", (household_id,)).fetchone():
        raise ValueError("Household not found.")
    connection.execute("UPDATE sims SET current_household_id=NULL WHERE current_household_id=?", (household_id,))
    connection.execute("UPDATE event_results SET household_id=NULL WHERE household_id=?", (household_id,))
    connection.execute(
        "UPDATE settings SET value='' WHERE key='main_household_id' AND value=?", (household_id,)
    )
    connection.execute("DELETE FROM households WHERE household_id=?", (household_id,))
    if commit:
        connection.commit()


def refresh_household_counts(connection, household_id=None, commit=True):
    household_ids = (
        [household_id]
        if household_id
        else [row[0] for row in connection.execute("SELECT household_id FROM households")]
    )
    day_row = connection.execute(
        "SELECT value FROM settings WHERE key='current_global_day'"
    ).fetchone()
    current_day = int(float(day_row[0])) if day_row and day_row[0] not in (None, "") else 1
    for identifier in household_ids:
        if not identifier:
            continue
        living = connection.execute(
            """SELECT COUNT(*) FROM sims WHERE current_household_id=?
               AND (death_global_day IS NULL OR death_global_day>?)""",
            (identifier, current_day),
        ).fetchone()[0]
        total = connection.execute(
            "SELECT COUNT(*) FROM sims WHERE current_household_id=?", (identifier,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE households SET living_members=?,total_assigned_members=? WHERE household_id=?",
            (living, total, identifier),
        )
    if commit:
        connection.commit()
