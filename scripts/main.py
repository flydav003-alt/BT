"""
main.py — 主執行腳本

使用方式：
    python main.py                   # 全部類別
    python main.py --category ETF    # 只跑 ETF
    python main.py --backfill-only   # 只補回測
    python main.py --regen-html      # 只重生 HTML
"""

import sys, argparse, logging, json, re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from config import (
    get_csv_files,
    CSV_COL_STOCK_ID, CSV_COL_NAME, CSV_COL_CLOSE,
    CSV_COL_RSI, CSV_COL_VOL_RATIO, CSV_COL_COMP_SCORE,
    FINMIND_TOKEN, SCORE_STRATEGY, SCORE_PERIOD, SCORE_DELAY, TOP_N,
    BACKTEST_T3, BACKTEST_T5, DOCS_DATA_DIR,
)
from db_manager import (
    init_db, upsert_daily_picks, upsert_stock_names,
    get_pending_backfill, update_backtest_prices,
    get_latest_picks, get_history_picks, get_win_rate_stats,
    log_run,
)
from kline_scorer import fetch_price, run_analysis, STRATEGY_PROFILES
from html_generator import generate_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
#  CSV 讀取
# ══════════════════════════════════════════════

def _parse_score(val: str) -> float:
    """
    支援多種分數格式：
      '8.6億' → 8.6    (ETF 買超金額，保留數值做排序)
      '62.77' → 62.77  (TSE/OTC 綜合分數)
      '—'     → 0.0
    """
    if not val or str(val).strip() in ("", "nan", "—"):
        return 0.0
    # 去掉「億」「萬」等單位，只保留數字與小數點
    cleaned = re.sub(r"[^\d.]", "", str(val))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def load_csv(category: str) -> tuple[list[str], dict[str, dict]]:
    """
    讀取對應 CSV。
    回傳：
      stock_ids : 依 composite_score 排序的前 TOP_N 代號列表
      info_map  : {stock_id: {"name":..., "close":..., "rsi":..., "vol_ratio":...}}
    """
    csv_files = get_csv_files()   # 即時搜尋最新檔案
    csv_path  = csv_files[category]

    if not csv_path.exists():
        logger.warning(f"[{category}] 找不到 CSV，跳過（路徑：{csv_path}）")
        return [], {}

    logger.info(f"[{category}] 讀取：{csv_path.name}")

    # 嘗試多種 encoding
    df = None
    for enc in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            df = pd.read_csv(csv_path, dtype=str, encoding=enc, on_bad_lines="skip")
            logger.info(f"  解析成功（encoding={enc}），{len(df)} 行，欄位：{list(df.columns)}")
            break
        except Exception as e:
            logger.debug(f"  {enc} 失敗：{e}")

    if df is None or df.empty:
        logger.error(f"[{category}] CSV 無法解析或為空")
        return [], {}

    # 欄位正規化：去除前後空白
    df.columns = [c.strip() for c in df.columns]
    df[CSV_COL_STOCK_ID] = df[CSV_COL_STOCK_ID].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)

    # 解析 composite_score 並排序
    if CSV_COL_COMP_SCORE in df.columns:
        df["_score_num"] = df[CSV_COL_COMP_SCORE].apply(_parse_score)
        df = df.sort_values("_score_num", ascending=False)

    stock_ids = [s for s in df[CSV_COL_STOCK_ID].dropna().tolist()
                 if s and s != "nan"][:TOP_N]

    # 建立 info_map（名稱、現有收盤價、RSI、量比）
    info_map: dict[str, dict] = {}
    for _, row in df.iterrows():
        sid = str(row[CSV_COL_STOCK_ID]).strip()
        if not sid or sid == "nan":
            continue
        info_map[sid] = {
            "name":      str(row.get(CSV_COL_NAME, "")).strip(),
            "close":     row.get(CSV_COL_CLOSE),     # TSE/OTC 有；ETF 無→None
            "rsi":       row.get(CSV_COL_RSI),        # TSE/OTC 有；ETF 無→None
            "vol_ratio": row.get(CSV_COL_VOL_RATIO),  # TSE/OTC 有；ETF 無→None
        }

    logger.info(f"  股票清單（{len(stock_ids)} 支）：{stock_ids}")
    return stock_ids, info_map


# ══════════════════════════════════════════════
#  評分
# ══════════════════════════════════════════════

def score_category(
    category: str,
    stock_ids: list[str],
    info_map: dict[str, dict],
    trade_date: str,
) -> list[dict]:
    import time
    results = []

    for i, sid in enumerate(stock_ids, 1):
        logger.info(f"  [{i}/{len(stock_ids)}] {sid} 評分中...")
        info = info_map.get(sid, {})
        try:
            raw_data = fetch_price(sid, FINMIND_TOKEN)
            sliced   = raw_data[-SCORE_PERIOD:] if len(raw_data) >= SCORE_PERIOD else raw_data

            chip_proc = []
            try:
                from kline_scorer import fetch_chip, process_chip
                chip_raw  = fetch_chip(sid, FINMIND_TOKEN)
                chip_proc = process_chip(chip_raw)
            except Exception:
                pass

            use_strategy = SCORE_STRATEGY
            if use_strategy == "auto":
                pre = run_analysis(sliced, chip_proc, "balanced")
                use_strategy = pre.get("auto_strategy", "balanced")

            result = run_analysis(sliced, chip_proc, use_strategy)
            result["stock_id"]      = sid
            result["stock_name"]    = info.get("name", "")
            result["strategy_used"] = use_strategy

            # TSE/OTC CSV 已有收盤價，優先用它（更即時）
            if info.get("close") is not None:
                try:
                    result["last_close"] = float(info["close"])
                except Exception:
                    pass
            # RSI / 量比 — CSV 已有直接補上，省 API 一次
            if info.get("rsi") is not None:
                try:
                    result["lrsi"] = float(info["rsi"])
                except Exception:
                    pass
            if info.get("vol_ratio") is not None:
                try:
                    result["vol_ratio"] = float(info["vol_ratio"])
                except Exception:
                    pass

            logger.info(f"    ✅ 分數={result['score']} {result['verdict']}")
            results.append(result)

        except Exception as e:
            logger.error(f"    ❌ {sid} 失敗：{e}")
            results.append({
                "stock_id":   sid,
                "stock_name": info.get("name", ""),
                "error":      str(e),
                "score":      0,
            })

        if i < len(stock_ids):
            time.sleep(SCORE_DELAY)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


# ══════════════════════════════════════════════
#  回測補填
# ══════════════════════════════════════════════

def backfill_prices() -> int:
    import time
    pending = get_pending_backfill()
    if not pending:
        logger.info("[回測補填] 無待補填紀錄")
        return 0

    logger.info(f"[回測補填] {len(pending)} 筆待補填")
    stock_cache: dict[str, list] = {}
    filled = 0

    for rec in pending:
        sid        = rec["stock_id"]
        base_date  = rec["date"]
        base_price = rec["close_price"]
        rec_id     = rec["id"]

        if sid not in stock_cache:
            try:
                stock_cache[sid] = fetch_price(sid, FINMIND_TOKEN)
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"  {sid} 抓價失敗：{e}")
                stock_cache[sid] = []

        date_price = {d["date"]: d["close"] for d in stock_cache[sid]}

        def nth_day(base: str, n: int):
            from datetime import date as _d, timedelta as _td
            dt = _d.fromisoformat(base)
            found = 0
            for _ in range(30):
                dt += _td(days=1)
                ds = dt.isoformat()
                if ds in date_price:
                    found += 1
                    if found == n:
                        return ds, date_price[ds]
            return None, None

        t3_date, t3_price = nth_day(base_date, BACKTEST_T3)
        t5_date, t5_price = nth_day(base_date, BACKTEST_T5)
        update_backtest_prices(rec_id, t3_date, t3_price, t5_date, t5_price, base_price)
        filled += 1
        logger.info(f"  {sid}: T+3={t3_price} T+5={t5_price}")

    log_run("BACKFILL", "ok", filled)
    return filled


# ══════════════════════════════════════════════
#  匯出 JSON
# ══════════════════════════════════════════════

def export_json() -> None:
    DOCS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "today_picks":  get_latest_picks(TOP_N),
        "history":      get_history_picks(days=5),
        "win_stats": {
            "t3_30d": get_win_rate_stats(days=30,  use_t5=False),
            "t5_30d": get_win_rate_stats(days=30,  use_t5=True),
            "t3_90d": get_win_rate_stats(days=90,  use_t5=False),
            "t5_90d": get_win_rate_stats(days=90,  use_t5=True),
            "t3_all": get_win_rate_stats(days=None, use_t5=False),
            "t5_all": get_win_rate_stats(days=None, use_t5=True),
        },
    }
    out = DOCS_DATA_DIR / "dashboard.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON 匯出：{out}")


# ══════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════

def run(category_filter: Optional[str] = None) -> None:
    trade_date = date.today().isoformat()
    logger.info(f"═══ 開始 | {trade_date} | {category_filter or '全部'} ═══")
    init_db()

    categories = [category_filter] if category_filter else ["ETF", "OTC", "TSE"]

    for cat in categories:
        logger.info(f"\n── {cat} ──")
        stock_ids, info_map = load_csv(cat)
        if not stock_ids:
            log_run(cat, "skipped", 0, "CSV 不存在或為空")
            continue

        # 更新名稱對照表
        name_map = {sid: d["name"] for sid, d in info_map.items() if d.get("name")}
        if name_map:
            upsert_stock_names(name_map)

        results = score_category(cat, stock_ids, info_map, trade_date)
        cnt = upsert_daily_picks(results, cat, trade_date)
        ok  = sum(1 for r in results if "error" not in r)
        log_run(cat, "ok", cnt, f"成功={ok} 失敗={len(results)-ok}")

    logger.info("\n── 補填回測 ──")
    backfill_prices()

    logger.info("\n── 生成靜態頁面 ──")
    export_json()
    generate_all()

    logger.info("\n═══ 完成 ═══")


# ══════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", choices=["ETF", "OTC", "TSE"])
    parser.add_argument("--backfill-only", action="store_true")
    parser.add_argument("--regen-html",    action="store_true")
    args = parser.parse_args()

    init_db()

    if args.backfill_only:
        backfill_prices()
        export_json()
        generate_all()
    elif args.regen_html:
        export_json()
        generate_all()
    else:
        run(category_filter=args.category)


if __name__ == "__main__":
    main()
