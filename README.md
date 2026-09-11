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

### 借阅期限与逾期查询

```console
bookflow --db library.db loans borrow --isbn 9787302511857 --reader-id R001 --loan-days 30
bookflow --db library.db loans list --overdue
bookflow --db library.db loans list --active --overdue
```

`--loan-days` 默认为 14，仅接受 1 到 365 的整数。非整数属于参数错误（退出码 2），超出范围属于业务错误（退出码 1）；均不产生借阅记录。应还时间从本次借出时间加指定天数计算，一天为 24 小时。

借阅文本列表增加“应还时间”列。`--overdue` 只显示尚未归还且应还时间严格早于本次查询的当前 UTC 时间的记录；恰好到期不算逾期。它隐含 `--active`，两者同时使用结果相同。状态文字仍为“已借出”或“已归还”，不另设逾期状态。

### CSV 批量导入

```console
bookflow --db library.db books import --file books.csv
```

文件使用 UTF-8 或 UTF-8 BOM，表头及顺序必须恰好为 `isbn,title,author`，每条记录必须恰好有三列。例如：

```csv
isbn,title,author
9780000000001,"Python, 实践",张三
9780000000002,"带""引号""的书名",李四
```

导入复用登记图书的字段清洗和业务校验，移除字段首尾空白；CSV 中的控制字符（包括字段边缘的换行或制表符）被拒绝。缺列、多列、空字段、文件内重复 ISBN 或数据库已有 ISBN 均导致整批失败，新增图书数为 0。错误说明文件物理行号（跨行记录取起始行）或文件无法读取、编码无效等原因。只有表头时成功报告导入 0 本；空文件和空白记录不合法。

### CSV 导出

```console
bookflow --db library.db books export --output books-export.csv
bookflow --db library.db books export --output books-export.csv --force
bookflow --db library.db loans export --output loans-export.csv
bookflow --db library.db loans export --output active.csv --active
bookflow --db library.db loans export --output overdue.csv --overdue
bookflow --db library.db loans export --output overdue.csv --active --overdue --force
```

输出使用 Python `csv` 模块、UTF-8 BOM 和标准 CSV 引号转义，中文、逗号及引号可通过标准 CSV 读取器还原。固定字段顺序如下：

| 导出资源 | 字段顺序 |
| --- | --- |
| books | `isbn,title,author,status` |
| loans | `loan_id,isbn,title,reader_id,reader_name,borrowed_at,due_at,returned_at,status` |

图书状态为“可借”或“已借出”；借阅状态为“已借出”或“已归还”。尚未归还时 CSV 的 `returned_at` 为空单元格。无结果时仍导出表头，成功信息报告实际导出条数。导出的图书含额外的 `status` 列，如需重新导入须先移除此列。

目标父目录必须已存在且可写。默认拒绝覆盖已有文件；仅 `--force` 允许覆盖，目录不能作为文件覆盖。导出先写同目录临时文件、刷新并关闭，再以 `os.replace` 原子替换目标；写入或替换失败时保留原文件并清理临时文件。导出过滤条件与借阅列表相同。

### JSON 列表

```console
bookflow --db library.db books list --format json
bookflow --db library.db loans list --format json
bookflow --db library.db loans list --active --format json
bookflow --db library.db loans list --overdue --format json
```

`--format text|json` 默认为 `text`。JSON 模式只向 stdout 输出数组，空结果为 `[]`，中文保留原字符；对象字段顺序与上面的对应 CSV 导出一致。`loan_id` 为整数，未归还的 `returned_at` 为 `null`，其余字段为字符串。错误仍写入 stderr，不会输出成功 JSON 或提示文字。默认文本措辞、排序与退出码保持兼容，唯借阅列表按新需求增加应还时间列；图书按 ISBN 排序，借阅按记录编号排序。

ISBN、读者编号分别唯一；书名、作者、编号和姓名均不能为空。文本首尾空白会被移除，内部换行、制表符等控制字符不被接受。未知图书、未知读者、重复登记、重复借出和未借出时归还均会失败，并说明原因。

成功结果写入标准输出，退出码为 `0`；业务或数据库错误写入标准错误，退出码为 `1`；命令参数错误由 argparse 报告，退出码为 `2`。各级命令可用 `--help` 查看参数。

## 数据位置与规则

- 每次显式指定 `--db`，没有隐含的默认数据库。相对路径相对于运行命令时的当前目录；支持绝对路径和 `~` 用户目录。
- `init` 创建数据库文件，其父目录需已存在且可写。对已初始化的 BookFlow 数据库重复执行 `init` 不会清空数据。普通业务命令不会创建不存在的数据库。
- 当前结构版本为 `PRAGMA user_version = 2`。第一阶段 v1 数据库须先再次运行 `bookflow --db library.db init`；普通业务命令不会隐式升级。升级在一个事务内保留图书、读者和所有借阅编号及原始借还时间，为每条旧借阅补入 `borrowed_at + 14 天` 的 UTC 应还时间，然后更新版本。新数据库直接创建 v2；重复 `init` 幂等。任何迁移失败均回滚结构、数据和版本，可修复原因后重试；旧借出时间缺失时区或无法解析会明确报错。升级前可先备份数据库文件。
- 三张表：`books`、`readers`、`loans`。SQLite 外键保证借阅引用完整；部分唯一索引保证每本书最多一条未归还借阅。图书状态从有效借阅推导，不重复存储。
- 登记、借出、归还与初始化均使用显式事务；业务检查和写入位于同一事务内，异常时整体回滚。所有外部输入通过 SQL 参数绑定。
- 新借出、应还与归还时间均存为带 UTC 时区的 ISO 8601 字符串，例如 `2026-09-11T09:30:00.123456+00:00`；未归还时 `returned_at` 为 SQL `NULL`。迁移保留旧记录的原始借还时间字符串，仅将新增应还时间规范为 UTC。
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
    time_utils.py         UTC 时间与应还时间计算
    csv_io.py             严格 CSV 读取和原子文件导出
    serialization.py      JSON / CSV 固定字段与状态映射
tests/test_bookflow.py    标准库 unittest 自动化测试
tests/test_extensions.py  v2 迁移、借期、CSV、JSON 和兼容性测试
```

安装后在仓库根目录运行全部测试：

```console
python -m unittest discover -s tests -v
```

每个测试使用独立临时目录与数据库，测试结束自动清理。原有 25 项测试保持不变，另有扩展测试覆盖迁移数据保留及后段失败回滚、借期边界、精确逾期边界和过滤组合、CSV Unicode 与整批回滚、导出覆盖保护及模拟权限/写入/替换失败、JSON 字段类型和旧命令默认措辞。

## 当前限制

- 仅面向单机、单用户，不提供身份认证、并发使用保证或联网同步。
- 一个 ISBN 对应一本实体图书，暂不支持同 ISBN 多副本；ISBN 按去除首尾空白后的字符串比较，不验证校验位，也不自动合并带连字符和不带连字符的 ISBN。读者编号区分大小写。
- 不提供编辑、删除、读者列表、搜索、分页、读者/借阅导入、预约、罚款或借阅数量上限。列表和 CSV 导入会将记录读入内存，适合小型阅览室；不提供后台逾期通知。
- 时间依赖本机时钟；不处理人为调整时钟引起的时间倒退。
- 仅提供 v1 到 v2 升级，不提供降级、任意未来版本迁移、数据库加密或自动备份。升级后的 v2 数据库不能由第一阶段程序直接操作。请使用 `init` 创建独立数据库，不要手工改动表结构或版本标记。
- CSV 保留字段原文，不改写以 `=` 等字符开头的内容；供电子表格打开时，应按文本导入不可信数据。文件原子替换面向正常运行下的单用户文件操作，不提供并发写入协调或断电后的文件系统持久性保证。
