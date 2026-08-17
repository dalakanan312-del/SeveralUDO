from __future__ import annotations

TABLE_DDLS = [
    "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)",
    "CREATE TABLE IF NOT EXISTS sims(sim_id TEXT PRIMARY KEY,include_in_tree INTEGER,title TEXT,first_name TEXT,last_name TEXT,suffix TEXT,maiden_married_name TEXT,sex TEXT,generation INTEGER,mother_id TEXT,father_id TEXT,spouse_ids TEXT,birth_global_day INTEGER,birth_date TEXT,birthplace TEXT,birth_status TEXT,multiple_birth TEXT,marriage_global_day INTEGER,marriage_date TEXT,marriage_place TEXT,reference_date TEXT,death_global_day INTEGER,death_date TEXT,death_place TEXT,cause_of_death TEXT,historical_household TEXT,notes TEXT,current_household_id TEXT,legitimate INTEGER,fertility_status TEXT,species_occult TEXT,succession_override TEXT,succession_note TEXT,played_through_global_day INTEGER)",
    "CREATE TABLE IF NOT EXISTS households(household_id TEXT PRIMARY KEY,household_name TEXT,branch_type TEXT,location TEXT,social_class TEXT,head_sim_id TEXT,active INTEGER,start_global_day INTEGER,end_global_day INTEGER,abbey_annual_cost DOUBLE PRECISION,living_members INTEGER,total_assigned_members INTEGER,notes TEXT,first_recorded_sim TEXT,data_source TEXT)",
    "CREATE TABLE IF NOT EXISTS pregnancies(pregnancy_id TEXT PRIMARY KEY,mother_id TEXT,mother_name TEXT,father_id TEXT,father_name TEXT,conception_global_day INTEGER,due_global_day INTEGER,delivery_date TEXT,babies_expected INTEGER,babies_delivered INTEGER,status TEXT,attempts_today INTEGER,max_attempts_today INTEGER,maternal_rolls_required INTEGER,birth_newborn_rolls_required INTEGER,outcome TEXT,complication TEXT,multiple_rule_check TEXT,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS rolls(roll_id TEXT PRIMARY KEY,due_global_day INTEGER,sim_id TEXT,sim_name TEXT,source_id TEXT,roll_type TEXT,die TEXT,bad_results TEXT,actual_roll TEXT,outcome TEXT,completed INTEGER,completed_global_day INTEGER,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS relationships(relationship_id TEXT PRIMARY KEY,partner1_id TEXT,partner2_id TEXT,partner1_name TEXT,partner2_name TEXT,type TEXT,start_global_day INTEGER,start_date TEXT,end_global_day INTEGER,status TEXT,location TEXT,legally_married INTEGER,children_count INTEGER,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,start_global_day INTEGER,end_global_day INTEGER,event_name TEXT,scope TEXT,location TEXT,roll_required INTEGER,affected_class TEXT,active INTEGER,source TEXT,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS event_results(result_id TEXT PRIMARY KEY,event_id TEXT,global_day INTEGER,household_id TEXT,sim_id TEXT,roll_choice TEXT,outcome TEXT,status TEXT,death INTEGER,cause_effect TEXT,completed INTEGER,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS rules(section TEXT,row_label TEXT,col_b TEXT,col_c TEXT,col_d TEXT,col_e TEXT,source_row INTEGER)",
    "CREATE TABLE IF NOT EXISTS calendar_rows(source_row INTEGER,payload_json TEXT)",
    "CREATE TABLE IF NOT EXISTS raw_import_rows(source_file TEXT,sheet_name TEXT,row_number INTEGER,payload_json TEXT)",
    "CREATE TABLE IF NOT EXISTS sim_photos(sim_id TEXT PRIMARY KEY,image_data BYTEA NOT NULL,mime_type TEXT,filename TEXT,updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS sim_lifestage_photos(sim_id TEXT NOT NULL,life_stage TEXT NOT NULL,image_data BYTEA NOT NULL,mime_type TEXT,filename TEXT,updated_at TEXT,PRIMARY KEY(sim_id,life_stage))",
    "CREATE TABLE IF NOT EXISTS relationship_photos(relationship_id TEXT PRIMARY KEY REFERENCES relationships(relationship_id) ON DELETE CASCADE,image_data BYTEA NOT NULL,mime_type TEXT,filename TEXT,updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS roll_rule_eras(era_id TEXT PRIMARY KEY,era_name TEXT NOT NULL,start_year INTEGER NOT NULL,end_year INTEGER NOT NULL,species TEXT NOT NULL DEFAULT 'Human',active INTEGER NOT NULL DEFAULT 1,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS roll_rule_values(era_id TEXT NOT NULL,roll_type TEXT NOT NULL,die TEXT,bad_results TEXT,notes TEXT,PRIMARY KEY(era_id,roll_type))",
    "CREATE TABLE IF NOT EXISTS notebook_entries(note_id TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT,body TEXT,pinned INTEGER NOT NULL DEFAULT 0,created_at TEXT,updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS illnesses(illness_id TEXT PRIMARY KEY,sim_id TEXT,sim_name TEXT,illness_name TEXT NOT NULL,onset_global_day INTEGER,end_global_day INTEGER,status TEXT,severity TEXT,contagious INTEGER,treatment TEXT,outcome TEXT,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS era_guidance(rule_id TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT,start_year INTEGER,end_year INTEGER,location TEXT,rule_text TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,source TEXT,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS military_campaigns(campaign_id TEXT PRIMARY KEY,event_id TEXT,name TEXT NOT NULL,start_global_day INTEGER,end_global_day INTEGER,location TEXT,min_age_days INTEGER,max_age_days INTEGER,eligible_sexes TEXT,eligible_classes TEXT,active INTEGER NOT NULL DEFAULT 1,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS military_service(service_id TEXT PRIMARY KEY,campaign_id TEXT,event_id TEXT,sim_id TEXT,sim_name TEXT,role TEXT,status TEXT,enlisted_global_day INTEGER,return_global_day INTEGER,outcome TEXT,injury TEXT,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS event_rule_configs(event_id TEXT PRIMARY KEY,die TEXT,bad_results TEXT,eligibility TEXT,min_age_days INTEGER,max_age_days INTEGER,eligible_sexes TEXT,frequency TEXT,followup_die TEXT,followup_results TEXT,effects_json TEXT,updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS action_queue(action_id TEXT PRIMARY KEY,source_type TEXT NOT NULL,source_id TEXT,roll_id TEXT UNIQUE,sim_id TEXT,household_id TEXT,due_global_day INTEGER,title TEXT,category TEXT,status TEXT NOT NULL,priority INTEGER NOT NULL DEFAULT 100,payload_json TEXT,created_at TEXT,updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS maintenance_jobs(job_key TEXT PRIMARY KEY,status TEXT,last_run_at TEXT,summary TEXT)",
    "CREATE TABLE IF NOT EXISTS death_cause_pools(death_group TEXT NOT NULL,cause TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(death_group,cause))",
    "CREATE TABLE IF NOT EXISTS game_birth_candidates(detection_id TEXT PRIMARY KEY,game_sim_id TEXT UNIQUE NOT NULL,first_name TEXT,last_name TEXT,sex TEXT,age_stage TEXT,is_baby INTEGER,game_day BIGINT,game_hour INTEGER,game_minute INTEGER,birth_global_day INTEGER,household_name TEXT,status TEXT NOT NULL DEFAULT 'pending',detected_at TEXT,resolved_at TEXT,created_sim_id TEXT)",
]

TABLES = [
    "settings", "sims", "households", "pregnancies", "rolls",
    "relationships", "events", "event_results", "rules", "calendar_rows",
    "raw_import_rows", "sim_photos", "sim_lifestage_photos", "relationship_photos", "roll_rule_eras", "roll_rule_values", "notebook_entries", "illnesses",
    "era_guidance", "military_campaigns", "military_service", "event_rule_configs", "action_queue", "maintenance_jobs", "death_cause_pools", "game_birth_candidates",
]


def create_registry(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS public.decades_saves(
                save_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                schema_name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                source_note TEXT,
                owner_hash TEXT
            )"""
        )
        cursor.execute("ALTER TABLE public.decades_saves ADD COLUMN IF NOT EXISTS owner_hash TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS decades_saves_owner_hash_idx ON public.decades_saves(owner_hash)")
        cursor.execute("""CREATE TABLE IF NOT EXISTS public.decades_identities(
            email TEXT PRIMARY KEY,
            workspace_hash TEXT NOT NULL,
            google_subject TEXT UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ
        )""")
        cursor.execute("CREATE INDEX IF NOT EXISTS decades_identities_workspace_idx ON public.decades_identities(workspace_hash)")
        cursor.execute("""CREATE TABLE IF NOT EXISTS public.decades_sessions(
            token_hash TEXT PRIMARY KEY,
            workspace_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        cursor.execute("CREATE INDEX IF NOT EXISTS decades_sessions_expiry_idx ON public.decades_sessions(expires_at)")
        cursor.execute("""CREATE TABLE IF NOT EXISTS public.decades_clock_sync(
            token_hash TEXT PRIMARY KEY,
            owner_hash TEXT NOT NULL,
            save_id TEXT NOT NULL,
            schema_name TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            game_anchor_day BIGINT,
            tracker_anchor_day INTEGER,
            last_game_day BIGINT,
            last_tracker_day INTEGER,
            last_seen_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")
        cursor.execute("CREATE INDEX IF NOT EXISTS decades_clock_sync_owner_idx ON public.decades_clock_sync(owner_hash,save_id)")
        cursor.execute("ALTER TABLE public.decades_clock_sync ADD COLUMN IF NOT EXISTS members_initialized BOOLEAN NOT NULL DEFAULT FALSE")
        cursor.execute("""CREATE TABLE IF NOT EXISTS public.decades_clock_members(
            token_hash TEXT NOT NULL,
            game_sim_id TEXT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(token_hash,game_sim_id)
        )""")
    connection.commit()


def create_save_schema(connection, schema_name):
    from psycopg import sql

    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema_name)))
        cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name)))
        for ddl in TABLE_DDLS:
            cursor.execute(ddl)
        cursor.execute("CREATE INDEX IF NOT EXISTS rolls_due_completed_idx ON rolls(due_global_day,completed)")
        cursor.execute("CREATE INDEX IF NOT EXISTS rolls_obligation_idx ON rolls(source_id,sim_id,due_global_day,roll_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS sims_birth_death_idx ON sims(birth_global_day,death_global_day)")
        cursor.execute("CREATE INDEX IF NOT EXISTS pregnancies_due_idx ON pregnancies(due_global_day,status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS events_start_active_idx ON events(start_global_day,active,roll_required)")
        cursor.execute(
            "INSERT INTO settings(key,value) VALUES('roll_tracking_start','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )
    connection.commit()


def ensure_game_sync_schema(connection):
    connection.execute(TABLE_DDLS[-1])
    connection.commit()


def ensure_performance_indexes(connection):
    """Add read-path indexes to both new and already-existing save schemas."""
    statements = (
        "CREATE INDEX IF NOT EXISTS rolls_due_completed_idx ON rolls(due_global_day,completed)",
        "CREATE INDEX IF NOT EXISTS rolls_obligation_idx ON rolls(source_id,sim_id,due_global_day,roll_type)",
        "CREATE INDEX IF NOT EXISTS rolls_recent_idx ON rolls(due_global_day DESC,roll_id)",
        "CREATE INDEX IF NOT EXISTS sims_birth_death_idx ON sims(birth_global_day,death_global_day)",
        "CREATE INDEX IF NOT EXISTS sims_household_idx ON sims(current_household_id)",
        "CREATE INDEX IF NOT EXISTS sims_parents_idx ON sims(mother_id,father_id)",
        "CREATE INDEX IF NOT EXISTS pregnancies_due_idx ON pregnancies(due_global_day,status)",
        "CREATE INDEX IF NOT EXISTS events_start_active_idx ON events(start_global_day,active,roll_required)",
        "CREATE INDEX IF NOT EXISTS events_recent_idx ON events(start_global_day DESC,event_id)",
        "CREATE INDEX IF NOT EXISTS event_results_recent_idx ON event_results(global_day DESC,result_id)",
        "CREATE INDEX IF NOT EXISTS relationships_partner1_idx ON relationships(partner1_id,start_global_day DESC)",
        "CREATE INDEX IF NOT EXISTS relationships_partner2_idx ON relationships(partner2_id,start_global_day DESC)",
        "CREATE INDEX IF NOT EXISTS households_name_idx ON households(household_name,household_id)",
        "CREATE INDEX IF NOT EXISTS illnesses_recent_idx ON illnesses(onset_global_day DESC,illness_id)",
        "CREATE INDEX IF NOT EXISTS illnesses_status_idx ON illnesses(status,onset_global_day DESC)",
    )
    for statement in statements:
        connection.execute(statement)
    connection.commit()
