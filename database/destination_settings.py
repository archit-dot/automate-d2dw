from database.db import connect


def ensure_destination_settings(destination_id: int) -> None:
    with connect() as con:
        con.execute(
            """
            INSERT OR IGNORE INTO destination_settings (
                destination_id,
                header_text,
                footer_text,
                header_enabled,
                footer_enabled
            )
            VALUES (?, '', '', 1, 1)
            """,
            (destination_id,),
        )


def get_destination_settings(destination_id: int) -> dict:
    ensure_destination_settings(destination_id)

    with connect() as con:
        row = con.execute(
            """
            SELECT
                destination_id,
                header_text,
                footer_text,
                header_enabled,
                footer_enabled
            FROM destination_settings
            WHERE destination_id = ?
            """,
            (destination_id,),
        ).fetchone()

    if row is None:
        return {
            "destination_id": destination_id,
            "header_text": "",
            "footer_text": "",
            "header_enabled": False,
            "footer_enabled": False,
        }

    return {
        "destination_id": row["destination_id"],
        "header_text": row["header_text"] or "",
        "footer_text": row["footer_text"] or "",
        "header_enabled": bool(row["header_enabled"]),
        "footer_enabled": bool(row["footer_enabled"]),
    }


def set_header(destination_id: int, text: str) -> None:
    ensure_destination_settings(destination_id)

    with connect() as con:
        con.execute(
            """
            UPDATE destination_settings
            SET header_text = ?,
                header_enabled = 1
            WHERE destination_id = ?
            """,
            (text.strip(), destination_id),
        )


def set_footer(destination_id: int, text: str) -> None:
    ensure_destination_settings(destination_id)

    with connect() as con:
        con.execute(
            """
            UPDATE destination_settings
            SET footer_text = ?,
                footer_enabled = 1
            WHERE destination_id = ?
            """,
            (text.strip(), destination_id),
        )


def clear_header(destination_id: int) -> None:
    ensure_destination_settings(destination_id)

    with connect() as con:
        con.execute(
            """
            UPDATE destination_settings
            SET header_text = '',
                header_enabled = 0
            WHERE destination_id = ?
            """,
            (destination_id,),
        )


def clear_footer(destination_id: int) -> None:
    ensure_destination_settings(destination_id)

    with connect() as con:
        con.execute(
            """
            UPDATE destination_settings
            SET footer_text = '',
                footer_enabled = 0
            WHERE destination_id = ?
            """,
            (destination_id,),
        )


def apply_header_footer(destination_id: int, text: str) -> str:
    settings = get_destination_settings(destination_id)

    parts = []

    if settings["header_enabled"] and settings["header_text"]:
        parts.append(settings["header_text"])

    if text:
        parts.append(text)

    if settings["footer_enabled"] and settings["footer_text"]:
        parts.append(settings["footer_text"])

    return "\n\n".join(parts)
