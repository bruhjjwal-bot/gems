"""Load approved taxonomy from Labels.xlsx and validate (L1, L2, L3) combos."""
from collections import defaultdict
from pathlib import Path
from typing import Optional

import openpyxl

DEFAULT_PATH = Path("/Users/headout/Documents/Gems/step 2/Labels.xlsx")


EXTRA_COMBOS = [
    # Operational Intelligence extensions
    ("Operational Intelligence", "Pricing", "Entrance Fee"),
    ("Operational Intelligence", "Pricing", "Add-on Cost"),
    ("Operational Intelligence", "Pricing", "Third-party Markup"),
    ("Operational Intelligence", "Policy & Booking", "Refund Policy"),
    ("Operational Intelligence", "Policy & Booking", "Reservation Required"),
    ("Operational Intelligence", "Policy & Booking", "Age Restriction"),
    ("Operational Intelligence", "Facilities", "Restroom"),
    ("Operational Intelligence", "Facilities", "Climate Control"),
    ("Operational Intelligence", "Facilities", "Seating"),
    ("Operational Intelligence", "Third-party Operator", "Tour Operator"),
    ("Operational Intelligence", "Third-party Operator", "Reseller Platform"),
    # Attention Intelligence extensions
    ("Attention Intelligence", "Iconic Artwork", "Painting"),
    ("Attention Intelligence", "Iconic Artwork", "Sculpture"),
    ("Attention Intelligence", "Iconic Landmark", "Monument"),
]


def load_taxonomy(path: Path = DEFAULT_PATH) -> list[tuple[str, str, str]]:
    """Return list of (l1, l2, l3) tuples from the approved taxonomy."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb["Sheet1"]
    rows = list(ws.iter_rows(values_only=True))
    # Skip header
    combos = []
    for r in rows[1:]:
        if r and r[0] and r[1] and r[2]:
            combos.append((str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()))
    # Inject v2 extension combos (only if not already present)
    seen = set(combos)
    for combo in EXTRA_COMBOS:
        if combo not in seen:
            combos.append(combo)
            seen.add(combo)
    return combos


def build_lookups(combos: list[tuple[str, str, str]]):
    """Build lookups for validation and prompt construction.

    Returns:
        valid_set:  set of (l1, l2, l3) tuples
        l1_to_l2:   {l1: sorted_unique_l2s}
        l1l2_to_l3: {(l1, l2): sorted_unique_l3s}
        l1_set:     set of all valid L1s
    """
    valid_set = set(combos)
    l1_to_l2: dict[str, list[str]] = defaultdict(list)
    l1l2_to_l3: dict[tuple[str, str], list[str]] = defaultdict(list)
    for l1, l2, l3 in combos:
        if l2 not in l1_to_l2[l1]:
            l1_to_l2[l1].append(l2)
        if l3 not in l1l2_to_l3[(l1, l2)]:
            l1l2_to_l3[(l1, l2)].append(l3)
    return {
        "valid_set": valid_set,
        "l1_to_l2": dict(l1_to_l2),
        "l1l2_to_l3": dict(l1l2_to_l3),
        "l1_set": set(l1_to_l2.keys()),
    }


def taxonomy_for_prompt(combos: list[tuple[str, str, str]]) -> str:
    """Compact taxonomy representation for the labelling prompt."""
    lookups = build_lookups(combos)
    lines = []
    for l1 in sorted(lookups["l1_set"]):
        lines.append(f"\n=== {l1} ===")
        for l2 in lookups["l1_to_l2"][l1]:
            l3s = lookups["l1l2_to_l3"][(l1, l2)]
            lines.append(f"  {l2}  →  {', '.join(l3s)}")
    return "\n".join(lines)


def is_valid_combo(l1: str, l2: str, l3: str, lookups: dict) -> bool:
    return (l1, l2, l3) in lookups["valid_set"]


def nearest_valid(l1: str, l2: str, l3: str, lookups: dict) -> Optional[tuple[str, str, str]]:
    """If (l1,l2,l3) invalid, try to recover by walking up.

    Strategy: keep l1 if valid; pick first l2 under l1; first l3 under (l1, l2).
    Returns None if l1 itself is invalid.
    """
    if l1 not in lookups["l1_set"]:
        return None
    valid_l2s = lookups["l1_to_l2"][l1]
    chosen_l2 = l2 if l2 in valid_l2s else valid_l2s[0]
    valid_l3s = lookups["l1l2_to_l3"][(l1, chosen_l2)]
    chosen_l3 = l3 if l3 in valid_l3s else valid_l3s[0]
    return (l1, chosen_l2, chosen_l3)
