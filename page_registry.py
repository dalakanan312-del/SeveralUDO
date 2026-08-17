"""Ordered navigation registry for the Decades Tracker 3.2 shell."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PageSpec:
    name: str
    label: str
    group: str


PAGES = (
    PageSpec("Today", "🏠 Today", "Play"),
    PageSpec("Game Clock Sync", "🕰️ Game Clock Sync", "Play"),
    PageSpec("Rolls", "🎲 Rolls", "Play"),
    PageSpec("Sims", "👤 Sims", "Family"),
    PageSpec("Family Tree", "🌳 Family Tree", "Family"),
    PageSpec("Timeline", "🕰️ Timeline", "Family"),
    PageSpec("Pregnancies", "🤰 Pregnancies", "Family"),
    PageSpec("Relationships", "💍 Relationships", "Family"),
    PageSpec("Households", "🏘️ Households", "Family"),
    PageSpec("Events", "📜 Events", "World"),
    PageSpec("Illnesses", "🩺 Illnesses", "World"),
    PageSpec("Challenge Management", "🗺️ Challenge Management", "World"),
    PageSpec("Statistics", "📊 Statistics", "Reference"),
    PageSpec("Notes", "📓 Notes", "Reference"),
    PageSpec("Planting Reference", "🌿 Planting Reference", "Reference"),
    PageSpec("Challenge Guides", "📚 Challenge Guides", "Reference"),
    PageSpec("Saves", "💾 Saves", "Manage"),
    PageSpec("Rules & Data", "⚙️ Rules & Data", "Manage"),
    PageSpec("Rules Health", "✅ Rules Health", "Manage"),
)


def navigation_labels():
    return {page.label: page.name for page in PAGES}


def page_names():
    return tuple(page.name for page in PAGES)


def grouped_pages():
    groups={}
    for page in PAGES:
        groups.setdefault(page.group,[]).append(page)
    return tuple((group,tuple(pages)) for group,pages in groups.items())
