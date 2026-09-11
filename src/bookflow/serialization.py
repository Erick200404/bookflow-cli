"""Stable JSON / CSV field names and status values."""

from .models import Book, Loan

BOOK_FIELDS = ("isbn", "title", "author", "status")
LOAN_FIELDS = ("loan_id", "isbn", "title", "reader_id", "reader_name",
               "borrowed_at", "due_at", "returned_at", "status")


def book_record(book: Book) -> dict:
    return {"isbn": book.isbn, "title": book.title, "author": book.author,
            "status": "已借出" if book.borrowed else "可借"}


def loan_record(loan: Loan) -> dict:
    return {"loan_id": loan.loan_id, "isbn": loan.isbn, "title": loan.title,
            "reader_id": loan.reader_id, "reader_name": loan.reader_name,
            "borrowed_at": loan.borrowed_at, "due_at": loan.due_at,
            "returned_at": loan.returned_at,
            "status": "已借出" if loan.returned_at is None else "已归还"}
