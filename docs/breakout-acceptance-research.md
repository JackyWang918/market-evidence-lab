# 突破后价格重心是否被接受

此研究现已集成到 `market-evidence-lab`，不再使用 Codex 自动化。程序负责数据采集、研究计算、每日监听与数据查看。

## 页面与后台

- `/breakout-research/`：监听总览、四资产切换、自动采集开关、手动采集队列、突破事件、历史对照、最近执行记录。
- `/breakout-research/data/`：按资产分页查看有效 UTC 日 K。
- `/breakout-research/events/<id>/`：统一突破起点、逐日收盘保持率、回撤底部、创新高、确认后的收益与回撤。
- `/breakout-research/runs/<id>/`：每轮状态、覆盖范围及 CSV、完整报告、SHA-256 与异常数据清单下载。
- `/system/schedules/`：现有调度列表中增加“突破接受度每日研究”。

每日默认北京时间 08:20（Asia/Shanghai），数据库保存启用状态与下一执行时间。到期任务由现有 `run_scheduler` 执行；也可只运行本研究 worker。两种执行器共用行锁、队列与活动任务唯一约束，避免同时重复执行。手动按钮仅创建队列任务，不在网页请求中运行历史回测。暂停开关只停止自动触发，手动队列仍可执行。

```bash
uv sync
uv run python manage.py migrate breakout_acceptance
uv run python manage.py run_scheduler
# 或只运行四资产研究后台（不会执行项目中其他研究任务）
uv run python manage.py run_breakout_research_worker
```

页面会展示后台在线状态。后台进程需持续运行；Web 服务不承担定时执行。若进程在采集过程中被强制终止，运行会保留为“运行中”，应核查进程后由管理员将该任务标为失败再重试；第一版不自动抢占尚可能活跃的任务。

## 首次快照与复现

已核实的首次原始响应被校验 SHA-256 后复制进项目，后续不依赖 `/home/jacky/breakout-acceptance-research`。

```bash
# 仅首次迁移历史快照时使用；已经完成，无需重复运行
uv run python manage.py import_breakout_history --source /home/jacky/breakout-acceptance-research
# 项目内离线重算并保存一轮研究记录
uv run python manage.py collect_breakout_research --offline --end 2026-10-14
# 在线采集（--end 不填时使用当日 UTC 日期）
uv run python manage.py collect_breakout_research
```

项目内 `data/breakout_acceptance/raw/` 保存采集缓存，每轮 `data/breakout_acceptance/runs/<id>/` 保存独立原始文件、标准化 CSV、事件、逐日路径、结果、报告与来源清单。运行数据在 Git 中忽略，需与数据库一起备份。`--end` 是不包含的 UTC 日期，例 2026-10-14 只使用 10-13 及之前已完成日线。

所有计算来自本模块独立采集的 SOL、BNB、ZEC、XAU，不读取项目其他行情、资金、新闻或研究数据。数据库也对本模块资产添加约束。原始来源：Binance 现货 SOLUSDT/BNBUSDT/ZECUSDT；Dukascopy XAU/USD BID。来源与收盘口径固定，不跨来源补价。

## 研究口径

日/周突破窗口为此前 20 根最高价，月为此前 12 根；阈值为最高价加 0.1 倍此前 14 根简单 ATR。窗口全部 shift(1)。日/周/月冷却分别 20/4/2 根，起点为突破 K 的结束时刻。

确认观察 5 日/2 周/1 月，要求至少 80% 收盘保持在突破水平以上，观察低点不低于水平减 0.5 ATR，且有收盘超过突破 K 最高价。逐日路径最多 180 个自然日。ATR 冻结于突破前，各资产按自己的 ATR 归一化。周线复核只使用起点时已完成周 K。

后续收益从确认后下一根开盘起算，日线 20/60 根，周线 4/12 根，月线 3/6 根。分别记录收益、收盘路径最大回撤和日内最低价不利变动，不假设 K 内高低发生顺序，不把右删失结果填零。同资产历史非突破对照只从当时已经成熟的历史选取。

XAU 以周/月为主：已结束年份取年度日线，当前年份已结束月份取月度小时线并聚合 UTC 日线。本月归档滞后明确展示，来源末尾的半周不视为完整周。零量占位忽略，异常 OHLC 隔离，污染的周/月与确认/结果窗口不进入统计。

## 验证和局限

```bash
uv run python manage.py test apps.breakout_acceptance --settings=config.test_settings
uv run python manage.py makemigrations --check --dry-run
```

验证包括历史前缀不变性、扰动未来不改变过去、已完成周期过滤、数据源滞后、污染周期隔离、收益回撤手算、固定资产白名单、计划时区、到期单次领取、队列去重、失败不发布结果、页面与 CSRF。

2023 年以后的结果只是历史时间切片，不是前瞻验证。黄金滞后及早期异常、短历史、小样本、重叠事件、对照复用、市场状态差异影响解释。没有计入成交成本、资金费及持仓约束；只做事件研究和回测，不接入真实交易。每日任务保持规则版本固定，不按新结果优化参数。

## 图表研究页

`/breakout-research/charts/` 为单独的图表页面。支持四资产、近 90 天/一年/两年/完整历史和突破尺度切换。
价格收盘线与日 K 可切换，点击突破标记或选择历史事件可联动收盘/回撤路径、累计保持率、创新高和确认后的收益/回撤柱图。
历史组间收益与最大回撤分开画图，并显示相应样本数。只有完整结果窗口才有柱图，不将未成熟结果画成零。
突破起点使用中性标记，不把后来确认的接受状态画成当时已知信号；路径上标出固定确认窗口，后续跟踪不改写历史接受状态。
图表页面仅查询已发布研究快照，不触发采集或改变研究规则。黄金显示来源滞后并默认周 K 突破，可选择月 K。
