"""Camera geography.

The Sentinel catalogue supplies only an id and a free-text name, so location is
resolved from the place names those labels contain. Coordinates below are
approximate junction-level positions for the named locality — accurate enough
for map display, cluster analysis and space-time feasibility filtering, and
flagged as approximate in the registry rather than presented as surveyed values.

ponytail: a hand-built table, not a geocoding service. Thirty fixed cameras do
not justify an API dependency; replace with a real gazetteer at deployment scale.
"""

# cam_id -> (lat, lon, city, district)
CAMERA_GEO: dict[str, tuple[float, float, str, str]] = {
    "cam01": (23.0290, 72.5580, "Ahmedabad", "Ahmedabad"),
    "cam02": (23.0330, 72.5620, "Ahmedabad", "Ahmedabad"),
    "cam03": (23.1090, 72.5900, "Ahmedabad", "Ahmedabad"),
    "cam04": (23.0130, 72.5620, "Ahmedabad", "Ahmedabad"),
    "cam05": (23.1010, 72.5860, "Ahmedabad", "Ahmedabad"),
    "cam06": (21.5060, 70.4620, "Junagadh", "Junagadh"),
    "cam07": (20.9000, 70.4000, "Gir Somnath", "Gir Somnath"),
    "cam08": (21.5150, 70.4500, "Junagadh", "Junagadh"),
    "cam09": (21.5200, 70.4300, "Junagadh", "Junagadh"),
    "cam10": (21.5220, 70.4570, "Junagadh", "Junagadh"),
    "cam11": (21.4900, 70.4300, "Junagadh", "Junagadh"),
    "cam12": (23.1650, 72.5800, "Adalaj", "Gandhinagar"),
    "cam13": (23.0380, 72.5510, "Ahmedabad", "Ahmedabad"),
    "cam14": (23.0290, 72.5700, "Ahmedabad", "Ahmedabad"),
    "cam15": (23.0180, 72.5300, "Ahmedabad", "Ahmedabad"),
    "cam16": (23.1020, 72.5870, "Ahmedabad", "Ahmedabad"),
    "cam17": (22.3000, 70.8000, "Rajkot", "Rajkot"),
    "cam18": (22.3050, 70.7950, "Rajkot", "Rajkot"),
    "cam19": (20.8200, 72.9800, "Gandevi", "Navsari"),
    "cam20": (23.2000, 72.6300, "Mohanpura", "Gandhinagar"),
    "cam21": (23.8500, 72.1200, "Patan", "Patan"),
    "cam22": (23.9000, 72.3000, "Mervada", "Banaskantha"),
    "cam23": (23.7000, 72.4000, "Kheram", "Banaskantha"),
    "cam24": (23.1700, 72.8200, "Dehgam", "Gandhinagar"),
    "cam25": (23.9500, 72.4500, "Dhanori", "Banaskantha"),
    "cam26": (20.9000, 72.9000, "Tankal", "Navsari"),
    "cam27": (20.7700, 72.9600, "Bilimora", "Navsari"),
    "cam28": (20.7710, 72.9610, "Bilimora", "Navsari"),
    "cam29": (20.7720, 72.9620, "Bilimora", "Navsari"),
    "cam30": (23.0800, 70.1300, "Gandhidham", "Kutch"),
}

# Cameras sharing a recorded clock. Only sightings within one group can be
# chained into a route — see docs/feed-recon-findings.md.
TIME_GROUPS: dict[str, list[str]] = {
    "ahmedabad-13jun": ["cam01", "cam02", "cam03", "cam04", "cam05",
                        "cam13", "cam14", "cam15"],
    "junagadh-13jun": ["cam08", "cam09", "cam10", "cam11"],
}


def time_group(cam_id: str) -> str | None:
    for group, members in TIME_GROUPS.items():
        if cam_id in members:
            return group
    return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    from math import radians, sin, cos, asin, sqrt
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))
