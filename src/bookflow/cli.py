"""Argument parsing, command dispatch, and human-readable output."""

import argparse
import json
import sqlite3
import sys

from .database import connect, initialize_database
from .csv_io import write_csv
from .errors import BookFlowError
from .repository import Repository
from .service import LibraryService
from .serialization import BOOK_FIELDS, LOAN_FIELDS, book_record, loan_record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bookflow", description="BookFlow：本地图书借阅管理")
    parser.add_argument("--db", required=True, help="SQLite 数据库路径（必须放在资源名称之前）")
    resources = parser.add_subparsers(dest="resource", required=True)
    resources.add_parser("init", help="初始化数据库，可重复执行")

    books = resources.add_parser("books", help="图书管理").add_subparsers(dest="action", required=True)
    add_book = books.add_parser("add", help="登记图书")
    add_book.add_argument("--isbn", required=True)
    add_book.add_argument("--title", required=True)
    add_book.add_argument("--author", required=True)
    list_books = books.add_parser("list", help="列出图书及当前状态")
    list_books.add_argument("--format", choices=("text", "json"), default="text")
    import_books = books.add_parser("import", help="从 CSV 整批导入图书")
    import_books.add_argument("--file", required=True)
    export_books = books.add_parser("export", help="导出图书 CSV")
    export_books.add_argument("--output", required=True)
    export_books.add_argument("--force", action="store_true", help="允许覆盖已有文件")

    readers = resources.add_parser("readers", help="读者管理").add_subparsers(dest="action", required=True)
    add_reader = readers.add_parser("add", help="登记读者")
    add_reader.add_argument("--id", dest="reader_id", required=True)
    add_reader.add_argument("--name", required=True)

    loans = resources.add_parser("loans", help="借阅管理").add_subparsers(dest="action", required=True)
    borrow = loans.add_parser("borrow", help="借书")
    borrow.add_argument("--isbn", required=True)
    borrow.add_argument("--reader-id", required=True)
    borrow.add_argument("--loan-days", type=int, default=14, help="借阅天数，1 到 365，默认 14")
    return_book = loans.add_parser("return", help="还书")
    return_book.add_argument("--isbn", required=True)
    list_loans = loans.add_parser("list", help="列出借阅历史")
    list_loans.add_argument("--format", choices=("text", "json"), default="text")
    export_loans = loans.add_parser("export", help="导出借阅 CSV")
    export_loans.add_argument("--output", required=True)
    export_loans.add_argument("--force", action="store_true", help="允许覆盖已有文件")
    for command in (list_loans, export_loans):
        command.add_argument("--active", action="store_true", help="仅显示当前有效借阅")
        command.add_argument("--overdue", action="store_true", help="仅显示尚未归还且已逾期的借阅")
    return parser


def dispatch(args: argparse.Namespace, service: LibraryService) -> None:
    if args.resource == "books":
        if args.action == "add":
            book = service.add_book(args.isbn, args.title, args.author)
            print(f"已登记图书：{book.isbn} | {book.title} | {book.author}")
        elif args.action == "import":
            count = service.import_books(args.file)
            print(f"已导入图书：{count} 本。")
        else:
            books = service.list_books()
            if args.action == "export":
                count = write_csv(args.output, BOOK_FIELDS, map(book_record, books), force=args.force)
                print(f"已导出图书：{count} 本 → {args.output}")
                return
            if args.format == "json":
                print(json.dumps([book_record(book) for book in books], ensure_ascii=False))
                return
            if not books:
                print("暂无图书。")
                return
            print("ISBN | 书名 | 作者 | 状态")
            for book in books:
                status = "已借出" if book.borrowed else "可借"
                print(f"{book.isbn} | {book.title} | {book.author} | {status}")
    elif args.resource == "readers":
        reader = service.add_reader(args.reader_id, args.name)
        print(f"已登记读者：{reader.reader_id} | {reader.name}")
    elif args.action == "borrow":
        loan = service.borrow(args.isbn, args.reader_id, args.loan_days)
        print(f"已借出：{loan.isbn} → {loan.reader_id} | 借阅 #{loan.loan_id} | {loan.borrowed_at}")
    elif args.action == "return":
        loan = service.return_book(args.isbn)
        print(f"已归还：{loan.isbn} | 借阅 #{loan.loan_id} | {loan.returned_at}")
    else:
        loans = service.list_loans(active=args.active, overdue=args.overdue)
        if args.action == "export":
            count = write_csv(args.output, LOAN_FIELDS, map(loan_record, loans), force=args.force)
            print(f"已导出借阅：{count} 条 → {args.output}")
            return
        if args.format == "json":
            print(json.dumps([loan_record(loan) for loan in loans], ensure_ascii=False))
            return
        if not loans:
            print("暂无有效借阅记录。" if args.active or args.overdue else "暂无借阅记录。")
            return
        print("记录编号 | ISBN | 书名 | 读者编号 | 姓名 | 借出时间 | 应还时间 | 归还时间 | 状态")
        for loan in loans:
            status = "已借出" if loan.returned_at is None else "已归还"
            print(f"{loan.loan_id} | {loan.isbn} | {loan.title} | {loan.reader_id} | "
                  f"{loan.reader_name} | {loan.borrowed_at} | {loan.due_at} | {loan.returned_at or '-'} | {status}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.db.strip():
            raise BookFlowError("数据库路径不能为空。")
        with connect(args.db, initialize=args.resource == "init") as connection:
            if args.resource == "init":
                initialize_database(connection)
                print(f"数据库已就绪：{args.db}")
            else:
                dispatch(args, LibraryService(Repository(connection)))
    except BookFlowError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError, ValueError) as error:
        print(f"错误：无法操作数据库：{error}", file=sys.stderr)
        return 1
    return 0
