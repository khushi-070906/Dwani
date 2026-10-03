"""
db.py -- storage for the careers portal.

DATABASE_URL = the portal's OWN Supabase project ("Session pooler" string).
Without it, a local SQLite file is used (development / tests).

Render's free disk is wiped on every deploy, so everything that must
survive (generated PDFs, uploaded resumes, the signature image) is stored
in the database, not on disk.
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
from contextlib import contextmanager

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text, create_engine,
                        func, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///" + os.path.join(os.path.dirname(os.path.abspath(__file__)), "careers.db")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


URL = _db_url()
engine = create_engine(
    URL,
    pool_pre_ping=True,  # Supabase's pooler drops idle connections
    **({"connect_args": {"check_same_thread": False}} if URL.startswith("sqlite") else {"pool_size": 3, "max_overflow": 2}),
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Role(Base):
    __tablename__ = "careers_roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text, default="")
    duties: Mapped[str] = mapped_column(Text, default="")        # one per line
    looking_for: Mapped[str] = mapped_column(Text, default="")   # one per line
    keywords: Mapped[str] = mapped_column(Text, default="")      # comma separated
    question: Mapped[str] = mapped_column(Text, default="")      # optional role-specific question
    projects: Mapped[str] = mapped_column(Text, default="")      # "Title :: what :: deliverable", one per line
    openings: Mapped[int] = mapped_column(Integer, default=0)    # 0 = not shown
    closes_on: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    def duty_list(self) -> list[str]:
        return [x.strip() for x in self.duties.splitlines() if x.strip()]

    def looking_list(self) -> list[str]:
        return [x.strip() for x in self.looking_for.splitlines() if x.strip()]

    def project_list(self) -> list[dict]:
        out = []
        for line in self.projects.splitlines():
            parts = [p.strip() for p in line.split("::")]
            if parts and parts[0]:
                out.append({"title": parts[0], "what": parts[1] if len(parts) > 1 else "",
                            "deliverable": parts[2] if len(parts) > 2 else ""})
        return out

    def keyword_list(self) -> list[str]:
        return [x.strip().lower() for x in self.keywords.split(",") if x.strip()]

    @property
    def accepting(self) -> bool:
        return self.is_open and (self.closes_on is None or self.closes_on >= dt.date.today())


class Candidate(Base):
    __tablename__ = "careers_candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(40), unique=True, default=lambda: secrets.token_urlsafe(18))
    role_id: Mapped[int] = mapped_column(ForeignKey("careers_roles.id"))
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), index=True)
    phone: Mapped[str] = mapped_column(String(40), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    college: Mapped[str] = mapped_column(String(200), default="")
    degree: Mapped[str] = mapped_column(String(120), default="")
    grad_year: Mapped[str] = mapped_column(String(10), default="")
    cgpa: Mapped[str] = mapped_column(String(20), default="")
    skills: Mapped[str] = mapped_column(Text, default="")
    links: Mapped[str] = mapped_column(Text, default="")
    resume: Mapped[str] = mapped_column(Text, default="")              # link
    resume_file: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    resume_name: Mapped[str] = mapped_column(String(200), default="")
    hours: Mapped[str] = mapped_column(String(20), default="")
    available_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    why: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")              # role-specific question
    source: Mapped[str] = mapped_column(String(80), default="")       # how they heard about us
    status: Mapped[str] = mapped_column(String(20), default="applied", index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    score_notes: Mapped[str] = mapped_column(Text, default="")
    interview_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)  # IST, naive
    interview_where: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now, onupdate=now)
    role: Mapped[Role] = relationship(lazy="joined")
    events: Mapped[list["Event"]] = relationship(order_by="Event.id", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(order_by="Document.issued", cascade="all, delete-orphan")
    scorecards: Mapped[list["Scorecard"]] = relationship(order_by="Scorecard.id", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "careers_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("careers_candidates.id", ondelete="CASCADE"), index=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    kind: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str] = mapped_column(Text, default="")
    by: Mapped[str] = mapped_column(String(120), default="")


class Scorecard(Base):
    __tablename__ = "careers_scorecards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("careers_candidates.id", ondelete="CASCADE"), index=True)
    reviewer: Mapped[str] = mapped_column(String(120))
    skills: Mapped[int] = mapped_column(Integer)
    communication: Mapped[int] = mapped_column(Integer)
    ownership: Mapped[int] = mapped_column(Integer)
    recommendation: Mapped[str] = mapped_column(String(20))  # strong_yes | yes | no | strong_no
    notes: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)

    @property
    def average(self) -> float:
        return round((self.skills + self.communication + self.ownership) / 3, 1)


class Document(Base):
    __tablename__ = "careers_documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # DL-OFF-2026-0001
    candidate_id: Mapped[int] = mapped_column(ForeignKey("careers_candidates.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # offer | certificate | lor
    issued: Mapped[dt.date] = mapped_column(Date)
    role_title: Mapped[str] = mapped_column(String(120))
    start: Mapped[dt.date] = mapped_column(Date)
    end: Mapped[dt.date] = mapped_column(Date)
    holder_name: Mapped[str] = mapped_column(String(120))
    holder_college: Mapped[str] = mapped_column(String(200), default="")
    sha256: Mapped[str] = mapped_column(String(64))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    credential: Mapped[str] = mapped_column(Text, default="")  # signed W3C VC (compact JWS), also embedded in the PDF
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    meta: Mapped[str] = mapped_column(Text, default="{}")

    @property
    def kind_label(self) -> str:
        return {"offer": "Internship Offer Letter", "certificate": "Internship Completion Certificate",
                "lor": "Letter of Recommendation"}.get(self.kind, self.kind)


class User(Base):
    __tablename__ = "careers_users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="reviewer")  # owner | reviewer
    pw_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)
    last_login: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class PublicKey(Base):
    """Every signing key ever used stays published (in /.well-known/did.json),
    so documents signed before a key rotation keep verifying."""
    __tablename__ = "careers_keys"
    kid: Mapped[str] = mapped_column(String(40), primary_key=True)
    public_hex: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now)


class Setting(Base):
    """Key/value overrides for config.toml, editable in Admin -> Settings."""
    __tablename__ = "careers_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Asset(Base):
    __tablename__ = "careers_assets"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)  # e.g. "signature"
    data: Mapped[bytes] = mapped_column(LargeBinary)
    mime: Mapped[str] = mapped_column(String(40), default="image/png")


def _role_from_seed(r: dict, sort: int) -> Role:
    return Role(key=r["key"], title=r["title"], summary=r.get("summary", ""),
                duties="\n".join(r.get("duties", [])), looking_for="\n".join(r.get("looking_for", [])),
                keywords=", ".join(r.get("keywords", [])), question=r.get("question", ""),
                projects="\n".join(r.get("projects", [])), openings=int(r.get("openings", 0)),
                is_open=bool(r.get("open", True)), sort=sort)


def ensure_columns() -> list[str]:
    """Tiny forward-only migration: add columns that a newer version of the
    models has but an existing table lacks (create_all never alters tables).
    Existing rows get the column's plain default. Alembic is the proper next step."""
    from sqlalchemy import inspect, text

    insp, added = inspect(engine), []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(dialect=engine.dialect)}"))
                default = col.default.arg if col.default is not None and getattr(col.default, "is_scalar", False) else None
                if default is not None:
                    conn.execute(text(f"UPDATE {table.name} SET {col.name} = :v"), {"v": default})
                added.append(f"{table.name}.{col.name}")
    return added


def missing_roles(seed_roles: list[dict]) -> list[dict]:
    with SessionLocal() as s:
        have = set(s.scalars(select(Role.key)))
    return [r for r in seed_roles if r["key"] not in have]


def add_missing_roles(seed_roles: list[dict]) -> list[str]:
    """Adds config.toml roles whose key isn't in the database yet. Never touches existing roles."""
    todo = missing_roles(seed_roles)
    with SessionLocal() as s:
        top = s.scalar(select(func.max(Role.sort))) or 0
        for i, r in enumerate(todo, 1):
            s.add(_role_from_seed(r, top + i))
        s.commit()
    return [r["title"] for r in todo]


def plan_changes(seed_roles: list[dict]) -> list[tuple[str, bool]]:
    """Roles whose open/closed state differs from config.toml: [(title, should_be_open)]."""
    want = {r["key"]: bool(r.get("open", True)) for r in seed_roles}
    with SessionLocal() as s:
        return [(r.title, want[r.key]) for r in s.scalars(select(Role).order_by(Role.sort, Role.id))
                if r.key in want and r.is_open != want[r.key]]


def apply_plan(seed_roles: list[dict]) -> int:
    """Sets open/closed from config.toml for roles that exist in it. Nothing else changes."""
    want = {r["key"]: bool(r.get("open", True)) for r in seed_roles}
    n = 0
    with SessionLocal() as s:
        for r in s.scalars(select(Role)):
            if r.key in want and r.is_open != want[r.key]:
                r.is_open, n = want[r.key], n + 1
        s.commit()
    return n


def init_db(seed_roles: list[dict]) -> None:
    Base.metadata.create_all(engine)
    ensure_columns()
    with SessionLocal() as s:
        if not s.scalar(select(func.count()).select_from(Role)):
            for i, r in enumerate(seed_roles):
                s.add(_role_from_seed(r, i))
            s.commit()


@contextmanager
def session() -> Session:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
