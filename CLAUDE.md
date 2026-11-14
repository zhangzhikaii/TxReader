# TxReader

用户自己用的记账网页：上传手机 App 的交易截图（本地 RapidOCR 识别）、从 Outlook 读通知邮件，或手动输入，核对后入库；有月度收支、账户结余与汇差、投资持仓，能导出 CSV。用户用中文交流。

联网的只有两处：邮件导入（Microsoft Graph，点「从邮件更新」时才读）和 Yahoo Finance（持仓页的证券价格、设置页的月汇率，只发代码如 `7203.T`、`JPYCNY=X`，不发数量、金额）。不要加别的联网功能，除非用户同意。

## 先读哪里

- `docs/plan.md`：还没做完的事。做完一步就打勾，一节做完移到 `docs/history.md`
- `docs/decisions.md`：仍然有效的决定和原因。提方案前先看，别把否决过的再提；决定变了就更新它
- `docs/history.md`：做完的步骤和实现细节的存档，平时不用读
- `README.md`：给用户看的功能说明、运行方法、CSV 格式。功能变了就改它，CLAUDE.md 里不写功能说明
- 各模块顶部的 docstring：这个模块怎么算、为什么这样（改之前先看）
- `static/CLAUDE.md`（前端）、`txreader/parsers/CLAUDE.md`（截图解析器、新增来源）：在那两个目录工作时才会加载

## 结构

每个文件一行，只写职责；细节看文件顶部的 docstring。新加文件要补一行（`tests/test_docs.py` 会检查）。

```
app.py                    FastAPI 入口：登录中间件、异常 -> 响应（ValueError 400、NotFound 404、UnknownNames 409 等，接口里不用自己 try/except）、挂载路由和 /static
txreader/routes/          接口，每个功能一个 APIRouter：pages、review、transactions、books、holdings、settings、email
txreader/errors.py        公共的 NotFound（子类可改 `detail`）
txreader/db.py            表结构、旧库升级（`_migrate`）、账户 / 币种登记、连接与 `transaction()`、截图存 WebP
txreader/store.py         识别入库、待核对行、确认入库、手动输入、交易修改、导出 CSV
txreader/lists.py         账户 / 币种 / 种类清单：提示、新名称、设置页的批量改动（`apply`）、不能删的原因
txreader/books.py         期初余额、月汇率、账户结余（`balances`）、月度收支（`monthly`）、每月对比（`trend`）
txreader/portfolio.py     投资账户按天的持仓（变动时填）、每天的市值、盈亏；自动取每日价格和汇率（`sync` / `refresh` / `lookup`）
txreader/quotes.py        Yahoo Finance 公开接口：搜索、最新价、收盘价、一段日线（`daily`）
txreader/fx.py            月汇率从 Yahoo Finance 自动取（`sync` / `refresh`）：过去的月份取月底收盘价，本月取最新价
txreader/mail.py          邮件导入：Outlook OAuth、邮件规则、读取、识别
txreader/auth.py          单用户密码登录、会话、登录记录、锁定；`python -m txreader.auth` 生成密码哈希
txreader/devtools.py      开发用命令：`clear`、`seed <file>`（见 README）
txreader/ocr.py           RapidOCR 封装：图片 -> Box
txreader/models.py        Tx（解析结果）、resolve_date（补全年 / 月并标记推测）
txreader/records.py       build_records：合并多张截图、跨截图去重、排序
txreader/matching.py      新识别的行与库里比对（跳过 / 合并 / 疑似重复 / 新增）
txreader/parsers/         每个来源一个解析器（见 txreader/parsers/CLAUDE.md）
static/                   前端，原生 JS、无构建步骤（见 static/CLAUDE.md）
tests/test_logic.py       纯逻辑：去重、日期推断、比对
tests/test_api.py         接口全流程（假识别结果 + 临时数据目录）
tests/test_email.py       邮件导入（假的微软服务器：替换 `mail._token_call` / `mail._messages`）
tests/test_devtools.py    开发用命令
tests/test_samples.py     samples/ 里的真实截图对照期望结果（没有就跳过）
tests/test_docs.py        检查每个文件都写进了 CLAUDE.md 的结构
```

测试不联网：持仓价格、月汇率的测试替换掉 `quotes.latest` / `quotes.closes` / `quotes.daily` / `quotes.search`。

## 数据流

- 截图：按哈希存成 WebP（传过的直接用上次结果）→ OCR Box → 来源解析器 → `Tx` → `build_records` → `match_rows` 与库里比对 → 新行存为 `ocr_rows`（pending）→ 核对时的修改存在 `draft` → 确认后写入 `transactions`（`origin = screenshot`）
- 手动输入：直接写入 `transactions`（`origin = manual`，没有 `ocr_row_id`）
- 邮件：读 `email_last_fetch` 之后的邮件 → 按规则挑出、存进 `emails`（按 message_id 去重，认不出来的也存）→ `ocr_rows`（`source = email`，有 `email_id` 没有 `image_id`，规则的账户 / 币种 / 种类放在 `draft`）→ 和截图一样核对入库（`origin = email`）
- 清单：`accounts`、`currencies`、`account_currencies`、`categories`，各有 `sort`；账户有 `type`（`lists.ACCOUNT_TYPES`），币种有 `is_base` 和 `code`（ISO 4217，添加时按名称猜，可以改；自动取汇率、持仓认币种都用它），种类有 `fixed`。截图入库自动登记；手动输入遇到新的要用户同意（接口参数 `add_accounts`，否则 409）。交易里存名称文字，改名时一起改
- 报表：结余、收支、汇差都从交易 + 期初余额 + 月汇率算出来，不存（规则见 `docs/decisions.md`）

## 约定

- 金额：支出为负，收入为正。正负号或日期是推出来的，设 `sign_guessed` / `year_guessed`，前端显示 ⚠
- `ocr_rows` 里的识别原始值不改，比对只用它们。入库后的交易只能改种类和描述（`store.TX_EDITABLE`），不能删除；手动输入的必填项见 `store.TX_REQUIRED`。没有「忽略」状态，不要的待核对行直接删除
- 代码注释用英文；界面文字、README、文档、提交信息用中文
- 旧库升级都写在 `db._migrate`，每项先检查再改，跑几次都安全

## 隐私（重要）

`.claude/settings.json` 里有强制保护，不要改动、绕过或关掉：读写 `data/`、`samples/`、`seed/`、`docs/example*.csv`、`txreader.db` 一律拒绝；`.claude/hooks/guard-private.sh` 会拦下提到这些路径、调用 sqlite 命令行，或没设 `TXREADER_DATA` 就启动 uvicorn / devtools 的 shell 命令。被拦了就换一种不碰真实数据的做法，或者先问用户，不要换个写法再试。

- `samples/`（真实截图和期望结果）、`seed/`（要导入的真实交易）、`data/` 或 `TXREADER_DATA` 指向的目录（真实数据库和截图）：**未经询问不要打开、读取、查询或 OCR 打印**。`expected.json` 由用户自己写
- 数据库里的 `emails` 表是真实邮件正文，`config` 里有 Client secret 和令牌，同样不要查询
- `docs/example.csv`、`docs/example2.csv` 是真实数字：只看结构，不要抄进代码、测试或文档，不要提交
- 调试用临时数据目录（跑 `devtools` 也先把 `TXREADER_DATA` 指向临时目录）
- 代码、docstring、提交信息里不要出现真实人名；示例商户名用「某某商户」这类占位
- 调试解析器时请用户提供打码后的截图或描述版式

## 测试与提交

- 测试：`.venv/bin/python -m pytest -q --tb=no`（只看通过 / 失败数量，避免截图内容出现在输出里）
- 前端没有自动测试：至少 `node --check` 改过的脚本，再请用户在浏览器里试
- 做完一项：跑测试；在 `docs/plan.md` 打勾；功能变了改 `README.md`；新文件写进上面的结构；有新决定写进 `docs/decisions.md`
- 提交直接在 `main` 上（历史是线性的）。提交信息用中文：一行标题加若干 `- ` 要点。只在用户要求时提交；推送也要等用户说
