"""Connection lifecycle, transactional writes, and schema initialization."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from collections.abc import Iterator

from .errors import BookFlowError

SCHEMA_VERSION = 1
SCHEMA = (
    """CREATE TABLE books (
        isbn TEXT PRIMARY KEY NOT NULL CHECK (length(trim(isbn)) > 0),
        title TEXT NOT NULL CHECK (length(trim(title)) > 0),
        author TEXT NOT NULL CHECK (length(trim(author)) > 0)
    )""",
    """CREATE TABLE readers (
        reader_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(reader_id)) > 0),
        name TEXT NOT NULL CHECK (length(trim(name)) > 0)
    )""",
    """CREATE TABLE loans (
        loan_id INTEGER PRIMARY KEY,
        isbn TEXT NOT NULL REFERENCES books(isbn),
        reader_id TEXT NOT NULL REFERENCES readers(reader_id),
        borrowed_at TEXT NOT NULL,
        returned_at TEXT
    )""",
    """CREATE UNIQUE INDEX one_active_loan_per_book
       ON loans(isbn) WHERE returned_at IS NULL""",
)


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Include validation and every write in a single atomic transaction."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


@contextmanager
def connect(db_path: str, *, initialize: bool = False) -> Iterator[sqlite3.Connection]:
    # URI rw mode prevents typos in ordinary commands from creating empty files.
    path = Path(db_path).expanduser().resolve()
    if not initialize and not path.is_file():
        raise BookFlowError("数据库不存在，请先使用相同的 --db 路径执行 init。")
    mode = "rwc" if initialize else "rw"
    connection = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True,
                                 isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if not initialize:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise BookFlowError("数据库未初始化或版本不受支持，请检查路径并执行 init。")
        yield connection
    finally:
        connection.close()


def initialize_database(connection: sqlite3.Connection) -> None:
    with transaction(connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version != 0:
            raise BookFlowError(f"不支持的数据库版本：{version}。")
        existing = connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchone()
        if existing:
            raise BookFlowError("该文件包含其他数据库结构，请为 BookFlow 选择独立的数据库文件。")
        for statement in SCHEMA:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
