# TxReader

用户自己用的记账网页：上传手机 App 的交易截图（OCR 识别）或从 Outlook 读通知邮件，核对、修改后入库；也可以手动输入；有月度收支、账户结余与汇差，能导出 CSV。截图识别完全在本地（RapidOCR）；只有邮件导入会联网（Microsoft Graph，用户点「从邮件更新」时才读）。用户用中文交流。

运行、CSV 格式、各来源的说明见 `README.md`。当前计划见 `docs/plan.md`（设置页、期初余额与月汇率、账户结余、月度收支、邮件导入、部署），做完一步就在上面打勾，决定有变就更新它。月度收支表参照 `docs/example.csv`，账户结余表参照 `docs/example2.csv`（用户自己写的，里面是真实数字：只看结构，不要把数字抄进代码、测试或文档，也不要提交）。

## 结构

```
app.py                    FastAPI 入口：登录检查（中间件）、异常 -> 响应（ValueError 400、NotFound 404、UnknownNames 409 等，接口里不用自己 try/except）、挂载路由和 /static
txreader/routes/          接口，按功能一个 APIRouter：pages（页面、登录）、review（截图识别、待核对）、transactions（交易、按筛选条件导出 CSV）、books（期初余额、汇率、两张报表）、settings（清单）、email
txreader/errors.py        公共的 NotFound（各模块可改 `detail` 提示文字）
txreader/db.py            SQLite 表结构、旧库升级（`_migrate`）、账户 / 币种登记（`register_accounts`）、连接（`connect`；`transaction()` 里的多次 `connect()` 共用一个连接，全部成功才提交）、截图存成 WebP（数据目录 TXREADER_DATA，默认 data/）
txreader/store.py         识别入库流程、待核对行、确认入库、手动输入、交易、导出 CSV（`export_csv`，不记录导出）
txreader/lists.py         账户 / 币种 / 种类清单：输入时的提示（`offered`）、找出新名称（`unknown`）、设置页的增删改排序（`apply`：一批改动一起保存）
txreader/devtools.py      开发用命令：`python -m txreader.devtools clear`（删除所有交易，要输入 yes）、`seed <file>`（从 seed/ 导入 CSV 并登记清单）
txreader/books.py         期初余额（起始月 + 账户 × 币种）、月汇率（1 外币 = 多少本位币，手填）、账户结余（`balances`：月底结余、折算、汇差、月度差额）；月度收支（`monthly`：种类 × 币种、折算、汇差、和结余页核对）
txreader/matching.py      新识别的行与库里已有行比对（跳过 / 合并 / 疑似重复 / 新增）
txreader/mail.py          邮件导入：Outlook OAuth（Client ID / secret / 令牌存在 config 表）、邮件规则（`email_rules`：发件人、标题、三个正则）、读取上次之后的邮件（`fetch`）、识别、重新识别没认出来的
txreader/auth.py          单用户密码登录、会话（闲置超时 + 30 天上限）、登录记录、错误次数锁定；`python -m txreader.auth` 生成密码哈希（填到 `.env`）
txreader/ocr.py           RapidOCR 封装：图片 -> Box(text, x0, y0, x1, y1, score, angle)
txreader/parsers/         每个来源一个解析器，parse_boxes(boxes, img) -> (list[Tx], warnings)
  __init__.py             PARSERS 注册表（来源 key -> 解析器）
  wechat.py               微信「零钱明细」列表；收支靠金额颜色区分（橙色为收入）
  boc.py                  中国银行单笔「明细」页（每张一笔）
  icbc.py                 工商银行「收支明细」列表；看不到月份标题时按星期推断月份
  suica.py                Apple 钱包 Suica 单笔详情页
  receipt.py              纸质小票照片（各家版式不同）；按文本框倾斜角配对「合計」与金额
txreader/models.py        Tx（解析结果）与 resolve_date（补全缺失的年 / 月，并标记为推测）
txreader/records.py       build_records：合并多张截图、跨截图去重、按时间排序，生成行
static/index.html         页面结构（记一笔 / 交易 / 收支 / 结余 / 设置 / 登录记录六个页面；设置里再分账户 / 币种 / 种类 / 期初余额 / 月汇率 / 邮件，按 #hash 切换；记一笔里再分截图识别 / 手动输入 / 邮件导入）
static/app.css            全部样式
static/js/                前端脚本，原生 JS、无构建步骤；普通 <script>（不是模块）按 index.html 里的顺序加载，共用一个全局作用域
  common.js               $ / api / send / esc / 金额日期格式化 / moneyCell / tableKeys（表格里回车跳格）
  viewer.js               右侧大图 / 邮件正文、缩略图
  add.js                  记一笔的三种方式切换
  recognize.js            SOURCES、截图上传、待核对表（邮件导入共用）
  manual.js               手动输入、确认框；loadSuggestions（清单提示，别的页面也用）
  email-import.js         从邮件更新、没认出来的邮件
  transactions.js / monthly.js / balances.js / security.js   各自的页面（交易页带「导出 CSV」：导出当前筛选出的交易）
  settings.js             设置页标签、上锁 / 统一保存（`lockable`）、账户 / 币种 / 种类清单（拖动排序）
  opening.js / rates.js / email-settings.js                              设置里的期初余额、月汇率、邮件
  main.js                 最后加载：按 #hash 切换页面（route）、启动
static/login.html         登录页
tests/test_logic.py       去重、日期推断、比对规则等纯逻辑测试
tests/test_api.py         接口全流程测试（假识别结果 + 临时数据目录）
tests/test_devtools.py    开发用命令的测试
tests/test_email.py       邮件导入全流程测试（假的微软服务器：`mail._token_call` / `mail._messages`）
tests/test_samples.py     用 samples/ 里的真实截图对照期望结果（没有 samples/ 时自动跳过）
```

数据流：截图（按哈希存成 WebP，传过的直接用上次结果）→ OCR Box → 来源解析器 → `Tx` → `build_records` → `match_rows` 与库里比对 → 新行存为 `ocr_rows`（pending）→ 前端核对、修改（存为 draft）→ 确认后写入 `transactions`（`origin = screenshot`）→ 在交易页按筛选条件导出 CSV（不记录导出）。手动输入直接写入 `transactions`（`origin = manual`，没有 `ocr_row_id`）。邮件：点「从邮件更新」→ 读 `email_last_fetch` 到现在收到的邮件 → 按规则挑出、存进 `emails`（按 message_id 去重，认不出来的也存，可以改规则后重新识别）→ 识别出的行存为 `ocr_rows`（`source = email`，`email_id`，没有 `image_id`；规则的账户 / 币种 / 种类放在 `draft` 里）→ 和截图一样核对入库（`origin = email`）。账户 / 币种 / 种类清单在 `accounts`、`currencies`、`account_currencies`（一个账户可有多个币种）、`categories`，各有 `sort`，币种有 `is_base`：截图入库时自动登记，手动输入遇到新的要用户在确认框里同意（接口参数 `add_accounts`，否则 409）；交易里仍存名称文字，设置页改名时一起改。

## 约定

- 金额：支出为负，收入为正。正负号或日期是推出来的，就设 `sign_guessed` / `year_guessed`，前端会显示 ⚠ 提醒核对。
- 去重：只去掉**不同截图之间**的重复（滚动截图会重叠），同一张截图里两笔一模一样的交易都保留；有余额时用余额区分。
- 每个来源的解析器顶部 docstring 画出该截图的版式，改解析器前先看它。示例里的商户名都用「某某商户」这类占位。
- 代码注释用英文，界面文字、README、提交信息用中文。
- `ocr_rows` 里的识别原始值不改，比对只用它们；待核对时的修改存在 `draft`（JSON）里。入库后的交易（不论怎么添加的）只能改种类和描述（`store.TX_EDITABLE`），不能删除。手动输入时日期、账户、币种、金额、种类必填（`store.TX_REQUIRED`）。没有「忽略」状态，不要的待核对行直接删除。

### 新增一个来源时要改

1. `txreader/parsers/<source>.py`，并在 `parsers/__init__.py` 的 `PARSERS` 注册
2. `static/index.html` 的 `<select id="source">`，以及 `static/js/recognize.js` 的 `SOURCES`（上传提示、默认账户 / 币种 / 种类）
3. `README.md` 的来源表格
4. 若是滚动列表类截图，加入 `tests/test_samples.py` 的 `LIST_SOURCES`

## 前端要点（static/）

- 脚本共用一个全局作用域：新文件里的顶层变量名不能和别的文件重复；文件加载时直接执行的代码只能用前面文件定义的东西（函数里的调用不受限制，`main.js` 最后才启动页面）。新加文件要写进 `index.html` 的 `<script>` 列表。
- 表格里的输入框要「回车跳到下一行同一列」，用 `tableKeys(tbody, column, atEnd)`，不要再自己写 keydown。
- `/static` 的文件响应头带 `Cache-Control: no-cache`，改完刷新页面就能拿到新版本。

- 「记一笔」的添加方式由 `showMethod` / `setMethod` 切换（后者还会加载数据），存在 localStorage 的 `tx.method`。手动输入的行在 `mRows`，未入库的暂存在 `tx.manual.draft`。
- 待核对行、交易都从服务器读取；`records` 是当前来源的待核对行，`images` 只是本次上传的缩略图。设置项存 localStorage，账户 / 币种 / 种类按来源分别记住；没手动改过的行跟随默认值。
- 切换来源会调用 `clearSession()`，清掉缩略图和大图，并让 `session` 加一，这样仍在识别中的请求返回后会被丢弃，然后重新读取该来源的待核对行。
- 删除缩略图只是从页面移除，不影响已识别出的行；每行的 🖼 按钮从服务器打开它的原截图。
- 点缩略图在右侧打开大图，方便对照核对；窄屏时大图全屏显示。
- 所有请求走 `api()`，收到 401 跳转到 /login。
- 「待核对」卡片（`#result`）截图识别和邮件导入共用：`pendingSource()` 在邮件标签下是 `email`，否则是所选截图来源；`loadPending` 发现来源变了会先 `clearSession()`。邮件行的 ✉ 按钮在右侧大图位置显示邮件正文（`showImage` 传 `text`）。
- 微软登录回来跳到 `/#settings-email`，带 `email_connected` 或 `email_error` 参数。
- 设置页防误触：每个标签用 `lockable(tab, {...})` 注册，默认只读；点「✎ 修改」后改动暂存在各自的状态里（`listEdit` / `opEdit` / `fxEdit` / `emEdit`），渲染时叠加在服务器数据上，所以中途重新读取不会丢；「保存」先列出改动确认，再调对应的批量接口（`/api/settings/batch`、`PUT /api/opening`、`PUT /api/rates`、`PUT /api/email/settings`），全部成功才写入。切标签、离开设置页、关页面时有未保存的改动会先问。拖动排序不上锁。新加设置项也要走这套，不要再做「离开输入框就保存」。

## 隐私（重要）

- `samples/` 里是用户真实的交易截图和期望结果（已被 git 忽略）。**不要在未询问的情况下打开、读取或 OCR 打印其中的任何文件。** `expected.json` 由用户自己写。
- `data/`（或 `TXREADER_DATA` 指向的目录）里是真实的数据库和截图，同样**不要在未询问的情况下打开或查询**。调试时用临时数据目录（`devtools` 也一样：先设 `TXREADER_DATA` 指向临时目录再跑）。
- `seed/` 里是用户要导入的真实交易（已被 git 忽略），同样不要在未询问的情况下打开。
- 跑样例测试用 `.venv/bin/python -m pytest -q --tb=no`，只看通过 / 失败数量，避免截图内容出现在输出里。
- 需要调试解析器时，请用户提供打码后的截图或描述版式。
- 代码、docstring、提交信息里不要出现真实人名。
- 数据库的 `emails` 表里是用户真实的邮件正文，`config` 里有 Client secret 和令牌，同样不要在未询问的情况下查询。

## 测试与提交

- 测试：`.venv/bin/python -m pytest -q --tb=no`
- 前端改动没有自动测试：至少用 `node --check` 检查脚本语法，再请用户在浏览器里实际试一下。
- 提交直接在 `main` 上（仓库历史是线性的，没有用分支）。提交信息用中文：一行标题加若干 `- ` 要点。只在用户要求时提交；推送也要等用户说。
