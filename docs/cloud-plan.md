# 云端部署计划：数据库、核对入库、导出记录、登录

状态：第 1–5 步已完成（2026-11-04），第 6 步（部署）暂缓

## 目标

部署到云端，只有我一个人用。每次上传截图识别后：

- 已经入库的交易自动跳过
- 新的交易显示出来让我核对，勾选后提交入库
- 没提交的交易保留为「待核对」，下次打开还在
- 从已入库的交易导出 CSV（格式不变），记录哪些导出过，也允许重复导出

## 数据库（SQLite）

**images**：上传的截图
- `id`, `sha256`（唯一）, `filename`, `source`, `uploaded_at`, `path`
- 图片压缩为 WebP（质量约 80，不缩小或稍缩小，留着以后重新识别用），存在磁盘目录里，库里只存路径
- 文件名不可靠，用哈希判断是否传过同一张图；传过的不再跑 OCR，直接用上次的识别结果

**ocr_rows**：每次识别出的每一行
- `id`, `image_id`, `pos`（在截图里的顺序）, `source`
- OCR 原始值：`date`, `amount`, `merchant`, `balance`, `sign_guessed`, `date_guessed`
- `status`：`pending` 待核对 / `confirmed` 已入库（不设「忽略」）

**transactions**：确认入库的交易
- `id`, `ocr_row_id`（可追溯到原截图）
- CSV 各列：`date`, `weekday`, `account`, `currency`, `amount`, `category`, `description`
- `confirmed_at`, `updated_at`
- 入库后只能改 `description`（描述），其他字段（包括种类）要在提交前核对好

**exports** / **export_items**：导出记录
- 每次导出一条（时间、文件名、条数），以及这次导出了哪些交易
- 「已导出」= 至少导出过一次

**login_events**：登录记录，`at`, `ip`, `user_agent`, `success`（失败的也记）

**sessions**：登录会话，存 token 哈希、创建 / 过期时间、IP，可以让所有会话下线

## 判断「已有」的规则

1. 截图哈希已存在 → 不跑 OCR，取出那张图的识别结果
2. 新截图 → OCR，先在这一批截图之间去重（同现在的 `build_records`）
3. 每一行和 `ocr_rows` 比对，用 **OCR 原始值**（不是改过的值），按数量计（同一天两笔一样的交易要分别对上）：
   - 完全一致且已入库 → 跳过，折叠显示
   - 完全一致且待核对 → 合并到原来那一行，不重复建
   - 日期和金额一致、商户不同 → 标「疑似重复」，由我判断
   - 其他 → 新的待核对行
4. 比对时带上来源，不同来源之间不比
5. 日期是推测的行（`date_guessed`），只比屏幕上看得到的部分（日、时间、金额、商户）

## 页面

- **登录页**：只有密码框。密码哈希放在环境变量里，连续失败后暂时锁定
- **识别页**（现在的主页）：上传 → 显示待核对的行（包括以前没提交的）→ 勾选 → 提交入库
- **交易页**：已入库交易，按来源和日期筛选，只能改描述，点一行能看到原截图
- **导出页**：选择交易导出 CSV，默认勾选未导出的，标出已导出过的

## 实施步骤

- [x] 1. 数据库与图片存储（WebP、哈希）
- [x] 2. 比对逻辑，测试写进 `tests/test_logic.py`
- [x] 3. 前端：识别页流程改造、交易页
- [x] 4. 导出页与导出记录
- [x] 5. 登录、会话、登录日志
- [ ] 6. 部署：Dockerfile、数据目录挂载、备份；更新 README 和 CLAUDE.md 里「不联网」的说法

## 实施时补充的细节

- `images.parsed`：存解析器的原始结果（JSON），同一张图再传时直接用它，不用再 OCR。同一张图换了来源上传时会重新识别
- `ocr_rows.suspect_of`：疑似重复时指向那一行；`ocr_rows.draft`：待核对时的修改（JSON），提交时才写进 `transactions`
- `login_events.reason`：`ok` / `bad_password` / `locked` / `not_configured`。只有 `bad_password` 计入锁定：连续 5 次（中间没有成功）且都在 15 分钟内，锁定到第一次失败后 15 分钟
- 密码哈希格式 `scrypt:n:r:p:salt:hash`，用 `python -m txreader.auth` 生成；`TXREADER_AUTH=off` 关闭登录（只用于本机）
- 会话 30 天过期，cookie 为 HttpOnly + SameSite=Lax，HTTPS 下加 Secure
- 「疑似重复」的行默认不勾选；确认是重复的就删除该待核对行（没有「忽略」状态，删掉后再传同一张图会重新出现）
- 导出的 CSV 在服务器生成，格式不变

## 以后再定

- 登录 IP：先直接记录连接过来的地址。如果以后在程序前面加反向代理（Caddy、Nginx 等），要配置成读取代理转发过来的真实 IP
- 部署后再用 Tailscale 挡一层
- 云主机至少 1–2GB 内存（OCR 比较吃内存）
