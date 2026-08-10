
from __future__ import annotations

def sim_dependency_summary(con, sim_id):
    counts={}
    counts["Children linked to this Sim"]=con.execute(
        "SELECT COUNT(*) FROM sims WHERE mother_id=? OR father_id=?",(sim_id,sim_id)
    ).fetchone()[0]
    counts["Relationships"]=con.execute(
        "SELECT COUNT(*) FROM relationships WHERE partner1_id=? OR partner2_id=?",(sim_id,sim_id)
    ).fetchone()[0]
    counts["Pregnancies as mother"]=con.execute(
        "SELECT COUNT(*) FROM pregnancies WHERE mother_id=?",(sim_id,)
    ).fetchone()[0]
    counts["Pregnancies as father"]=con.execute(
        "SELECT COUNT(*) FROM pregnancies WHERE father_id=?",(sim_id,)
    ).fetchone()[0]
    counts["Rolls"]=con.execute(
        "SELECT COUNT(*) FROM rolls WHERE sim_id=? OR source_id=?",(sim_id,sim_id)
    ).fetchone()[0]
    counts["Event results"]=con.execute(
        "SELECT COUNT(*) FROM event_results WHERE sim_id=?",(sim_id,)
    ).fetchone()[0]
    counts["Households headed"]=con.execute(
        "SELECT COUNT(*) FROM households WHERE head_sim_id=?",(sim_id,)
    ).fetchone()[0]
    counts["Portraits"]=con.execute(
        "SELECT COUNT(*) FROM sim_photos WHERE sim_id=?",(sim_id,)
    ).fetchone()[0]
    return counts

def delete_sim(con, sim_id):
    """Delete one Sim while preserving unrelated history and removing dangling references."""
    preg_ids=[r[0] for r in con.execute(
        "SELECT pregnancy_id FROM pregnancies WHERE mother_id=?",(sim_id,)
    ).fetchall()]
    for pid in preg_ids:
        con.execute("DELETE FROM rolls WHERE source_id=?",(pid,))
    con.execute("DELETE FROM pregnancies WHERE mother_id=?",(sim_id,))
    con.execute("UPDATE pregnancies SET father_id=NULL,father_name=NULL WHERE father_id=?",(sim_id,))
    con.execute("DELETE FROM rolls WHERE sim_id=? OR source_id=?",(sim_id,sim_id))
    con.execute("DELETE FROM relationships WHERE partner1_id=? OR partner2_id=?",(sim_id,sim_id))
    con.execute("DELETE FROM sim_photos WHERE sim_id=?",(sim_id,))
    con.execute("UPDATE sims SET mother_id=NULL WHERE mother_id=?",(sim_id,))
    con.execute("UPDATE sims SET father_id=NULL WHERE father_id=?",(sim_id,))
    con.execute("UPDATE event_results SET sim_id=NULL WHERE sim_id=?",(sim_id,))
    con.execute("UPDATE households SET head_sim_id=NULL WHERE head_sim_id=?",(sim_id,))
    con.execute("UPDATE settings SET value='' WHERE key='current_heir_id' AND value=?",(sim_id,))
    con.execute("DELETE FROM sims WHERE sim_id=?",(sim_id,))
    con.commit()

def refresh_household_counts(con, household_id=None):
    ids=[household_id] if household_id else [r[0] for r in con.execute("SELECT household_id FROM households")]
    for hid in ids:
        if not hid: continue
        gd_row=con.execute("SELECT value FROM settings WHERE key='current_global_day'").fetchone()
        current_gd=int(float(gd_row[0])) if gd_row and gd_row[0] not in (None,"") else 1
        living=con.execute(
            """SELECT COUNT(*) FROM sims WHERE current_household_id=?
               AND (death_global_day IS NULL OR death_global_day>?)""",(hid,current_gd)
        ).fetchone()[0]
        total=con.execute(
            "SELECT COUNT(*) FROM sims WHERE current_household_id=?",(hid,)
        ).fetchone()[0]
        con.execute("""UPDATE households SET living_members=?,total_assigned_members=?
                       WHERE household_id=?""",(living,total,hid))
    con.commit()
