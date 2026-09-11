"""Business rules and transaction boundaries, independent of argument parsing."""

from datetime import datetime, timezone

from .database import transaction
from .errors import BookFlowError
from .models import Book, Loan, Reader
from .repository import Repository


def required(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise BookFlowError(f"{label}不能为空。")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise BookFlowError(f"{label}不能包含换行、制表符等控制字符。")
    return value


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class LibraryService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def add_book(self, isbn: str, title: str, author: str) -> Book:
        book = Book(required(isbn, "ISBN"), required(title, "书名"), required(author, "作者"))
        with transaction(self.repository.connection):
            if self.repository.get_book(book.isbn):
                raise BookFlowError(f"ISBN 已存在：{book.isbn}。")
            self.repository.insert_book(book)
        return book

    def add_reader(self, reader_id: str, name: str) -> Reader:
        reader = Reader(required(reader_id, "读者编号"), required(name, "姓名"))
        with transaction(self.repository.connection):
            if self.repository.get_reader(reader.reader_id):
                raise BookFlowError(f"读者编号已存在：{reader.reader_id}。")
            self.repository.insert_reader(reader)
        return reader

    def borrow(self, isbn: str, reader_id: str) -> Loan:
        isbn = required(isbn, "ISBN")
        reader_id = required(reader_id, "读者编号")
        with transaction(self.repository.connection):
            if not self.repository.get_book(isbn):
                raise BookFlowError(f"图书不存在：{isbn}。")
            if not self.repository.get_reader(reader_id):
                raise BookFlowError(f"读者不存在：{reader_id}。")
            if self.repository.active_loan(isbn):
                raise BookFlowError(f"图书已借出：{isbn}，请先归还。")
            loan = self.repository.insert_loan(isbn, reader_id, now_iso())
        return loan

    def return_book(self, isbn: str) -> Loan:
        isbn = required(isbn, "ISBN")
        with transaction(self.repository.connection):
            if not self.repository.get_book(isbn):
                raise BookFlowError(f"图书不存在：{isbn}。")
            active = self.repository.active_loan(isbn)
            if not active:
                raise BookFlowError(f"图书当前未借出：{isbn}，不能归还。")
            loan = self.repository.finish_loan(active.loan_id, now_iso())
        return loan

    def list_books(self) -> list[Book]:
        return self.repository.list_books()

    def list_loans(self, *, active: bool = False) -> list[Loan]:
        return self.repository.list_loans(active=active)
