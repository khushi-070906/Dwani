"""An existing database created by an older version gets new columns added automatically."""
import importlib
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_old_roles_table_gets_projects_column(tmp_path, monkeypatch):
    f = tmp_path / "old.db"
    con = sqlite3.connect(f)
    con.execute("""CREATE TABLE careers_roles (id INTEGER PRIMARY KEY, key VARCHAR(40) UNIQUE, title VARCHAR(120),
        summary TEXT, duties TEXT, looking_for TEXT, keywords TEXT, question TEXT, openings INTEGER, closes_on DATE,
        is_open BOOLEAN, sort INTEGER, created_at DATETIME)""")
    con.execute("INSERT INTO careers_roles (key,title,summary,duties,looking_for,keywords,question,openings,is_open,sort) "
                "VALUES ('old','Old Role','s','d','l','k','q',1,1,0)")
    con.commit(); con.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{f}")
    sys.modules.pop("careers.db", None)
    db = importlib.import_module("careers.db")
    db.init_db([])
    cols = {r[1] for r in sqlite3.connect(f).execute("PRAGMA table_info(careers_roles)")}
    assert "projects" in cols
    with db.session() as s:
        role = s.scalar(db.select(db.Role).where(db.Role.key == "old"))
        assert role.title == "Old Role" and role.projects in ("", None) and role.project_list() == []
    assert db.ensure_columns() == []  # idempotent
    sys.modules.pop("careers.db", None)
