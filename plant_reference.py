from __future__ import annotations

REGIONS=("Britain & Ireland","Northern/Western Europe","Mediterranean Europe","Americas","Other / custom")

# Dates are deliberately conservative gameplay cutoffs, not claims about a single first specimen.
# None means the plant is fictional or supernatural and should follow the player's challenge rules.
# plant, pack, Sims season, origin, Britain, N/W Europe, Mediterranean, Americas, historical note
PLANTS=(
    ("Apple","Base Game","Fall","Central Asia",-1000,-1000,-1000,1600,"Cultivated across Europe since antiquity; brought to colonial North America."),
    ("Basil","Base Game","Summer, Fall","South Asia",1500,1200,-500,1600,"Ancient Mediterranean herb; conservative later cutoff for northern household gardens."),
    ("Bird of Paradise","Base Game","Spring, Summer","South Africa",1773,1773,1773,1800,"An elite ornamental in European collections from the late eighteenth century."),
    ("Blackberry","Base Game","Summer","Europe and Asia",-1000,-1000,-1000,-1000,"Wild regional Rubus species were long gathered and cultivated."),
    ("Bluebell","Base Game","Spring, Summer","Western Europe",-1000,-1000,1500,1600,"Native woodland flower in Britain and western Europe."),
    ("Bonsai Buds","Base Game","All seasons","Fictional Sims harvestable",None,None,None,None,"Treat as a challenge-rule or decorative plant."),
    ("Carrot","Base Game","Spring, Fall","Central and Southwest Asia",1400,1200,1000,1600,"Medieval carrots were often purple, red, yellow, or white rather than modern orange."),
    ("Cherry","Base Game","Summer","Europe and western Asia",50,50,-500,1600,"Known around the Mediterranean and spread through Roman and medieval Europe."),
    ("Chrysanthemum","Base Game","Summer, Fall","East Asia",1790,1680,1650,1800,"Imported ornamental; dates vary by cultivar and country."),
    ("Cow Berry","Base Game","All seasons","Fictional Sims harvestable",None,None,None,None,"Cowplants are supernatural; enable only when your challenge permits them."),
    ("Daisy","Base Game","Spring","Europe",-1000,-1000,-1000,1600,"Common European wildflower."),
    ("Death Flower","Base Game","Winter","Fictional Sims harvestable",None,None,None,None,"Supernatural plant; use challenge rules rather than historical availability."),
    ("Dragonfruit","Base Game","Fall","Tropical Americas",1800,1700,1600,-1000,"American cactus fruit; European cultivation required later collecting and protected heat."),
    ("Forbidden Fruit of the PlantSim","Base Game","All seasons","Fictional Sims harvestable",None,None,None,None,"PlantSim gameplay item; use challenge rules."),
    ("Grape","Base Game","Fall","Mediterranean and western Asia",50,-500,-2000,1600,"Roman Britain grew some grapes; reliable wine production favored warmer regions."),
    ("Growfruit","Base Game","All seasons","Fictional Sims harvestable",None,None,None,None,"Event/fantasy harvestable; use challenge rules."),
    ("Lemon","Base Game","All seasons","South and East Asia",1650,1400,800,1600,"Known in medieval Mediterranean gardens; northern climates generally required protection."),
    ("Lily","Base Game","Summer","Northern Hemisphere",-1000,-1000,-1000,-1000,"Native species and cultivated lilies have a long history."),
    ("Mushroom","Base Game","Spring, Fall","Worldwide",-1000,-1000,-1000,-1000,"Wild mushrooms were gathered long before controlled cultivation."),
    ("Onion","Base Game","Fall, Winter","Central and western Asia",-1000,-1000,-2000,1500,"Ancient Old World staple."),
    ("Orchid","Base Game","Winter, Spring","Worldwide wild species",1750,1700,1600,1700,"Local wild orchids existed earlier; exotic household cultivation belongs to later elite collections."),
    ("Parsley","Base Game","Spring, Summer","Mediterranean",-500,-500,-1000,1600,"Ancient Mediterranean herb established in European gardens."),
    ("Pear","Base Game","Fall, Winter","Europe and Asia",-500,-500,-1000,1600,"Cultivated in classical and medieval Europe."),
    ("Plantain","Base Game","Summer","Tropical Southeast Asia",1800,1700,1500,1500,"Tropical crop; in northern Europe it requires a heated glasshouse and remains elite."),
    ("Pomegranate","Base Game","Winter","Iran to northern India",1650,1400,-500,1600,"Ancient Mediterranean fruit; northern cultivation generally needed a warm protected site."),
    ("Potato","Base Game","Winter","Andes",1590,1570,1570,-1000,"Reached Europe in the late sixteenth century; widespread staple use came substantially later."),
    ("Rose","Base Game","Spring, Fall","Europe and Asia",-1000,-1000,-1000,1600,"Cultivated since antiquity."),
    ("Sage","Base Game","All seasons","Mediterranean",800,500,-1000,1600,"Long-used medicinal and culinary herb."),
    ("Snapdragon","Base Game","Spring, Fall","Mediterranean",1500,1200,-1000,1600,"Mediterranean ornamental known in later medieval and early-modern European gardens."),
    ("Spinach","Base Game","Winter","Persia",1400,1200,1000,1600,"Spread through the Islamic world into medieval Europe."),
    ("Strawberry","Base Game","Spring","Northern Hemisphere",-1000,-1000,-1000,-1000,"Wild strawberries are ancient; modern large garden strawberries are eighteenth-century hybrids."),
    ("Tomato","Base Game","Summer","Andes and Mesoamerica",1700,1600,1535,-1000,"Cultivated in Iberia by the sixteenth century; northern Europe first treated it mainly as an ornamental."),
    ("Trash Fruit","Base Game","All seasons","Fictional Sims harvestable",None,None,None,None,"Sims gameplay plant; use challenge rules."),
    ("Tulip","Base Game","Spring","Central Asia and Ottoman lands",1570,1550,1500,1700,"Entered western European elite gardens in the sixteenth century."),
    ("U.F.O. Fruit","Base Game","Fall","Fictional Sims harvestable",None,None,None,None,"Alien harvestable; use challenge rules."),
    ("Wolfsbane","Base Game","All seasons","Europe",-1000,-1000,-1000,1600,"Real aconite species are Old World plants, though the Vampires gameplay use is fictional."),
    ("Garlic","Vampires","All seasons","Central and western Asia",-1000,-1000,-2000,1500,"Ancient Old World crop."),
    ("Plasma Fruit","Vampires","All seasons","Fictional Sims harvestable",None,None,None,None,"Vampire gameplay plant; use challenge rules."),
    ("Sixam Mosquito Trap","Vampires","All seasons","Fictional Sims harvestable",None,None,None,None,"Alien gameplay plant; use challenge rules."),
    ("Aubergine","Cottage Living","All seasons","South and East Asia",1600,1500,800,1600,"Known earlier around the Mediterranean; northern cultivation is later and warmth-dependent."),
    ("Lettuce","Cottage Living","All seasons","Mediterranean and western Asia",-500,-500,-1000,1500,"Cultivated since antiquity."),
    ("Oversized Mushroom","Cottage Living","All seasons","Worldwide",-1000,-1000,-1000,-1000,"Historically plausible species vary; oversized form is Sims gameplay."),
    ("Pumpkin","Cottage Living","All seasons","Americas",1600,1550,1500,-1000,"New World squash reached Europe after 1492."),
    ("Watermelon","Cottage Living","All seasons","Africa",1700,1400,1000,1500,"Known around the medieval Mediterranean; northern outdoor cultivation was less reliable."),
)

def region_for(location):
    text=str(location or "").casefold()
    if any(x in text for x in ("england","britain","scotland","wales","ireland","uk","london")):
        return "Britain & Ireland"
    if any(x in text for x in ("france","germany","netherlands","belgium","switzerland","austria","northern europe","western europe")):
        return "Northern/Western Europe"
    if any(x in text for x in ("italy","spain","portugal","greece","mediterranean","rome","iberia")):
        return "Mediterranean Europe"
    if any(x in text for x in ("america","canada","mexico","brazil","peru","andes","caribbean")):
        return "Americas"
    return "Other / custom"

def rows(year,region):
    index={"Britain & Ireland":4,"Northern/Western Europe":5,"Mediterranean Europe":6,"Americas":7}
    result=[]
    for item in PLANTS:
        plant,pack,season,origin,*rest=item
        dates=rest[:4]; note=rest[4]
        earliest=dates[index.get(region,4)-4] if region in index else None
        if earliest is None:
            status="Challenge-dependent" if "Fictional" in origin else "Needs local research"
        elif int(year)>=earliest:
            status="Historically available"
        else:
            status=f"Not yet — around {earliest}"
        result.append({"Plant":plant,"Status":status,"Historical cutoff":earliest,
                       "Sims outdoor season":season,"Pack":pack,"Origin":origin,"Historical note":note})
    return result
