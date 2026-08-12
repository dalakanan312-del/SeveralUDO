from __future__ import annotations

SEASONS = {
    "Apple":"Fall", "Basil":"Summer, Fall", "Bird of Paradise":"Spring, Summer", "Blackberry":"Summer",
    "Bluebell":"Spring, Summer", "Bonsai Buds":"All seasons", "Carrot":"Spring, Fall", "Cherry":"Summer",
    "Chrysanthemum":"Summer, Fall", "Cow Berry":"All seasons", "Daisy":"Spring", "Death Flower":"Winter",
    "Dragonfruit":"Fall", "Forbidden Fruit of the PlantSim":"All seasons", "Grape":"Fall", "Growfruit":"All seasons",
    "Lemon":"All seasons", "Lily":"Summer", "Mushroom":"Spring, Fall", "Onion":"Fall, Winter",
    "Orchid":"Winter, Spring", "Parsley":"Spring, Summer", "Pear":"Fall, Winter", "Plantain":"Summer",
    "Pomegranate":"Winter", "Potato":"Winter", "Rose":"Spring, Fall", "Sage":"All seasons",
    "Snapdragon":"Spring, Fall", "Spinach":"Winter", "Strawberry":"Spring", "Tomato":"Summer",
    "Trash Fruit":"All seasons", "Tulip":"Spring", "U.F.O. Fruit":"Fall", "Wolfsbane":"All seasons",
    "Garlic":"All seasons", "Plasma Fruit":"All seasons", "Sixam Mosquito Trap":"All seasons",
}

FOUND = {
    "Apple":"Willow Creek, Windenburg", "Basil":"Sylvan Glade, seed packets",
    "Blackberry":"Oasis Springs — Desert Bloom Park; Windenburg", "Bluebell":"Willow Creek, Windenburg",
    "Carrot":"Oasis Springs; Windenburg", "Cherry":"Oasis Springs; Sylvan Glade; Windenburg",
    "Chrysanthemum":"Willow Creek; Oasis Springs", "Daisy":"Oasis Springs; Windenburg",
    "Grape":"Starter fruit seed packets", "Lemon":"Oasis Springs; Windenburg",
    "Lily":"Willow Creek; Sylvan Glade; Forgotten Grotto", "Mushroom":"Willow Creek; Forgotten Grotto; Windenburg",
    "Onion":"Willow Creek; Oasis Springs; Forgotten Grotto; Windenburg", "Parsley":"Windenburg; herb seed packets",
    "Pear":"Willow Creek; Sylvan Glade; Windenburg", "Potato":"Willow Creek; Forgotten Grotto; Windenburg",
    "Rose":"Willow Creek; Windenburg", "Sage":"Oasis Springs; Forgotten Grotto; Windenburg",
    "Snapdragon":"Willow Creek; Sylvan Glade; Windenburg", "Spinach":"Windenburg; vegetable seed packets",
    "Strawberry":"Willow Creek; Oasis Springs; Windenburg", "Tomato":"Oasis Springs; Windenburg", "Tulip":"Oasis Springs",
}

SPECIAL = {
    "Bird of Paradise":"Graft Tulip + Chrysanthemum or rare seed packet", "Bonsai Buds":"Graft Daisy + Strawberry or uncommon seed packet",
    "Cow Berry":"Graft Dragonfruit + Snapdragon; fishing/space/rare packet", "Death Flower":"Graft Orchid + Pomegranate or rare seed packet",
    "Dragonfruit":"Graft Snapdragon + Strawberry or rare seed packet", "Forbidden Fruit of the PlantSim":"PlantSim challenge/magic-bean tree",
    "Growfruit":"Starter Growfruit packet or existing tree", "Orchid":"Graft Lily + Snapdragon or rare seed packet",
    "Pomegranate":"Graft Apple + Cherry or uncommon seed packet", "Trash Fruit":"Leave outdoor trash until it sprouts",
    "U.F.O. Fruit":"Space exploration or rare seed packet", "Wolfsbane":"Vampires seed packet / Forgotten Hollow",
    "Garlic":"Vampires garlic seed packet / Forgotten Hollow", "Plasma Fruit":"Vampires plasma fruit seed packet / Forgotten Hollow",
    "Sixam Mosquito Trap":"Vampires seed packet / Forgotten Hollow",
}

def rows():
    result=[]
    for plant,season in SEASONS.items():
        pack="Vampires" if plant in {"Garlic","Plasma Fruit","Sixam Mosquito Trap"} else "Base Game"
        result.append({"Plant":plant,"Pack":pack,"Outdoor season":season,
                       "Where to obtain":SPECIAL.get(plant) or FOUND.get(plant) or "Seed packets, grafting, or world harvesting",
                       "Where to grow":"Ground or planter; sheltered/greenhouse for year-round growth"})
    for plant in ("Aubergine","Lettuce","Oversized Mushroom","Pumpkin","Watermelon"):
        result.append({"Plant":plant,"Pack":"Cottage Living","Outdoor season":"All seasons",
                       "Where to obtain":"Purchase oversized crop seeds","Where to grow":"Oversized Crop Garden Patch only"})
    return result
