from __future__ import annotations


ACTIVE_STATUSES=("Active","Improving","Worsening","Chronic")
ALL_STATUSES=ACTIVE_STATUSES+("Recovered","Fatal","Resolved")
SEVERITIES=("Mild","Moderate","Severe","Critical")


def ensure_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS illnesses(
        illness_id TEXT PRIMARY KEY,sim_id TEXT,sim_name TEXT,illness_name TEXT NOT NULL,
        onset_global_day INTEGER,end_global_day INTEGER,status TEXT,severity TEXT,
        contagious INTEGER,treatment TEXT,outcome TEXT,notes TEXT
    )""")
    con.commit()


def next_id(con):
    numbers=[]
    for (value,) in con.execute("SELECT illness_id FROM illnesses WHERE illness_id LIKE ?",("ILL-%",)):
        try: numbers.append(int(str(value).rsplit("-",1)[1]))
        except (TypeError,ValueError): pass
    return f"ILL-{max(numbers,default=0)+1:04d}"
