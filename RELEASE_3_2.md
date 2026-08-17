# Decades Tracker 3.2

Version 3.2 rebuilds the interaction runtime around isolated page fragments.
It preserves the existing Neon schemas, saves, and feature set while reducing
the work performed after ordinary button presses and form interactions.

## Runtime changes

- Nineteen independent page renderers replace the single conditional page
  chain.
- Native Streamlit fragments rerun only the active page after an interaction.
- Workspace access, save selection, migrations, the sidebar, and automatic Sim
  detection are no longer rebuilt by every control inside a page.
- Plotly loads only for Timeline and Statistics.
- NetworkX and PyVis load only for Family Tree.
- Older local Streamlit installations retain a safe full-rerun fallback.

## Compatibility

- Existing Neon workspaces and save schemas remain unchanged.
- No user database migration or destructive rewrite is required.
- All 19 existing tracker destinations remain available.
- Existing automatic rolls, game-clock sync, events, illnesses, family data,
  notes, rules, statistics, and backups use the same underlying services.
