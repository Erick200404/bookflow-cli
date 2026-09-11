"""SQL and row-to-model mapping; no CLI or business-rule decisions."""

import sqlite3

from .models import Book, Loan, Reader

LOAN_SELECT = """
    SELECT l.loan_id, l.isbn, b.title, l.reader_id,
           r.name AS reader_name, l.borrowed_at, l.due_at, l.returned_at
    FROM loans AS l
    JOIN books AS b ON b.isbn = l.isbn
    JOIN readers AS r ON r.reader_id = l.reader_id
"""


class Repository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get_book(self, isbn: str) -> Book | None:
        row = self.connection.execute(
            "SELECT isbn, title, author FROM books WHERE isbn = ?", (isbn,)
        ).fetchone()
        return Book(**dict(row)) if row else None

    def insert_book(self, book: Book) -> None:
        self.connection.execute(
            "INSERT INTO books (isbn, title, author) VALUES (?, ?, ?)",
            (book.isbn, book.title, book.author),
        )

    def get_reader(self, reader_id: str) -> Reader | None:
        row = self.connection.execute(
            "SELECT reader_id, name FROM readers WHERE reader_id = ?", (reader_id,)
        ).fetchone()
        return Reader(**dict(row)) if row else None

    def insert_reader(self, reader: Reader) -> None:
        self.connection.execute(
            "INSERT INTO readers (reader_id, name) VALUES (?, ?)",
            (reader.reader_id, reader.name),
        )

    def active_loan(self, isbn: str) -> Loan | None:
        row = self.connection.execute(
            LOAN_SELECT + " WHERE l.isbn = ? AND l.returned_at IS NULL", (isbn,)
        ).fetchone()
        return Loan(**dict(row)) if row else None

    def insert_loan(self, isbn: str, reader_id: str, borrowed_at: str, due_at: str) -> Loan:
        cursor = self.connection.execute(
            "INSERT INTO loans (isbn, reader_id, borrowed_at, due_at) VALUES (?, ?, ?, ?)",
            (isbn, reader_id, borrowed_at, due_at),
        )
        return self.get_loan(cursor.lastrowid)

    def get_loan(self, loan_id: int) -> Loan:
        row = self.connection.execute(
            LOAN_SELECT + " WHERE l.loan_id = ?", (loan_id,)
        ).fetchone()
        return Loan(**dict(row))

    def finish_loan(self, loan_id: int, returned_at: str) -> Loan:
        self.connection.execute(
            "UPDATE loans SET returned_at = ? WHERE loan_id = ?",
            (returned_at, loan_id),
        )
        return self.get_loan(loan_id)

    def list_books(self) -> list[Book]:
        rows = self.connection.execute("""
            SELECT b.isbn, b.title, b.author,
                   EXISTS (SELECT 1 FROM loans AS l
                           WHERE l.isbn = b.isbn AND l.returned_at IS NULL) AS borrowed
            FROM books AS b ORDER BY b.isbn
        """)
        return [Book(row["isbn"], row["title"], row["author"], bool(row["borrowed"]))
                for row in rows]

    def list_loans(self, *, active: bool = False) -> list[Loan]:
        where = " WHERE l.returned_at IS NULL" if active else ""
        rows = self.connection.execute(LOAN_SELECT + where + " ORDER BY l.loan_id")
        return [Loan(**dict(row)) for row in rows]
