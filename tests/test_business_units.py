from sqlmodel import select

from app.models import BusinessUnit, Customer
from app.seed import seed_business_units


def test_reference_business_units_seed_cleanly(session):
    seed_business_units(session)
    units = {unit.name: unit for unit in session.exec(select(BusinessUnit))}
    customers = list(session.exec(select(Customer)))

    assert "Transport" in units
    assert "Highways" in units
    assert "Rail" in units
    assert "SCADA" in units
    assert "TfL" in units
    assert "Network Services" in units
    assert "HPC / Hinkley Point C" in units
    assert "Nuclear Power" in units
    assert "Core Central Asset Management" in units
    assert units["Highways"].parent_id == units["Transport"].id
    assert any(
        customer.customer_name == "Transport for London (TfL)" and customer.business_unit_id == units["TfL"].id
        for customer in customers
    )
    assert any(
        customer.customer_name == "National Rail" and customer.business_unit_id == units["Rail"].id
        for customer in customers
    )
    assert any(
        customer.customer_name == "National Rail" and customer.business_unit_id == units["SCADA"].id
        for customer in customers
    )


def test_reference_business_units_rename_legacy_labels(session):
    transport = BusinessUnit(name="Transport", description="Top-level transport business unit.")
    scada = BusinessUnit(name="Scada", description="Transport business unit for SCADA work.")
    hinkley = BusinessUnit(name="HPC / Hinckley Point C", description="HPC / Hinckley Point C business unit.")
    session.add(transport)
    session.commit()
    session.refresh(transport)
    scada.parent_id = transport.id
    session.add(scada)
    session.add(hinkley)
    session.commit()

    seed_business_units(session)
    seed_business_units(session)
    units = {unit.name: unit for unit in session.exec(select(BusinessUnit))}
    national_rail_customers = list(
        session.exec(select(Customer).where(Customer.customer_name == "National Rail"))
    )

    assert "Scada" not in units
    assert "Hinckley" not in " ".join(units)
    assert "SCADA" in units
    assert "HPC / Hinkley Point C" in units
    assert len(national_rail_customers) == 2
