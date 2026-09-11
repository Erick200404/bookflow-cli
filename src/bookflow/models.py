"""Immutable values exchanged between storage, business logic, and the CLI."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Book:
    isbn: str
    title: str
    author: str
    borrowed: bool = False


@dataclass(frozen=True)
class Reader:
    reader_id: str
    name: str


@dataclass(frozen=True)
class Loan:
    loan_id: int
    isbn: str
    title: str
    reader_id: str
    reader_name: str
    borrowed_at: str
    due_at: str
    returned_at: str | None
