"""Read-only wayfinding: existing pages, section anchors and related tasks."""

RELATED_TASKS = {
    'play-next': (('Family plans', '/p/planner'), ('Family projects', '/p/family-projects'), ('Review game changes','/p/automation')),
    'family-projects': (('Prioritize households','/p/play-next'), ('Play a drama scene','/p/drama'), ('View events','/p/events')),
    'branch-comparison': (('Manage branches','/p/infinite-decades'), ('Explore family tree','/p/family-tree')),
    'historical-check': (('Read source guidance','/p/historical-guidance'), ('Edit rule tables','/p/roll-tables')),
    'writers-room': (('Read Storyline','/p/storyline'), ('Family correspondence tools','/p/historical-life#letters')),
    'drama': (('Roll an in-game drama prompt', '/p/drama-randomizer'), ('Read recorded outcomes', '/p/storyline')),
    'drama-randomizer': (('Play the decision-tree game', '/p/drama'), ('Read recorded outcomes', '/p/storyline'), ('Review game changes', '/p/automation')),
    'today': (('What should I play next?', '/p/play-next'), ('Review game updates', '/p/automation'), ('Plan household turns', '/p/planner#rotation'), ('View the timeline', '/p/timeline'), ('Edit roll rules', '/p/roll-tables')),
    'automation': (('Check game connection', '/p/clock'), ('Return to Today', '/p/today'), ('Correct a Sim profile', '/p/sims')),
    'clock': (('Review received changes', '/p/automation'), ('Open Today', '/p/today'), ('Sync desktop / online records', '/p/sync')),
    'rolls': (('Roll due tasks on Today', '/p/today?task=rolls'), ('Edit dice and outcomes', '/p/roll-tables'), ('Check duplicate rolls', '/p/health')),
    'relationships': (('Plan dowries & settlements', '/p/life-records#dowries'), ('Plan pregnancies', '/p/planner#family-plans'), ('Marriage ages & rules', '/p/roll-tables#family-planning'), ('Check the family tree', '/p/family-tree')),
    'infinite-decades': (('Compare branches', '/p/branch-comparison'), ('Plan the next household', '/p/play-next')),
    'planner': (('What should I play next?', '/p/play-next'), ('Marriage rolls & dates', '/p/relationships#marriage-rolls'), ('Manage succession', '/p/challenge#succession'), ('Choose a dynasty branch', '/p/infinite-decades'), ('Play today', '/p/today')),
    'pregnancies': (('View family plans', '/p/planner#family-plans'), ('Roll due maternal checks', '/p/today?task=rolls'), ('Review detected deliveries', '/p/automation'), ('Choose a baby name', '/p/names')),
    'households': (('Plan household rotations', '/p/planner#rotation'), ('Record a move', '/p/world#migration-new'), ('Manage estates & money', '/p/historical-life#estate'), ('View household Sims', '/p/sims')),
    'sims': (('Add family connections', '/p/relationships'), ('Explore the family tree', '/p/family-tree'), ('Check living ages', '/p/today'), ('Review detected Sims', '/p/automation')),
    'family-tree': (('Edit family connections', '/p/relationships'), ('View Sim profiles', '/p/sims'), ('Manage dynasty branches', '/p/infinite-decades')),
    'challenge': (('Marriage rolls & dates', '/p/relationships#marriage-rolls'), ('Configure historical events', '/p/events'), ('Military service history', '/p/historical-life#service'), ('Roll due tasks', '/p/today?task=rolls')),
    'illnesses': (('Review detected illnesses', '/p/automation'), ('Check today’s care', '/p/today?task=illnesses'), ('Grief & long-term care', '/p/life-records#care')),
    'university': (('Earlier education & life plans', '/p/historical-life#education'), ('Review game updates', '/p/automation'), ('Check due term reviews', '/p/today')),
    'world': (('Edit household locations', '/p/households'), ('Plan household turns', '/p/planner#rotation'), ('Check regional events', '/p/events')),
    'events': (('Plan recovery', '/p/family-projects#recovery'), ('Roll due event checks', '/p/today?task=rolls'), ('Plan wars & campaigns', '/p/challenge#campaigns'), ('Check historical locations', '/p/world'), ('Read era guidance', '/p/historical-guidance')),
    'life-records': (('Courtships & marriage dates', '/p/relationships#marriage-dates'), ('Family plans', '/p/planner#family-plans'), ('Heirlooms & memorials', '/p/historical-life#memory')),
    'historical-life': (('Generate letters & journals', '/p/writers-room'), ('Historical checks', '/p/historical-check'), ('Dowries, guardians & care', '/p/life-records'), ('Record migration', '/p/world#migration-new'), ('Succession & campaigns', '/p/challenge')),
    'timeline': (('Read yearly paragraphs', '/p/storyline'), ('Add personal notes', '/p/notes'), ('Explore family connections', '/p/family-tree')),
    'storyline': (('See the visual timeline', '/p/timeline'), ('Play the drama deck', '/p/drama'), ('Add personal notes', '/p/notes'), ('View statistics', '/p/statistics')),
    'saves': (('Connect this save to the game', '/p/clock'), ('Sync desktop / online records', '/p/sync'), ('Set calendar & rules', '/p/rules')),
    'sync': (('Back up before switching', '/p/saves'), ('Connect the game instead', '/p/clock'), ('Manage account access', '/p/account')),
}

PAGE_SECTIONS = {
    'family-projects': (('Ambitions','ambition'), ('Secrets & knowledge','secret'), ('Event recovery','recovery'), ('Achievements','achievement')),
    'planner': (('Household rotation', 'rotation'), ('Family plans', 'family-plans'), ('Add a plan', 'new-family-plan'), ('Dynasty health', 'dynasty-health')),
    'relationships': (('Marriage rolls', 'marriage-rolls'), ('Create courtship', 'courtship'), ('Marriage dates', 'marriage-dates'), ('Add relationship', 'new-relationship'), ('Recorded connections', 'relationship-register')),
    'challenge': (('Succession & heirs', 'succession'), ('War & campaigns', 'campaigns'), ('Era guidance', 'era-guidance')),
    'world': (('Current locations', 'locations'), ('Record a move', 'migration-new'), ('Migration routes', 'migration-routes'), ('Migration ledger', 'migration-ledger')),
}

def related_tasks(page):
    return RELATED_TASKS.get(page, ())

def page_sections(page):
    return PAGE_SECTIONS.get(page, ())
