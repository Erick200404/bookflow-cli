from contextlib import closing, redirect_stderr, redirect_stdout
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bookflow.cli import main
from bookflow.database import SCHEMA, connect
from bookflow.errors import BookFlowError
from bookflow.repository import Repository
from bookflow.service import LibraryService
from bookflow.serialization import BOOK_FIELDS, LOAN_FIELDS

NOW = "2026-09-11T10:00:00.000000+00:00"

# Independent v1 fixture: never derive the old schema from the current schema.
V1_SCHEMA = """
CREATE TABLE books (isbn TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL, author TEXT NOT NULL);
CREATE TABLE readers (reader_id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL);
CREATE TABLE loans (
    loan_id INTEGER PRIMARY KEY,
    isbn TEXT NOT NULL REFERENCES books(isbn),
    reader_id TEXT NOT NULL REFERENCES readers(reader_id),
    borrowed_at TEXT NOT NULL, returned_at TEXT
);
CREATE UNIQUE INDEX one_active_loan_per_book ON loans(isbn) WHERE returned_at IS NULL;
PRAGMA user_version = 1;
"""


class ExtensionCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "图书 room.db"
        self.ok("init")

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = main(["--db", str(self.db), *map(str, args)])
            except SystemExit as error:
                code = error.code
        return code, stdout.getvalue(), stderr.getvalue()

    def ok(self, *args):
        code, stdout, stderr = self.cli(*args)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stderr, "")
        return stdout

    def fails(self, *args, reason, code=1):
        result, stdout, stderr = self.cli(*args)
        self.assertEqual(result, code, stderr)
        self.assertEqual(stdout, "")
        self.assertIn(reason, stderr)
        return stderr

    def seed(self, *isbns):
        self.ok("readers", "add", "--id", "R1", "--name", "读者甲")
        for isbn in isbns or ("A",):
            self.ok("books", "add", "--isbn", isbn, "--title", f"中文,{isbn}的\"书\"", "--author", "作者甲")

    def borrow(self, isbn="A", *extra):
        return self.ok("loans", "borrow", "--isbn", isbn, "--reader-id", "R1", *extra)

    def query(self, sql, parameters=()):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, parameters)]

    def csv_file(self, text, *, name="导入.csv", encoding="utf-8"):
        path = self.root / name
        path.write_text(text, encoding=encoding, newline="")
        return path

    def read_csv(self, path):
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            return reader.fieldnames, list(reader)

    def filtered_seed(self):
        self.seed("PAST", "EQUAL", "FUTURE", "RETURNED")
        with patch("bookflow.service.now_iso", return_value=NOW):
            for isbn in ("PAST", "EQUAL", "FUTURE", "RETURNED"):
                self.borrow(isbn)
        with closing(sqlite3.connect(self.db)) as connection, connection:
            for isbn, due in (
                ("PAST", "2026-09-11T09:59:59.999999+00:00"),
                ("EQUAL", "2026-09-11T18:00:00+08:00"),
                ("FUTURE", "2026-09-11T10:00:00.000001+00:00"),
                ("RETURNED", "2026-09-01T00:00:00+00:00"),
            ):
                connection.execute("UPDATE loans SET due_at = ? WHERE isbn = ?", (due, isbn))
            connection.execute("UPDATE loans SET returned_at = ? WHERE isbn = 'RETURNED'", (NOW,))


class MigrationTests(ExtensionCase):
    def v1(self):
        self.db = self.root / "legacy.db"
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.executescript(V1_SCHEMA)
            connection.execute("INSERT INTO books VALUES ('A', '旧书', '作者')")
            connection.execute("INSERT INTO readers VALUES ('R1', '旧读者')")
            connection.executemany("INSERT INTO loans VALUES (?, 'A', 'R1', ?, ?)", [
                (4, "2025-12-20T12:34:56+08:00", "2025-12-25T12:00:00+08:00"),
                (9, "2026-01-01T04:00:00.123456+00:00", None),
            ])

    def snapshot(self):
        return {table: self.query(f"SELECT * FROM {table}") for table in ("books", "readers", "loans")}

    def assert_v1_unchanged(self, before):
        self.assertEqual(self.query("PRAGMA user_version"), [{"user_version": 1}])
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("due_at", [row["name"] for row in self.query("PRAGMA table_info(loans)")])
        self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE name = 'loans_v2'"), [])
        self.assertEqual(len(self.query("SELECT name FROM sqlite_master WHERE name = 'one_active_loan_per_book'")), 1)

    def test_new_database_is_v2_and_due_is_required(self):
        self.assertEqual(self.query("PRAGMA user_version"), [{"user_version": 2}])
        columns = {row["name"]: row for row in self.query("PRAGMA table_info(loans)")}
        self.assertEqual(columns["due_at"]["notnull"], 1)

    def test_v1_migration_preserves_all_data_and_normalizes_due_to_utc(self):
        self.v1()
        before = self.snapshot()
        self.ok("init")
        self.assertEqual(self.query("PRAGMA user_version"), [{"user_version": 2}])
        self.assertEqual(self.query("SELECT * FROM books"), before["books"])
        self.assertEqual(self.query("SELECT * FROM readers"), before["readers"])
        for old, new in zip(before["loans"], self.query("SELECT * FROM loans ORDER BY loan_id")):
            due = new.pop("due_at")
            self.assertEqual(new, old)
            expected = datetime.fromisoformat(old["borrowed_at"]).astimezone(timezone.utc) + timedelta(days=14)
            self.assertEqual(datetime.fromisoformat(due), expected)
            self.assertTrue(due.endswith("+00:00"))
        self.assertEqual(self.query("PRAGMA foreign_key_check"), [])
        self.ok("loans", "return", "--isbn", "A")
        self.borrow()
        self.assertEqual(self.query("SELECT max(loan_id) AS id FROM loans")[0]["id"], 10)

    def test_v1_commands_require_explicit_init(self):
        self.v1()
        before = self.snapshot()
        self.fails("loans", "list", reason="init")
        self.assert_v1_unchanged(before)

    def test_migration_invalid_legacy_time_rolls_back_partial_copy(self):
        self.v1()
        self.query("UPDATE loans SET borrowed_at = 'invalid' WHERE loan_id = 9")
        before = self.snapshot()
        self.fails("init", reason="借阅 #9")
        self.assert_v1_unchanged(before)

    def test_migration_naive_time_is_rejected(self):
        self.v1()
        self.query("UPDATE loans SET borrowed_at = '2026-01-01T00:00:00' WHERE loan_id = 9")
        before = self.snapshot()
        self.fails("init", reason="时区")
        self.assert_v1_unchanged(before)

    def test_migration_late_failure_restores_dropped_table_and_index(self):
        self.v1()
        before = self.snapshot()
        with patch("bookflow.database.SCHEMA", (*SCHEMA[:3], "INVALID INDEX SQL")):
            self.fails("init", reason="错误")
        self.assert_v1_unchanged(before)
        self.ok("init")

    def test_repeated_v2_init_preserves_due_dates(self):
        self.v1()
        self.ok("init")
        before = self.snapshot()
        self.ok("init")
        self.assertEqual(self.snapshot(), before)

    def test_migrated_schema_retains_active_uniqueness_and_foreign_keys(self):
        self.v1()
        self.ok("init")
        with connect(str(self.db)) as connection:
            for isbn, reader_id in (("A", "R1"), ("missing", "R1"), ("A", "missing")):
                with self.subTest(isbn=isbn, reader_id=reader_id), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO loans (isbn, reader_id, borrowed_at, due_at) VALUES (?, ?, ?, ?)",
                        (isbn, reader_id, NOW, NOW),
                    )
        self.assertEqual(len(self.query("SELECT * FROM loans")), 2)


class LoanPeriodTests(ExtensionCase):
    def test_default_and_boundary_loan_days(self):
        self.seed("DEFAULT", "MIN", "MAX")
        with patch("bookflow.service.now_iso", return_value=NOW):
            self.borrow("DEFAULT")
            self.borrow("MIN", "--loan-days", "1")
            self.borrow("MAX", "--loan-days", "365")
        for row, days in zip(self.query("SELECT * FROM loans ORDER BY loan_id"), (14, 1, 365)):
            self.assertEqual(datetime.fromisoformat(row["due_at"]) - datetime.fromisoformat(row["borrowed_at"]), timedelta(days=days))
            self.assertTrue(row["due_at"].endswith("+00:00"))

    def test_out_of_range_days_are_business_errors_without_writes(self):
        self.seed()
        for value in ("0", "-1", "366", "99999999999999999999"):
            with self.subTest(value=value):
                self.fails("loans", "borrow", "--isbn", "A", "--reader-id", "R1", "--loan-days", value, reason="1 到 365")
        self.assertEqual(self.query("SELECT * FROM loans"), [])

    def test_noninteger_days_are_argparse_errors_without_writes(self):
        self.seed()
        for value in ("abc", "1.5", ""):
            with self.subTest(value=value):
                self.fails("loans", "borrow", "--isbn", "A", "--reader-id", "R1", "--loan-days", value, reason="--loan-days", code=2)
        self.assertEqual(self.query("SELECT * FROM loans"), [])

    def test_service_rejects_noninteger_days(self):
        self.seed()
        with connect(str(self.db)) as connection:
            service = LibraryService(Repository(connection))
            for value in (True, "14", 1.5, None):
                with self.subTest(value=value), self.assertRaises(BookFlowError):
                    service.borrow("A", "R1", value)
        self.assertEqual(self.query("SELECT * FROM loans"), [])

    def test_overdue_is_strict_and_excludes_returned_records(self):
        self.filtered_seed()
        with patch("bookflow.service.now_iso", return_value=NOW):
            rows = json.loads(self.ok("loans", "list", "--overdue", "--format", "json"))
        self.assertEqual([row["isbn"] for row in rows], ["PAST"])

    def test_all_filter_combinations_and_order(self):
        self.filtered_seed()
        with patch("bookflow.service.now_iso", return_value=NOW):
            for flags, expected in (
                ((), ["PAST", "EQUAL", "FUTURE", "RETURNED"]),
                (("--active",), ["PAST", "EQUAL", "FUTURE"]),
                (("--overdue",), ["PAST"]),
                (("--active", "--overdue"), ["PAST"]),
            ):
                with self.subTest(flags=flags):
                    rows = json.loads(self.ok("loans", "list", *flags, "--format", "json"))
                    self.assertEqual([row["isbn"] for row in rows], expected)

    def test_return_does_not_change_due_date(self):
        self.seed()
        self.borrow("A", "--loan-days", "1")
        before = self.query("SELECT * FROM loans")[0]
        self.ok("loans", "return", "--isbn", "A")
        self.assertEqual(self.query("SELECT * FROM loans")[0]["due_at"], before["due_at"])


class ImportTests(ExtensionCase):
    def test_utf8_and_bom_unicode_and_csv_quoting(self):
        for index, encoding in enumerate(("utf-8", "utf-8-sig")):
            path = self.root / f"{index}.csv"
            with path.open("w", encoding=encoding, newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["isbn", "title", "author"])
                writer.writerow([f" {index} ", ' 中文,带"引号" ', " 作者 "])
            self.assertEqual(self.ok("books", "import", "--file", path), "已导入图书：1 本。\n")
        rows = self.query("SELECT * FROM books ORDER BY isbn")
        self.assertEqual([row["title"] for row in rows], ['中文,带"引号"'] * 2)
        self.assertEqual([row["isbn"] for row in rows], ["0", "1"])

    def test_header_only_succeeds_with_zero(self):
        path = self.csv_file("isbn,title,author\n")
        self.assertEqual(self.ok("books", "import", "--file", path), "已导入图书：0 本。\n")
        self.assertEqual(self.query("SELECT * FROM books"), [])

    def test_wrong_headers_are_rejected(self):
        for text in ("", "title,isbn,author\n", "isbn,title\n", "isbn,title,author,extra\n", " isbn,title,author\n"):
            with self.subTest(text=text):
                path = self.csv_file(text)
                self.fails("books", "import", "--file", path, reason="第 1 行")

    def test_missing_extra_and_blank_row_columns_are_rejected(self):
        for bad in ("B,书\n", "B,书,人,extra\n", "\n"):
            with self.subTest(bad=bad):
                path = self.csv_file("isbn,title,author\nA,首行,作者\n" + bad)
                self.fails("books", "import", "--file", path, reason="第 3 行")
                self.assertEqual(self.query("SELECT * FROM books"), [])

    def test_empty_fields_roll_back_preceding_rows(self):
        for bad in (",书,人", "B, ,人", "B,书,"):
            with self.subTest(bad=bad):
                path = self.csv_file("isbn,title,author\nA,首行,作者\n" + bad)
                self.fails("books", "import", "--file", path, reason="第 3 行")
                self.assertEqual(self.query("SELECT * FROM books"), [])

    def test_controls_including_field_edges_are_rejected(self):
        for value in ("甲\t乙", "\t甲", "甲\x7f", "甲\x85", "甲\n乙"):
            with self.subTest(value=value):
                path = self.root / "controls.csv"
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerows([("isbn", "title", "author"), ("A", "第一本", "人"), ("B", value, "人")])
                error = self.fails("books", "import", "--file", path, reason="第 3 行")
                self.assertIn("控制字符", error)
                self.assertEqual(self.query("SELECT * FROM books"), [])

    def test_duplicate_in_csv_after_cleaning_rolls_back_batch(self):
        path = self.csv_file("isbn,title,author\nA,首行,作者\n A ,另一行,作者\n")
        self.fails("books", "import", "--file", path, reason="第 3 行")
        self.assertEqual(self.query("SELECT * FROM books"), [])

    def test_existing_isbn_rolls_back_batch_and_preserves_original(self):
        self.seed("B")
        before = self.query("SELECT * FROM books")
        path = self.csv_file("isbn,title,author\nA,新书,作者\nB,重复,作者\n")
        self.fails("books", "import", "--file", path, reason="第 3 行")
        self.assertEqual(self.query("SELECT * FROM books"), before)

    def test_invalid_utf8_is_a_file_error(self):
        path = self.root / "bad.csv"
        path.write_bytes(b"isbn,title,author\nA,title,author\n\xff")
        self.fails("books", "import", "--file", path, reason="编码无效")
        self.assertEqual(self.query("SELECT * FROM books"), [])

    def test_missing_directory_and_unreadable_input(self):
        for path in (self.root / "missing.csv", self.root):
            with self.subTest(path=path):
                self.fails("books", "import", "--file", path, reason="无法读取 CSV 文件")
        with patch("bookflow.csv_io.Path.open", side_effect=PermissionError("permission denied")):
            self.fails("books", "import", "--file", self.root / "denied.csv", reason="permission denied")

    def test_malformed_quoted_csv_is_rejected_without_writes(self):
        path = self.csv_file('isbn,title,author\nA,首行,作者\nB,"unclosed,author\n')
        self.fails("books", "import", "--file", path, reason="第 3 行")
        self.assertEqual(self.query("SELECT * FROM books"), [])


class ExportTests(ExtensionCase):
    def test_books_export_bom_fixed_fields_and_round_trip(self):
        self.seed("B", "A")
        path = self.root / "图书.csv"
        self.ok("books", "export", "--output", path)
        fields, rows = self.read_csv(path)
        self.assertEqual(fields, list(BOOK_FIELDS))
        self.assertEqual([row["isbn"] for row in rows], ["A", "B"])
        self.assertEqual(rows[0]["title"], '中文,A的"书"')
        self.assertEqual(rows[0]["status"], "可借")

    def test_loans_export_fields_and_null_return_as_empty_csv_cell(self):
        self.seed()
        self.borrow()
        path = self.root / "借阅.csv"
        self.ok("loans", "export", "--output", path)
        fields, rows = self.read_csv(path)
        self.assertEqual(fields, list(LOAN_FIELDS))
        self.assertEqual(rows[0]["returned_at"], "")
        self.assertEqual(rows[0]["title"], '中文,A的"书"')
        self.assertEqual(rows[0]["reader_name"], "读者甲")
        self.assertTrue(rows[0]["due_at"].endswith("+00:00"))

    def test_export_filter_combinations_match_json(self):
        self.filtered_seed()
        with patch("bookflow.service.now_iso", return_value=NOW):
            for index, flags in enumerate(((), ("--active",), ("--overdue",), ("--active", "--overdue"))):
                with self.subTest(flags=flags):
                    path = self.root / f"loans-{index}.csv"
                    self.ok("loans", "export", "--output", path, *flags)
                    _, rows = self.read_csv(path)
                    expected = json.loads(self.ok("loans", "list", "--format", "json", *flags))
                    self.assertEqual([row["isbn"] for row in rows], [row["isbn"] for row in expected])

    def test_existing_file_is_unchanged_without_force(self):
        for resource in ("books", "loans"):
            with self.subTest(resource=resource):
                path = self.root / f"{resource}.csv"
                path.write_bytes(b"original bytes")
                self.fails(resource, "export", "--output", path, reason="--force")
                self.assertEqual(path.read_bytes(), b"original bytes")
                self.assertEqual(list(self.root.glob(".bookflow-*.tmp")), [])

    def test_force_atomically_replaces_existing_file(self):
        self.seed()
        path = self.root / "overwrite.csv"
        path.write_bytes(b"old")
        self.ok("books", "export", "--output", path, "--force")
        _, rows = self.read_csv(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(self.root.glob(".bookflow-*.tmp")), [])

    def test_empty_exports_contain_only_header(self):
        for resource, expected in (("books", BOOK_FIELDS), ("loans", LOAN_FIELDS)):
            path = self.root / f"{resource}.csv"
            self.ok(resource, "export", "--output", path)
            fields, rows = self.read_csv(path)
            self.assertEqual(fields, list(expected))
            self.assertEqual(rows, [])

    def test_missing_parent_and_directory_target(self):
        self.fails("books", "export", "--output", self.root / "missing" / "out.csv", reason="无法导出 CSV 文件")
        for flags in ((), ("--force",)):
            self.fails("books", "export", "--output", self.root, *flags, reason="目标是目录")
        self.assertFalse((self.root / "missing").exists())
        self.assertEqual(list(self.root.glob(".bookflow-*.tmp")), [])

    def test_permission_failure_preserves_original(self):
        path = self.root / "denied.csv"
        path.write_bytes(b"original")
        with patch("bookflow.csv_io.tempfile.NamedTemporaryFile", side_effect=PermissionError("permission denied")):
            self.fails("books", "export", "--output", path, "--force", reason="permission denied")
        self.assertEqual(path.read_bytes(), b"original")
        self.assertEqual(list(self.root.glob(".bookflow-*.tmp")), [])

    def test_write_and_replace_failures_preserve_original_and_clean_temp(self):
        self.seed()
        for target in ("csv.DictWriter.writerow", "os.fsync", "os.replace"):
            with self.subTest(target=target):
                path = self.root / "failure.csv"
                path.write_bytes(b"original")
                with patch(f"bookflow.csv_io.{target}", side_effect=OSError("injected failure")):
                    self.fails("books", "export", "--output", path, "--force", reason="无法导出 CSV 文件")
                self.assertEqual(path.read_bytes(), b"original")
                self.assertEqual(list(self.root.glob(".bookflow-*.tmp")), [])

    def test_replace_failure_without_original_leaves_no_output(self):
        path = self.root / "new.csv"
        with patch("bookflow.csv_io.os.replace", side_effect=PermissionError("replace denied")):
            self.fails("loans", "export", "--output", path, reason="replace denied")
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.glob(".bookflow-*.tmp")), [])


class JsonCompatibilityTests(ExtensionCase):
    def test_empty_json_is_array_without_explanation(self):
        for resource in ("books", "loans"):
            self.assertEqual(self.ok(resource, "list", "--format", "json"), "[]\n")

    def test_book_json_keys_types_status_and_unicode(self):
        self.seed("B", "A")
        self.borrow("A")
        output = self.ok("books", "list", "--format", "json")
        self.assertIn("中文", output)
        self.assertNotIn("\\u", output)
        rows = json.loads(output)
        self.assertEqual([row["isbn"] for row in rows], ["A", "B"])
        self.assertEqual([row["status"] for row in rows], ["已借出", "可借"])
        for row in rows:
            self.assertEqual(list(row), list(BOOK_FIELDS))
            self.assertTrue(all(type(value) is str for value in row.values()))

    def test_loan_json_keys_types_and_returned_null(self):
        self.seed()
        self.borrow()
        output = self.ok("loans", "list", "--format", "json")
        self.assertIn("读者甲", output)
        self.assertNotIn("\\u", output)
        row = json.loads(output)[0]
        self.assertEqual(list(row), list(LOAN_FIELDS))
        self.assertIs(type(row["loan_id"]), int)
        self.assertIsNone(row["returned_at"])
        for key in set(LOAN_FIELDS) - {"loan_id", "returned_at"}:
            self.assertIs(type(row[key]), str)
        self.assertEqual(row["status"], "已借出")
        self.ok("loans", "return", "--isbn", "A")
        returned = json.loads(self.ok("loans", "list", "--format", "json"))[0]
        self.assertIs(type(returned["returned_at"]), str)
        self.assertEqual(returned["status"], "已归还")

    def test_invalid_format_is_argparse_error(self):
        for resource in ("books", "loans"):
            self.fails(resource, "list", "--format", "yaml", reason="--format", code=2)

    def test_default_text_matches_explicit_text_and_legacy_empty_messages(self):
        self.assertEqual(self.ok("books", "list"), "暂无图书。\n")
        self.assertEqual(self.ok("loans", "list"), "暂无借阅记录。\n")
        self.assertEqual(self.ok("loans", "list", "--active"), "暂无有效借阅记录。\n")
        self.seed("B", "A")
        self.borrow("B")
        self.borrow("A")
        for resource in ("books", "loans"):
            self.assertEqual(self.ok(resource, "list"), self.ok(resource, "list", "--format", "text"))
        self.assertEqual(self.ok("books", "list"), 'ISBN | 书名 | 作者 | 状态\nA | 中文,A的"书" | 作者甲 | 已借出\nB | 中文,B的"书" | 作者甲 | 已借出\n')
        lines = self.ok("loans", "list").splitlines()
        self.assertEqual(lines[0], "记录编号 | ISBN | 书名 | 读者编号 | 姓名 | 借出时间 | 应还时间 | 归还时间 | 状态")
        self.assertTrue(lines[1].startswith("1 | B |"))
        self.assertTrue(lines[2].startswith("2 | A |"))

    def test_legacy_success_messages_unchanged(self):
        self.assertEqual(self.ok("init"), f"数据库已就绪：{self.db}\n")
        self.assertEqual(self.ok("books", "add", "--isbn", "A", "--title", "书", "--author", "人"), "已登记图书：A | 书 | 人\n")
        self.assertEqual(self.ok("readers", "add", "--id", "R1", "--name", "读者"), "已登记读者：R1 | 读者\n")
        with patch("bookflow.service.now_iso", return_value=NOW):
            self.assertEqual(self.borrow(), f"已借出：A → R1 | 借阅 #1 | {NOW}\n")
            self.assertEqual(self.ok("loans", "return", "--isbn", "A"), f"已归还：A | 借阅 #1 | {NOW}\n")


if __name__ == "__main__":
    unittest.main()
