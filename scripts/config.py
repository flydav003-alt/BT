"""
config.py
=========
集中設定檔：路徑、API、常數全部在這裡管理
"""

import os
from pathlib import Path

# ══════════════════════════════════════════════
#  路徑設定
# ══════════════════════════════════════════════

# 專案根目錄（相對於此檔案向上一層）
ROOT_DIR   = Path(__file__).parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
DATA_DIR   = ROOT_DIR / "data"
INPUT_DIR  = DATA_DIR / "input"
DB_PATH    = DATA_DIR / "history.db"
DOCS_DIR   = ROOT_DIR / "docs"
DOCS_DATA_DIR = DOCS_DIR / "data"

# ══════════════════════════════════════════════
#  FinMind API
# ══════════════════════════════════════════════

# 優先讀取環境變數（GitHub Secrets 注入）；本地測試可不填
FINMIND_TOKEN: str = os.environ.get("FINMIND_TOKEN", "")

# ══════════════════════════════════════════════
#  CSV 輸入設定（各 Repo 匯入的檔案名稱）
# ══════════════════════════════════════════════

CSV_FILES = {
    "ETF": INPUT_DIR / "etf.csv",   # ETF 買超前6清單
    "OTC": INPUT_DIR / "otc.csv",   # 上櫃綜合分數前6清單
    "TSE": INPUT_DIR / "tse.csv",   # 上市綜合分數前6清單
}

# CSV 欄位名稱設定（依照你的 CSV 實際欄位調整）
CSV_COL_STOCK_ID   = "stock_id"        # 股票代號欄位
CSV_COL_NAME       = "stock_name"      # 中文名稱欄位（若無則填 None，系統會跳過）
CSV_COL_COMP_SCORE = "composite_score" # 綜合分數欄位（排序用）

# ══════════════════════════════════════════════
#  評分引擎設定
# ══════════════════════════════════════════════

SCORE_STRATEGY = "auto"   # auto / balanced / breakout / pullback / reversal
SCORE_PERIOD   = 60        # K線天數
SCORE_DELAY    = 1.0       # 每支股票間隔秒數（避免 API 過頻）
TOP_N          = 8         # 每欄最多顯示幾支

# ══════════════════════════════════════════════
#  回測設定
# ══════════════════════════════════════════════

BACKTEST_DAYS       = 5    # 歷史回測顯示最近幾天
BACKTEST_T3         = 3    # T+3 天
BACKTEST_T5         = 5    # T+5 天
WIN_RATE_THRESHOLD  = 0.0  # 勝率計算：報酬 > 此值視為獲利（%）

# ══════════════════════════════════════════════
#  GitHub Pages 設定
# ══════════════════════════════════════════════

SITE_TITLE       = "台股 K線評分排名報告與回測系統"
SITE_SUBTITLE    = "每日自動更新｜ETF × 上櫃 × 上市"
KLINE_TOOL_URL   = "https://flydav003-alt.github.io/k-line/"  # 點擊股票代號跳轉

# ══════════════════════════════════════════════
#  lock 檔路徑（防止並發寫入衝突）
# ══════════════════════════════════════════════

LOCK_FILE = DATA_DIR / ".db_write.lock"
