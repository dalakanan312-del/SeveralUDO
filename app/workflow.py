"""Read-only wayfinding: existing pages, section anchors and related tasks."""

RELATED_TASKS = {
    'today': (('Review game updates', '/p/automation'), ('Plan household turns', '/p/planner#rotation'), ('View the timeline', '/p/timeline'), ('Edit roll rules', '/p/roll-tables')),
    'automation': (('Check game connection', '/p/clock'), ('Return to Today', '/p/today'), ('Correct a Sim profile', '/p/sims')),
    'clock': (('Review received changes', '/p/automation'), ('Open Today', '/p/today'), ('Sync desktop / online records', '/p/sync')),
    'rolls': (('Roll due tasks on Today', '/p/today?task=rolls'), ('Edit dice and outcomes', '/p/roll-tables'), ('Check duplicate rolls', '/p/health')),
    'relationships': (('Plan dowries & settlements', '/p/life-records#dowries'), ('Plan pregnancies', '/p/planner#family-plans'), ('Marriage ages & rules', '/p/roll-tables#family-planning'), ('Check the family tree', '/p/family-tree')),
    'planner': (('Marriage rolls & dates', '/p/relationships#marriage-rolls'), ('Manage succession', '/p/challenge#succession'), ('Choose a dynasty branch', '/p/infinite-decades'), ('Play today', '/p/today')),
    'pregnancies': (('View family plans', '/p/planner#family-plans'), ('Roll due maternal checks', '/p/today?task=rolls'), ('Review detected deliveries', '/p/automation'), ('Choose a baby name', '/p/names')),
    'households': (('Plan household rotations', '/p/planner#rotation'), ('Record a move', '/p/world#migration-new'), ('Manage estates & money', '/p/historical-life#estate'), ('View household Sims', '/p/sims')),
    'sims': (('Add family connections', '/p/relationships'), ('Explore the family tree', '/p/family-tree'), ('Check living ages', '/p/today'), ('Review detected Sims', '/p/automation')),
    'family-tree': (('Edit family connections', '/p/relationships'), ('View Sim profiles', '/p/sims'), ('Manage dynasty branches', '/p/infinite-decades')),
    'challenge': (('Marriage rolls & dates', '/p/relationships#marriage-rolls'), ('Configure historical events', '/p/events'), ('Military service history', '/p/historical-life#service'), ('Roll due tasks', '/p/today?task=rolls')),
    'illnesses': (('Review detected illnesses', '/p/automation'), ('Check today’s care', '/p/today?task=illnesses'), ('Grief & long-term care', '/p/life-records#care')),
    'university': (('Earlier education & life plans', '/p/historical-life#education'), ('Review game updates', '/p/automation'), ('Check due term reviews', '/p/today')),
    'world': (('Edit household locations', '/p/households'), ('Plan household turns', '/p/planner#rotation'), ('Check regional events', '/p/events')),
    'events': (('Roll due event checks', '/p/today?task=rolls'), ('Plan wars & campaigns', '/p/challenge#campaigns'), ('Check historical locations', '/p/world'), ('Read era guidance', '/p/historical-guidance')),
    'life-records': (('Courtships & marriage dates', '/p/relationships#marriage-dates'), ('Family plans', '/p/planner#family-plans'), ('Heirlooms & memorials', '/p/historical-life#memory')),
    'historical-life': (('Dowries, guardians & care', '/p/life-records'), ('Record migration', '/p/world#migration-new'), ('Succession & campaigns', '/p/challenge')),
    'timeline': (('Read yearly paragraphs', '/p/storyline'), ('Add personal notes', '/p/notes'), ('Explore family connections', '/p/family-tree')),
    'storyline': (('See the visual timeline', '/p/timeline'), ('Play the drama deck', '/p/drama'), ('Add personal notes', '/p/notes'), ('View statistics', '/p/statistics')),
    'saves': (('Connect this save to the game', '/p/clock'), ('Sync desktop / online records', '/p/sync'), ('Set calendar & rules', '/p/rules')),
    'sync': (('Back up before switching', '/p/saves'), ('Connect the game instead', '/p/clock'), ('Manage account access', '/p/account')),
}

PAGE_SECTIONS = {
    'planner': (('Household rotation', 'rotation'), ('Family plans', 'family-plans'), ('Add a plan', 'new-family-plan'), ('Dynasty health', 'dynasty-health')),
    'relationships': (('Marriage rolls', 'marriage-rolls'), ('Create courtship', 'courtship'), ('Marriage dates', 'marriage-dates'), ('Add relationship', 'new-relationship'), ('Recorded connections', 'relationship-register')),
    'challenge': (('Succession & heirs', 'succession'), ('War & campaigns', 'campaigns'), ('Era guidance', 'era-guidance')),
    'world': (('Current locations', 'locations'), ('Record a move', 'migration-new'), ('Migration routes', 'migration-routes'), ('Migration ledger', 'migration-ledger')),
}

def related_tasks(page):
    return RELATED_TASKS.get(page, ())

def page_sections(page):
    return PAGE_SECTIONS.get(page, ())
