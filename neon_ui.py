from pathlib import Path

import save_manager
import storage


def render_connection_setup(st):
    st.title("Connect Decades Tracker to Neon")
    st.write("Decades Tracker stores its live saves in Neon PostgreSQL.")
    with st.form("neon_connection_setup"):
        pooled = st.text_input("Neon connection string", type="password")
        submitted = st.form_submit_button("Test connection and use Neon", type="primary")
    if submitted:
        if not pooled.strip():
            st.error("Paste your Neon connection string.")
            return
        try:
            storage.test_connection(pooled.strip())
            storage.save_config(pooled.strip(), pooled.strip(), None)
            save_manager.ensure_setup()
            st.success("Connected to Neon successfully.")
            st.rerun()
        except Exception as error:
            st.error(f"Could not connect to Neon: {error}")
    st.caption("Your connection string is stored locally and is excluded from Git.")


def render_first_save_setup(st):
    if save_manager.list_saves():
        return False
    st.title("Neon is connected")
    st.subheader("Move an existing tracker into the cloud")
    local = [item for item in save_manager.discover_local_saves() if int(item.get("sims") or 0) > 0]
    if local:
        labels = [f"{item['name']} — {item['sims']:,} Sims — {Path(item['path']).name}" for item in local]
        selected = st.multiselect("SQLite saves to migrate", labels, default=labels)
        if st.button("Migrate selected saves to Neon", type="primary"):
            for label in selected:
                item = local[labels.index(label)]
                save_manager.migrate_sqlite_file(item["path"], item["name"], True)
            st.success("Migration complete.")
            st.rerun()
        st.divider()
    st.subheader("Or create a blank cloud save")
    name = st.text_input("Save name", value="My First Save")
    start = st.number_input("Calendar start year", -10000, 10000, 1200)
    if st.button("Create blank Neon save"):
        save_manager.create_blank(name, int(start), int(start), 1)
        st.rerun()
    return True
