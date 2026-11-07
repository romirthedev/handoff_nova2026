"""The demo restaurant: its menu (with cook times) and 25 orders from different sources."""

# cook: minutes on the line. A dict means the time depends on the doneness the guest asked for.
# look: what the dish looks like, for the vision model. aliases: words staff say out loud.
MENU = [
    {"id": "ribeye", "name": "Ribeye Steak", "emoji": "🥩", "look": "a grilled steak / raw-looking cut of red meat",
     "cook": {"rare": 12, "medium rare": 15, "medium": 17, "medium well": 19, "well done": 21},
     "aliases": ["ribeye", "rib eye", "ribeye steak", "steak", "steaks"]},
    {"id": "lobster", "name": "Lobster Tail", "emoji": "🦞", "look": "a red lobster or lobster tail",
     "cook": 12, "aliases": ["lobster", "lobster tail", "lobsters"]},
    {"id": "mash", "name": "Mashed Potatoes", "emoji": "🥔", "look": "mashed potatoes, or a potato",
     "cook": 0, "aliases": ["mashed potatoes", "mashed potato", "mash", "potatoes", "potato"]},
    {"id": "fries", "name": "Fries", "emoji": "🍟", "look": "french fries, often in a red carton",
     "cook": 4, "aliases": ["fries", "french fries", "fry"]},
    {"id": "burger", "name": "Smash Burger", "emoji": "🍔", "look": "a hamburger / cheeseburger",
     "cook": {"medium rare": 8, "medium": 9, "medium well": 10, "well done": 11},
     "aliases": ["burger", "burgers", "smash burger", "smashburger", "smash burgers", "hamburger", "cheeseburger"]},
    {"id": "caesar", "name": "Caesar Salad", "emoji": "🥗", "look": "a green leafy salad in a bowl",
     "cook": 0, "aliases": ["caesar", "caesar salad", "salad", "salads"]},
    {"id": "salmon", "name": "Grilled Salmon", "emoji": "🐟", "look": "a fish or a fillet of salmon",
     "cook": 10, "aliases": ["salmon", "fish"]},
    {"id": "shrimp", "name": "Fried Shrimp", "emoji": "🍤", "look": "fried / tempura shrimp",
     "cook": 6, "aliases": ["shrimp", "prawns", "fried shrimp"]},
    {"id": "pasta", "name": "Spaghetti Pomodoro", "emoji": "🍝", "look": "spaghetti / pasta with tomato sauce",
     "cook": 9, "aliases": ["pasta", "spaghetti", "pomodoro"]},
    {"id": "pizza", "name": "Margherita Pizza", "emoji": "🍕", "look": "a pizza or slice of pizza",
     "cook": 7, "aliases": ["pizza", "pizzas", "margherita", "margarita", "margherita pizza", "margarita pizza"]},
    {"id": "sushi", "name": "Salmon Nigiri", "emoji": "🍣", "look": "sushi / nigiri",
     "cook": 0, "aliases": ["sushi", "nigiri", "salmon nigiri"]},
    {"id": "taco", "name": "Carnitas Taco", "emoji": "🌮", "look": "a taco",
     "cook": 5, "aliases": ["taco", "tacos", "carnitas"]},
    {"id": "chicken", "name": "Roast Chicken", "emoji": "🍗", "look": "a roasted chicken leg / drumstick",
     "cook": 18, "aliases": ["chicken", "roast chicken", "drumstick"]},
    {"id": "corn", "name": "Street Corn", "emoji": "🌽", "look": "an ear of corn",
     "cook": 6, "aliases": ["corn", "street corn", "elote"]},
    {"id": "bread", "name": "Garlic Bread", "emoji": "🥖", "look": "a baguette / loaf of bread",
     "cook": 3, "aliases": ["garlic bread", "bread", "baguette"]},
    {"id": "soup", "name": "Tomato Soup", "emoji": "🥣", "look": "a bowl of soup",
     "cook": 3, "aliases": ["soup", "tomato soup", "bisque"]},
    {"id": "broccoli", "name": "Charred Broccoli", "emoji": "🥦", "look": "broccoli",
     "cook": 5, "aliases": ["broccoli"]},
    {"id": "rice", "name": "Jasmine Rice", "emoji": "🍚", "look": "a bowl of white rice",
     "cook": 0, "aliases": ["rice", "jasmine rice"]},
    {"id": "cake", "name": "Chocolate Cake", "emoji": "🍰", "look": "a slice of cake",
     "cook": 0, "aliases": ["cake", "chocolate cake", "dessert"]},
    {"id": "lemonade", "name": "Lemonade", "emoji": "🍋", "look": "a lemon or a glass of lemonade",
     "cook": 0, "aliases": ["lemonade", "lemon"]},
    {"id": "soda", "name": "Fountain Soda", "emoji": "🥤", "look": "a soda cup with a straw",
     "cook": 0, "aliases": ["soda", "coke", "drink", "pop"]},
]
BY_ID = {m["id"]: m for m in MENU}

SOURCES = ["DoorDash", "Uber Eats", "Grubhub", "Phone", "Website", "Walk-in"]


def cook_minutes(menu_id: str, mod: str | None) -> int:
    cook = BY_ID[menu_id]["cook"]
    if isinstance(cook, dict):
        return cook.get((mod or "").lower()) or cook.get("medium") or next(iter(cook.values()))
    return cook


# (number, guest, source, minutes ago, courier, [(item, qty, modifier)])
DEMO_ORDERS = [
    (41, "Maya Chen", "DoorDash", 24, "Alex R.", [("burger", 1, "medium"), ("fries", 1, None), ("lemonade", 1, None)]),
    (42, "Jordan Ellis", "Uber Eats", 23, "Sam K.", [("salmon", 1, None), ("rice", 1, None), ("broccoli", 1, None)]),
    (43, "Priya Shah", "Walk-in", 22, None, [("pizza", 1, None), ("caesar", 1, "dressing on side")]),
    (44, "Marcus Webb", "DoorDash", 21, "Dee M.", [("chicken", 1, None), ("mash", 1, None), ("corn", 1, None)]),
    (45, "John Carter", "DoorDash", 20, "Chris P.", [("ribeye", 1, "medium rare"), ("lobster", 1, None), ("mash", 1, None)]),
    (46, "Sofia Romero", "Grubhub", 19, "Tina L.", [("pasta", 1, None), ("bread", 1, None)]),
    (47, "Ethan Brooks", "Phone", 18, None, [("taco", 3, "no onion"), ("rice", 1, None)]),
    (48, "Ava Thompson", "DoorDash", 17, "Marco V.", [("sushi", 1, None), ("soup", 1, None)]),
    (49, "Liam Nguyen", "Uber Eats", 16, "Jess W.", [("shrimp", 1, None), ("fries", 1, None), ("caesar", 1, None)]),
    (50, "Chloe Kim", "Website", 15, None, [("cake", 1, None), ("lemonade", 1, None)]),
    (51, "Noah Patel", "DoorDash", 14, "Alex R.", [("burger", 1, "well done"), ("burger", 1, "medium"), ("fries", 2, None)]),
    (52, "Isabella Rossi", "Grubhub", 13, "Omar H.", [("salmon", 1, None), ("mash", 1, None), ("broccoli", 1, None)]),
    (53, "Lucas Martin", "Walk-in", 12, None, [("ribeye", 1, "medium"), ("fries", 1, None), ("caesar", 1, None)]),
    (54, "Mia Johnson", "DoorDash", 11, "Dee M.", [("pizza", 1, "extra basil"), ("bread", 1, None), ("soda", 1, None)]),
    (55, "Oliver Davis", "Uber Eats", 10, "Sam K.", [("chicken", 1, None), ("rice", 1, None), ("broccoli", 1, None)]),
    (56, "Emma Wilson", "Phone", 9, None, [("lobster", 1, None), ("corn", 1, None), ("bread", 1, None)]),
    (57, "James Lee", "DoorDash", 8, "Chris P.", [("sushi", 2, None), ("soda", 1, None)]),
    (58, "Harper Scott", "Website", 7, None, [("pasta", 1, "gluten free"), ("caesar", 1, None), ("cake", 1, None)]),
    (59, "Benjamin Ortiz", "DoorDash", 6, "Marco V.", [("taco", 2, None), ("corn", 1, None), ("lemonade", 1, None)]),
    (60, "Amelia Brown", "Uber Eats", 5, "Jess W.", [("ribeye", 1, "rare"), ("shrimp", 1, None), ("broccoli", 1, None)]),
    (61, "Elijah Moore", "Grubhub", 4, "Tina L.", [("burger", 1, "medium rare"), ("soup", 1, None)]),
    (62, "Charlotte Hall", "DoorDash", 3, "Alex R.", [("salmon", 1, None), ("caesar", 1, None)]),
    (63, "Henry Young", "Walk-in", 2, None, [("pizza", 2, None), ("soda", 2, None)]),
    (64, "Evelyn King", "DoorDash", 1, "Dee M.", [("chicken", 1, None), ("mash", 1, None), ("cake", 1, None)]),
    (65, "Daniel Wright", "Uber Eats", 0, "Omar H.", [("ribeye", 1, "medium well"), ("lobster", 1, None), ("fries", 1, None)]),
]
