# Seed data for #550. Division numbers and titles follow CSI MasterFormat 2016
# (the division list is published openly; the numbered SECTION catalogue is
# CSI's licensed product, so only a modest set of common sections ships, and
# companies import their own full list through POST /api/cost-codes/import).
# Reserved division numbers (15-20, 24, 29, 30, 36-39, 47, 49) have no title
# and are not created; a company can add them.

import re

from sqlalchemy.orm import Session

DIVISIONS = (
    ("00", "Procurement and Contracting Requirements"),
    ("01", "General Requirements"),
    ("02", "Existing Conditions"),
    ("03", "Concrete"),
    ("04", "Masonry"),
    ("05", "Metals"),
    ("06", "Wood, Plastics, and Composites"),
    ("07", "Thermal and Moisture Protection"),
    ("08", "Openings"),
    ("09", "Finishes"),
    ("10", "Specialties"),
    ("11", "Equipment"),
    ("12", "Furnishings"),
    ("13", "Special Construction"),
    ("14", "Conveying Equipment"),
    ("21", "Fire Suppression"),
    ("22", "Plumbing"),
    ("23", "Heating, Ventilating, and Air Conditioning (HVAC)"),
    ("25", "Integrated Automation"),
    ("26", "Electrical"),
    ("27", "Communications"),
    ("28", "Electronic Safety and Security"),
    ("31", "Earthwork"),
    ("32", "Exterior Improvements"),
    ("33", "Utilities"),
    ("34", "Transportation"),
    ("35", "Waterway and Marine Construction"),
    ("40", "Process Interconnections"),
    ("41", "Material Processing and Handling Equipment"),
    ("42", "Process Heating, Cooling, and Drying Equipment"),
    ("43", "Process Gas and Liquid Handling, Purification, and Storage Equipment"),
    ("44", "Pollution and Waste Control Equipment"),
    ("45", "Industry-Specific Manufacturing Equipment"),
    ("46", "Water and Wastewater Equipment"),
    ("48", "Electrical Power Generation"),
)

# Common sections ("DD SS SS", parent = the division). A modest, widely used set.
SECTIONS = (
    ("01 10 00", "Summary"),
    ("01 20 00", "Price and Payment Procedures"),
    ("01 30 00", "Administrative Requirements"),
    ("01 40 00", "Quality Requirements"),
    ("01 50 00", "Temporary Facilities and Controls"),
    ("01 60 00", "Product Requirements"),
    ("01 70 00", "Execution and Closeout Requirements"),
    ("02 41 00", "Demolition"),
    ("03 10 00", "Concrete Forming and Accessories"),
    ("03 20 00", "Concrete Reinforcing"),
    ("03 30 00", "Cast-in-Place Concrete"),
    ("03 40 00", "Precast Concrete"),
    ("04 20 00", "Unit Masonry"),
    ("04 40 00", "Stone Assemblies"),
    ("05 10 00", "Structural Metal Framing"),
    ("05 30 00", "Metal Decking"),
    ("05 50 00", "Metal Fabrications"),
    ("06 10 00", "Rough Carpentry"),
    ("06 20 00", "Finish Carpentry"),
    ("06 40 00", "Architectural Woodwork"),
    ("07 10 00", "Dampproofing and Waterproofing"),
    ("07 20 00", "Thermal Protection"),
    ("07 40 00", "Roofing and Siding Panels"),
    ("07 50 00", "Membrane Roofing"),
    ("07 60 00", "Flashing and Sheet Metal"),
    ("07 90 00", "Joint Protection"),
    ("08 10 00", "Doors and Frames"),
    ("08 30 00", "Specialty Doors and Frames"),
    ("08 40 00", "Entrances, Storefronts, and Curtain Walls"),
    ("08 50 00", "Windows"),
    ("08 70 00", "Hardware"),
    ("08 80 00", "Glazing"),
    ("09 20 00", "Plaster and Gypsum Board"),
    ("09 30 00", "Tiling"),
    ("09 50 00", "Ceilings"),
    ("09 60 00", "Flooring"),
    ("09 90 00", "Painting and Coating"),
    ("10 10 00", "Information Specialties"),
    ("10 20 00", "Interior Specialties"),
    ("10 40 00", "Safety Specialties"),
    ("11 30 00", "Residential Equipment"),
    ("11 40 00", "Foodservice Equipment"),
    ("12 30 00", "Casework"),
    ("12 50 00", "Furniture"),
    ("14 20 00", "Elevators"),
    ("21 10 00", "Water-Based Fire-Suppression Systems"),
    ("22 10 00", "Plumbing Piping"),
    ("22 40 00", "Plumbing Fixtures"),
    ("23 05 00", "Common Work Results for HVAC"),
    ("23 30 00", "HVAC Air Distribution"),
    ("23 80 00", "Decentralized HVAC Equipment"),
    ("26 05 00", "Common Work Results for Electrical"),
    ("26 20 00", "Low-Voltage Electrical Transmission"),
    ("26 50 00", "Lighting"),
    ("27 10 00", "Structured Cabling"),
    ("28 10 00", "Access Control"),
    ("28 46 00", "Fire Detection and Alarm"),
    ("31 10 00", "Site Clearing"),
    ("31 20 00", "Earth Moving"),
    ("31 60 00", "Special Foundations and Load-Bearing Elements"),
    ("32 10 00", "Bases, Ballasts, and Paving"),
    ("32 30 00", "Site Improvements"),
    ("32 90 00", "Planting"),
    ("33 10 00", "Water Utilities"),
    ("33 30 00", "Sanitary Sewerage Utilities"),
    ("33 40 00", "Storm Drainage Utilities"),
)

# Default trade packages -> the sections they bid. Packages are what subs bid
# and get awarded; sections are what costs post to. A package can span
# divisions; a division can be split across packages.
TRADE_PACKAGES = (
    (
        "General Conditions",
        (
            "01 10 00",
            "01 20 00",
            "01 30 00",
            "01 40 00",
            "01 50 00",
            "01 60 00",
            "01 70 00",
        ),
    ),
    ("Demolition", ("02 41 00",)),
    ("Concrete", ("03 10 00", "03 20 00", "03 30 00", "03 40 00")),
    ("Masonry", ("04 20 00", "04 40 00")),
    ("Structural Steel and Misc. Metals", ("05 10 00", "05 30 00", "05 50 00")),
    ("Rough Carpentry", ("06 10 00",)),
    ("Finish Carpentry and Millwork", ("06 20 00", "06 40 00", "12 30 00")),
    ("Waterproofing and Insulation", ("07 10 00", "07 20 00")),
    ("Roofing and Sheet Metal", ("07 40 00", "07 50 00", "07 60 00")),
    ("Joint Sealants", ("07 90 00",)),
    ("Doors, Frames and Hardware", ("08 10 00", "08 30 00", "08 70 00")),
    ("Glass and Glazing", ("08 40 00", "08 50 00", "08 80 00")),
    ("Drywall and Ceilings", ("09 20 00", "09 50 00")),
    ("Tile", ("09 30 00",)),
    ("Flooring", ("09 60 00",)),
    ("Painting", ("09 90 00",)),
    ("Specialties", ("10 10 00", "10 20 00", "10 40 00")),
    ("Equipment and Furnishings", ("11 30 00", "11 40 00", "12 50 00")),
    ("Elevators", ("14 20 00",)),
    ("Fire Protection", ("21 10 00",)),
    ("Plumbing", ("22 10 00", "22 40 00")),
    ("HVAC", ("23 05 00", "23 30 00", "23 80 00")),
    ("Electrical", ("26 05 00", "26 20 00", "26 50 00")),
    ("Low Voltage and Life Safety", ("27 10 00", "28 10 00", "28 46 00")),
    ("Earthwork", ("31 10 00", "31 20 00", "31 60 00")),
    ("Paving and Site Improvements", ("32 10 00", "32 30 00")),
    ("Landscaping", ("32 90 00",)),
    ("Site Utilities", ("33 10 00", "33 30 00", "33 40 00")),
)

# A division title by its two-digit number, so a section whose division is
# not yet in the chart can create it on the fly.
DIVISION_TITLES = dict(DIVISIONS)

# ---------------------------------------------------------------------------
# Code shape: CSI "DD SS SS" with an optional ".SS" extension. A MasterFormat
# code is all digits and separators with 2 (division), 4, 6 (section) or 8
# (section + extension) digits; anything else (L-100, EQ) is a custom code and
# is left exactly as typed.
# ---------------------------------------------------------------------------

_MF_SHAPE = re.compile(r"^[\d\s.\-]+$")
_DIVISION = re.compile(r"^\d{2}$")
_SECTION = re.compile(r"^\d{2} \d{2} \d{2}(\.\d{2})?$")


def normalize_code(raw: str) -> str:
    """Normalize a typed cost code to 'DD SS SS' (optional '.SS'). A code
    that isn't MasterFormat-shaped is returned stripped but otherwise as
    typed."""
    s = (raw or "").strip()
    if not s or not _MF_SHAPE.match(s):
        return s
    digits = re.sub(r"\D", "", s)
    n = len(digits)
    if n == 2:
        return digits
    if n == 4:
        digits += "00"
        n = 6
    if n == 6:
        return f"{digits[0:2]} {digits[2:4]} {digits[4:6]}"
    if n == 8:
        return f"{digits[0:2]} {digits[2:4]} {digits[4:6]}.{digits[6:8]}"
    return s


def code_level(code: str) -> str | None:
    """'division', 'section', or None for a code that isn't MasterFormat."""
    c = (code or "").strip()
    if _DIVISION.match(c):
        return "division"
    if _SECTION.match(c):
        return "section"
    return None


def division_of(code: str) -> str | None:
    """The division code 'DD' a normalized section belongs under, else None."""
    if code_level(code) == "section":
        return code[:2]
    return None


def load_masterformat(db: Session) -> dict:
    """Load the division titles and the common sections: divisions first,
    then each section parented to its division (created from the title table
    when missing). Codes already present are skipped, so it is safe to
    repeat. Seeded codes carry cost_type 'other' — the per-line type is what
    reports read."""
    from app.models.cost_codes import CostCode

    by_code = {c.code.lower(): c for c in db.query(CostCode).all()}
    created = 0

    def ensure_division(code: str, name: str) -> CostCode:
        nonlocal created
        row = by_code.get(code.lower())
        if row is None:
            row = CostCode(code=code, name=name, cost_type="other", level="division")
            db.add(row)
            db.flush()
            by_code[code.lower()] = row
            created += 1
        return row

    for code, name in DIVISIONS:
        ensure_division(code, name)

    for raw, name in SECTIONS:
        code = normalize_code(raw)
        if code.lower() in by_code:
            continue
        div = division_of(code)
        parent = ensure_division(div, DIVISION_TITLES.get(div, div)) if div else None
        row = CostCode(
            code=code,
            name=name,
            cost_type="other",
            level="section",
            parent_id=parent.id if parent else None,
        )
        db.add(row)
        db.flush()
        by_code[code.lower()] = row
        created += 1

    db.flush()
    return {"created": created}


def seed_trade_packages(db: Session) -> int:
    """Seed the default trade packages and their section mappings, once, when
    the company has none. Maps each package by section code and skips a code
    the chart doesn't have. Returns the number of packages created."""
    from app.models.cost_codes import CostCode
    from app.models.trade_packages import TradePackage, TradePackageSection

    if db.query(TradePackage.id).first():
        return 0
    by_code = {c.code.lower(): c for c in db.query(CostCode).all()}
    created = 0
    for order, (name, section_codes) in enumerate(TRADE_PACKAGES):
        pkg = TradePackage(name=name, sort_order=order)
        db.add(pkg)
        db.flush()
        for raw in section_codes:
            code = by_code.get(normalize_code(raw).lower())
            if code is not None:
                db.add(TradePackageSection(package_id=pkg.id, cost_code_id=code.id))
        created += 1
    db.flush()
    return created
