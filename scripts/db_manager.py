"""
db_manager.py
=============
SQLite 資料庫管理模組

Schema：
  - daily_picks   : 每日推薦紀錄（含 T+3/T+5 回測資料）
  - stock_names   : 股票代號 ↔ 中文名稱對照
  - run_log       : 每次執行紀錄（偵錯用）

設計原則：
  - category（ETF/OTC/TSE）欄位完全隔離寫入
  - INSERT OR REPLACE 保證冪等（重跑不會重複）
  - portalocker 檔案鎖防止並發寫入衝突
"""

import sqlite3
import json
import time
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

try:
    import portalocker  # pip install portalocker
    HAS_PORTALOCKER = True
except ImportError:
    HAS_PORTALOCKER = False
    logging.warning("portalocker 未安裝，跳過檔案鎖（單一執行環境下安全）")

from config import DB_PATH, LOCK_FILE, BACKTEST_T3, BACKTEST_T5, BACKFILL_CALENDAR_BUFFER, HISTORY_DAYS

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
#  初始化資料庫
# ══════════════════════════════════════════════

def init_db() -> None:
    """建立所有資料表（若不存在）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
        -- ── 每日推薦紀錄 ──────────────────────────────────
        CREATE TABLE IF NOT EXISTS daily_picks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,               -- YYYY-MM-DD
            category        TEXT    NOT NULL,               -- ETF / OTC / TSE
            rank            INTEGER NOT NULL,               -- 當日排名
            stock_id        TEXT    NOT NULL,               -- 股票代號
            stock_name      TEXT    DEFAULT '',             -- 中文名稱
            kline_score     INTEGER DEFAULT 0,              -- K線分數 0~100
            verdict         TEXT    DEFAULT '',             -- 偏多/中性/偏空
            close_price     REAL    DEFAULT 0,              -- 收盤價
            rsi             REAL,                           -- RSI 值
            kd_k            REAL,                           -- KD-K 值
            vol_ratio       REAL,                           -- 量比
            strategy_used   TEXT    DEFAULT '',             -- 使用策略
            top_signals     TEXT    DEFAULT '[]',           -- 前3訊號 JSON
            -- 回測欄位
            t3_date         TEXT,                           -- T+3 日期
            t3_price        REAL,                           -- T+3 收盤價
            t3_pnl          REAL,                           -- T+3 損益%
            t5_date         TEXT,                           -- T+5 日期
            t5_price        REAL,                           -- T+5 收盤價
            t5_pnl          REAL,                           -- T+5 損益%
            created_at      TEXT    DEFAULT (datetime('now','localtime')),
            -- 同一天同一類別同一股票只能有一筆
            UNIQUE(date, category, stock_id)
        );

        -- ── 股票名稱對照表 ──────────────────────────────────
        CREATE TABLE IF NOT EXISTS stock_names (
            stock_id    TEXT PRIMARY KEY,
            stock_name  TEXT NOT NULL,
            updated_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        -- ── 執行紀錄（偵錯用）──────────────────────────────
        CREATE TABLE IF NOT EXISTS run_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at      TEXT    DEFAULT (datetime('now','localtime')),
            category    TEXT    NOT NULL,               -- ETF / OTC / TSE / BACKFILL
            status      TEXT    NOT NULL,               -- ok / error / skipped
            stocks_cnt  INTEGER DEFAULT 0,
            message     TEXT    DEFAULT ''
        );

        -- ── 價格快取（每次評分時順帶存入，供趨勢圖使用）────
        CREATE TABLE IF NOT EXISTS price_cache (
            stock_id    TEXT    NOT NULL,
            date        TEXT    NOT NULL,   -- YYYY-MM-DD
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL    NOT NULL,
            volume      REAL,
            updated_at  TEXT    DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (stock_id, date)
        );

        -- ── 索引 ────────────────────────────────────────────
        CREATE INDEX IF NOT EXISTS idx_picks_date     ON daily_picks(date);
        CREATE INDEX IF NOT EXISTS idx_picks_category ON daily_picks(category);
        CREATE INDEX IF NOT EXISTS idx_picks_stock    ON daily_picks(stock_id);
        CREATE INDEX IF NOT EXISTS idx_picks_t3       ON daily_picks(t3_price);
        CREATE INDEX IF NOT EXISTS idx_picks_t5       ON daily_picks(t5_price);
        CREATE INDEX IF NOT EXISTS idx_cache_stock    ON price_cache(stock_id);
        CREATE INDEX IF NOT EXISTS idx_cache_date     ON price_cache(date);
        """)
    logger.info(f"DB 初始化完成：{DB_PATH}")


# ══════════════════════════════════════════════
#  連線（帶 WAL 模式，減少寫入鎖衝突）
# ══════════════════════════════════════════════

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")    # 允許讀寫並行
    conn.execute("PRAGMA synchronous=NORMAL")  # 效能與安全平衡
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ══════════════════════════════════════════════
#  檔案鎖（最外層防護）
# ══════════════════════════════════════════════

class DBWriteLock:
    """Context manager：取得檔案鎖才能寫入 DB"""
    def __enter__(self):
        if HAS_PORTALOCKER:
            LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(LOCK_FILE, "w")
            portalocker.lock(self._fh, portalocker.LOCK_EX)
            logger.debug("DB 寫入鎖已取得")
        return self

    def __exit__(self, *_):
        if HAS_PORTALOCKER:
            portalocker.unlock(self._fh)
            self._fh.close()
            logger.debug("DB 寫入鎖已釋放")


# ══════════════════════════════════════════════
#  寫入每日推薦
# ══════════════════════════════════════════════

def upsert_daily_picks(picks: list[dict], category: str, trade_date: str) -> int:
    """
    將評分結果寫入 daily_picks。
    使用 INSERT OR REPLACE 保證冪等（重跑安全）。

    參數
    ----
    picks       : run_analysis 回傳結果列表（已排序）
    category    : 'ETF' / 'OTC' / 'TSE'
    trade_date  : 'YYYY-MM-DD'

    回傳
    ----
    成功寫入筆數
    """
    rows = []
    for rank, p in enumerate(picks, 1):
        if "error" in p:
            continue
        # 存全部訊號（前端 Modal 自行決定顯示幾筆）
        all_sigs = p.get("signals", [])
        sig_json = json.dumps(
            [{"type": s["type"], "cat": s["cat"], "text": s["text"]} for s in all_sigs],
            ensure_ascii=False
        )
        rows.append((
            trade_date, category, rank,
            str(p["stock_id"]),
            p.get("stock_name", ""),
            int(p.get("score", 0)),
            p.get("verdict", ""),
            float(p.get("last_close", 0)),
            p.get("lrsi"),
            p["lkdj"]["k"] if p.get("lkdj") else None,
            p.get("vol_ratio"),
            p.get("strategy_used", ""),
            sig_json,
        ))

    if not rows:
        return 0

    with DBWriteLock():
        with _connect() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO daily_picks
                    (date, category, rank, stock_id, stock_name,
                     kline_score, verdict, close_price, rsi, kd_k,
                     vol_ratio, strategy_used, top_signals)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, rows)

    logger.info(f"[{category}] 寫入 {len(rows)} 筆到 daily_picks（日期：{trade_date}）")
    return len(rows)


# ══════════════════════════════════════════════
#  更新股票名稱對照表
# ══════════════════════════════════════════════

def upsert_stock_names(name_map: dict[str, str]) -> None:
    """
    name_map = {"2330": "台積電", "8069": "元太", ...}
    """
    if not name_map:
        return
    rows = [(sid, name) for sid, name in name_map.items() if name]
    with DBWriteLock():
        with _connect() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO stock_names (stock_id, stock_name)
                VALUES (?, ?)
            """, rows)
    logger.info(f"更新股票名稱對照表：{len(rows)} 筆")


def get_stock_name(stock_id: str) -> str:
    """查詢股票中文名稱，找不到回傳空字串"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT stock_name FROM stock_names WHERE stock_id = ?", (stock_id,)
        ).fetchone()
    return row["stock_name"] if row else ""


# ══════════════════════════════════════════════
#  補填 T+3 / T+5 回測價格
# ══════════════════════════════════════════════

def get_pending_backfill() -> list[dict]:
    """
    取得所有「尚未補齊 T+3 或 T+5 價格」的紀錄。

    修正說明：
    - 原本用 BACKTEST_T3+1(4天) / BACKTEST_T5+1(6天) 日曆天，遇週末假日會過早觸發
    - 台股T+5最多跨2個週末 ≈ 9個日曆天，加上台灣假日需要更多緩衝
    - 改用 BACKFILL_CALENDAR_BUFFER=14 天統一判斷，確保T+5交易日真的到了再補填
    """
    cutoff = (date.today() - timedelta(days=BACKFILL_CALENDAR_BUFFER)).isoformat()

    with _connect() as conn:
        rows = conn.execute("""
            SELECT id, date, category, stock_id, close_price
            FROM daily_picks
            WHERE date <= ?
              AND (t3_price IS NULL OR t5_price IS NULL)
        """, (cutoff,)).fetchall()

    return [dict(r) for r in rows]


def update_backtest_prices(
    pick_id: int,
    t3_date: Optional[str], t3_price: Optional[float],
    t5_date: Optional[str], t5_price: Optional[float],
    base_price: float,
) -> None:
    """更新單筆紀錄的 T+3 / T+5 回測資料"""
    t3_pnl = round((t3_price - base_price) / base_price * 100, 2) if t3_price else None
    t5_pnl = round((t5_price - base_price) / base_price * 100, 2) if t5_price else None

    with DBWriteLock():
        with _connect() as conn:
            conn.execute("""
                UPDATE daily_picks
                SET t3_date  = COALESCE(?, t3_date),
                    t3_price = COALESCE(?, t3_price),
                    t3_pnl   = COALESCE(?, t3_pnl),
                    t5_date  = COALESCE(?, t5_date),
                    t5_price = COALESCE(?, t5_price),
                    t5_pnl   = COALESCE(?, t5_pnl)
                WHERE id = ?
            """, (t3_date, t3_price, t3_pnl,
                  t5_date, t5_price, t5_pnl,
                  pick_id))


# ══════════════════════════════════════════════
#  查詢函數（供 html_generator 使用）
# ══════════════════════════════════════════════

def get_latest_picks(top_n: int = 8) -> dict[str, list[dict]]:
    """
    取得最新一個交易日的三欄推薦資料。
    回傳 {"ETF": [...], "OTC": [...], "TSE": [...]}
    """
    result = {}
    with _connect() as conn:
        for cat in ("ETF", "OTC", "TSE"):
            # 取該類別最新日期
            latest = conn.execute(
                "SELECT MAX(date) as d FROM daily_picks WHERE category = ?", (cat,)
            ).fetchone()
            if not latest or not latest["d"]:
                result[cat] = []
                continue
            rows = conn.execute("""
                SELECT * FROM daily_picks
                WHERE category = ? AND date = ?
                ORDER BY rank ASC
                LIMIT ?
            """, (cat, latest["d"], top_n)).fetchall()
            result[cat] = [dict(r) for r in rows]

    return result


def get_history_picks(days: int = None) -> list[dict]:
    """
    取得最近 N 天的所有推薦紀錄（含回測欄位），依日期降序排列。
    days 預設使用 HISTORY_DAYS（90天），確保 T+5 資料完整可見。
    """
    if days is None:
        days = HISTORY_DAYS
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with _connect() as conn:
        rows = conn.execute("""
            SELECT dp.*, sn.stock_name as sn_name
            FROM daily_picks dp
            LEFT JOIN stock_names sn ON dp.stock_id = sn.stock_id
            WHERE dp.date >= ? AND dp.category != 'US'
            ORDER BY dp.date DESC, dp.category, dp.rank
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def get_latest_picks_us(top_n: int = 10) -> list[dict]:
    """
    取得美股（category='US'）最新一個交易日的推薦資料。
    回傳：[{...}, ...] 依 rank 升序
    """
    with _connect() as conn:
        latest = conn.execute(
            "SELECT MAX(date) as d FROM daily_picks WHERE category = 'US'"
        ).fetchone()
        if not latest or not latest["d"]:
            return []
        rows = conn.execute("""
            SELECT dp.*, COALESCE(sn.stock_name, dp.stock_name, '') as sn_name
            FROM daily_picks dp
            LEFT JOIN stock_names sn ON dp.stock_id = sn.stock_id
            WHERE dp.category = 'US' AND dp.date = ?
            ORDER BY dp.rank ASC
            LIMIT ?
        """, (latest["d"], top_n)).fetchall()
    return [dict(r) for r in rows]


def get_history_picks_us(days: int = None) -> list[dict]:
    """
    取得美股近 N 天歷史推薦紀錄。
    days 預設使用 HISTORY_DAYS（90天）。
    """
    if days is None:
        days = HISTORY_DAYS
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with _connect() as conn:
        rows = conn.execute("""
            SELECT dp.*, COALESCE(sn.stock_name, dp.stock_name, '') as sn_name
            FROM daily_picks dp
            LEFT JOIN stock_names sn ON dp.stock_id = sn.stock_id
            WHERE dp.category = 'US' AND dp.date >= ?
            ORDER BY dp.date DESC, dp.rank
        """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def get_win_rate_stats(days: Optional[int] = None, use_t5: bool = False) -> list[dict]:
    """
    計算歷史勝率統計。

    參數
    ----
    days    : 近幾天（None = 全部）
    use_t5  : True = T+5，False = T+3

    回傳（前30名，依勝率降序）
    ----
    [{"stock_id", "stock_name", "win_rate", "avg_pnl", "count"}, ...]
    """
    pnl_col  = "t5_pnl"  if use_t5 else "t3_pnl"
    date_col = "t5_date" if use_t5 else "t3_date"

    where_parts = [f"{pnl_col} IS NOT NULL"]
    params: list = []
    if days:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        where_parts.append("date >= ?")
        params.append(cutoff)

    where_clause = " AND ".join(where_parts)

    with _connect() as conn:
        rows = conn.execute(f"""
            SELECT
                dp.stock_id,
                COALESCE(sn.stock_name, dp.stock_name, '') as stock_name,
                COUNT(*)                                    as total_cnt,
                SUM(CASE WHEN {pnl_col} > 0 THEN 1 ELSE 0 END) as win_cnt,
                ROUND(AVG({pnl_col}), 2)                   as avg_pnl
            FROM daily_picks dp
            LEFT JOIN stock_names sn ON dp.stock_id = sn.stock_id
            WHERE {where_clause}
            GROUP BY dp.stock_id
            HAVING total_cnt >= 2
            ORDER BY CAST(win_cnt AS REAL)/total_cnt DESC, avg_pnl DESC
            LIMIT 30
        """, params).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["win_rate"] = round(d["win_cnt"] / d["total_cnt"] * 100, 1)
        result.append(d)
    return result


# ══════════════════════════════════════════════
#  執行紀錄
# ══════════════════════════════════════════════

def log_run(category: str, status: str, stocks_cnt: int = 0, message: str = "") -> None:
    with _connect() as conn:
        conn.execute("""
            INSERT INTO run_log (category, status, stocks_cnt, message)
            VALUES (?, ?, ?, ?)
        """, (category, status, stocks_cnt, message))
    logger.info(f"[run_log] {category} | {status} | {stocks_cnt} 筆 | {message}")


def get_last_run_date(category: str) -> Optional[str]:
    """取得該類別最後成功執行的日期（YYYY-MM-DD）"""
    with _connect() as conn:
        row = conn.execute("""
            SELECT MAX(date) as d FROM daily_picks WHERE category = ?
        """, (category,)).fetchone()
    return row["d"] if row else None


# ══════════════════════════════════════════════
#  價格快取（price_cache）
# ══════════════════════════════════════════════

def upsert_price_cache(stock_id: str, raw_data: list) -> int:
    """
    將 fetch_price() 回傳的 raw_data 最近 60 天存入 price_cache。
    raw_data 格式：[{"date":"YYYY-MM-DD","open":...,"max":...,"min":...,"close":...,"Trading_Volume":...}, ...]
    PRIMARY KEY (stock_id, date) → 重跑安全，不重複。
    回傳寫入筆數。
    """
    if not raw_data:
        return 0

    # 只存最近 60 天（足夠趨勢圖+回測，不囤太多）
    recent = raw_data[-60:] if len(raw_data) > 60 else raw_data

    rows = []
    for d in recent:
        dt = d.get("date") or d.get("Date")
        if not dt:
            continue
        rows.append((
            stock_id,
            str(dt)[:10],                          # 確保 YYYY-MM-DD
            d.get("open")  or d.get("Open"),
            d.get("max")   or d.get("High"),
            d.get("min")   or d.get("Low"),
            d.get("close") or d.get("Close"),
            d.get("Trading_Volume") or d.get("volume"),
        ))

    if not rows:
        return 0

    with DBWriteLock():
        with _connect() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO price_cache
                    (stock_id, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, rows)

    logger.debug(f"[price_cache] {stock_id}: 寫入 {len(rows)} 筆")
    return len(rows)


def get_price_trend(stock_id: str, days: int = 7) -> list[dict]:
    """
    從 price_cache 取得個股近 N 個交易日的收盤價趨勢。
    回傳：[{"date": "YYYY-MM-DD", "close": 85.9}, ...] 依日期升序
    """
    with _connect() as conn:
        rows = conn.execute("""
            SELECT date, open, high, low, close, volume
            FROM price_cache
            WHERE stock_id = ?
            ORDER BY date DESC
            LIMIT ?
        """, (stock_id, days)).fetchall()
    # 反轉成升序
    return [dict(r) for r in reversed(rows)]


def get_all_price_trends(stock_ids: list[str], days: int = 7) -> dict[str, list[dict]]:
    """
    批次取得多支股票的價格趨勢（一次 SQL）。
    回傳：{"3580": [{"date":..., "close":...}, ...], ...}
    """
    if not stock_ids:
        return {}

    placeholders = ",".join("?" * len(stock_ids))
    with _connect() as conn:
        rows = conn.execute(f"""
            SELECT stock_id, date, close
            FROM price_cache
            WHERE stock_id IN ({placeholders})
              AND date IN (
                  SELECT DISTINCT date FROM price_cache
                  ORDER BY date DESC LIMIT ?
              )
            ORDER BY stock_id, date ASC
        """, stock_ids + [days]).fetchall()

    result: dict[str, list[dict]] = {}
    for r in rows:
        sid = r["stock_id"]
        if sid not in result:
            result[sid] = []
        result[sid].append({"date": r["date"], "close": r["close"]})
    return result


# ══════════════════════════════════════════════
#  個股分數趨勢（供 Modal & 迷你折線圖使用）
# ══════════════════════════════════════════════

def get_score_trend(stock_id: str, days: int = 7) -> list[dict]:
    """
    取得單支股票近 N 天的 K線分數趨勢。
    回傳：[{"date": "YYYY-MM-DD", "score": 72}, ...] 依日期升序
    """
    cutoff = (date.today() - timedelta(days=days + 1)).isoformat()
    with _connect() as conn:
        rows = conn.execute("""
            SELECT date, kline_score as score
            FROM daily_picks
            WHERE stock_id = ? AND date >= ?
            ORDER BY date ASC
            LIMIT ?
        """, (stock_id, cutoff, days)).fetchall()
    return [dict(r) for r in rows]


def get_stock_price_history(stock_id: str, days: int = 10) -> list[dict]:
    """
    從 daily_picks 取得個股近 N 天收盤價（供 Modal 小折線圖使用）。
    回傳：[{"date": "YYYY-MM-DD", "close": 85.9, "score": 72}, ...] 依日期升序
    """
    cutoff = (date.today() - timedelta(days=days + 3)).isoformat()
    with _connect() as conn:
        rows = conn.execute("""
            SELECT date, close_price as close, kline_score as score
            FROM daily_picks
            WHERE stock_id = ? AND date >= ?
            ORDER BY date ASC
            LIMIT ?
        """, (stock_id, cutoff, days)).fetchall()
    return [dict(r) for r in rows]


def get_all_score_trends(days: int = 7) -> dict[str, list[dict]]:
    """
    批次取得所有「今日推薦」股票的近 N 交易日趨勢。
    - 價格來自 price_cache（每次評分主動存入，資料連續）
    - 分數來自 daily_picks LEFT JOIN（沒上榜的日期分數為 None）
    回傳：{"3580": [{"date":..., "close":..., "score":...}, ...], ...}  依日期升序
    """
    # 取各類別最新日期的推薦股票
    with _connect() as conn:
        latest_rows = conn.execute("""
            SELECT DISTINCT dp.stock_id
            FROM daily_picks dp
            INNER JOIN (
                SELECT category, MAX(date) as latest_date
                FROM daily_picks GROUP BY category
            ) latest ON dp.category = latest.category
                     AND dp.date = latest.latest_date
        """).fetchall()

        stock_ids = [r["stock_id"] for r in latest_rows]
        if not stock_ids:
            return {}

        placeholders = ",".join("?" * len(stock_ids))

        # price_cache 取近 N 個交易日（依 date 排序取最近 days 筆唯一日期）
        rows = conn.execute(f"""
            SELECT
                pc.stock_id,
                pc.date,
                pc.close,
                dp.kline_score as score
            FROM price_cache pc
            LEFT JOIN daily_picks dp
                   ON dp.stock_id = pc.stock_id
                  AND dp.date     = pc.date
            WHERE pc.stock_id IN ({placeholders})
              AND pc.date IN (
                  SELECT DISTINCT date FROM price_cache
                  ORDER BY date DESC LIMIT ?
              )
            ORDER BY pc.stock_id, pc.date ASC
        """, stock_ids + [days]).fetchall()

    result: dict[str, list[dict]] = {}
    for r in rows:
        sid = r["stock_id"]
        if sid not in result:
            result[sid] = []
        result[sid].append({
            "date":  r["date"],
            "close": r["close"],
            "score": r["score"],   # 沒上榜的日期為 None
        })
    return result


# ══════════════════════════════════════════════
#  統計分析函數（供 html_generator stats Tab 使用）
# ══════════════════════════════════════════════

def get_score_band_stats(days: int = None, use_t5: bool = False) -> list[dict]:
    """
    依 K線分數區間統計勝率與平均報酬。
    回答：「高分推薦真的比低分準嗎？」

    回傳：
    [{"band":"78+","range":"78~100","total":45,"win":32,"win_rate":71.1,"avg_pnl":2.3,"best_pnl":8.1,"worst_pnl":-2.1}, ...]
    """
    if days is None:
        days = HISTORY_DAYS
    pnl_col = "t5_pnl" if use_t5 else "t3_pnl"
    cutoff  = (date.today() - timedelta(days=days)).isoformat()

    bands = [
        ("78+",   78,  100),
        ("71-77", 71,  77),
        ("62-70", 62,  70),
        ("<62",    0,  61),
    ]

    result = []
    with _connect() as conn:
        for label, lo, hi in bands:
            row = conn.execute(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN {pnl_col} > 0 THEN 1 ELSE 0 END) as win,
                    ROUND(AVG({pnl_col}), 2)  as avg_pnl,
                    ROUND(MAX({pnl_col}), 2)  as best_pnl,
                    ROUND(MIN({pnl_col}), 2)  as worst_pnl
                FROM daily_picks
                WHERE {pnl_col} IS NOT NULL
                  AND kline_score >= ? AND kline_score <= ?
                  AND date >= ?
            """, (lo, hi, cutoff)).fetchone()
            if row and row["total"] > 0:
                d = dict(row)
                d["band"]     = label
                d["range"]    = f"{lo}~{hi}"
                d["win_rate"] = round(d["win"] / d["total"] * 100, 1)
                result.append(d)
            else:
                result.append({
                    "band": label, "range": f"{lo}~{hi}",
                    "total": 0, "win": 0, "win_rate": 0.0,
                    "avg_pnl": None, "best_pnl": None, "worst_pnl": None,
                })
    return result


def get_category_stats(days: int = None, use_t5: bool = False) -> list[dict]:
    """
    各類別（ETF/OTC/TSE/US）整體勝率與平均報酬比較。
    回答：「哪個市場信號最準？」

    回傳：
    [{"category":"ETF","label":"ETF","total":50,"win":32,"win_rate":64.0,"avg_pnl":1.8,"avg_score":68.0,...}, ...]
    """
    if days is None:
        days = HISTORY_DAYS
    pnl_col = "t5_pnl" if use_t5 else "t3_pnl"
    cutoff  = (date.today() - timedelta(days=days)).isoformat()
    labels  = {"ETF": "ETF", "OTC": "上櫃", "TSE": "上市", "US": "美股"}

    result = []
    with _connect() as conn:
        for cat, lbl in labels.items():
            row = conn.execute(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN {pnl_col} > 0 THEN 1 ELSE 0 END) as win,
                    ROUND(AVG({pnl_col}), 2)          as avg_pnl,
                    ROUND(AVG(kline_score), 1)         as avg_score,
                    ROUND(MAX({pnl_col}), 2)           as best_pnl,
                    ROUND(MIN({pnl_col}), 2)           as worst_pnl
                FROM daily_picks
                WHERE {pnl_col} IS NOT NULL
                  AND category = ?
                  AND date >= ?
            """, (cat, cutoff)).fetchone()
            if row and row["total"] > 0:
                d = dict(row)
                d["category"] = cat
                d["label"]    = lbl
                d["win_rate"] = round(d["win"] / d["total"] * 100, 1)
                result.append(d)
    return result


def get_monthly_summary(use_t5: bool = False, months: int = 6) -> list[dict]:
    """
    按月份統計整體勝率與平均報酬（近 N 個月）。
    回答：「系統近況是在進步還是退步？」

    回傳（依月份升序）：
    [{"month":"2025-03","total":120,"win":72,"win_rate":60.0,"avg_pnl":1.2,"avg_score":67.5}, ...]
    """
    pnl_col = "t5_pnl" if use_t5 else "t3_pnl"
    cutoff  = (date.today() - timedelta(days=months * 31)).isoformat()

    with _connect() as conn:
        rows = conn.execute(f"""
            SELECT
                SUBSTR(date, 1, 7)               as month,
                COUNT(*)                         as total,
                SUM(CASE WHEN {pnl_col} > 0 THEN 1 ELSE 0 END) as win,
                ROUND(AVG({pnl_col}), 2)         as avg_pnl,
                ROUND(AVG(kline_score), 1)        as avg_score
            FROM daily_picks
            WHERE {pnl_col} IS NOT NULL
              AND date >= ?
            GROUP BY month
            ORDER BY month ASC
        """, (cutoff,)).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["win_rate"] = round(d["win"] / d["total"] * 100, 1) if d["total"] > 0 else 0.0
        result.append(d)
    return result
