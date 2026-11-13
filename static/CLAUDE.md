# 前端（static/）

原生 JS、无构建步骤。改完用 `node --check` 检查语法，再请用户在浏览器里试（不要自己开浏览器检查，除非用户要求）。`/static` 的响应头带 `Cache-Control: no-cache`，刷新就是新版本。

## 文件

新加文件要补一行（`tests/test_docs.py` 会检查），并写进 `index.html` 的 `<script>` 列表。

```
index.html          页面结构：记一笔 / 交易 / 收支 / 结余 / 持仓 / 设置 / 登录记录，按 #hash 切换；子标签见各脚本
login.html          登录页
app.css             全部样式
js/common.js        $ / api / send / esc / 金额日期格式化 / moneyCell / tableKeys
js/viewer.js        右侧大图 / 邮件正文、缩略图
js/add.js           「记一笔」三种方式的切换（showMethod / setMethod，存在 localStorage 的 tx.method）
js/recognize.js     SOURCES、截图上传、待核对表（邮件导入共用）
js/manual.js        手动输入（mRows，未入库的暂存在 tx.manual.draft）、确认框；loadSuggestions（别的页面也用）
js/email-import.js  从邮件更新、没认出来的邮件
js/transactions.js  交易页：周期栏 + 高级筛选、导出 CSV、上锁 / 统一保存（txEdit）；txPreset 供别的页面跳过来
js/monthly.js       收支 › 本月
js/trend.js         收支页的标签切换、收支 › 每月对比
js/balances.js      结余页
js/holdings.js      持仓页：按周切换、上锁 / 统一保存（hdEdit）、取价格（hdSync / hdFind）、列顺序和排序（localStorage）
js/security.js      登录记录
js/settings.js      设置页标签、上锁 / 统一保存（lockable）、账户 / 币种 / 种类清单（拖动排序）
js/opening.js       设置 › 期初余额
js/rates.js         设置 › 月汇率
js/email-settings.js 设置 › 邮件
js/main.js          最后加载：按 #hash 切换页面（route）、启动
```

## 必须遵守的

- **一个全局作用域**：脚本是普通 `<script>`，按 `index.html` 里的顺序加载。新文件的顶层变量名不能和别的文件重复；加载时直接执行的代码只能用前面文件定义的东西（函数里不受限制，`main.js` 最后才启动）
- 所有请求走 `api()`，收到 401 跳转到 /login
- 表格里的输入框（回车跳到下一行同一列、方向键移动）用 `tableKeys(tbody, column, atEnd)`，不要自己写 keydown
- **防误触的编辑**：设置页每个标签用 `lockable(tab, {...})` 注册，默认只读；「✎ 修改」后改动暂存在各自的状态里（`listEdit` / `opEdit` / `fxEdit` / `emEdit`），渲染时叠加在服务器数据上，中途重新读取不会丢；「保存」先列出改动确认，再调批量接口，全部成功才写入；切标签、离开、关页面时有未保存的改动先问。拖动排序不上锁。交易页（`txEdit`）、持仓页（`hdEdit`）也是这一套。新加的可编辑内容都走这套，不要做「离开输入框就保存」
- 浏览器里的设置（来源的默认账户 / 币种 / 种类、列顺序、排序、上次的标签）存 localStorage；数据都从服务器读

## 截图识别与待核对

- 待核对行、交易都从服务器读；`records` 是当前来源的待核对行，`images` 只是本次上传的缩略图。没手动改过的行跟随默认值
- 切换来源调用 `clearSession()`：清掉缩略图和大图，`session` 加一（仍在识别中的请求返回后丢弃），再读取该来源的待核对行
- 删除缩略图只从页面移除，不影响已识别的行；每行的 🖼 从服务器打开原截图。点缩略图在右侧打开大图，窄屏时全屏
- 「待核对」卡片（`#result`）截图和邮件共用：`pendingSource()` 在邮件标签下是 `email`，否则是所选来源；`loadPending` 发现来源变了先 `clearSession()`。邮件行的 ✉ 在大图位置显示正文（`showImage` 传 `text`）
- 微软登录回来跳到 `/#settings-email`，带 `email_connected` 或 `email_error`
