# BookFlow

BookFlow 是面向小型阅览室的本地 Python 命令行工具，用于登记图书和读者、办理借还、查询图书状态与完整借阅历史。业务功能仅使用 Python 标准库，数据保存在 SQLite 文件中，无需网络服务、图形界面或外部数据库。

## 环境与安装

- Python 3.11 或更高版本（需包含标准库 `sqlite3`）和 pip。
- 打包使用 setuptools；无第三方运行时或测试依赖。首次安装时 pip 可能需要取得构建依赖，安装后的全部业务操作均为本地操作。

在项目根目录执行：

```console
python -m pip install -e .
bookflow --help
```

建议使用虚拟环境。Windows PowerShell 示例：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

macOS / Linux 可使用 `source .venv/bin/activate` 激活。如果 Python 的 Scripts/bin 目录不在 PATH 中，也可将以下示例中的 `bookflow` 替换为 `python -m bookflow`。

## 命令示例

统一形式为 `bookflow --db <数据库路径> <资源> <操作> [参数]`，初始化为 `bookflow --db <数据库路径> init`。`--db` 必须在资源名称之前；包含空格的路径、书名和姓名应使用引号。

```console
bookflow --db library.db init
bookflow --db library.db books add --isbn 9787302511857 --title "Python 入门" --author "张三"
bookflow --db library.db readers add --id R001 --name "李四"
bookflow --db library.db books list
bookflow --db library.db loans borrow --isbn 9787302511857 --reader-id R001
bookflow --db library.db loans list --active
bookflow --db library.db books list
bookflow --db library.db loans return --isbn 9787302511857
bookflow --db library.db loans list --active
bookflow --db library.db loans list
bookflow --db library.db books list
```

借出后图书状态为“已借出”；归还后恢复“可借”，有效借阅列表为空，历史列表仍显示借出与归还时间。再次借出会创建新的借阅记录。

ISBN、读者编号分别唯一；书名、作者、编号和姓名均不能为空。文本首尾空白会被移除，内部换行、制表符等控制字符不被接受。未知图书、未知读者、重复登记、重复借出和未借出时归还均会失败，并说明原因。

成功结果写入标准输出，退出码为 `0`；业务或数据库错误写入标准错误，退出码为 `1`；命令参数错误由 argparse 报告，退出码为 `2`。各级命令可用 `--help` 查看参数。

## 数据位置与规则

- 每次显式指定 `--db`，没有隐含的默认数据库。相对路径相对于运行命令时的当前目录；支持绝对路径和 `~` 用户目录。
- `init` 创建数据库文件，其父目录需已存在且可写。对已初始化的 BookFlow 数据库重复执行 `init` 不会清空数据。普通业务命令不会创建不存在的数据库。
- 三张表：`books`、`readers`、`loans`。SQLite 外键保证借阅引用完整；部分唯一索引保证每本书最多一条未归还借阅。图书状态从有效借阅推导，不重复存储。
- 登记、借出、归还与初始化均使用显式事务；业务检查和写入位于同一事务内，异常时整体回滚。所有外部输入通过 SQL 参数绑定。
- 借出与归还时间均由本机时钟生成，存为带 UTC 时区的 ISO 8601 字符串，例如 `2026-09-11T09:30:00.123456+00:00`；未归还时 `returned_at` 为 SQL `NULL`。
- 退出命令后可以复制数据库文件进行备份；数据库文件包含读者姓名，请自行保管。数据库、日志、虚拟环境、缓存及构建产物已加入 `.gitignore`；非标准后缀数据库需自行添加忽略规则。

## 项目结构

```text
pyproject.toml             安装元数据和 bookflow 入口
src/bookflow/
    __init__.py           包版本
    __main__.py           python -m bookflow 入口
    cli.py                参数解析、命令分发、终端输出
    service.py            输入校验、业务规则、事务边界
    repository.py         参数化 SQL 与模型映射
    database.py           连接、数据库结构、初始化和事务工具
    models.py             图书、读者、借阅的不可变数据模型
    errors.py             可直接展示的业务错误
tests/test_bookflow.py    标准库 unittest 自动化测试
```

安装后在仓库根目录运行全部测试：

```console
python -m unittest discover -s tests -v
```

每个测试使用独立临时目录与数据库，测试结束自动清理。覆盖登记、重复登记、输入校验、借出、重复借出、归还、重复归还、历史保留、再借出、独立进程间持久化、数据库约束、事务整体回滚以及 CLI 错误输出。

## 当前限制

- 仅面向单机、单用户，不提供身份认证、并发使用保证或联网同步。
- 一个 ISBN 对应一本实体图书，暂不支持同 ISBN 多副本；ISBN 按去除首尾空白后的字符串比较，不验证校验位，也不自动合并带连字符和不带连字符的 ISBN。读者编号区分大小写。
- 不提供编辑、删除、读者列表、搜索、分页、导入导出、到期日、预约、罚款或借阅数量上限。列表一次显示全部结果。
- 时间依赖本机时钟；不处理人为调整时钟引起的时间倒退。
- 数据库结构版本为 1，不提供跨版本迁移、数据库加密或自动备份。请使用 `init` 创建独立数据库，不要手工改动表结构或版本标记。
