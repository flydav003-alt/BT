"""
kline_scorer.py
===============
台股 K線評分引擎 — 完整移植自 HTML 版 K線分析系統
評分邏輯、指標計算、權重設定與 HTML 版完全一致。

【Colab 使用方式】
直接在 cell 設定參數後執行：

    STOCKS   = "8069,4768,3707"
    TOKEN    = "YOUR_TOKEN"
    OUTPUT   = "kline_report.html"
    STRATEGY = "auto"
    PERIOD   = 60

    run_batch(stocks=STOCKS, token=TOKEN, output=OUTPUT,
              strategy=STRATEGY, period=PERIOD)

【命令列使用方式（非 Colab 環境）】
    python kline_scorer.py --stocks 8069,4768 --token YOUR_TOKEN --output report.html
    python kline_scorer.py --csv otc_list.csv  --token YOUR_TOKEN --output report.html
"""

import requests
import pandas as pd
import numpy as np
import sys
import time
import math
from datetime import datetime, timedelta
from typing import Optional

# ═══════════════════════════════════════
#  策略額外加分函數（對應 HTML bonusCond）
# ═══════════════════════════════════════

def _bonus_breakout(data: list) -> float:
    """突破追強：紅K額外 +8"""
    last = data[-1]
    return 8.0 if last["close"] > last["open"] else 0.0


def _bonus_pullback(data: list) -> float:
    """回檔承接：乖離率在 -5%~+2% 額外 +10"""
    n = len(data)
    if n < 20:
        return 0.0
    ma20 = sum(d["close"] for d in data[n - 20:n]) / 20
    bias = (data[n - 1]["close"] - ma20) / ma20 * 100
    return 10.0 if -5 < bias < 2 else 0.0


def _bonus_reversal(data: list) -> float:
    """反轉搶彈：RSI 超賣深度額外加分（RSI<25→+15, RSI<35→+8）"""
    n = len(data)
    if n < 15:
        return 0.0
    closes = [d["close"] for d in data]
    gains, losses = [], []
    for i in range(1, len(closes)):
        dv = closes[i] - closes[i - 1]
        gains.append(dv if dv > 0 else 0)
        losses.append(-dv if dv < 0 else 0)
    idx = n - 2
    ag = sum(gains[idx - 13:idx + 1]) / 14
    al = sum(losses[idx - 13:idx + 1]) / 14
    rsi_val = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    if rsi_val < 25:
        return 15.0
    elif rsi_val < 35:
        return 8.0
    return 0.0


# ═══════════════════════════════════════
#  策略模式設定（與 HTML 版完全對應）
# ═══════════════════════════════════════
STRATEGY_PROFILES = {
    "balanced": {
        "name": "平衡模式",
        "w": {"ma": 1.0, "rsi": 1.0, "kd": 1.0, "macd": 1.0, "vol": 1.0, "pattern": 1.0, "chip": 1.0},
        "rsiOBWarn": 70, "rsiOSBuy": 35,
        "bonus_fn": None,
    },
    "breakout": {
        "name": "突破追強",
        "w": {"ma": 1.3, "rsi": 0.6, "kd": 0.7, "macd": 1.4, "vol": 1.8, "pattern": 1.3, "chip": 1.2},
        "rsiOBWarn": 85, "rsiOSBuy": 30,
        "bonus_fn": _bonus_breakout,
    },
    "pullback": {
        "name": "回檔承接",
        "w": {"ma": 1.4, "rsi": 1.3, "kd": 1.3, "macd": 0.7, "vol": 0.6, "pattern": 1.1, "chip": 1.3},
        "rsiOBWarn": 65, "rsiOSBuy": 40,
        "bonus_fn": _bonus_pullback,
    },
    "reversal": {
        "name": "反轉搶彈",
        "w": {"ma": 0.5, "rsi": 1.8, "kd": 1.8, "macd": 0.7, "vol": 1.2, "pattern": 1.6, "chip": 0.7},
        "rsiOBWarn": 60, "rsiOSBuy": 45,
        "bonus_fn": _bonus_reversal,
    },
}

# ═══════════════════════════════════════
#  指標計算函數（與 HTML 版一致）
# ═══════════════════════════════════════

def calc_ma(closes: list, period: int) -> list:
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = sum(closes[i - period + 1:i + 1]) / period
    return result


def calc_rsi(closes: list, period: int = 14) -> list:
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(d if d > 0 else 0)
        losses.append(-d if d < 0 else 0)

    result = [None] * len(closes)
    for i in range(period, len(closes)):
        idx = i - 1
        ag = sum(gains[idx - period + 1:idx + 1]) / period
        al = sum(losses[idx - period + 1:idx + 1]) / period
        result[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return result


def calc_kdj(data: list, period: int = 9) -> list:
    pk, pd_ = 50.0, 50.0
    result = [None] * len(data)
    for i in range(period - 1, len(data)):
        sl = data[i - period + 1:i + 1]
        hi = max(d["max"] for d in sl)
        lo = min(d["min"] for d in sl)
        rsv = 50.0 if hi == lo else (data[i]["close"] - lo) / (hi - lo) * 100
        kv = pk * 2 / 3 + rsv / 3
        dv = pd_ * 2 / 3 + kv / 3
        pk, pd_ = kv, dv
        result[i] = {"k": kv, "d": dv}
    return result


def calc_bb(closes: list, period: int = 20, m: float = 2.0) -> list:
    ma_arr = calc_ma(closes, period)
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        mean = ma_arr[i]
        sl = closes[i - period + 1:i + 1]
        std = math.sqrt(sum((x - mean) ** 2 for x in sl) / period)
        result[i] = {"upper": mean + m * std, "lower": mean - m * std, "mid": mean}
    return result


def calc_avg_vol(volumes: list, period: int = 20) -> list:
    result = [None] * len(volumes)
    for i in range(period - 1, len(volumes)):
        result[i] = sum(volumes[i - period + 1:i + 1]) / period
    return result


def calc_macd(closes: list, fast: int = 12, slow: int = 26, sig: int = 9) -> list:
    def ema(arr, p):
        k = 2 / (p + 1)
        e = arr[0]
        result = []
        for i, v in enumerate(arr):
            if i == 0:
                result.append(e)
            else:
                e = v * k + e * (1 - k)
                result.append(e)
        return result

    ef = ema(closes, fast)
    es = ema(closes, slow)
    ml = [ef[i] - es[i] for i in range(len(closes))]
    sig_line = ema(ml[slow - 1:], sig)

    result = [None] * len(closes)
    for i in range(slow - 1, len(closes)):
        si = i - (slow - 1)
        m = ml[i]
        s = sig_line[si - (sig - 1)] if si >= sig - 1 else None
        result[i] = {"macd": m, "signal": s, "hist": m - s if s is not None else None}
    return result


def calc_atr(data: list, period: int = 14) -> list:
    result = []
    for i, d in enumerate(data):
        if i == 0:
            result.append(d["max"] - d["min"])
        else:
            tr = max(d["max"] - d["min"],
                     abs(d["max"] - data[i - 1]["close"]),
                     abs(d["min"] - data[i - 1]["close"]))
            if i < period:
                result.append(tr)
            else:
                sl = data[i - period + 1:i + 1]
                avg = sum(max(b["max"] - b["min"],
                              abs(b["max"] - data[i - period + j]["close"]),
                              abs(b["min"] - data[i - period + j]["close"]))
                          for j, b in enumerate(sl) if j > 0) / period
                result.append(avg)
    return result


def detect_rsi_divergence(data: list, rsi_arr: list) -> Optional[dict]:
    n = len(data)
    if n < 20:
        return None
    wb = 5
    price_highs, price_lows = [], []
    rsi_highs, rsi_lows = [], []

    for i in range(wb, n - wb):
        rsi_val = rsi_arr[i]
        if rsi_val is None:
            continue
        window_data = data[i - wb:i + wb + 1]
        window_rsi = rsi_arr[i - wb:i + wb + 1]
        window_rsi_valid = [r for r in window_rsi if r is not None]

        is_phigh = all(d["close"] <= data[i]["close"] for j, d in enumerate(window_data) if j != wb)
        is_plow  = all(d["close"] >= data[i]["close"] for j, d in enumerate(window_data) if j != wb)
        is_rhigh = all(r <= rsi_val for r in window_rsi_valid)
        is_rlow  = all(r >= rsi_val for r in window_rsi_valid)

        if is_phigh and is_rhigh:
            price_highs.append({"i": i, "price": data[i]["close"]})
            rsi_highs.append({"i": i, "val": rsi_val})
        if is_plow and is_rlow:
            price_lows.append({"i": i, "price": data[i]["close"]})
            rsi_lows.append({"i": i, "val": rsi_val})

    if (len(price_highs) >= 2 and len(rsi_highs) >= 2 and
            price_highs[-1]["price"] > price_highs[-2]["price"] and
            rsi_highs[-1]["val"] < rsi_highs[-2]["val"]):
        return {"type": "bear",
                "text": f"RSI頂背離：價格創新高（{price_highs[-1]['price']:.2f}）但RSI走低，潛在見頂訊號"}

    if (len(price_lows) >= 2 and len(rsi_lows) >= 2 and
            price_lows[-1]["price"] < price_lows[-2]["price"] and
            rsi_lows[-1]["val"] > rsi_lows[-2]["val"]):
        return {"type": "bull",
                "text": f"RSI底背離：價格創新低（{price_lows[-1]['price']:.2f}）但RSI走高，潛在止跌訊號"}

    return None


# ═══════════════════════════════════════
#  資料抓取
# ═══════════════════════════════════════

def fetch_price(stock_id: str, token: str = "", days: int = 400) -> list:
    end_date = datetime.today()
    start_date = end_date - timedelta(days=days)
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
    }
    if token:
        params["token"] = token

    r = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, timeout=20)
    j = r.json()
    if j.get("status") != 200 or not j.get("data"):
        raise ValueError(f"{stock_id}: {j.get('msg', '查無資料')}")

    return [
        {
            "date": d["date"],
            "open": float(d["open"]),
            "max": float(d["max"]),
            "min": float(d["min"]),
            "close": float(d["close"]),
            "Trading_Volume": float(d["Trading_Volume"]),
        }
        for d in j["data"]
    ]


def fetch_chip(stock_id: str, token: str = "") -> list:
    end_date = datetime.today()
    start_date = end_date - timedelta(days=60)
    params = {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "data_id": stock_id,
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
    }
    if token:
        params["token"] = token
    try:
        r = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, timeout=20)
        j = r.json()
        return j.get("data", [])
    except Exception:
        return []


def process_chip(chip_raw: list) -> list:
    if not chip_raw:
        return []
    normalized = []
    for row in chip_raw:
        buy  = float(row.get("buy",  row.get("buy_amount",  0)) or 0)
        sell = float(row.get("sell", row.get("sell_amount", 0)) or 0)
        name = str(row.get("name", row.get("investors", "")))
        normalized.append({"date": row["date"], "name": name, "diff": buy - sell})

    by_date = {}
    for r in normalized:
        dt = r["date"]
        if dt not in by_date:
            by_date[dt] = {"date": dt, "foreign": 0.0, "trust": 0.0, "dealer": 0.0}
        if "Foreign_Investor" in r["name"] or "外資" in r["name"]:
            by_date[dt]["foreign"] += r["diff"]
        elif "Investment_Trust" in r["name"] or "投信" in r["name"]:
            by_date[dt]["trust"] += r["diff"]
        elif "Dealer" in r["name"] or "自營" in r["name"]:
            by_date[dt]["dealer"] += r["diff"]

    return sorted(by_date.values(), key=lambda x: x["date"])


# ═══════════════════════════════════════
#  主評分引擎（與 HTML runAnalysis 完全一致）
# ═══════════════════════════════════════

def run_analysis(data: list, chip_processed: list, strategy_key: str = "balanced") -> dict:
    n = len(data)
    if n < 20:
        return {"score": 50, "verdict": "資料不足", "signals": [], "error": "資料不足20筆"}

    strat = STRATEGY_PROFILES[strategy_key]
    w = strat["w"]
    rsiOB = strat["rsiOBWarn"]
    rsiOS = strat["rsiOSBuy"]

    signals = []
    bull_pts = 0.0
    bear_pts = 0.0

    closes  = [d["close"] for d in data]
    volumes = [d["Trading_Volume"] for d in data]

    last, prev = data[n - 1], data[n - 2]

    # ── 計算所有指標 ──
    ma5a  = calc_ma(closes, 5)
    ma10a = calc_ma(closes, 10)
    ma20a = calc_ma(closes, 20)
    ma60a = calc_ma(closes, 60)
    rsi_a = calc_rsi(closes, 14)
    kdj_a = calc_kdj(data, 9)
    bb_a  = calc_bb(closes, 20)
    avgv_a = calc_avg_vol(volumes, 20)
    macd_a = calc_macd(closes)
    atr_a  = calc_atr(data, 14)

    lma5  = ma5a[n - 1];  pma5  = ma5a[n - 2]
    lma10 = ma10a[n - 1]; pma10 = ma10a[n - 2]
    lma20 = ma20a[n - 1]
    lma60 = ma60a[n - 1]
    lrsi  = rsi_a[n - 1]; prsi  = rsi_a[n - 2]
    lkdj  = kdj_a[n - 1]; pkdj  = kdj_a[n - 2]
    lbb   = bb_a[n - 1]
    lavg  = avgv_a[n - 1]
    lmacd = macd_a[n - 1]; pmacd = macd_a[n - 2]
    lATR  = atr_a[n - 1]

    body       = abs(last["close"] - last["open"])
    range_     = last["max"] - last["min"]
    up_shadow  = last["max"] - max(last["close"], last["open"])
    down_shadow = min(last["close"], last["open"]) - last["min"]
    is_bull = last["close"] > last["open"]
    is_bear = last["close"] < last["open"]
    vol_ratio = (last["Trading_Volume"] / lavg) if lavg else 1.0

    # ── 均線 ──
    if lma5 and lma10 and lma20 and lma60:
        if lma5 > lma10 > lma20 > lma60:
            signals.append({"type": "bull", "cat": "均線", "text": "多頭排列（MA5>10>20>60），主趨勢強勁向上"})
            bull_pts += 15 * w["ma"]
        elif lma5 > lma10 > lma20:
            signals.append({"type": "bull", "cat": "均線", "text": "短期多頭排列（MA5>10>20），趨勢偏多"})
            bull_pts += 8 * w["ma"]
        elif lma5 < lma10 < lma20:
            signals.append({"type": "bear", "cat": "均線", "text": "空頭排列（MA5<10<20），短線趨勢向下"})
            bear_pts += 12 * w["ma"]

    if lma20 and last["close"] > lma20: bull_pts += 5 * w["ma"]
    if lma60 and last["close"] > lma60: bull_pts += 5 * w["ma"]

    if pma5 and lma5 and pma10 and lma10:
        if pma5 < pma10 and lma5 > lma10:
            signals.append({"type": "bull", "cat": "均線", "text": "MA5黃金交叉MA10，短線轉多"})
            bull_pts += 10 * w["ma"]
        elif pma5 > pma10 and lma5 < lma10:
            signals.append({"type": "bear", "cat": "均線", "text": "MA5死亡交叉MA10，短線轉空"})
            bear_pts += 10 * w["ma"]

    if lma20:
        if last["close"] > lma20 and prev["close"] <= lma20:
            signals.append({"type": "bull", "cat": "均線", "text": f"剛突破MA20（{lma20:.2f}），短線動能轉強"})
            bull_pts += 8 * w["ma"]
        elif last["close"] < lma20 and prev["close"] >= lma20:
            signals.append({"type": "bear", "cat": "均線", "text": f"跌破MA20（{lma20:.2f}），注意下方支撐"})
            bear_pts += 8 * w["ma"]

    # ── RSI ──
    if lrsi is not None:
        if prsi is not None and prsi < rsiOS and lrsi > rsiOS:
            signals.append({"type": "bull", "cat": "RSI", "text": f"RSI由超賣區回升至{lrsi:.1f}，短線反彈機率高"})
            bull_pts += 10 * w["rsi"]
        elif lrsi < rsiOS:
            signals.append({"type": "bull", "cat": "RSI", "text": f"RSI={lrsi:.1f} 超賣區，有反彈空間"})
            bull_pts += 6 * w["rsi"]
        elif prsi is not None and prsi > rsiOB and lrsi < rsiOB:
            signals.append({"type": "bear", "cat": "RSI", "text": "RSI從超買區回落，注意短線壓力"})
            bear_pts += 8 * w["rsi"]
        elif lrsi > 80:
            signals.append({"type": "bear", "cat": "RSI", "text": f"RSI={lrsi:.1f} 極度超買，短線回調風險高"})
            bear_pts += 8 * w["rsi"]
        elif lrsi > rsiOB:
            signals.append({"type": "bear", "cat": "RSI", "text": f"RSI={lrsi:.1f} 超買區，追高謹慎"})
            bear_pts += 4 * w["rsi"]
        elif lrsi >= 50:
            signals.append({"type": "bull", "cat": "RSI", "text": f"RSI={lrsi:.1f}，處於偏多健康區間"})
            bull_pts += 4 * w["rsi"]
        else:
            signals.append({"type": "neutral", "cat": "RSI", "text": f"RSI={lrsi:.1f}，偏弱，觀望"})

    # ── RSI 背離 ──
    div = detect_rsi_divergence(data, rsi_a)
    if div:
        signals.append({"type": div["type"], "cat": "RSI背離", "text": div["text"]})
        if div["type"] == "bull":
            bull_pts += 14 * w["rsi"]
        else:
            bear_pts += 14 * w["rsi"]

    # ── KD ──
    if lkdj and pkdj:
        if pkdj["k"] < pkdj["d"] and lkdj["k"] > lkdj["d"] and lkdj["k"] < 50:
            signals.append({"type": "bull", "cat": "KD", "text": f"低檔KD金叉（K={lkdj['k']:.1f}），短線強烈買入訊號"})
            bull_pts += 12 * w["kd"]
        elif pkdj["k"] < pkdj["d"] and lkdj["k"] > lkdj["d"]:
            signals.append({"type": "bull", "cat": "KD", "text": f"KD金叉（K={lkdj['k']:.1f}），短線偏多"})
            bull_pts += 7 * w["kd"]
        elif pkdj["k"] > pkdj["d"] and lkdj["k"] < lkdj["d"] and lkdj["k"] > 80:
            signals.append({"type": "bear", "cat": "KD", "text": f"高檔KD死叉（K={lkdj['k']:.1f}），短線賣出訊號"})
            bear_pts += 12 * w["kd"]
        elif pkdj["k"] > pkdj["d"] and lkdj["k"] < lkdj["d"]:
            signals.append({"type": "bear", "cat": "KD", "text": f"KD死叉（K={lkdj['k']:.1f}），短線壓力"})
            bear_pts += 6 * w["kd"]
        elif lkdj["k"] > lkdj["d"] and lkdj["k"] < 80:
            bull_pts += 3 * w["kd"]

    # ── MACD ──
    if lmacd and pmacd:
        if lmacd["macd"] is not None:
            if lmacd["macd"] > 0:
                bull_pts += 5 * w["macd"]
            else:
                bear_pts += 5 * w["macd"]

            if pmacd["macd"] is not None:
                if pmacd["macd"] < 0 and lmacd["macd"] >= 0:
                    signals.append({"type": "bull", "cat": "MACD", "text": "MACD零軸上穿，中線轉多確認"})
                    bull_pts += 12 * w["macd"]
                elif pmacd["macd"] > 0 and lmacd["macd"] <= 0:
                    signals.append({"type": "bear", "cat": "MACD", "text": "MACD零軸下穿，中線轉空"})
                    bear_pts += 12 * w["macd"]

        if (lmacd["signal"] is not None and pmacd["signal"] is not None and
                lmacd["macd"] is not None and pmacd["macd"] is not None):
            if pmacd["macd"] < pmacd["signal"] and lmacd["macd"] > lmacd["signal"]:
                signals.append({"type": "bull", "cat": "MACD", "text": "MACD黃金交叉Signal，買入訊號"})
                bull_pts += 8 * w["macd"]
            elif pmacd["macd"] > pmacd["signal"] and lmacd["macd"] < lmacd["signal"]:
                signals.append({"type": "bear", "cat": "MACD", "text": "MACD死亡交叉Signal，賣出訊號"})
                bear_pts += 8 * w["macd"]

        if lmacd["hist"] is not None and pmacd["hist"] is not None:
            if lmacd["hist"] > 0 and lmacd["hist"] > pmacd["hist"]:
                signals.append({"type": "bull", "cat": "MACD", "text": "MACD柱狀放大，多方動能增強"})
                bull_pts += 4 * w["macd"]
            elif lmacd["hist"] < 0 and lmacd["hist"] < pmacd["hist"]:
                signals.append({"type": "bear", "cat": "MACD", "text": "MACD柱狀向下擴大，空方動能增強"})
                bear_pts += 4 * w["macd"]

    # ── 量價 ──
    if is_bull and vol_ratio > 1.5:
        signals.append({"type": "bull", "cat": "量價", "text": f"大量紅K（量比均{vol_ratio:.1f}倍），多方積極攻擊"})
        bull_pts += 10 * w["vol"]
    elif is_bull and vol_ratio > 1.2:
        signals.append({"type": "bull", "cat": "量價", "text": f"量增紅K（量比均{vol_ratio:.1f}倍），有效上漲"})
        bull_pts += 6 * w["vol"]
    elif is_bear and vol_ratio > 1.5:
        signals.append({"type": "bear", "cat": "量價", "text": f"大量黑K（量比均{vol_ratio:.1f}倍），賣壓沉重"})
        bear_pts += 10 * w["vol"]
    elif vol_ratio < 0.5:
        signals.append({"type": "neutral", "cat": "量價", "text": f"量大幅萎縮（均量{vol_ratio * 100:.0f}%），等待方向確認"})

    # ── 布林 ──
    if lbb:
        if last["close"] < lbb["lower"]:
            signals.append({"type": "bull", "cat": "布林", "text": f"跌破布林下軌（{lbb['lower']:.2f}），超跌反彈機率高"})
            bull_pts += 7 * w["pattern"]
        elif last["close"] > lbb["upper"]:
            signals.append({"type": "bear", "cat": "布林", "text": "突破布林上軌，短線超漲追高謹慎"})
            bear_pts += 4 * w["pattern"]
        bw = (lbb["upper"] - lbb["lower"]) / lbb["mid"]
        if bw < 0.04:
            signals.append({"type": "neutral", "cat": "布林", "text": f"布林通道極度收窄（{bw * 100:.1f}%），即將出現方向性突破"})

    # ── K線型態 ──
    prev2 = data[n - 3] if n >= 3 else None
    r60 = data[-min(60, n):]
    hi60 = max(d["max"] for d in r60)
    lo60 = min(d["min"] for d in r60)
    pos60 = (last["close"] - lo60) / (hi60 - lo60 + 0.0001)
    pos_tag = "低檔" if pos60 < 0.3 else ("高檔" if pos60 > 0.7 else "整理區")
    vol_tag = "爆量" if vol_ratio > 1.5 else ("量增" if vol_ratio > 1.2 else ("縮量" if vol_ratio < 0.7 else "平量"))

    def effectiveness(pattern_dir, min_vol=1.0):
        vol_ok = vol_ratio >= min_vol
        if pattern_dir == "bull":
            if pos60 < 0.35 and vol_ok:
                return {"level": "高", "note": f"出現在低檔（近60日{pos60*100:.0f}%位），{vol_tag}，訊號可信度高"}
            elif pos60 < 0.35:
                return {"level": "中", "note": f"出現在低檔，但量能不足（{vol_tag}），需等量增確認"}
            elif pos60 > 0.65:
                return {"level": "低", "note": f"出現在高檔（近60日{pos60*100:.0f}%位），反轉型態高檔效果差，謹慎"}
            else:
                return {"level": "中", "note": "整理區出現，需配合均線突破確認"}
        else:
            if pos60 > 0.65 and vol_ok:
                return {"level": "高", "note": f"出現在高檔，{vol_tag}，看跌訊號可信度高"}
            elif pos60 > 0.65:
                return {"level": "中", "note": "出現在高檔，量能普通，觀察後續走勢"}
            elif pos60 < 0.35:
                return {"level": "低", "note": "出現在低檔，空頭型態低檔效果差"}
            else:
                return {"level": "中", "note": "整理區出現，觀察方向選擇"}

    def push_pattern(sig_type, name, direction, min_vol=1.0, extra_pts=0):
        eff = effectiveness(direction, min_vol)
        pts_map = {"高": 12 + extra_pts, "中": 7 + extra_pts, "低": 3 + extra_pts}
        pts = pts_map[eff["level"]] * w["pattern"]
        eff_mark = "⭐" if eff["level"] == "高" else ("▷" if eff["level"] == "中" else "△")
        signals.append({"type": sig_type, "cat": "K線型態",
                         "text": f"「{name}」{eff_mark} 有效性{eff['level']}｜{eff['note']}"})
        if direction == "bull":
            nonlocal bull_pts
            bull_pts += pts
        else:
            nonlocal bear_pts
            bear_pts += pts

    if range_ > 0 and body > 0:
        if down_shadow >= body * 2 and up_shadow <= body * 0.5:
            push_pattern("bull", "錘頭線", "bull", 1.2)
        if up_shadow >= body * 2 and down_shadow <= body * 0.5 and pos60 < 0.4:
            push_pattern("bull", "倒錘頭", "bull", 1.0)
    if range_ > 0 and up_shadow >= range_ * 0.7 and down_shadow <= range_ * 0.05:
        push_pattern("bear", "墓碑十字", "bear", 1.2)
    if range_ > 0 and body <= range_ * 0.08:
        cross_type = "bear" if pos60 > 0.6 else "neutral"
        signals.append({"type": cross_type, "cat": "K線型態",
                         "text": f"「十字線」多空僵持｜位置：{pos_tag}（{pos60*100:.0f}%），{'高檔出現需警惕' if pos60 > 0.6 else '等待下一根確認方向'}"})
    if is_bull and range_ > 0 and body >= range_ * 0.7 and vol_ratio >= 1.2:
        push_pattern("bull", "長紅K", "bull", 1.2, 2)
    if is_bear and range_ > 0 and body >= range_ * 0.7 and vol_ratio >= 1.2:
        push_pattern("bear", "長黑K", "bear", 1.2, 2)
    if is_bear and body > 0 and up_shadow >= body * 2 and down_shadow <= body * 0.5:
        push_pattern("bear", "射擊之星", "bear", 1.0)

    if is_bull and prev["close"] < prev["open"] and last["open"] <= prev["close"] and last["close"] >= prev["open"]:
        push_pattern("bull", "多頭吞噬", "bull", 1.3, 3)
    if is_bear and prev["close"] > prev["open"] and last["open"] >= prev["close"] and last["close"] <= prev["open"]:
        push_pattern("bear", "空頭吞噬", "bear", 1.3, 3)

    if is_bull and prev["close"] < prev["open"]:
        prev_mid = (prev["open"] + prev["close"]) / 2
        if last["open"] < prev["close"] and last["close"] > prev_mid and last["close"] < prev["open"]:
            push_pattern("bull", "穿頭破腳", "bull", 1.1)
    if is_bear and prev["close"] > prev["open"]:
        prev_mid = (prev["open"] + prev["close"]) / 2
        if last["open"] > prev["close"] and last["close"] < prev_mid and last["close"] > prev["open"]:
            push_pattern("bear", "烏雲罩頂", "bear", 1.1)

    if prev2:
        b0, b1, b2 = prev2, prev, last
        if (b0["close"] > b0["open"] and b1["close"] > b1["open"] and b2["close"] > b2["open"] and
                b1["close"] > b0["close"] and b2["close"] > b1["close"]):
            push_pattern("bull", "紅三兵", "bull", 1.0, 5)
        if (b0["close"] < b0["open"] and b1["close"] < b1["open"] and b2["close"] < b2["open"] and
                b1["close"] < b0["close"] and b2["close"] < b1["close"]):
            push_pattern("bear", "黑三鴉", "bear", 1.0, 5)

        b0_body = abs(b0["close"] - b0["open"])
        b1_body = abs(b1["close"] - b1["open"])
        b1_range = b1["max"] - b1["min"]
        if (b0["close"] < b0["open"] and b1_range > 0 and b1_body < b1_range * 0.3 and
                b2["close"] > b2["open"] and b2["close"] >= (b0["open"] + b0["close"]) / 2):
            push_pattern("bull", "晨星", "bull", 1.2, 5)
        if (b0["close"] > b0["open"] and b1_range > 0 and b1_body < b1_range * 0.3 and
                b2["close"] < b2["open"] and b2["close"] <= (b0["open"] + b0["close"]) / 2):
            push_pattern("bear", "暮星", "bear", 1.2, 5)

    if is_bear and vol_ratio > 2.5:
        signals.append({"type": "bear", "cat": "K線型態",
                         "text": f"「爆量長黑」量比均量{vol_ratio:.1f}倍 + 收黑｜主力出貨訊號"})
        bear_pts += 12 * w["pattern"]

    if vol_ratio < 0.4 and abs(last["close"] - prev["close"]) / prev["close"] < 0.005:
        signals.append({"type": "neutral", "cat": "K線型態",
                         "text": f"「量縮止跌」成交量萎縮至均量{vol_ratio*100:.0f}%，跌勢趨緩"})
        if pos60 < 0.4:
            bull_pts += 5 * w["pattern"]

    if is_bull and vol_ratio > 2.0 and n > 5:
        recent5_max = max(d["max"] for d in data[-5:])
        if last["close"] > recent5_max:
            signals.append({"type": "bull", "cat": "K線型態",
                             "text": f"「爆量突破」量比均量{vol_ratio:.1f}倍，同時創近期新高｜強烈追多訊號"})
            bull_pts += 13 * w["pattern"]

    # ── 支撐壓力 ──
    r20 = data[-20:]
    rhi = max(d["max"] for d in r20)
    rlo = min(d["min"] for d in r20)
    dhi = (rhi - last["close"]) / last["close"] * 100
    dlo = (last["close"] - rlo) / last["close"] * 100
    signals.append({"type": "bear" if dhi < 3 else "neutral", "cat": "支撐壓力",
                     "text": f"近20日壓力：{rhi:.2f}（+{dhi:.1f}%）／支撐：{rlo:.2f}（-{dlo:.1f}%）"})
    if dhi < 3:
        bear_pts += 5

    # ── 籌碼面 ──
    if chip_processed:
        last5 = chip_processed[-5:]
        foreign_sum = sum(d["foreign"] for d in last5)
        trust_sum   = sum(d["trust"]   for d in last5)
        if foreign_sum > 0:
            signals.append({"type": "bull", "cat": "籌碼", "text": f"外資近5日買超 {foreign_sum/1000:.0f}張"})
            bull_pts += 8 * w["chip"]
        elif foreign_sum < 0:
            signals.append({"type": "bear", "cat": "籌碼", "text": f"外資近5日賣超 {abs(foreign_sum)/1000:.0f}張"})
            bear_pts += 8 * w["chip"]
        if trust_sum > 0:
            signals.append({"type": "bull", "cat": "籌碼", "text": f"投信近5日買超 {trust_sum/1000:.0f}張"})
            bull_pts += 7 * w["chip"]
        elif trust_sum < 0:
            signals.append({"type": "bear", "cat": "籌碼", "text": f"投信近5日賣超 {abs(trust_sum)/1000:.0f}張"})
            bear_pts += 7 * w["chip"]

    # ── 短線強度（收盤位置）──
    day_range = last["max"] - last["min"] or 0.01
    close_position = (last["close"] - last["min"]) / day_range

    if close_position >= 0.8:
        bull_pts += 5
    elif close_position >= 0.6:
        bull_pts += 3
    elif close_position >= 0.2:
        bear_pts += 3
    else:
        bear_pts += 5

    # 上影線意圖
    upper_ratio = up_shadow / day_range if day_range > 0 else 0
    lower_ratio = down_shadow / day_range if day_range > 0 else 0
    if upper_ratio > 0.35 and is_bull:
        bear_pts += 3
    elif upper_ratio > 0.35:
        bear_pts += 5
    elif lower_ratio > 0.35 and is_bull:
        bull_pts += 5
    elif lower_ratio > 0.35:
        bull_pts += 3
    elif upper_ratio < 0.1 and is_bull:
        bull_pts += 4

    # 縮量整理後量增
    if n >= 6:
        prev5_vols = [d["Trading_Volume"] for d in data[-6:-1]]
        prev5_avg  = sum(prev5_vols) / 5
        prev3_avg  = sum(prev5_vols[-3:]) / 3
        is_consolidating = prev3_avg < prev5_avg * 0.85
        if is_consolidating and vol_ratio > 1.4:
            if is_bull:
                bull_pts += 12
            else:
                bear_pts += 8

    # 實體收縮
    if n >= 4:
        bodies = [abs(d["close"] - d["open"]) for d in data[-4:]]
        shrinking = bodies[0] > bodies[1] > bodies[2] > bodies[3]
        avg_body3 = (bodies[0] + bodies[1] + bodies[2]) / 3
        if shrinking and bodies[3] < avg_body3 * 0.5:
            bull_pts += 3

    # 週線強弱
    week5  = sum(closes[-5:])  / min(5,  n)
    week20 = sum(closes[-20:]) / min(20, n)
    if week5 > week20:
        bull_pts += 6
    else:
        bear_pts += 6

    # 月線強弱
    if n >= 60:
        month60 = sum(closes[-60:]) / 60
        if last["close"] > month60:
            bull_pts += 5
        else:
            bear_pts += 5

    # 均線糾結
    if lma5 and lma10 and lma20:
        ma_high = max(lma5, lma10, lma20)
        ma_low  = min(lma5, lma10, lma20)
        ma_spread = (ma_high - ma_low) / last["close"] * 100
        if ma_spread < 1.5:
            ma_center = (lma5 + lma10 + lma20) / 3
            price_above = last["close"] > ma_center
            if price_above and is_bull:
                bull_pts += 6
            if not price_above and is_bear:
                bear_pts += 6
        elif ma_spread < 3.0:
            bull_pts += 2

    # 支撐壓力強度（測試次數）
    if range_ > 0:
        res_tolerance = (rhi - rlo) * 0.015
        sup_tolerance = (rhi - rlo) * 0.015
        res_count = sum(1 for d in data if abs(d["max"] - rhi) <= res_tolerance)
        sup_count = sum(1 for d in data if abs(d["min"] - rlo) <= sup_tolerance)
        if res_count >= 3:
            bear_pts += 3
        if sup_count >= 3:
            bull_pts += 3

    # ── 策略 bonus（對應 HTML bonusCond）──
    bonus_fn = strat.get("bonus_fn")
    bonus_pts = bonus_fn(data) if bonus_fn else 0.0

    # ── 最終評分 ──
    net = bull_pts - bear_pts + bonus_pts
    max_possible = 200
    score = round(50 + (net / max_possible) * 50)
    score = max(0, min(100, score))

    # 自動策略建議
    auto_strategy = "balanced"
    if lrsi is not None and lrsi < 35 and lkdj and lkdj["k"] < 30:
        auto_strategy = "reversal"
    elif vol_ratio > 1.5 and is_bull and lmacd and lmacd["macd"] and lmacd["macd"] > 0 and lmacd["hist"] and lmacd["hist"] > 0:
        auto_strategy = "breakout"
    elif lma20 and abs(last["close"] - lma20) / lma20 < 0.03 and lrsi and 40 < lrsi < 60:
        auto_strategy = "pullback"
    elif lma5 and lma10 and lma20 and lma5 > lma10 > lma20:
        auto_strategy = "breakout"

    if score >= 78:
        verdict = "📈 強烈偏多"
    elif score >= 62:
        verdict = "📊 偏多觀察"
    elif score >= 45:
        verdict = "⚖️ 中性觀望"
    elif score >= 30:
        verdict = "⚠️ 偏空謹慎"
    else:
        verdict = "🔻 強烈偏空"

    return {
        "score": score,
        "verdict": verdict,
        "bull_pts": round(bull_pts, 1),
        "bear_pts": round(bear_pts, 1),
        "bonus_pts": round(bonus_pts, 1),
        "net": round(net, 1),
        "signals": signals,
        "auto_strategy": auto_strategy,
        "last_close": last["close"],
        "last_date": last["date"],
        "lrsi": lrsi,
        "lkdj": lkdj,
        "lma20": lma20,
        "vol_ratio": vol_ratio,
        "close_pos": close_position,
        "rhi": rhi,
        "rlo": rlo,
    }


# ═══════════════════════════════════════
#  HTML 報告產生
# ═══════════════════════════════════════

def generate_html(results: list, csv_source: str = "") -> str:
    today_str = datetime.today().strftime("%Y-%m-%d %H:%M")

    def score_color(s):
        if s >= 78: return "#ff4d6d"
        if s >= 62: return "#ff9f40"
        if s >= 45: return "#e8b84b"
        if s >= 30: return "#4a9eff"
        return "#00c896"

    def sig_html(signals, max_show=5):
        type_color = {"bull": "#ff4d6d", "bear": "#00c896", "neutral": "#6a85a8"}
        html = ""
        for s in signals[:max_show]:
            col = type_color.get(s["type"], "#6a85a8")
            html += f'<span style="display:inline-block;font-size:.65rem;padding:1px 5px;border-radius:3px;margin:1px;background:rgba(255,255,255,.05);color:{col};border-left:2px solid {col};">[{s["cat"]}] {s["text"]}</span><br>'
        return html

    rows_html = ""
    for rank, r in enumerate(results, 1):
        if "error" in r:
            rows_html += f"""
            <tr>
              <td style="padding:12px 8px;text-align:center;color:#4a6080;">{rank}</td>
              <td style="padding:12px 8px;font-family:var(--mono);font-weight:700;">{r['stock_id']}</td>
              <td colspan="8" style="padding:12px 8px;color:#ff4d6d;font-size:.78rem;">❌ {r['error']}</td>
            </tr>"""
            continue

        sc = r["score"]
        col = score_color(sc)
        circ = round(sc / 100 * 276.46, 1)

        rows_html += f"""
        <tr style="border-bottom:1px solid #1e2d4a;">
          <td style="padding:12px 8px;text-align:center;color:#4a6080;font-family:'IBM Plex Mono',monospace;">{rank}</td>
          <td style="padding:12px 8px;">
            <div style="font-family:'IBM Plex Mono',monospace;font-size:1rem;font-weight:700;color:#d4dff0;">{r['stock_id']}</div>
            <div style="font-size:.68rem;color:#4a6080;">{r.get('name','')}</div>
          </td>
          <td style="padding:12px 8px;text-align:center;">
            <div style="position:relative;width:52px;height:52px;display:inline-flex;align-items:center;justify-content:center;">
              <svg width="52" height="52" viewBox="0 0 52 52" style="position:absolute;top:0;left:0;">
                <circle cx="26" cy="26" r="22" fill="none" stroke="#1a2340" stroke-width="5"/>
                <circle cx="26" cy="26" r="22" fill="none" stroke="{col}" stroke-width="5"
                  stroke-dasharray="{circ} {276.46 - circ}"
                  stroke-dashoffset="34.6" stroke-linecap="round"
                  transform="rotate(-90 26 26)"/>
              </svg>
              <span style="font-family:'IBM Plex Mono',monospace;font-size:.85rem;font-weight:700;color:{col};z-index:1;">{sc}</span>
            </div>
          </td>
          <td style="padding:12px 8px;font-size:.8rem;color:{col};font-weight:600;">{r['verdict']}</td>
          <td style="padding:12px 8px;font-family:'IBM Plex Mono',monospace;font-size:.9rem;color:#d4dff0;">{r['last_close']:.2f}</td>
          <td style="padding:12px 8px;font-family:'IBM Plex Mono',monospace;font-size:.8rem;color:#a78bfa;">{f"{r['lrsi']:.1f}" if r.get('lrsi') else '-'}</td>
          <td style="padding:12px 8px;font-family:'IBM Plex Mono',monospace;font-size:.8rem;color:#ff9f40;">{f"{r['lkdj']['k']:.1f}" if r.get('lkdj') else '-'}</td>
          <td style="padding:12px 8px;font-family:'IBM Plex Mono',monospace;font-size:.8rem;color:#e8b84b;">{r['vol_ratio']:.2f}x</td>
          <td style="padding:12px 16px 12px 8px;font-size:.7rem;line-height:1.6;color:#d4dff0;max-width:360px;">
            {sig_html(r['signals'])}
          </td>
        </tr>"""

    ok_count = sum(1 for r in results if "error" not in r)
    bull_count = sum(1 for r in results if r.get("score", 0) >= 62 and "error" not in r)
    avg_score = round(sum(r["score"] for r in results if "error" not in r) / max(ok_count, 1))

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>K線評分排名 {today_str}</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{
  --bg:#080c14;--s1:#0d1220;--s2:#131a2e;--border:#1e2d4a;
  --gold:#e8b84b;--cyan:#29c5c5;--text:#d4dff0;--muted:#4a6080;
  --font:'Noto Sans TC',sans-serif;--mono:'IBM Plex Mono',monospace;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);padding:24px;}}
h1{{font-size:1.4rem;font-weight:700;color:var(--gold);font-family:var(--mono);letter-spacing:2px;margin-bottom:4px;}}
.meta{{font-size:.75rem;color:var(--muted);margin-bottom:20px;font-family:var(--mono);}}
.stat-bar{{display:flex;gap:20px;margin-bottom:24px;flex-wrap:wrap;}}
.stat-card{{background:var(--s1);border:1px solid var(--border);border-radius:8px;padding:12px 20px;}}
.stat-val{{font-size:1.6rem;font-weight:700;font-family:var(--mono);color:var(--gold);}}
.stat-key{{font-size:.7rem;color:var(--muted);margin-top:2px;}}
table{{width:100%;border-collapse:collapse;background:var(--s1);border-radius:10px;overflow:hidden;border:1px solid var(--border);}}
thead tr{{background:var(--s2);border-bottom:2px solid var(--border);}}
thead th{{padding:12px 8px;text-align:left;font-size:.68rem;font-weight:700;letter-spacing:2px;color:var(--muted);text-transform:uppercase;}}
thead th:first-child{{padding-left:16px;}}
thead th:last-child{{padding-right:16px;}}
tbody tr:hover{{background:rgba(255,255,255,.02);}}
.footer{{margin-top:20px;font-size:.68rem;color:var(--muted);text-align:center;line-height:1.8;}}
@media(max-width:768px){{body{{padding:12px;}}table{{font-size:.75rem;}}}}
</style>
</head>
<body>
<h1>📊 K線評分排名報告</h1>
<div class="meta">產生時間：{today_str} ｜ 來源：{csv_source} ｜ 評分引擎：Python K線分析系統（與 HTML 版一致）</div>

<div class="stat-bar">
  <div class="stat-card"><div class="stat-val">{len(results)}</div><div class="stat-key">本次掃描股數</div></div>
  <div class="stat-card"><div class="stat-val" style="color:#ff4d6d;">{bull_count}</div><div class="stat-key">偏多（分數≥62）</div></div>
  <div class="stat-card"><div class="stat-val" style="color:#e8b84b;">{avg_score}</div><div class="stat-key">平均K線分數</div></div>
  <div class="stat-card"><div class="stat-val" style="color:#4a9eff;">{ok_count}</div><div class="stat-key">成功取得資料</div></div>
</div>

<table>
<thead>
  <tr>
    <th style="padding-left:16px;width:48px;">#</th>
    <th>股票</th>
    <th style="text-align:center;width:72px;">K線分</th>
    <th>訊號</th>
    <th>收盤</th>
    <th>RSI</th>
    <th>KD-K</th>
    <th>量比</th>
    <th style="padding-right:16px;">主要訊號</th>
  </tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>

<div class="footer">
  ⚠️ 本報告由 Python K線評分引擎自動產生，評分邏輯與 HTML 互動版完全一致。<br>
  所有分析僅供參考，不構成買賣建議。投資人應自行判斷風險。
</div>
</body>
</html>"""
    return html


# ═══════════════════════════════════════
#  批次執行函數（Colab 友善介面）
# ═══════════════════════════════════════

def run_batch(
    stocks: str = "",
    csv_path: str = "",
    token: str = "",
    output: str = "kline_report.html",
    strategy: str = "auto",
    period: int = 60,
    delay: float = 0.8,
    download: bool = True,
) -> list:
    """
    Colab 主入口，直接呼叫即可，不需要 argparse。

    參數
    ----
    stocks   : 逗號分隔股票代號，例如 "8069,4768,3707"
    csv_path : CSV 檔案路徑（有 stock_id 欄位），與 stocks 擇一
    token    : FinMind API Token
    output   : 輸出 HTML 檔名
    strategy : "auto" / "balanced" / "breakout" / "pullback" / "reversal"
    period   : 取幾日K線（建議 60）
    delay    : 每支股票間隔秒數
    download : 在 Colab 自動下載報告（True/False）

    回傳
    ----
    results list（可進一步做 pd.DataFrame 分析）
    """
    # ── 取得股票清單 ──
    stock_ids = []
    csv_source = ""

    if csv_path:
        df = pd.read_csv(csv_path)
        id_col = "stock_id" if "stock_id" in df.columns else df.columns[0]
        stock_ids = [str(int(x)) for x in df[id_col].dropna().tolist()]
        csv_source = csv_path.split("/")[-1]
        print(f"✅ 讀取 CSV：{csv_path}，共 {len(stock_ids)} 支")
    elif stocks:
        stock_ids = [s.strip() for s in stocks.split(",") if s.strip()]
        csv_source = "手動輸入"
    else:
        raise ValueError("請提供 stocks 或 csv_path 參數")

    if not token:
        print("⚠️  未提供 Token，API 可能有速率限制")

    print(f"📊 開始評分，共 {len(stock_ids)} 支，期間 {period} 日，策略：{strategy}")
    print("=" * 60)

    results = []
    for i, sid in enumerate(stock_ids, 1):
        print(f"[{i:2d}/{len(stock_ids)}] {sid} 處理中...", end=" ", flush=True)
        try:
            raw_data  = fetch_price(sid, token)
            chip_raw  = fetch_chip(sid, token)
            chip_proc = process_chip(chip_raw)
            sliced    = raw_data[-period:] if len(raw_data) >= period else raw_data

            use_strategy = strategy
            if use_strategy == "auto":
                pre = run_analysis(sliced, chip_proc, "balanced")
                use_strategy = pre.get("auto_strategy", "balanced")

            result = run_analysis(sliced, chip_proc, use_strategy)
            result["stock_id"] = sid
            result["strategy_used"] = use_strategy
            results.append(result)
            print(f"分數={result['score']:3d}  {result['verdict']}  策略={STRATEGY_PROFILES[use_strategy]['name']}")

        except Exception as e:
            print(f"❌ 失敗：{e}")
            results.append({"stock_id": sid, "error": str(e), "score": 0})

        if i < len(stock_ids):
            time.sleep(delay)

    # 依分數排序
    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    # 輸出 HTML
    html = generate_html(results, csv_source)
    with open(output, "w", encoding="utf-8") as f:
        f.write(html)

    print("=" * 60)
    ok = [r for r in results if "error" not in r]
    print(f"✅ 報告輸出：{output}")
    print(f"📊 成功：{len(ok)}/{len(results)} 支")
    if ok:
        print(f"🏆 最高分：{ok[0]['stock_id']} = {ok[0]['score']} 分")
        print(f"📈 偏多（≥62）：{sum(1 for r in ok if r['score'] >= 62)} 支")
        print(f"📉 平均分：{sum(r['score'] for r in ok) / len(ok):.1f}")

    # Colab 自動下載
    if download:
        try:
            from google.colab import files
            files.download(output)
        except ImportError:
            pass  # 非 Colab 環境，跳過

    return results


# ═══════════════════════════════════════
#  命令列入口（非 Colab 環境使用）
# ═══════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="K線評分批次引擎")
    parser.add_argument("--csv",      type=str, default="",  help="建議清單 CSV 路徑")
    parser.add_argument("--stocks",   type=str, default="",  help="股票代號，逗號分隔，例如 8069,4768")
    parser.add_argument("--token",    type=str, default="",  help="FinMind API Token")
    parser.add_argument("--output",   type=str, default="kline_report.html", help="輸出 HTML 路徑")
    parser.add_argument("--strategy", type=str, default="auto",
                        choices=["auto", "balanced", "breakout", "pullback", "reversal"],
                        help="策略模式（auto = 每支股票自動選）")
    parser.add_argument("--period",   type=int, default=60,  help="K線天數（60/120/240）")
    parser.add_argument("--delay",    type=float, default=0.8, help="每支股票間隔秒數")
    args = parser.parse_args()

    if not args.csv and not args.stocks:
        print("❌ 請指定 --csv 或 --stocks")
        sys.exit(1)

    run_batch(
        stocks=args.stocks,
        csv_path=args.csv,
        token=args.token,
        output=args.output,
        strategy=args.strategy,
        period=args.period,
        delay=args.delay,
        download=False,
    )


if __name__ == "__main__":
    # 偵測是否在 Colab / Jupyter 環境
    in_jupyter = False
    try:
        shell = get_ipython().__class__.__name__
        if shell in ("ZMQInteractiveShell", "Shell"):
            in_jupyter = True
    except NameError:
        pass

    if in_jupyter:
        # ════════════════════════════════════════════
        #  ▼ Colab 使用者：在這裡修改參數後執行 ▼
        # ════════════════════════════════════════════
        STOCKS   = "8069,4768,3707"   # 股票代號，逗號分隔
        CSV_PATH = ""                 # 或填 CSV 路徑，例如 "otc_list.csv"
        TOKEN    = ""                 # FinMind API Token
        OUTPUT   = "kline_report.html"
        STRATEGY = "auto"             # auto / balanced / breakout / pullback / reversal
        PERIOD   = 60
        DELAY    = 0.8

        results = run_batch(
            stocks=STOCKS,
            csv_path=CSV_PATH,
            token=TOKEN,
            output=OUTPUT,
            strategy=STRATEGY,
            period=PERIOD,
            delay=DELAY,
            download=True,
        )
    else:
        main()
