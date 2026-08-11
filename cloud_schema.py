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
    "CREATE TABLE IF NOT EXISTS roll_rule_eras(era_id TEXT PRIMARY KEY,era_name TEXT NOT NULL,start_year INTEGER NOT NULL,end_year INTEGER NOT NULL,species TEXT NOT NULL DEFAULT 'Human',active INTEGER NOT NULL DEFAULT 1,notes TEXT)",
    "CREATE TABLE IF NOT EXISTS roll_rule_values(era_id TEXT NOT NULL,roll_type TEXT NOT NULL,die TEXT,bad_results TEXT,notes TEXT,PRIMARY KEY(era_id,roll_type))",
]

TABLES = [
    "settings", "sims", "households", "pregnancies", "rolls",
    "relationships", "events", "event_results", "rules", "calendar_rows",
    "raw_import_rows", "sim_photos", "roll_rule_eras", "roll_rule_values",
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
    connection.commit()


def create_save_schema(connection, schema_name):
    from psycopg import sql

    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema_name)))
        cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema_name)))
        for ddl in TABLE_DDLS:
            cursor.execute(ddl)
    connection.commit()
