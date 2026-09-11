from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bookflow.cli import main
from bookflow.database import connect, initialize_database, transaction
from bookflow.models import Book
from bookflow.repository import Repository
from bookflow.service import LibraryService


class BookFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "library with spaces 图书.db"
        self.ok("init")

    def run_cli(self, *args, db=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--db", str(db or self.db), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def ok(self, *args):
        code, stdout, stderr = self.run_cli(*args)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        return stdout

    def fails(self, *args, reason):
        code, stdout, stderr = self.run_cli(*args)
        self.assertNotEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertIn(reason, stderr)
        return stderr

    def seed(self):
        self.ok("books", "add", "--isbn", "9787302511857", "--title", "Python 入门", "--author", "张三")
        self.ok("readers", "add", "--id", "R001", "--name", "李四")

    def borrow(self):
        return self.ok("loans", "borrow", "--isbn", "9787302511857", "--reader-id", "R001")

    def return_book(self):
        return self.ok("loans", "return", "--isbn", "9787302511857")

    def rows(self, sql):
        with connect(str(self.db)) as connection:
            return [dict(row) for row in connection.execute(sql)]

    def test_register_book_and_reader(self):
        self.seed()
        output = self.ok("books", "list")
        self.assertIn("Python 入门", output)
        self.assertIn("可借", output)
        self.assertEqual(self.rows("SELECT * FROM readers"), [{"reader_id": "R001", "name": "李四"}])

    def test_duplicate_isbn_is_rejected_without_changing_book(self):
        self.seed()
        self.fails("books", "add", "--isbn", "9787302511857", "--title", "另一本", "--author", "其他", reason="ISBN 已存在")
        rows = self.rows("SELECT * FROM books")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Python 入门")

    def test_duplicate_reader_is_rejected(self):
        self.seed()
        self.fails("readers", "add", "--id", "R001", "--name", "其他", reason="读者编号已存在")
        self.assertEqual(len(self.rows("SELECT * FROM readers")), 1)

    def test_blank_inputs_are_rejected(self):
        cases = [
            ("books", "add", "--isbn", " ", "--title", "书", "--author", "人"),
            ("books", "add", "--isbn", "A", "--title", " ", "--author", "人"),
            ("books", "add", "--isbn", "A", "--title", "书", "--author", " "),
            ("readers", "add", "--id", " ", "--name", "人"),
            ("readers", "add", "--id", "R", "--name", " "),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.fails(*args, reason="不能为空")
        self.assertEqual(self.rows("SELECT * FROM books"), [])
        self.assertEqual(self.rows("SELECT * FROM readers"), [])

    def test_whitespace_is_trimmed(self):
        self.ok("books", "add", "--isbn", " A ", "--title", " 书 ", "--author", " 人 ")
        self.assertEqual(self.rows("SELECT * FROM books"), [{"isbn": "A", "title": "书", "author": "人"}])

    def test_control_characters_are_rejected(self):
        self.fails("readers", "add", "--id", "R", "--name", "甲\n乙", reason="控制字符")

    def test_borrow_and_active_listing(self):
        self.seed()
        self.assertIn("已借出", self.borrow())
        self.assertIn("已借出", self.ok("books", "list"))
        output = self.ok("loans", "list", "--active")
        self.assertIn("R001", output)
        self.assertIn("李四", output)
        row = self.rows("SELECT * FROM loans")[0]
        self.assertIsNone(row["returned_at"])
        self.assertEqual(datetime.fromisoformat(row["borrowed_at"]).utcoffset(), timedelta(0))

    def test_duplicate_borrow_is_rejected(self):
        self.seed()
        self.borrow()
        self.fails("loans", "borrow", "--isbn", "9787302511857", "--reader-id", "R001", reason="图书已借出")
        self.assertEqual(len(self.rows("SELECT * FROM loans")), 1)

    def test_borrow_requires_existing_book_and_reader(self):
        self.seed()
        self.fails("loans", "borrow", "--isbn", "missing", "--reader-id", "R001", reason="图书不存在")
        self.fails("loans", "borrow", "--isbn", "9787302511857", "--reader-id", "missing", reason="读者不存在")
        self.assertEqual(self.rows("SELECT * FROM loans"), [])

    def test_return_preserves_history_and_updates_availability(self):
        self.seed()
        self.borrow()
        before = self.rows("SELECT * FROM loans")[0]
        self.assertIn("已归还", self.return_book())
        self.assertIn("可借", self.ok("books", "list"))
        self.assertIn("暂无有效借阅记录", self.ok("loans", "list", "--active"))
        self.assertIn("已归还", self.ok("loans", "list"))
        after = self.rows("SELECT * FROM loans")[0]
        self.assertEqual(after["loan_id"], before["loan_id"])
        self.assertEqual(after["borrowed_at"], before["borrowed_at"])
        self.assertEqual(datetime.fromisoformat(after["returned_at"]).utcoffset(), timedelta(0))

    def test_duplicate_return_is_rejected_and_history_unchanged(self):
        self.seed()
        self.borrow()
        self.return_book()
        before = self.rows("SELECT * FROM loans")
        self.fails("loans", "return", "--isbn", "9787302511857", reason="当前未借出")
        self.assertEqual(self.rows("SELECT * FROM loans"), before)

    def test_return_requires_existing_borrowed_book(self):
        self.seed()
        self.fails("loans", "return", "--isbn", "missing", reason="图书不存在")
        self.fails("loans", "return", "--isbn", "9787302511857", reason="当前未借出")

    def test_reborrow_creates_distinct_history_record(self):
        self.seed()
        self.borrow()
        self.return_book()
        self.borrow()
        rows = self.rows("SELECT * FROM loans ORDER BY loan_id")
        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(rows[0]["returned_at"])
        self.assertIsNone(rows[1]["returned_at"])
        with connect(str(self.db)) as connection:
            active = LibraryService(Repository(connection)).list_loans(active=True)
            self.assertEqual([loan.loan_id for loan in active], [rows[1]["loan_id"]])

    def test_persistence_across_separate_processes(self):
        self.seed()
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        commands = [
            ["loans", "borrow", "--isbn", "9787302511857", "--reader-id", "R001"],
            ["loans", "list", "--active"],
        ]
        for args in commands:
            result = subprocess.run(
                [sys.executable, "-m", "bookflow", "--db", str(self.db), *args],
                capture_output=True, text=True, encoding="utf-8", env=env,
                cwd=self.temp.name, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("R001", result.stdout)
        self.assertEqual(len(self.rows("SELECT * FROM loans")), 1)

    def test_init_is_idempotent_and_preserves_data(self):
        self.seed()
        self.borrow()
        before = self.rows("SELECT * FROM loans")
        self.ok("init")
        self.assertEqual(self.rows("SELECT * FROM loans"), before)

    def test_empty_lists(self):
        self.assertIn("暂无图书", self.ok("books", "list"))
        self.assertIn("暂无借阅记录", self.ok("loans", "list"))
        self.assertIn("暂无有效借阅记录", self.ok("loans", "list", "--active"))

    def test_missing_database_is_not_created(self):
        path = Path(self.temp.name) / "missing.db"
        code, stdout, stderr = self.run_cli("books", "list", db=path)
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("请先", stderr)
        self.assertFalse(path.exists())

    def test_uninitialized_database_requires_init(self):
        path = Path(self.temp.name) / "empty.db"
        path.touch()
        code, _, stderr = self.run_cli("books", "list", db=path)
        self.assertEqual(code, 1)
        self.assertIn("未初始化", stderr)

    def test_invalid_database_reports_error_without_traceback(self):
        path = Path(self.temp.name) / "invalid.db"
        path.write_text("not a database", encoding="utf-8")
        code, _, stderr = self.run_cli("books", "list", db=path)
        self.assertEqual(code, 1)
        self.assertIn("无法操作数据库", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_schema_constraints_enforce_active_uniqueness_and_foreign_keys(self):
        self.seed()
        self.borrow()
        with connect(str(self.db)) as connection:
            with self.assertRaises(sqlite3.IntegrityError), transaction(connection):
                connection.execute("INSERT INTO loans (isbn, reader_id, borrowed_at) VALUES (?, ?, ?)",
                                   ("9787302511857", "R001", "2026-01-01T00:00:00+00:00"))
            with self.assertRaises(sqlite3.IntegrityError), transaction(connection):
                connection.execute("INSERT INTO loans (isbn, reader_id, borrowed_at) VALUES (?, ?, ?)",
                                   ("missing", "R001", "2026-01-01T00:00:00+00:00"))
        self.assertEqual(len(self.rows("SELECT * FROM loans")), 1)

    def test_multi_write_transaction_rolls_back_all_writes(self):
        with connect(str(self.db)) as connection:
            repository = Repository(connection)
            with self.assertRaises(sqlite3.IntegrityError), transaction(connection):
                repository.insert_book(Book("A", "First", "Author"))
                repository.insert_book(Book("A", "Duplicate", "Author"))
        self.assertEqual(self.rows("SELECT * FROM books"), [])

    def test_failure_after_return_update_rolls_back(self):
        self.seed()
        self.borrow()
        before = self.rows("SELECT * FROM loans")
        with connect(str(self.db)) as connection:
            repository = Repository(connection)
            # finish_loan writes, then reads the result. Fail that read to prove
            # the enclosing service transaction rolls the write back.
            with patch.object(repository, "get_loan", side_effect=sqlite3.OperationalError("injected failure")):
                with self.assertRaises(sqlite3.OperationalError):
                    LibraryService(repository).return_book("9787302511857")
        self.assertEqual(self.rows("SELECT * FROM loans"), before)

    def test_failed_schema_initialization_rolls_back(self):
        path = Path(self.temp.name) / "schema-failure.db"
        with connect(str(path), initialize=True) as connection:
            with patch("bookflow.database.SCHEMA", ("CREATE TABLE partial (id INTEGER)", "INVALID SQL")):
                with self.assertRaises(sqlite3.OperationalError):
                    initialize_database(connection)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_sql_metacharacters_are_data(self):
        self.ok("books", "add", "--isbn", "X'; DROP TABLE books; --", "--title", "O'Reilly", "--author", "A")
        self.assertEqual(len(self.rows("SELECT * FROM books")), 1)

    def test_missing_required_argument_exits_two(self):
        with redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as raised:
                main(["--db", str(self.db), "books", "add"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--isbn", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
