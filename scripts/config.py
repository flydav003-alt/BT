"""
config.py — 集中設定檔
"""
import os, glob
from pathlib import Path

# ── 路徑 ───────────────────────────────────────
ROOT_DIR      = Path(__file__).parent.parent
DATA_DIR      = ROOT_DIR / "data"
INPUT_DIR     = DATA_DIR / "input"
DB_PATH       = DATA_DIR / "history.db"
DOCS_DIR      = ROOT_DIR / "docs"
DOCS_DATA_DIR = DOCS_DIR / "data"
LOCK_FILE     = DATA_DIR / ".db_write.lock"

# ── FinMind ────────────────────────────────────
FINMIND_TOKEN: str = os.environ.get("FINMIND_TOKEN", "")

# ── CSV 自動搜尋 ───────────────────────────────
# 支援：etf.csv  或  etf_20260430.csv（取最新日期）

def _find_latest_csv(prefix: str) -> Path:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    fixed = INPUT_DIR / f"{prefix}.csv"
    if fixed.exists():
        return fixed
    dated = sorted(glob.glob(str(INPUT_DIR / f"{prefix}_*.csv")), reverse=True)
    if dated:
        return Path(dated[0])
    return fixed   # 不存在→回傳預期路徑，load_csv 會印警告

def get_csv_files() -> dict:
    """每次執行時即時搜尋，確保取到最新檔案"""
    return {
        "ETF": _find_latest_csv("etf"),
        "OTC": _find_latest_csv("otc"),
        "TSE": _find_latest_csv("tse"),
    }

CSV_FILES = get_csv_files()   # 模組載入時的預設值

# ── CSV 欄位名稱 ───────────────────────────────
# 真實 CSV 確認欄位如下：
#   ETF : stock_id | name | composite_score(帶「億」字串)
#   TSE : stock_id | name | close | rsi14 | vol_ratio | composite_score(純數字)
#   OTC : 同 TSE

CSV_COL_STOCK_ID   = "stock_id"
CSV_COL_NAME       = "name"            # ← 真實欄名是 name，不是 stock_name
CSV_COL_CLOSE      = "close"           # TSE/OTC 已有收盤價
CSV_COL_RSI        = "rsi14"
CSV_COL_VOL_RATIO  = "vol_ratio"
CSV_COL_COMP_SCORE = "composite_score"

# ── 評分設定 ────────────────────────────────────
SCORE_STRATEGY = "auto"
SCORE_PERIOD   = 60
SCORE_DELAY    = 1.0
TOP_N          = 10      # ← 改為每欄 10 支

# ── 回測設定 ────────────────────────────────────
BACKTEST_DAYS      = 5
BACKTEST_T3        = 3
BACKTEST_T5        = 5
WIN_RATE_THRESHOLD = 0.0

# ── 網站設定 ────────────────────────────────────
SITE_TITLE     = "台股 K線評分排名報告與回測系統"
SITE_SUBTITLE  = "每日自動更新｜ETF × 上櫃 × 上市"
KLINE_TOOL_URL = "https://flydav003-alt.github.io/k-line/"
