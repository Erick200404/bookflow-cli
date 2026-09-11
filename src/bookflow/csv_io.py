"""Strict CSV input and atomic, same-directory CSV publication."""

import csv
import os
from pathlib import Path
import tempfile
from collections.abc import Iterable, Mapping

from .errors import BookFlowError

BOOK_IMPORT_FIELDS = ("isbn", "title", "author")


def read_book_rows(file_path: str) -> list[tuple[int, list[str]]]:
    rows = []
    line = 1
    try:
        with Path(file_path).expanduser().open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, strict=True)
            if next(reader, None) != list(BOOK_IMPORT_FIELDS):
                raise BookFlowError(f"CSV 文件 {file_path} 第 1 行：表头必须为 isbn,title,author。")
            while True:
                line = reader.line_num + 1
                fields = next(reader, None)
                if fields is None:
                    break
                if len(fields) != 3:
                    raise BookFlowError(f"CSV 文件 {file_path} 第 {line} 行：必须恰好包含 3 列。")
                rows.append((line, fields))
    except UnicodeError as error:
        raise BookFlowError(f"无法读取 CSV 文件 {file_path}：编码无效，必须使用 UTF-8 或 UTF-8 BOM。") from error
    except (OSError, ValueError) as error:
        raise BookFlowError(f"无法读取 CSV 文件 {file_path}：{error}") from error
    except csv.Error as error:
        raise BookFlowError(f"CSV 文件 {file_path} 第 {line} 行：格式无效：{error}") from error
    return rows


def write_csv(output: str, fieldnames: tuple[str, ...], rows: Iterable[Mapping], *, force: bool = False) -> int:
    temporary = None
    try:
        if not output.strip():
            raise BookFlowError("导出文件路径不能为空。")
        target = Path(output).expanduser().absolute()
        if target.is_dir():
            raise BookFlowError(f"无法导出 CSV 文件 {output}：目标是目录。")
        if os.path.lexists(target) and not force:
            raise BookFlowError(f"导出文件已存在：{output}；如需覆盖请使用 --force。")
        count = 0
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="", dir=target.parent,
            prefix=".bookflow-", suffix=".tmp", delete=False,
        ) as destination:
            temporary = Path(destination.name)
            writer = csv.DictWriter(destination, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
                count += 1
            destination.flush()
            os.fsync(destination.fileno())
        # Recheck after writing as well. Single-user use is the supported model.
        if os.path.lexists(target) and not force:
            raise BookFlowError(f"导出文件已存在：{output}；如需覆盖请使用 --force。")
        os.replace(temporary, target)
        return count
    except (OSError, ValueError, csv.Error) as error:
        raise BookFlowError(f"无法导出 CSV 文件 {output}：{error}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as error:
                raise BookFlowError(f"导出文件 {output} 的临时文件无法清理：{temporary}：{error}") from error
