"""
main.py
=======
主執行腳本：讀取 CSV → 評分 → 寫入 DB → 補回測 → 生成靜態頁面

使用方式：
    # 處理所有有新 CSV 的欄位
    python main.py

    # 只處理特定類別（GitHub Actions 用）
    python main.py --category ETF
    python main.py --category OTC
    python main.py --category TSE

    # 只補回測資料（不重跑評分）
    python main.py --backfill-only

    # 強制重新生成 HTML（不重跑評分）
    python main.py --regen-html
"""

import sys
import argparse
import logging
import json
from datetime import date, datetime
from pathlib import Path

# 確保 scripts/ 在 import 路徑中
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from config import (
    CSV_FILES, CSV_COL_STOCK_ID, CSV_COL_NAME, CSV_COL_COMP_SCORE,
    FINMIND_TOKEN, SCORE_STRATEGY, SCORE_PERIOD, SCORE_DELAY, TOP_N,
    BACKTEST_T3, BACKTEST_T5, DOCS_DATA_DIR,
)
from db_manager import (
    init_db, upsert_daily_picks, upsert_stock_names,
    get_pending_backfill, update_backtest_prices,
    get_latest_picks, get_history_picks, get_win_rate_stats,
    get_last_run_date, log_run,
)
from kline_scorer import fetch_price, run_analysis, STRATEGY_PROFILES
from html_generator import generate_all

# ── 日誌設定 ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
#  CSV 讀取與解析
# ══════════════════════════════════════════════

def load_csv(category: str) -> tuple[list[str], dict[str, str]]:
    """
    讀取對應 CSV，回傳 (stock_ids, name_map)
    name_map = {"2330": "台積電", ...}
    """
    csv_path = CSV_FILES[category]
    if not csv_path.exists():
        logger.warning(f"[{category}] CSV 不存在，跳過：{csv_path}")
        return [], {}

    df = pd.read_csv(csv_path, dtype=str)
    logger.info(f"[{category}] 讀取 CSV：{csv_path.name}，欄位：{list(df.columns)}")

    # 找股票代號欄
    id_col = CSV_COL_STOCK_ID if CSV_COL_STOCK_ID in df.columns else df.columns[0]
    df[id_col] = df[id_col].str.strip()

    # 若有綜合分數欄，依分數降序排列（取前 TOP_N）
    if CSV_COL_COMP_SCORE in df.columns:
        df[CSV_COL_COMP_SCORE] = pd.to_numeric(df[CSV_COL_COMP_SCORE], errors="coerce")
        df = df.sort_values(CSV_COL_COMP_SCORE, ascending=False)

    stock_ids = df[id_col].dropna().tolist()[:TOP_N]

    # 名稱對照
    name_map: dict[str, str] = {}
    if CSV_COL_NAME and CSV_COL_NAME in df.columns:
        for _, row in df.iterrows():
            sid = str(row[id_col]).strip()
            name = str(row[CSV_COL_NAME]).strip()
            if sid and name and name != "nan":
                name_map[sid] = name

    logger.info(f"[{category}] 股票清單（{len(stock_ids)} 支）：{stock_ids}")
    return stock_ids, name_map


# ══════════════════════════════════════════════
#  評分批次處理
# ══════════════════════════════════════════════

def score_category(
    category: str,
    stock_ids: list[str],
    name_map: dict[str, str],
    trade_date: str,
) -> list[dict]:
    """
    對一個類別的股票跑評分，回傳結果列表。
    結果已附加 stock_name、stock_id、strategy_used。
    """
    import time
    results = []
    total = len(stock_ids)

    for i, sid in enumerate(stock_ids, 1):
        logger.info(f"  [{i}/{total}] {sid} 評分中...")
        try:
            # 抓價格資料
            raw_data = fetch_price(sid, FINMIND_TOKEN)
            sliced   = raw_data[-SCORE_PERIOD:] if len(raw_data) >= SCORE_PERIOD else raw_data

            # 抓籌碼（可選，失敗不中斷）
            chip_proc = []
            try:
                from kline_scorer import fetch_chip, process_chip
                chip_raw  = fetch_chip(sid, FINMIND_TOKEN)
                chip_proc = process_chip(chip_raw)
            except Exception as e:
                logger.debug(f"    籌碼資料取得失敗（{e}），以空陣列繼續")

            # 決定策略
            use_strategy = SCORE_STRATEGY
            if use_strategy == "auto":
                pre = run_analysis(sliced, chip_proc, "balanced")
                use_strategy = pre.get("auto_strategy", "balanced")

            result = run_analysis(sliced, chip_proc, use_strategy)
            result["stock_id"]      = sid
            result["stock_name"]    = name_map.get(sid, "")
            result["strategy_used"] = use_strategy
            results.append(result)

            score_str = f"分數={result['score']:3d}  {result['verdict']}"
            logger.info(f"    ✅ {score_str}  策略={STRATEGY_PROFILES[use_strategy]['name']}")

        except Exception as e:
            logger.error(f"    ❌ {sid} 失敗：{e}")
            results.append({
                "stock_id":   sid,
                "stock_name": name_map.get(sid, ""),
                "error":      str(e),
                "score":      0,
            })

        if i < total:
            time.sleep(SCORE_DELAY)

    # 依分數降序
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


# ══════════════════════════════════════════════
#  回測價格補填
# ══════════════════════════════════════════════

def backfill_prices() -> int:
    """
    掃描 DB 中缺少 T+3/T+5 價格的紀錄並補填。
    回傳補填筆數。
    """
    import time
    pending = get_pending_backfill()
    if not pending:
        logger.info("[回測補填] 無待補填紀錄")
        return 0

    logger.info(f"[回測補填] 共 {len(pending)} 筆待補填")

    # 合併同股票的請求，避免重複抓 API
    stock_cache: dict[str, list[dict]] = {}
    filled = 0

    for rec in pending:
        sid        = rec["stock_id"]
        base_date  = rec["date"]          # YYYY-MM-DD
        base_price = rec["close_price"]
        rec_id     = rec["id"]

        # 抓（或從 cache 取）該股票的歷史價格
        if sid not in stock_cache:
            try:
                stock_cache[sid] = fetch_price(sid, FINMIND_TOKEN)
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"  [回測] {sid} 抓價格失敗：{e}")
                stock_cache[sid] = []

        price_data = stock_cache[sid]
        # 建立 date → close 對照
        date_price = {d["date"]: d["close"] for d in price_data}

        def nth_trading_day(base: str, n: int) -> tuple[Optional[str], Optional[float]]:
            """找第 n 個交易日的日期與收盤價"""
            from datetime import date as _date, timedelta
            dt = _date.fromisoformat(base)
            found = 0
            for _ in range(30):  # 最多往後30個日曆日
                dt += timedelta(days=1)
                ds = dt.isoformat()
                if ds in date_price:
                    found += 1
                    if found == n:
                        return ds, date_price[ds]
            return None, None

        t3_date, t3_price = nth_trading_day(base_date, BACKTEST_T3)
        t5_date, t5_price = nth_trading_day(base_date, BACKTEST_T5)

        update_backtest_prices(rec_id, t3_date, t3_price, t5_date, t5_price, base_price)
        filled += 1
        logger.info(f"  補填 {sid} (id={rec_id}): T+3={t3_price} T+5={t5_price}")

    log_run("BACKFILL", "ok", filled, f"補填 {filled} 筆回測資料")
    return filled


# ══════════════════════════════════════════════
#  生成靜態 JSON（供前端讀取）
# ══════════════════════════════════════════════

def export_json() -> None:
    """將 DB 資料匯出為 docs/data/dashboard.json"""
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "today_picks":  get_latest_picks(TOP_N),
        "history":      get_history_picks(days=5),
        "win_stats": {
            "t3_30d":  get_win_rate_stats(days=30,  use_t5=False),
            "t5_30d":  get_win_rate_stats(days=30,  use_t5=True),
            "t3_90d":  get_win_rate_stats(days=90,  use_t5=False),
            "t5_90d":  get_win_rate_stats(days=90,  use_t5=True),
            "t3_all":  get_win_rate_stats(days=None, use_t5=False),
            "t5_all":  get_win_rate_stats(days=None, use_t5=True),
        },
    }

    out_path = DOCS_DATA_DIR / "dashboard.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON 匯出：{out_path}")


# ══════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════

def run(category_filter: Optional[str] = None) -> None:
    """
    主流程：
    1. 讀取 CSV
    2. 評分
    3. 寫入 DB
    4. 補回測
    5. 匯出 JSON + 生成 HTML
    """
    trade_date = date.today().isoformat()
    logger.info(f"═══ 開始執行 | 日期：{trade_date} | 類別：{category_filter or '全部'} ═══")

    init_db()

    # 決定要處理哪些類別
    categories = [category_filter] if category_filter else list(CSV_FILES.keys())
    any_updated = False

    for cat in categories:
        logger.info(f"\n── 處理類別：{cat} ──")
        stock_ids, name_map = load_csv(cat)

        if not stock_ids:
            log_run(cat, "skipped", 0, "CSV 不存在或為空")
            continue

        # 更新名稱對照表
        if name_map:
            upsert_stock_names(name_map)

        # 評分
        results = score_category(cat, stock_ids, name_map, trade_date)

        # 寫入 DB
        cnt = upsert_daily_picks(results, cat, trade_date)
        ok_cnt  = sum(1 for r in results if "error" not in r)
        err_cnt = len(results) - ok_cnt
        log_run(cat, "ok", cnt, f"成功={ok_cnt} 失敗={err_cnt}")
        any_updated = True

    # 補回測（每次都跑，補齊舊資料）
    logger.info("\n── 補填回測資料 ──")
    backfill_prices()

    # 匯出靜態資料
    logger.info("\n── 生成靜態頁面 ──")
    export_json()
    generate_all()

    logger.info("\n═══ 執行完成 ═══")


# ══════════════════════════════════════════════
#  CLI 入口
# ══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="台股K線評分儀表板 主執行腳本")
    parser.add_argument(
        "--category", type=str, choices=["ETF", "OTC", "TSE"],
        help="只處理特定類別（不指定 = 全部）"
    )
    parser.add_argument(
        "--backfill-only", action="store_true",
        help="只補回測資料，不重跑評分"
    )
    parser.add_argument(
        "--regen-html", action="store_true",
        help="只重新生成 HTML/JSON，不重跑評分"
    )
    args = parser.parse_args()

    init_db()

    if args.backfill_only:
        logger.info("模式：只補回測資料")
        backfill_prices()
        export_json()
        generate_all()
        return

    if args.regen_html:
        logger.info("模式：重新生成靜態頁面")
        export_json()
        generate_all()
        return

    run(category_filter=args.category)


if __name__ == "__main__":
    from typing import Optional
    main()
