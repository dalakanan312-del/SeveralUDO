from __future__ import annotations

from datetime import datetime, timezone

MAX_PHOTO_BYTES = 8 * 1024 * 1024


def ensure_schema(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS relationship_photos(
            relationship_id TEXT PRIMARY KEY,
            image_data BLOB NOT NULL,
            mime_type TEXT,
            filename TEXT,
            updated_at TEXT,
            FOREIGN KEY(relationship_id) REFERENCES relationships(relationship_id) ON DELETE CASCADE
        )
    """)
    connection.commit()


def get_photo(connection, relationship_id):
    return connection.execute(
        "SELECT image_data,mime_type,filename,updated_at FROM relationship_photos WHERE relationship_id=?",
        (relationship_id,),
    ).fetchone()


def save_photo(connection, relationship_id, uploaded_file):
    if uploaded_file is None:
        return
    data = uploaded_file.getvalue()
    mime = getattr(uploaded_file, "type", None) or "application/octet-stream"
    if not mime.startswith("image/"):
        raise ValueError("Marriage portraits must be image files.")
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("Marriage portrait is larger than 8 MB.")
    connection.execute(
        """INSERT INTO relationship_photos(relationship_id,image_data,mime_type,filename,updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(relationship_id) DO UPDATE SET
             image_data=excluded.image_data,
             mime_type=excluded.mime_type,
             filename=excluded.filename,
             updated_at=excluded.updated_at""",
        (
            relationship_id,
            data,
            mime,
            getattr(uploaded_file, "name", None),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def delete_photo(connection, relationship_id):
    connection.execute(
        "DELETE FROM relationship_photos WHERE relationship_id=?", (relationship_id,)
    )
