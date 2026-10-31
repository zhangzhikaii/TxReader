# TxReader

本地网页工具：上传手机 App 的交易截图，用 OCR 识别出交易，在网页里核对、修改后导出记账用的 CSV。用户自己记账用，识别完全在本地（RapidOCR），不联网。用户用中文交流。

运行、CSV 格式、各来源的说明见 `README.md`。

## 结构

```
app.py                    FastAPI：GET / 返回页面，POST /api/parse 接收截图 + year + source，返回 records
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
static/index.html         整个前端，单文件原生 JS，无构建步骤
tests/test_logic.py       去重、日期推断等纯逻辑测试
tests/test_samples.py     用 samples/ 里的真实截图对照期望结果（没有 samples/ 时自动跳过）
```

数据流：截图 → OCR Box → 来源解析器 → `Tx` → `build_records` → JSON 行 → 前端预览表（可编辑）→ 导出 CSV。

## 约定

- 金额：支出为负，收入为正。正负号或日期是推出来的，就设 `sign_guessed` / `year_guessed`，前端会显示 ⚠ 提醒核对。
- 去重：只去掉**不同截图之间**的重复（滚动截图会重叠），同一张截图里两笔一模一样的交易都保留；有余额时用余额区分。
- 每个来源的解析器顶部 docstring 画出该截图的版式，改解析器前先看它。示例里的商户名都用「某某商户」这类占位。
- 代码注释用英文，界面文字、README、提交信息用中文。

### 新增一个来源时要改

1. `txreader/parsers/<source>.py`，并在 `parsers/__init__.py` 的 `PARSERS` 注册
2. `static/index.html` 的 `<select id="source">` 和 `SOURCES`（上传提示、默认账户 / 币种 / 种类）
3. `README.md` 的来源表格
4. 若是滚动列表类截图，加入 `tests/test_samples.py` 的 `LIST_SOURCES`

## 前端要点（static/index.html）

- 状态都在内存里：`records`（表格行）、`images`（已上传截图的缩略图）。只有设置项存 localStorage，账户 / 币种 / 种类按来源分别记住。
- 切换来源或点「清空」都会调用 `clearAll()`，清掉记录、截图和大图，并让 `session` 加一，这样仍在识别中的请求返回后会被丢弃。
- 删除缩略图只删截图，不影响已识别出的行。
- 点缩略图在右侧打开大图，方便对照核对；窄屏时大图全屏显示。

## 隐私（重要）

- `samples/` 里是用户真实的交易截图和期望结果（已被 git 忽略）。**不要在未询问的情况下打开、读取或 OCR 打印其中的任何文件。** `expected.json` 由用户自己写。
- 跑样例测试用 `.venv/bin/python -m pytest -q --tb=no`，只看通过 / 失败数量，避免截图内容出现在输出里。
- 需要调试解析器时，请用户提供打码后的截图或描述版式。
- 代码、docstring、提交信息里不要出现真实人名。

## 测试与提交

- 测试：`.venv/bin/python -m pytest -q --tb=no`
- 前端改动没有自动测试：至少用 `node --check` 检查脚本语法，再请用户在浏览器里实际试一下。
- 提交直接在 `main` 上（仓库历史是线性的，没有用分支）。提交信息用中文：一行标题加若干 `- ` 要点。只在用户要求时提交；推送也要等用户说。
