# 美股爆发扫描器 V7 部署包

这个包已经包含：
- 200 支美股扫描池
- V7 评分：突破 + 放量 + 吸筹 + 主力资金 + 新闻 + 盘前确认
- Telegram 推送
- Web API：`/`, `/api/health`, `/run-scan`, `/run-scan-local`
- Railway 可部署
- 可选内置定时扫描（默认关闭）

## 1) 部署到 Railway

把这整个文件夹上传到 GitHub，然后在 Railway：
1. New Project
2. Deploy from GitHub Repo
3. 选你的 repo
4. 等自动 build

Railway 官方文档说明，可以直接从 GitHub 部署 Python/Flask 服务；Cron 也可在服务设置里配置 crontab 表达式。citeturn669746search9turn669746search0

## 2) 必填 Variables

在 Railway 的 Variables 里加入：

```bash
TELEGRAM_BOT_TOKEN=你的bot token
TELEGRAM_CHAT_ID=你的chat id
ENABLE_TELEGRAM=true
TIMEZONE=Asia/Kuala_Lumpur
```

## 3) 建议再加的 Variables

```bash
INTERNAL_SCAN_TOKEN=你自定义一个密钥
ENABLE_INTERNAL_SCHEDULER=false
MAX_WORKERS=10
PREMARKET_TOP_CANDIDATES=25
NEWS_TOP_CANDIDATES=40
```

说明：
- `INTERNAL_SCAN_TOKEN`：保护 `/run-scan`
- `ENABLE_INTERNAL_SCHEDULER=false`：默认先关闭，避免你刚部署时重复跑

## 4) 测试网址

部署后打开：

```bash
https://你的域名/
https://你的域名/api/health
https://你的域名/run-scan?token=你的密钥
https://你的域名/run-scan-local
```

- `/`：服务在线状态
- `/api/health`：健康检查
- `/run-scan`：立即扫描并发 Telegram
- `/run-scan-local`：立即扫描，但只在网页显示，不发 Telegram

## 5) 定时扫描两种方式

### 方式 A：用 Railway Cron（更稳）
Railway 文档说明可以在服务的 Settings 里填写 Cron Schedule。citeturn669746search0

这个包本身是 Web Service，所以更简单的做法是：
- 继续保留 web service 常驻
- 用外部定时器或第二个 cron service 去请求 `/run-scan?token=你的密钥`

### 方式 B：开启内置定时器（比较省事）
Variables 设成：

```bash
ENABLE_INTERNAL_SCHEDULER=true
TIMEZONE=Asia/Kuala_Lumpur
```

会在每天：
- 04:00
- 22:00

自动跑扫描。

注意：这方式简单，但不如平台原生 cron 稳定；服务重启时，扫描会依赖服务是否已恢复。

## 6) 盘前数据说明

`yfinance.download(..., prepost=True)` 官方文档说明可以把盘前/盘后数据包含进结果。citeturn669746search3turn669746search7

所以 V7 的逻辑是：
- 先扫 200 支日线
- 再只对高分股补跑新闻与盘前
- 这样速度不会太慢

## 7) Telegram 说明

Telegram Bot API 官方说明 Bot API 是 HTTP-based interface，所以这个包是直接用 `sendMessage` 发通知。citeturn669746search2turn669746search10

## 8) 现在这包包含什么

主名单会推送：
- 股票代码
- 等级 / 分数
- 5%潜力标签
- 收盘涨幅
- 量比
- 距离突破
- 吸筹等级
- 主力资金分
- 新闻条数
- 盘前变化
- 明天买点
- 不追价区
- 止损 / 止盈
- 行动策略

提前预警名单会推送：
- 吸筹 A / B
- 距离突破
- 量比
- 趋势
- 新闻条数

## 9) 你接下来最可能要改的地方

### 想更保守：
```bash
MIN_AVG_DOLLAR_VOLUME=8000000
VOLUME_RATIO_MIN=2.2
BREAKOUT_DISTANCE_PCT=4
```

### 想更激进：
```bash
MIN_AVG_DOLLAR_VOLUME=2000000
VOLUME_RATIO_MIN=1.5
BREAKOUT_DISTANCE_PCT=6
```

## 10) 备注

- 盘前数据与新闻有时会因为数据源限制而为空，这时程序会给 0 分，不会直接报错。
- 这套是“提高命中率”的扫描工具，不是保证第二天一定涨 5%。
