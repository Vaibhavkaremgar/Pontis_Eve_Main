from sqlalchemy import text


ATS_AGENCY_DEFAULTS = {
    "greenhouse": {
        "name": "Greenhouse",
        "slug": "greenhouse",
    },
    "lever": {
        "name": "Lever",
        "slug": "lever",
    },
    "ashby": {
        "name": "Ashby",
        "slug": "ashby",
    },
}


async def get_or_create_ats_agency(db, ats_type: str):
    """
    Find the default system agency for an ATS.
    Create it if it does not exist.

    Returns:
        agency UUID
    """

    ats_type = ats_type.strip().lower()

    if ats_type not in ATS_AGENCY_DEFAULTS:
        raise ValueError(
            f"Unsupported ATS type: {ats_type}"
        )

    defaults = ATS_AGENCY_DEFAULTS[ats_type]

    # 1. Try to find existing ATS agency
    result = await db.execute(
        text("""
            SELECT id
            FROM agencies
            WHERE ats_type = :ats_type
            ORDER BY created_at ASC
            LIMIT 1
        """),
        {
            "ats_type": ats_type,
        },
    )

    row = result.first()

    if row:
        return row[0]

    # 2. Create the default ATS agency
    result = await db.execute(
        text("""
            INSERT INTO agencies (
                name,
                slug,
                is_active,
                linkedin_connected,
                linkedin_connection_status,
                ats_type
            )
            VALUES (
                :name,
                :slug,
                TRUE,
                FALSE,
                'pending',
                :ats_type
            )
            RETURNING id
        """),
        {
            "name": defaults["name"],
            "slug": defaults["slug"],
            "ats_type": ats_type,
        },
    )

    agency_id = result.scalar_one()

    await db.commit()

    return agency_id