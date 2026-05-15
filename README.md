# 美股职业做T扫描器 V23.1

职业级 VWAP / ORB / Hot Pool 做T系统

适用于：

* Railway
* Flask
* Telegram Bot
* 美股做T / 日内交易 / 波段观察

---

# V23.1 核心功能

## 1. 专业做T雷达 `/run-t-radar`

系统自动筛选：

* VWAP
* ORB
* RVOL
* 振幅
* OBV资金流
* 趋势结构
* Gap风险
* 财报风险

输出：

* 做T分数
* A/B/C级
* 做T策略
* 做T止损
* 波段止损
* 低吸区
* 高抛区

---

## 2. 实时做T确认 `/run-t-live`

实时监控：

* VWAP回踩
* ORB突破
* ORB失败
* 放量启动
* 假突破
* 趋势确认

适合：

* 开盘后
* 盘中做T
* 低吸确认
* 突破确认

---

## 3. 今日热点池 `/run-hot-pool`

自动发现：

* AI热点
* 爆量股
* 新闻股
* 财报股
* 高波动股

热点条件：

* RVOL
* 日振幅
* 成交额
* 波动性

V23.1 已升级：

* 不再发送“暂无热点”刷屏
* 防重复发送
* 冷却锁保护

---

# 做T等级说明

## A级

重点做T。

必须满足：

* VWAP健康
* ORB突破
* RVOL强
* 趋势强
* 没有重大事件风险

---

## B级

轻仓做T。

代表：

* 条件不错
* 但未完全确认
* 或量能不足

---

## C级

只观察。

代表：

* VWAP未确认
* ORB未突破
* RVOL太低
* Gap风险
* 财报风险

---

# 推荐观察池

## A级核心观察池

```python
CORE_STOCKS = [
    "IONQ",
    "APP",
    "AFRM",
    "PLTR",
    "NVDA",
    "TSLA",
    "AMD",
    "META",
    "AMZN",
    "CRM",
]
```

---

## B级热点观察池

```python
HOT_STOCKS = [
    "SOUN",
    "LUNR",
    "SMCI",
    "SOFI",
    "TEM",
]
```

---

# 职业做T时间参数

## Hot Pool

市场热点扫描：

```python
schedule.every().day.at("20:20").do(run_hot_pool)
schedule.every().day.at("00:00").do(run_hot_pool)
schedule.every().day.at("03:10").do(run_hot_pool)
```

---

## T Radar

主扫描：

```python
schedule.every().day.at("20:50").do(run_t_radar)
schedule.every().day.at("22:00").do(run_t_radar)
schedule.every().day.at("03:00").do(run_t_radar)
```

---

## T Live

实时确认：

```python
schedule.every().day.at("22:15").do(run_t_live)
schedule.every().day.at("23:00").do(run_t_live)
schedule.every().day.at("01:00").do(run_t_live)
```

---

# 防重复发送锁（V23.1）

已加入：

* Hot Pool 冷却锁
* T Radar 冷却锁
* T Live 冷却锁

避免：

* Telegram刷屏
* Railway重复执行
* 浏览器重复触发

---

# 环境变量

```env
TELEGRAM_BOT_TOKEN=你的bot token
TELEGRAM_CHAT_ID=你的chat id
PORT=5000

MAX_WORKERS=20
BATCH_SIZE=50
BATCH_SLEEP=1.2

ACCOUNT_SIZE=5000
MAX_RISK_PER_TRADE=0.02
MAX_POSITION_RATIO=0.30
```

---

# 做T风控参数

```python
T_A_MIN_RVOL = 1.5
T_B_MIN_RVOL = 0.9
T_NO_TRADE_RVOL = 0.6
T_NO_CHASE_DIST_VWAP = 3.0
```

---

# 简短交易记录系统

## 买入

```text
/b/IONQ/54.2/20/vwap
```

---

## 卖出

```text
/s/IONQ/57.1/20
```

---

# Setup分类

| Setup    | 意思     |
| -------- | ------ |
| vwap     | VWAP回踩 |
| orb      | ORB突破  |
| dip      | 低吸     |
| breakout | 突破     |
| swing    | 波段     |
| scalp    | 超短     |
| news     | 新闻     |
| earn     | 财报     |

---

# Railway 部署

## Start Command

```bash
python main.py
```

---

## requirements.txt

必须包含：

```text
flask
pandas
numpy
yfinance
requests
schedule
pytz
```

---

# 推荐使用流程

## 20:20

/run-hot-pool

查看今日热点。

---

## 20:50

/run-t-radar

筛选今日重点。

---

## 22:15

/run-t-live

等待真正进场。

---

## 23:00

/run-t-live

观察二波机会。

---

## 03:00

/run-t-radar

筛选次日预备股。

---

# V23.1 核心理念

不是预测市场。

而是：

* 跟随资金
* 过滤垃圾机会
* 只做高质量波动
* 避免财报黑天鹅
* 等待确认后出手

真正职业做T：

重点不是“买”。

而是：

“什么时候不做”。
