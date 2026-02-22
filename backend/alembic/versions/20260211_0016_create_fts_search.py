from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260211_0016"
down_revision = "20260211_0015"
branch_labels = None
depends_on = None


def _try_create_fts5(conn: sa.Connection, *, table_name: str, columns_sql: str) -> bool:
    candidates = [
        f"CREATE VIRTUAL TABLE {table_name} USING fts5({columns_sql}, tokenize='trigram');",
        f"CREATE VIRTUAL TABLE {table_name} USING fts5({columns_sql});",
    ]
    for sql in candidates:
        try:
            conn.exec_driver_sql(sql)
            return True
        except Exception:
            continue
    return False


def upgrade() -> None:
    conn = op.get_bind()

    tags_ok = _try_create_fts5(conn, table_name="tags_fts", columns_sql="name, translated_name")
    if tags_ok:
        conn.exec_driver_sql(
            "INSERT INTO tags_fts(rowid, name, translated_name) "
            "SELECT id, name, COALESCE(translated_name,'') FROM tags;"
        )
        conn.exec_driver_sql(
            """
CREATE TRIGGER tags_ai_fts AFTER INSERT ON tags BEGIN
  INSERT INTO tags_fts(rowid, name, translated_name)
  VALUES (new.id, new.name, COALESCE(new.translated_name,''));
END;
""".strip()
        )
        conn.exec_driver_sql(
            """
CREATE TRIGGER tags_ad_fts AFTER DELETE ON tags BEGIN
  DELETE FROM tags_fts WHERE rowid = old.id;
END;
""".strip()
        )
        conn.exec_driver_sql(
            """
CREATE TRIGGER tags_au_fts AFTER UPDATE OF name, translated_name ON tags BEGIN
  DELETE FROM tags_fts WHERE rowid = old.id;
  INSERT INTO tags_fts(rowid, name, translated_name)
  VALUES (new.id, new.name, COALESCE(new.translated_name,''));
END;
""".strip()
        )

    authors_ok = _try_create_fts5(conn, table_name="authors_fts", columns_sql="user_name")
    if authors_ok:
        conn.exec_driver_sql(
            "INSERT INTO authors_fts(rowid, user_name) "
            "SELECT user_id, MAX(user_name) "
            "FROM images "
            "WHERE user_id IS NOT NULL AND user_name IS NOT NULL AND status=1 "
            "GROUP BY user_id;"
        )
        conn.exec_driver_sql(
            """
CREATE TRIGGER images_ai_authors_fts AFTER INSERT ON images
WHEN new.user_id IS NOT NULL
BEGIN
  DELETE FROM authors_fts WHERE rowid = new.user_id;
  INSERT INTO authors_fts(rowid, user_name)
  SELECT new.user_id, MAX(user_name)
  FROM images
  WHERE user_id = new.user_id AND status = 1 AND user_name IS NOT NULL
  HAVING MAX(user_name) IS NOT NULL;
END;
""".strip()
        )
        conn.exec_driver_sql(
            """
CREATE TRIGGER images_au_authors_fts AFTER UPDATE OF user_id, user_name, status ON images
BEGIN
  DELETE FROM authors_fts WHERE rowid = old.user_id;
  INSERT INTO authors_fts(rowid, user_name)
  SELECT old.user_id, MAX(user_name)
  FROM images
  WHERE old.user_id IS NOT NULL AND user_id = old.user_id AND status = 1 AND user_name IS NOT NULL
  HAVING MAX(user_name) IS NOT NULL;

  DELETE FROM authors_fts
  WHERE rowid = new.user_id AND new.user_id IS NOT NULL AND (old.user_id IS NULL OR new.user_id != old.user_id);
  INSERT INTO authors_fts(rowid, user_name)
  SELECT new.user_id, MAX(user_name)
  FROM images
  WHERE new.user_id IS NOT NULL
    AND (old.user_id IS NULL OR new.user_id != old.user_id)
    AND user_id = new.user_id AND status = 1 AND user_name IS NOT NULL
  HAVING MAX(user_name) IS NOT NULL;
END;
""".strip()
        )
        conn.exec_driver_sql(
            """
CREATE TRIGGER images_ad_authors_fts AFTER DELETE ON images
WHEN old.user_id IS NOT NULL
BEGIN
  DELETE FROM authors_fts WHERE rowid = old.user_id;
  INSERT INTO authors_fts(rowid, user_name)
  SELECT old.user_id, MAX(user_name)
  FROM images
  WHERE user_id = old.user_id AND status = 1 AND user_name IS NOT NULL
  HAVING MAX(user_name) IS NOT NULL;
END;
""".strip()
        )


def downgrade() -> None:
    conn = op.get_bind()

    conn.exec_driver_sql("DROP TRIGGER IF EXISTS images_ad_authors_fts;")
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS images_au_authors_fts;")
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS images_ai_authors_fts;")
    conn.exec_driver_sql("DROP TABLE IF EXISTS authors_fts;")

    conn.exec_driver_sql("DROP TRIGGER IF EXISTS tags_au_fts;")
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS tags_ad_fts;")
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS tags_ai_fts;")
    conn.exec_driver_sql("DROP TABLE IF EXISTS tags_fts;")

