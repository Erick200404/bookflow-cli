"""Business rules and transaction boundaries, independent of argument parsing."""

from datetime import datetime

from .csv_io import read_book_rows
from .database import transaction
from .errors import BookFlowError
from .models import Book, Loan, Reader
from .repository import Repository
from .time_utils import due_date, now_iso


def required(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise BookFlowError(f"{label}不能为空。")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise BookFlowError(f"{label}不能包含换行、制表符等控制字符。")
    return value


def clean_book(isbn: str, title: str, author: str) -> Book:
    return Book(required(isbn, "ISBN"), required(title, "书名"), required(author, "作者"))


class LibraryService:
    def __init__(self, repository: Repository):
        self.repository = repository

    def add_book(self, isbn: str, title: str, author: str) -> Book:
        book = clean_book(isbn, title, author)
        with transaction(self.repository.connection):
            if self.repository.get_book(book.isbn):
                raise BookFlowError(f"ISBN 已存在：{book.isbn}。")
            self.repository.insert_book(book)
        return book

    def import_books(self, file_path: str) -> int:
        rows = read_book_rows(file_path)
        with transaction(self.repository.connection):
            for line, fields in rows:
                try:
                    # CSV must reject controls even at field edges; ordinary add
                    # retains its existing trim-first behavior.
                    for value in fields:
                        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
                            raise BookFlowError("字段不能包含换行、制表符等控制字符。")
                    book = clean_book(*fields)
                    if self.repository.get_book(book.isbn):
                        raise BookFlowError(f"ISBN 已存在或在 CSV 内重复：{book.isbn}。")
                    self.repository.insert_book(book)
                except BookFlowError as error:
                    raise BookFlowError(f"CSV 文件 {file_path} 第 {line} 行：{error}") from error
        return len(rows)

    def add_reader(self, reader_id: str, name: str) -> Reader:
        reader = Reader(required(reader_id, "读者编号"), required(name, "姓名"))
        with transaction(self.repository.connection):
            if self.repository.get_reader(reader.reader_id):
                raise BookFlowError(f"读者编号已存在：{reader.reader_id}。")
            self.repository.insert_reader(reader)
        return reader

    def borrow(self, isbn: str, reader_id: str, loan_days: int = 14) -> Loan:
        if type(loan_days) is not int or not 1 <= loan_days <= 365:
            raise BookFlowError("借阅天数必须为 1 到 365 的整数。")
        isbn = required(isbn, "ISBN")
        reader_id = required(reader_id, "读者编号")
        with transaction(self.repository.connection):
            if not self.repository.get_book(isbn):
                raise BookFlowError(f"图书不存在：{isbn}。")
            if not self.repository.get_reader(reader_id):
                raise BookFlowError(f"读者不存在：{reader_id}。")
            if self.repository.active_loan(isbn):
                raise BookFlowError(f"图书已借出：{isbn}，请先归还。")
            borrowed_at = now_iso()
            loan = self.repository.insert_loan(isbn, reader_id, borrowed_at, due_date(borrowed_at, loan_days))
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

    def list_loans(self, *, active: bool = False, overdue: bool = False) -> list[Loan]:
        loans = self.repository.list_loans(active=active or overdue)
        if overdue:
            cutoff = datetime.fromisoformat(now_iso())
            # Compare instants, not ISO strings, so precision and offset spelling
            # do not change the strictly-earlier boundary.
            loans = [loan for loan in loans if datetime.fromisoformat(loan.due_at) < cutoff]
        return loans
