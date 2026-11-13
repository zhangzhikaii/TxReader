# 截图解析器（txreader/parsers/）

每个来源一个解析器：`parse_boxes(boxes, img) -> (list[Tx], warnings)`，在 `__init__.py` 的 `PARSERS` 注册。

```
wechat.py    微信「零钱明细」列表；收支靠金额颜色区分（橙色为收入）
boc.py       中国银行单笔「明细」页（每张一笔）
icbc.py      工商银行「收支明细」列表；看不到月份标题时按星期推断月份
suica.py     Apple 钱包 Suica 单笔详情页
saison.py    セゾン卡 App「明細」列表；日期是利用日，没有年份和时间
receipt.py   纸质小票照片（各家版式不同）；按文本框倾斜角配对「合計」与金额
```

## 约定

- 每个解析器顶部的 docstring 画出该截图的版式，**改解析器前先看它**；示例里的商户名用「某某商户」这类占位，不要出现真实人名
- 正负号或年份是推出来的，设 `sign_guessed` / `year_guessed`（年 / 月用 `models.resolve_date` 补全）
- 去重不在解析器里做：`records.build_records` 只去掉**不同截图之间**的重复（滚动截图会重叠），同一张截图里两笔一样的都保留；有余额时用余额区分
- 不要打开 `samples/` 里的截图；调试时请用户提供打码后的截图或描述版式。样例测试只看通过 / 失败数量：`.venv/bin/python -m pytest -q --tb=no tests/test_samples.py`

## 新增一个来源时要改

1. `txreader/parsers/<source>.py`，并在 `__init__.py` 的 `PARSERS` 注册
2. `static/index.html` 的 `<select id="source">`，以及 `static/js/recognize.js` 的 `SOURCES`（上传提示、默认账户 / 币种 / 种类）
3. `README.md` 的来源表格；本文件的列表
4. 滚动列表类截图：加入 `tests/test_samples.py` 的 `LIST_SOURCES`
