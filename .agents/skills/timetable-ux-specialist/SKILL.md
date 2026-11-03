---
name: timetable-ux-specialist
description: Improve university timetable workflows: weekly grids, conflicts, room/instructor selection, teacher availability and safe automatic schedule generation. Use for scheduling interface tasks.
---

# Timetable UX specialist
1. Read `AGENTS.md`, `main.py`, `models.py`, `scheduler.py` and UI view; distinguish implemented vs planned features.
2. Map admin, instructor and student journeys and their permissions.
3. Show day, time, course, group, instructor and room with clear empty/error/loading states.
4. Enforce hard scheduling rules: overlapping resources, blocked instructor slots, room fit/capacity and authorization; soft preferences never override hard rules.
5. For Generate Timetable, prefer labels and validated selectors over manual UUID typing. Clearly explain actions that persist data.
6. Generation is not a dry run. `replace_existing=true` deletes prior schedule rows. Never test it on production.
7. Design scenario-based checks for no availability, oversized groups, insufficient slots, inactive data, overlaps, and failed or partial generation.
8. Deliver a role-by-role flow, state table, prioritized usability improvements and acceptance tests grounded in actual code.
