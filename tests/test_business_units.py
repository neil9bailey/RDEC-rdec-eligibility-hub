from sqlmodel import select

from app.models import BusinessUnit
from app.seed import seed_business_units


def test_reference_business_units_seed_cleanly(session):
    seed_business_units(session)
    units = {unit.name: unit for unit in session.exec(select(BusinessUnit))}

    assert "Transport" in units
    assert "Highways" in units
    assert "Rail" in units
    assert "Scada" in units
    assert "TfL" in units
    assert "Network Services" in units
    assert "HPC / Hinckley Point C" in units
    assert "Nuclear Power" in units
    assert "Core Central Asset Management" in units
    assert units["Highways"].parent_id == units["Transport"].id
