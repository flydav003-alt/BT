"""
html_generator.py  ── PATCHED (修改 1 & 2 & 3 & 4)
=================
靜態 HTML 生成器：從 DB 資料生成 docs/index.html

修改記錄：
  [修改1] render_pick_card：中文名稱移到代號正下方（flex-direction:column）
  [修改2] generate_index_html：navbar 連結變亮 + 三個 section 改成 Tab 切換
  [修改3] 多條件篩選器
  [修改4] 迷你折線圖改用 close 收盤價（price_cache 7天連續資料），不再依賴 score 欄位
           score 為 None 的日期（沒上榜）不影響折線圖，但上榜的日期在 Modal 大圖中顯示圓點分數標記
"""

import json
import logging
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from config import DOCS_DIR, DOCS_DATA_DIR, SITE_TITLE, SITE_SUBTITLE, KLINE_TOOL_URL, TOP_N
from db_manager import get_latest_picks, get_history_picks, get_win_rate_stats, get_all_score_trends, get_watchlist, get_watchlist_latest_picks

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════
#  輔助函數
# ══════════════════════════════════════════════

def score_color(s: int) -> str:
    if s >= 78: return "#ff4d6d"
    if s >= 62: return "#ff9f40"
    if s >= 45: return "#e8b84b"
    if s >= 30: return "#4a9eff"
    return "#00c896"

def score_bg(s: int) -> str:
    if s >= 78: return "rgba(255,77,109,0.12)"
    if s >= 62: return "rgba(255,159,64,0.10)"
    if s >= 45: return "rgba(232,184,75,0.10)"
    if s >= 30: return "rgba(74,158,255,0.10)"
    return "rgba(0,200,150,0.10)"

def pnl_color(v) -> str:
    if v is None: return "#4a6080"
    return "#ff4d6d" if float(v) > 0 else ("#00c896" if float(v) < 0 else "#e8b84b")

def pnl_str(v) -> str:
    if v is None: return "—"
    return f"+{v:.2f}%" if float(v) > 0 else f"{v:.2f}%"

def kline_url(stock_id: str) -> str:
    return f"{KLINE_TOOL_URL}?stock={stock_id}"

def format_date(d: str) -> str:
    if not d: return "—"
    parts = d.split("-")
    return f"{parts[1]}/{parts[2]}" if len(parts) == 3 else d

def cat_label(cat: str) -> str:
    return {"ETF": "ETF", "OTC": "上櫃", "TSE": "上市"}.get(cat, cat)

def cat_color(cat: str) -> str:
    return {"ETF": "#a78bfa", "OTC": "#29c5c5", "TSE": "#ff9f40"}.get(cat, "#4a6080")

def sig_icon(sig_type: str) -> str:
    return {"bull": "▲", "bear": "▼", "neutral": "◆"}.get(sig_type, "◆")

def sig_color(sig_type: str) -> str:
    return {"bull": "#ff4d6d", "bear": "#00c896", "neutral": "#6a85a8"}.get(sig_type, "#6a85a8")


# ══════════════════════════════════════════════
#  今日推薦卡片
#  [修改4] 迷你折線圖改用 close 收盤價，score=None 不影響折線
# ══════════════════════════════════════════════

def render_pick_card(p: dict, trend: list[dict] | None = None) -> str:
    sc   = p.get("kline_score", 0)
    col  = score_color(sc)
    bg   = score_bg(sc)
    sid  = p.get("stock_id", "")
    name = p.get("stock_name", "")
    cat  = p.get("category", "")
    circ = round(sc / 100 * 276.46, 1)
    gap  = round(276.46 - circ, 1)

    rsi_val = float(p["rsi"]) if p.get("rsi") else 0
    vr_val  = float(p["vol_ratio"]) if p.get("vol_ratio") else 0

    rsi_str = f'{rsi_val:.1f}' if rsi_val else "—"
    kd_str  = f'{p["kd_k"]:.1f}' if p.get("kd_k") else "—"
    vr_str  = f'{vr_val:.2f}x' if vr_val else "—"

    # [修改4] trend 結構: [{"date":..., "close":..., "score":...}, ...]
    # close 來自 price_cache，7天連續，不會有 None
    # score 來自 daily_picks LEFT JOIN，沒上榜的日期為 None → 不影響折線
    trend_json = "[]"
    mini_svg = '<div style="width:60px;flex-shrink:0;"></div>'

    if trend and len(trend) >= 2:
        # 只過濾 close 為 None 的項目（price_cache 理論上不會有，純防呆）
        valid = [t for t in trend if t.get("close") is not None]
        if len(valid) >= 2:
            closes = [float(t["close"]) for t in valid]
            scores = [t.get("score") for t in valid]   # 可能含 None，沒關係
            dates  = [t["date"] for t in valid]

            # trend_json 傳給 Modal 大圖，格式含 close + score
            trend_json = json.dumps(
                [{"d": d, "s": s, "close": c} for d, s, c in zip(dates, scores, closes)],
                ensure_ascii=False
            )

            # 迷你折線 SVG（60x28）— 用 close 繪製
            rng = max(closes) - min(closes)
            mn = min(closes) - rng * 0.1 - 0.01
            mx = max(closes) + rng * 0.1 + 0.01
            if mx == mn:
                mx = mn + 1
            n = len(closes)
            pts = " ".join(
                f"{round(i / (n - 1) * 56, 1)},{round(26 - (c - mn) / (mx - mn) * 22, 1)}"
                for i, c in enumerate(closes)
            )
            last_x = round((n - 1) / (n - 1) * 56, 1)
            last_y = round(26 - (closes[-1] - mn) / (mx - mn) * 22, 1)
            prev_c = closes[-2]
            arrow  = "↑" if closes[-1] > prev_c else ("↓" if closes[-1] < prev_c else "→")
            tr_col = col if closes[-1] >= prev_c else "#4a9eff"
            delta_str = f"{closes[0]:.1f}→{closes[-1]:.1f} {arrow}"

            mini_svg = f"""
  <div style="flex-shrink:0;text-align:center;cursor:pointer;" title="點擊查看詳情">
    <svg width="60" height="28" viewBox="0 0 60 28">
      <polyline points="{pts}"
        fill="none" stroke="{tr_col}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="{last_x}" cy="{last_y}" r="2.5" fill="{tr_col}"/>
    </svg>
    <div style="font-size:.58rem;color:{tr_col};margin-top:1px;font-family:'IBM Plex Mono',monospace;">{delta_str}</div>
  </div>"""

    # signals JSON for modal
    sigs_raw = p.get("top_signals", "[]")
    try:
        sigs_list = json.loads(sigs_raw) if isinstance(sigs_raw, str) else sigs_raw
    except Exception:
        sigs_list = []
    sigs_json = json.dumps(sigs_list, ensure_ascii=False).replace('"', '&quot;')

    close_price = p.get('close_price', 0)
    close_str = f"{close_price:.2f}" if close_price else "—"

    return f"""
<div class="pick-card"
  data-score="{sc}"
  data-cat="{cat}"
  data-rsi="{round(rsi_val,1)}"
  data-volratio="{round(vr_val,2)}"
  data-trend="{trend_json.replace('"', '&quot;')}"
  data-signals="{sigs_json}"
  data-sid="{sid}"
  data-name="{name}"
  data-verdict="{p.get('verdict','')}"
  data-score-val="{sc}"
  data-color="{col}"
  data-price="{close_str}"
  data-rsi-val="{rsi_str}"
  data-kd="{kd_str}"
  data-vol="{vr_str}"
  data-cat-label="{cat_label(cat)}"
  data-cat-color="{cat_color(cat)}"
  onclick="openPickModal(this)"
  style="background:{bg};border:1px solid {col}33;border-radius:10px;padding:10px 14px;margin-bottom:6px;display:flex;align-items:center;gap:12px;cursor:pointer;transition:border-color .2s;"
  onmouseover="this.style.borderColor='{col}88'" onmouseout="this.style.borderColor='{col}33'">
  <!-- 排名 -->
  <div style="font-size:.75rem;color:#4a6080;font-family:'IBM Plex Mono',monospace;min-width:20px;text-align:center;">{p.get('rank','')}</div>
  <!-- 分數圓環 -->
  <div style="position:relative;width:44px;height:44px;flex-shrink:0;display:flex;align-items:center;justify-content:center;">
    <svg width="44" height="44" viewBox="0 0 44 44" style="position:absolute;">
      <circle cx="22" cy="22" r="18" fill="none" stroke="#1a2340" stroke-width="4"/>
      <circle cx="22" cy="22" r="18" fill="none" stroke="{col}" stroke-width="4"
        stroke-dasharray="{circ} {gap}"
        stroke-dashoffset="28.3" stroke-linecap="round"
        transform="rotate(-90 22 22)"/>
    </svg>
    <span style="font-size:.78rem;font-weight:700;color:{col};font-family:'IBM Plex Mono',monospace;z-index:1;">{sc}</span>
  </div>
  <!-- 主體資訊 -->
  <div style="flex:1;min-width:0;">
    <div style="display:flex;flex-direction:column;gap:3px;">
      <a href="{kline_url(sid)}" target="_blank"
         onclick="event.stopPropagation()"
         style="font-family:'IBM Plex Mono',monospace;font-size:.95rem;font-weight:700;color:#d4dff0;text-decoration:none;border-bottom:1px dashed #4a6080;display:inline-block;"
         title="點擊開啟K線分析">{sid}</a>
      <span style="font-size:.72rem;color:#6a85a8;">{name}</span>
    </div>
    <div style="font-size:.7rem;color:{col};font-weight:600;margin-top:3px;">{p.get('verdict','')}</div>
  </div>
  <!-- 迷你折線 -->
  {mini_svg}
  <!-- 數值欄 -->
  <div style="text-align:right;flex-shrink:0;">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:.88rem;color:#d4dff0;">{close_str}</div>
    <div style="font-size:.62rem;color:#4a6080;margin-top:2px;">RSI {rsi_str} ｜ K {kd_str}</div>
    <div style="font-size:.62rem;color:#e8b84b;">量比 {vr_str}</div>
  </div>
</div>"""


def render_today_section(picks: dict[str, list[dict]], score_trends: dict[str, list[dict]] | None = None) -> str:
    all_dates = [p["date"] for cat_list in picks.values() for p in cat_list if p.get("date")]
    latest_date = max(all_dates) if all_dates else "—"
    if score_trends is None:
        score_trends = {}

    cols_html = ""
    for cat in ("ETF", "OTC", "TSE"):
        cat_picks = picks.get(cat, [])
        col_color = cat_color(cat)
        cards = "".join(
            render_pick_card(p, trend=score_trends.get(p.get("stock_id", ""), []))
            for p in cat_picks
        ) if cat_picks else '<div style="color:#4a6080;font-size:.8rem;padding:20px 0;">暫無資料</div>'

        cols_html += f"""
<div class="today-col" id="col-{cat}">
  <div class="col-header" style="border-bottom:2px solid {col_color};margin-bottom:12px;padding-bottom:8px;">
    <span style="font-size:.9rem;font-weight:700;color:{col_color};">{cat_label(cat)}</span>
    <span class="col-count" style="font-size:.68rem;color:#4a6080;margin-left:8px;">{len(cat_picks)} 支</span>
  </div>
  <div class="cards-wrapper">
  {cards}
  </div>
  <div class="no-result" style="display:none;color:#4a6080;font-size:.8rem;padding:20px 0;text-align:center;">無符合條件的股票</div>
</div>"""

    return f"""
<section class="section" id="today">
  <div class="section-header">
    <div class="section-title">📈 今日推薦</div>
    <div class="section-date">資料日期：{latest_date}</div>
  </div>

  <div id="today-filters" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px;
    padding:12px 16px;background:var(--s1);border:1px solid var(--border);border-radius:10px;align-items:center;">

    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">類別</span>
      <button class="f-btn active" data-group="cat" data-val="">全部</button>
      <button class="f-btn" data-group="cat" data-val="ETF">ETF</button>
      <button class="f-btn" data-group="cat" data-val="OTC">上櫃</button>
      <button class="f-btn" data-group="cat" data-val="TSE">上市</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">分數</span>
      <button class="f-btn active" data-group="score" data-val="0">全部</button>
      <button class="f-btn" data-group="score" data-val="62">62+</button>
      <button class="f-btn" data-group="score" data-val="78">78+</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">RSI</span>
      <button class="f-btn active" data-group="rsi" data-val="all">全部</button>
      <button class="f-btn" data-group="rsi" data-val="50-70">50-70</button>
      <button class="f-btn" data-group="rsi" data-val="70+">70+</button>
      <button class="f-btn" data-group="rsi" data-val="50-">50以下</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">量比</span>
      <button class="f-btn active" data-group="vol" data-val="0">全部</button>
      <button class="f-btn" data-group="vol" data-val="1.5">1.5x+</button>
      <button class="f-btn" data-group="vol" data-val="2">2x+</button>
    </div>

    <button onclick="resetTodayFilters()" style="margin-left:auto;font-size:.7rem;color:var(--muted);
      background:transparent;border:1px solid var(--border);border-radius:5px;
      padding:3px 10px;cursor:pointer;">重設</button>
  </div>

  <div class="today-grid">
    {cols_html}
  </div>
</section>"""


# ══════════════════════════════════════════════
#  歷史回測表格
# ══════════════════════════════════════════════

def render_history_section(history: list[dict]) -> str:
    rows_html = ""
    for r in history:
        sc    = r.get("kline_score", 0)
        col   = score_color(sc)
        cat   = r.get("category", "")
        cc    = cat_color(cat)
        sid   = r.get("stock_id", "")
        name  = r.get("stock_name") or r.get("sn_name") or ""
        t3pnl = r.get("t3_pnl")
        t5pnl = r.get("t5_pnl")
        rsi_v = float(r["rsi"]) if r.get("rsi") else 0
        vr_v  = float(r["vol_ratio"]) if r.get("vol_ratio") else 0

        rows_html += f"""
<tr class="history-row"
  data-search="{sid} {name}"
  data-cat="{cat}"
  data-score="{sc}"
  data-rsi="{round(rsi_v,1)}"
  data-volratio="{round(vr_v,2)}">
  <td><span style="font-size:.72rem;color:#6a85a8;">{r.get('date','')}</span></td>
  <td><span style="font-size:.7rem;color:{cc};border:1px solid {cc}44;border-radius:4px;padding:1px 6px;">{cat_label(cat)}</span></td>
  <td>
    <a href="{kline_url(sid)}" target="_blank"
       style="font-family:'IBM Plex Mono',monospace;font-weight:700;color:#d4dff0;text-decoration:none;">{sid}</a>
  </td>
  <td style="color:#6a85a8;font-size:.78rem;">{name}</td>
  <td><span style="color:{col};font-weight:700;font-family:'IBM Plex Mono',monospace;">{sc}</span></td>
  <td style="font-family:'IBM Plex Mono',monospace;font-size:.82rem;">{r.get('close_price','—')}</td>
  <td style="font-family:'IBM Plex Mono',monospace;font-size:.78rem;color:#6a85a8;">{r.get('t3_price','—') or '—'}</td>
  <td style="font-weight:600;color:{pnl_color(t3pnl)};">{pnl_str(t3pnl)}</td>
  <td style="font-family:'IBM Plex Mono',monospace;font-size:.78rem;color:#6a85a8;">{r.get('t5_price','—') or '—'}</td>
  <td style="font-weight:600;color:{pnl_color(t5pnl)};">{pnl_str(t5pnl)}</td>
</tr>"""

    return f"""
<section class="section" id="history">
  <div class="section-header">
    <div class="section-title">📋 歷史回測（近5天）</div>
  </div>

  <div id="history-filters" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px;
    padding:12px 16px;background:var(--s1);border:1px solid var(--border);border-radius:10px;align-items:center;">

    <input id="history-search" type="text" placeholder="搜尋代號或名稱..."
      style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:5px 10px;
             color:var(--text);font-size:.78rem;outline:none;width:150px;"
      oninput="applyHistoryFilters()">

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">類別</span>
      <button class="f-btn active" data-group="hcat" data-val="">全部</button>
      <button class="f-btn" data-group="hcat" data-val="ETF">ETF</button>
      <button class="f-btn" data-group="hcat" data-val="OTC">上櫃</button>
      <button class="f-btn" data-group="hcat" data-val="TSE">上市</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">分數</span>
      <button class="f-btn active" data-group="hscore" data-val="0">全部</button>
      <button class="f-btn" data-group="hscore" data-val="62">62+</button>
      <button class="f-btn" data-group="hscore" data-val="78">78+</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">量比</span>
      <button class="f-btn active" data-group="hvol" data-val="0">全部</button>
      <button class="f-btn" data-group="hvol" data-val="1.5">1.5x+</button>
      <button class="f-btn" data-group="hvol" data-val="2">2x+</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">T+3</span>
      <button class="f-btn active" data-group="hpnl" data-val="all">全部</button>
      <button class="f-btn" data-group="hpnl" data-val="win">獲利</button>
      <button class="f-btn" data-group="hpnl" data-val="loss">虧損</button>
    </div>

    <button onclick="resetHistoryFilters()" style="margin-left:auto;font-size:.7rem;color:var(--muted);
      background:transparent;border:1px solid var(--border);border-radius:5px;
      padding:3px 10px;cursor:pointer;">重設</button>
  </div>

  <div id="history-count" style="font-size:.72rem;color:var(--muted);margin-bottom:8px;"></div>

  <div style="overflow-x:auto;">
  <table class="data-table" id="history-table">
    <thead>
      <tr>
        <th>日期</th><th>類別</th><th>代號</th><th>名稱</th>
        <th>K線分</th><th>收盤價</th>
        <th>T+3價</th><th>T+3損益</th>
        <th>T+5價</th><th>T+5損益</th>
      </tr>
    </thead>
    <tbody id="history-body">
      {rows_html}
    </tbody>
  </table>
  </div>
</section>"""


# ══════════════════════════════════════════════
#  歷史勝率排行
# ══════════════════════════════════════════════

def render_win_row(rank: int, r: dict, pnl_key: str = "avg_pnl") -> str:
    wr  = r.get("win_rate", 0)
    wr_col = "#ff4d6d" if wr >= 60 else ("#e8b84b" if wr >= 50 else "#4a9eff")
    pnl = r.get("avg_pnl", 0)
    cnt = r.get("total_cnt", 0)
    return f"""
<tr>
  <td style="color:#4a6080;font-size:.72rem;text-align:center;">{rank}</td>
  <td>
    <a href="{kline_url(r['stock_id'])}" target="_blank"
       style="font-family:'IBM Plex Mono',monospace;font-weight:700;color:#d4dff0;text-decoration:none;">{r['stock_id']}</a>
    <span style="font-size:.68rem;color:#4a6080;margin-left:4px;">{r.get('stock_name','')}</span>
  </td>
  <td style="text-align:center;">
    <span style="color:{wr_col};font-weight:700;font-family:'IBM Plex Mono',monospace;">{wr}%</span>
  </td>
  <td style="text-align:center;color:{pnl_color(pnl)};font-weight:600;">{pnl_str(pnl)}</td>
  <td style="text-align:center;color:#4a6080;font-size:.72rem;">{cnt}次</td>
</tr>"""


def render_win_table(stats: list[dict], table_id: str) -> str:
    if not stats:
        return '<div style="color:#4a6080;padding:20px;text-align:center;">資料不足（需至少2筆紀錄）</div>'
    rows = "".join(render_win_row(i + 1, r) for i, r in enumerate(stats))
    return f"""
<table class="data-table" id="{table_id}">
  <thead>
    <tr>
      <th style="width:36px;">#</th>
      <th>股票</th>
      <th style="text-align:center;">勝率</th>
      <th style="text-align:center;">平均報酬</th>
      <th style="text-align:center;">次數</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""


def render_winrate_section() -> str:
    combos = {
        "t3_30d": get_win_rate_stats(days=30,  use_t5=False),
        "t5_30d": get_win_rate_stats(days=30,  use_t5=True),
        "t3_90d": get_win_rate_stats(days=90,  use_t5=False),
        "t5_90d": get_win_rate_stats(days=90,  use_t5=True),
        "t3_all": get_win_rate_stats(days=None, use_t5=False),
        "t5_all": get_win_rate_stats(days=None, use_t5=True),
    }

    tables_html = ""
    for key, stats in combos.items():
        visible = "block" if key == "t3_30d" else "none"
        tables_html += f'<div class="win-panel" id="win-{key}" style="display:{visible};">{render_win_table(stats, f"tbl-{key}")}</div>'

    return f"""
<section class="section" id="winrate">
  <div class="section-header">
    <div class="section-title">🏆 歷史勝率排行</div>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">
    <div style="display:flex;gap:4px;background:#0d1220;border:1px solid #1e2d4a;border-radius:8px;padding:4px;">
      <button class="tab-btn active" onclick="switchWin('t3')" id="btn-t3">T+3</button>
      <button class="tab-btn" onclick="switchWin('t5')"  id="btn-t5">T+5</button>
    </div>
    <div style="display:flex;gap:4px;background:#0d1220;border:1px solid #1e2d4a;border-radius:8px;padding:4px;">
      <button class="tab-btn active" onclick="switchRange('30d')" id="btn-30d">近30天</button>
      <button class="tab-btn" onclick="switchRange('90d')"  id="btn-90d">近90天</button>
      <button class="tab-btn" onclick="switchRange('all')"  id="btn-all">全部</button>
    </div>
  </div>
  <div style="overflow-x:auto;">
    {tables_html}
  </div>
</section>"""


# ══════════════════════════════════════════════
#  自選股 Watchlist 頁面
# ══════════════════════════════════════════════

def render_watchlist_section() -> str:
    """
    自選股頁面：
    - 上方管理 UI（新增/刪除，帶說明）
    - 下方今日評分結果卡片（category=WATCH）
    """
    watch_items   = get_watchlist()           # 完整 watchlist（含今日分）
    watch_picks   = get_watchlist_latest_picks()   # 已評分紀錄（有 score）

    # 今日已評分的代號 set
    scored_ids = {p["stock_id"] for p in watch_picks}
    total_cnt  = len(watch_items)
    scored_cnt = len(scored_ids)
    bull_cnt   = sum(1 for p in watch_picks if "偏多" in (p.get("verdict") or ""))

    # ── 管理表格列 ─────────────────────────────
    table_rows = ""
    for item in watch_items:
        sid  = item["stock_id"]
        name = item.get("stock_name") or item.get("display_name") or ""
        note = item.get("note") or ""
        sc   = item.get("kline_score")
        verdict = item.get("verdict") or ""
        close_p = item.get("close_price")

        sc_badge = "—"
        if sc is not None:
            col = score_color(sc)
            bg  = score_bg(sc)
            sc_badge = f'<span style="color:{col};background:{bg};font-family:var(--mono);font-size:.82rem;font-weight:700;padding:2px 8px;border-radius:5px;border:1px solid {col}33;">{sc}</span>'

        verdict_html = f'<span style="font-size:.72rem;color:{"#ff4d6d" if "多" in verdict else ("#4a9eff" if "空" in verdict else "#6a85a8")};">{verdict or "—"}</span>'
        close_html   = f'<span style="font-family:var(--mono);font-size:.8rem;">{close_p:.2f}</span>' if close_p else '<span style="color:#4a6080;">—</span>'
        status_badge = (
            f'<span style="font-size:.7rem;color:#e8b84b;background:#e8b84b18;border:1px solid #e8b84b44;border-radius:4px;padding:2px 7px;">★ 已追蹤</span>'
            if sid in scored_ids else
            f'<span style="font-size:.7rem;color:#4a6080;background:#1a2340;border:1px solid #1e2d4a;border-radius:4px;padding:2px 7px;">待評分</span>'
        )
        note_html = f'<span style="font-size:.68rem;color:#4a6080;">{note}</span>' if note else ""

        table_rows += f"""
<tr class="wl-row" data-sid="{sid}">
  <td>
    <a href="{kline_url(sid)}" target="_blank"
       style="font-family:var(--mono);font-weight:700;color:#d4dff0;text-decoration:none;
              border-bottom:1px dashed #4a6080;">{sid}</a>
  </td>
  <td style="color:#6a85a8;font-size:.8rem;">{name}<br>{note_html}</td>
  <td>{sc_badge}</td>
  <td>{verdict_html}</td>
  <td>{close_html}</td>
  <td>{status_badge}</td>
  <td>
    <button class="wl-del-btn" onclick="wlRemove('{sid}')"
      title="從自選股移除"
      style="background:transparent;border:1px solid #1e2d4a;color:#4a6080;border-radius:5px;
             padding:3px 10px;cursor:pointer;font-size:.72rem;transition:all .15s;"
      onmouseover="this.style.color='#ff4d6d';this.style.borderColor='#ff4d6d66';"
      onmouseout="this.style.color='#4a6080';this.style.borderColor='#1e2d4a';">
      ✕ 移除
    </button>
  </td>
</tr>"""

    empty_row = "" if watch_items else """
<tr><td colspan="7" style="text-align:center;padding:28px 0;color:#4a6080;font-size:.85rem;">
  自選股清單為空，請在上方輸入代號新增。
</td></tr>"""

    # ── 今日評分卡片 ────────────────────────────
    pick_cards = ""
    if watch_picks:
        for p in watch_picks:
            sc  = p.get("kline_score", 0)
            col = score_color(sc)
            bg  = score_bg(sc)
            sid = p.get("stock_id", "")
            name = p.get("display_name") or p.get("stock_name", "")
            circ = round(sc / 100 * 276.46, 1)
            gap  = round(276.46 - circ, 1)
            rsi_v = float(p["rsi"]) if p.get("rsi") else 0
            vr_v  = float(p["vol_ratio"]) if p.get("vol_ratio") else 0
            close_str = f"{p['close_price']:.2f}" if p.get("close_price") else "—"
            rsi_str = f"{rsi_v:.1f}" if rsi_v else "—"
            kd_str  = f"{p['kd_k']:.1f}" if p.get("kd_k") else "—"
            vr_str  = f"{vr_v:.2f}x" if vr_v else "—"
            verdict = p.get("verdict", "")
            verdict_col = "#ff4d6d" if "多" in verdict else ("#4a9eff" if "空" in verdict else "#6a85a8")
            note_tag = f'<span style="font-size:.65rem;color:#e8b84b;background:#e8b84b18;border:1px solid #e8b84b44;border-radius:3px;padding:1px 5px;margin-left:4px;">備註</span>' if p.get("note") else ""

            sigs_raw = p.get("top_signals", "[]")
            try:
                sigs_list = json.loads(sigs_raw) if isinstance(sigs_raw, str) else sigs_raw
            except Exception:
                sigs_list = []
            sigs_json = json.dumps(sigs_list, ensure_ascii=False).replace('"', '&quot;')

            pick_cards += f"""
<div class="pick-card"
  data-score="{sc}" data-cat="WATCH"
  data-rsi="{round(rsi_v,1)}" data-volratio="{round(vr_v,2)}"
  data-trend="[]" data-signals="{sigs_json}"
  data-sid="{sid}" data-name="{name}"
  data-verdict="{verdict}"
  data-score-val="{sc}" data-color="{col}"
  data-price="{close_str}" data-rsi-val="{rsi_str}"
  data-kd="{kd_str}" data-vol="{vr_str}"
  data-cat-label="自選" data-cat-color="#e8b84b"
  onclick="openPickModal(this)"
  style="background:{bg};border:1px solid {col}33;border-radius:10px;padding:10px 14px;
         margin-bottom:6px;display:flex;align-items:center;gap:12px;cursor:pointer;
         transition:border-color .2s;"
  onmouseover="this.style.borderColor='{col}88'" onmouseout="this.style.borderColor='{col}33'">
  <div style="position:relative;width:44px;height:44px;flex-shrink:0;display:flex;align-items:center;justify-content:center;">
    <svg width="44" height="44" viewBox="0 0 44 44" style="position:absolute;">
      <circle cx="22" cy="22" r="18" fill="none" stroke="#1a2340" stroke-width="4"/>
      <circle cx="22" cy="22" r="18" fill="none" stroke="{col}" stroke-width="4"
        stroke-dasharray="{circ} {gap}" stroke-dashoffset="28.3" stroke-linecap="round"
        transform="rotate(-90 22 22)"/>
    </svg>
    <span style="font-size:.78rem;font-weight:700;color:{col};font-family:var(--mono);z-index:1;">{sc}</span>
  </div>
  <div style="flex:1;min-width:0;">
    <div style="display:flex;align-items:center;gap:4px;">
      <a href="{kline_url(sid)}" target="_blank"
         onclick="event.stopPropagation()"
         style="font-family:var(--mono);font-size:.95rem;font-weight:700;color:#d4dff0;
                text-decoration:none;border-bottom:1px dashed #4a6080;">{sid}</a>
      {note_tag}
    </div>
    <div style="font-size:.72rem;color:#6a85a8;margin-top:2px;">{name}</div>
    <div style="font-size:.7rem;color:{verdict_col};font-weight:600;margin-top:2px;">{verdict}</div>
  </div>
  <div style="text-align:right;flex-shrink:0;">
    <div style="font-family:var(--mono);font-size:.88rem;color:#d4dff0;">{close_str}</div>
    <div style="font-size:.62rem;color:#4a6080;margin-top:2px;">RSI {rsi_str} ｜ K {kd_str}</div>
    <div style="font-size:.62rem;color:#e8b84b;">量比 {vr_str}</div>
  </div>
</div>"""
    else:
        pick_cards = '<div style="color:#4a6080;font-size:.82rem;padding:28px 0;text-align:center;">今日尚無評分（明日自動執行）</div>'

    latest_date_label = watch_picks[0]["date"] if watch_picks else "—"

    return f"""
<section class="section" id="watchlist">
  <div class="section-header">
    <div class="section-title">⭐ 自選股 Watchlist</div>
    <div class="section-date">最後評分：{latest_date_label}</div>
  </div>

  <!-- 提示列 -->
  <div style="background:#e8b84b18;border:1px solid #e8b84b44;border-radius:8px;
    padding:10px 16px;margin-bottom:12px;font-size:.78rem;color:#e8b84b;
    display:flex;align-items:center;gap:10px;">
    <span style="font-size:1rem;">⚡</span>
    <span>自選股每天<strong>必定</strong>跑評分，不受當日 CSV 限制。目前追蹤 <strong id="wl-stat-total">{total_cnt}</strong> 支，今日已評分 <strong>{scored_cnt}</strong> 支，偏多訊號 <strong>{bull_cnt}</strong> 支。</span>
  </div>

  <!-- API 連線狀態 -->
  <div style="margin-bottom:12px;font-size:.72rem;">
    <span id="wl-api-status" style="color:#4a6080;">⏳ 檢查 API 連線中...</span>
    <span style="color:#1e2d4a;margin:0 8px;">｜</span>
    <span style="color:#4a6080;">API 伺服器：</span>
    <code style="background:#131a2e;padding:1px 8px;border-radius:3px;font-size:.7rem;color:#4a9eff;">python watchlist_api.py</code>
  </div>

  <!-- 新增區 -->
  <div style="background:var(--s1);border:1px solid var(--border);border-radius:10px;
    padding:14px 16px;margin-bottom:16px;">
    <div style="font-size:.7rem;color:var(--gold);letter-spacing:1.5px;text-transform:uppercase;
      font-family:var(--mono);margin-bottom:10px;">新增自選股</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
      <input id="wl-input-sid" type="text" placeholder="股票代號（如 2330）"
        style="background:var(--s2);border:1px solid var(--border);border-radius:7px;
               padding:7px 12px;color:var(--text);font-size:.82rem;outline:none;
               font-family:var(--mono);width:160px;"
        onkeydown="if(event.key==='Enter')wlAdd()">
      <input id="wl-input-note" type="text" placeholder="備註（選填）"
        style="background:var(--s2);border:1px solid var(--border);border-radius:7px;
               padding:7px 12px;color:var(--text);font-size:.82rem;outline:none;flex:1;min-width:120px;"
        onkeydown="if(event.key==='Enter')wlAdd()">
      <button id="wl-add-btn" onclick="wlAdd()"
        style="background:#4a9eff22;border:1px solid #4a9eff55;color:var(--accent);
               border-radius:7px;padding:7px 18px;font-size:.82rem;cursor:pointer;
               white-space:nowrap;transition:all .15s;"
        onmouseover="this.style.background='#4a9eff33'" onmouseout="this.style.background='#4a9eff22'">
        + 新增
      </button>
    </div>
    <div id="wl-msg" style="font-size:.72rem;margin-top:8px;min-height:1.2em;color:#4a9eff;"></div>
    <div style="font-size:.68rem;color:var(--muted);margin-top:4px;">
      代號直接輸入即可，系統每日自動補齊名稱與評分。最多建議 30 支。
    </div>
  </div>

  <!-- 管理表格 -->
  <div style="background:var(--s1);border:1px solid var(--border);border-radius:10px;
    padding:0;margin-bottom:24px;overflow:hidden;">
    <div style="padding:12px 16px;border-bottom:1px solid var(--border);
      font-size:.7rem;color:var(--muted);letter-spacing:1px;text-transform:uppercase;
      font-family:var(--mono);">
      清單管理
      <span style="margin-left:8px;color:#4a6080;">共 <span id="wl-count-badge">{total_cnt}</span> 支</span>
    </div>
    <div style="overflow-x:auto;">
    <table class="data-table" id="wl-table">
      <thead>
        <tr>
          <th>代號</th>
          <th>名稱 / 備註</th>
          <th>今日分數</th>
          <th>訊號</th>
          <th>收盤價</th>
          <th>狀態</th>
          <th></th>
        </tr>
      </thead>
      <tbody id="wl-tbody">
        {table_rows}
        <tr id="wl-empty-row" style="{'display:none' if watch_items else ''}">
          <td colspan="7" style="text-align:center;padding:28px 0;color:#4a6080;font-size:.85rem;">
            自選股清單為空，請在上方輸入代號新增。
          </td>
        </tr>
      </tbody>
    </table>
    </div>
  </div>

  <!-- 今日評分結果 -->
  <div class="section-header" style="margin-bottom:12px;">
    <div class="section-title" style="font-size:.95rem;">📈 今日評分結果（WATCH）</div>
    <div class="section-date">{scored_cnt} 支</div>
  </div>
  <div style="background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:16px;">
    {pick_cards}
  </div>

  <!-- 統計列 -->
  <div style="display:flex;gap:10px;margin-top:16px;flex-wrap:wrap;">
    <div style="flex:1;min-width:100px;background:var(--s1);border:1px solid var(--border);border-radius:8px;padding:10px 12px;text-align:center;">
      <div style="font-family:var(--mono);font-size:1.4rem;font-weight:700;color:var(--accent);">{total_cnt}</div>
      <div style="font-size:.68rem;color:var(--muted);margin-top:3px;">自選股總數</div>
    </div>
    <div style="flex:1;min-width:100px;background:var(--s1);border:1px solid var(--border);border-radius:8px;padding:10px 12px;text-align:center;">
      <div style="font-family:var(--mono);font-size:1.4rem;font-weight:700;color:#29c5c5;">{scored_cnt}</div>
      <div style="font-size:.68rem;color:var(--muted);margin-top:3px;">今日已評分</div>
    </div>
    <div style="flex:1;min-width:100px;background:var(--s1);border:1px solid var(--border);border-radius:8px;padding:10px 12px;text-align:center;">
      <div style="font-family:var(--mono);font-size:1.4rem;font-weight:700;color:#ff4d6d;">{bull_cnt}</div>
      <div style="font-size:.68rem;color:var(--muted);margin-top:3px;">今日偏多</div>
    </div>
    <div style="flex:1;min-width:100px;background:var(--s1);border:1px solid var(--border);border-radius:8px;padding:10px 12px;text-align:center;">
      <div style="font-family:var(--mono);font-size:1.4rem;font-weight:700;color:#e8b84b;">{round(sum(p.get('kline_score',0) for p in watch_picks)/len(watch_picks)) if watch_picks else "—"}</div>
      <div style="font-size:.68rem;color:var(--muted);margin-top:3px;">平均分數</div>
    </div>
  </div>
</section>

<script>
// ── Watchlist 前端管理（新增/刪除透過 server-side action URL 或直接 reload）──
// 注意：靜態 HTML 不能直接呼叫 Python，這裡的新增/刪除僅示範 UI 回饋。
// 若部署於本地，可改為呼叫 FastAPI / Flask 端點。
// 目前版本：僅顯示訊息，真正修改請透過 CLI 或後台 API。

// ── Watchlist API 設定 ────────────────────────────────────
var WL_API = 'http://localhost:5050';

// 啟動時 ping API，更新連線狀態指示
(function() {{
  fetch(WL_API + '/ping', {{method:'GET', signal: AbortSignal.timeout(1500)}})
    .then(function(r) {{ return r.json(); }})
    .then(function() {{
      var el = document.getElementById('wl-api-status');
      if (el) {{ el.textContent = '🟢 API 已連線'; el.style.color = '#00c896'; }}
    }})
    .catch(function() {{
      var el = document.getElementById('wl-api-status');
      if (el) {{
        el.innerHTML = '🔴 API 未啟動 — 請執行 <code style="background:#131a2e;padding:1px 6px;border-radius:3px;font-size:.72rem;">python watchlist_api.py</code>';
        el.style.color = '#ff4d6d';
      }}
    }});
}})();

function wlAdd() {{
  var sid  = (document.getElementById('wl-input-sid').value  || '').trim().toUpperCase();
  var note = (document.getElementById('wl-input-note').value || '').trim();
  var msg  = document.getElementById('wl-msg');
  var btn  = document.querySelector('#wl-add-btn');

  if (!sid) {{ msg.style.color='#ff4d6d'; msg.textContent='請輸入股票代號'; return; }}

  // 簡易格式檢查：台股代號通常 4~6 碼
  if (!/^[0-9A-Z]{{2,10}}$/.test(sid)) {{
    msg.style.color='#ff4d6d'; msg.textContent='代號格式有誤，請確認後重試'; return;
  }}

  if (btn) {{ btn.disabled = true; btn.textContent = '新增中...'; }}
  msg.style.color='#6a85a8'; msg.textContent='連線中...';

  fetch(WL_API + '/watchlist/add', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{stock_id: sid, note: note}}),
    signal: AbortSignal.timeout(8000),
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function(d) {{
    if (d.ok) {{
      msg.style.color = '#00c896';
      msg.textContent = d.is_new
        ? '✓ 已新增 ' + sid + '（' + (d.stock_name || '名稱待補') + '）— 下次執行時加入評分'
        : '⚡ ' + sid + ' 已存在，備註已更新';
      document.getElementById('wl-input-sid').value  = '';
      document.getElementById('wl-input-note').value = '';
      // 動態插入新列到表格
      if (d.is_new) {{
        _wlInsertRow(sid, d.stock_name || '', note);
        _wlUpdateCount(1);
        _wlHideEmpty();
      }}
    }} else {{
      msg.style.color = '#e8b84b';
      msg.textContent = '⚠ ' + (d.msg || '新增失敗');
    }}
  }})
  .catch(function(err) {{
    msg.style.color = '#ff4d6d';
    msg.textContent = '❌ 無法連線 API — 請確認 watchlist_api.py 正在執行（python watchlist_api.py）';
  }})
  .finally(function() {{
    if (btn) {{ btn.disabled = false; btn.textContent = '+ 新增'; }}
  }});
}}

function wlRemove(sid) {{
  if (!confirm('確定要從自選股移除 ' + sid + '？')) return;

  fetch(WL_API + '/watchlist/remove', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{stock_id: sid}}),
    signal: AbortSignal.timeout(8000),
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function(d) {{
    if (d.ok) {{
      var row = document.querySelector('.wl-row[data-sid="' + sid + '"]');
      if (row) {{ row.style.opacity='0'; row.style.transition='opacity .3s'; setTimeout(function(){{row.remove();}},300); }}
      _wlUpdateCount(-1);
      _wlCheckEmpty();
    }} else {{
      alert('移除失敗：' + (d.msg || '未知錯誤'));
    }}
  }})
  .catch(function() {{
    alert('❌ 無法連線 API — 請確認 watchlist_api.py 正在執行');
  }});
}}

// ── DOM 輔助 ─────────────────────────────────────────────

function _wlInsertRow(sid, name, note) {{
  var tbody = document.getElementById('wl-tbody');
  if (!tbody) return;
  var noteHtml = note ? '<br><span style="font-size:.68rem;color:#4a6080;">' + note + '</span>' : '';
  var tr = document.createElement('tr');
  tr.className = 'wl-row';
  tr.dataset.sid = sid;
  tr.innerHTML =
    '<td><a href="{KLINE_TOOL_URL}?stock=' + sid + '" target="_blank" ' +
      'style="font-family:var(--mono);font-weight:700;color:#d4dff0;text-decoration:none;border-bottom:1px dashed #4a6080;">' + sid + '</a></td>' +
    '<td style="color:#6a85a8;font-size:.8rem;">' + (name||'—') + noteHtml + '</td>' +
    '<td><span style="color:#4a6080;">—</span></td>' +
    '<td><span style="color:#4a6080;font-size:.72rem;">待評分</span></td>' +
    '<td><span style="color:#4a6080;">—</span></td>' +
    '<td><span style="font-size:.7rem;color:#4a6080;background:#1a2340;border:1px solid #1e2d4a;border-radius:4px;padding:2px 7px;">待評分</span></td>' +
    '<td><button class="wl-del-btn" onclick="wlRemove(\'' + sid + '\')" ' +
      'style="background:transparent;border:1px solid #1e2d4a;color:#4a6080;border-radius:5px;padding:3px 10px;cursor:pointer;font-size:.72rem;" ' +
      'onmouseover="this.style.color=\'#ff4d6d\';this.style.borderColor=\'#ff4d6d66\';" ' +
      'onmouseout="this.style.color=\'#4a6080\';this.style.borderColor=\'#1e2d4a\';">✕ 移除</button></td>';
  tbody.insertBefore(tr, tbody.firstChild);
}}

function _wlUpdateCount(delta) {{
  var el = document.getElementById('wl-count-badge');
  if (!el) return;
  var n = parseInt(el.textContent) + delta;
  el.textContent = n;
  // 同步更新頁首統計數字
  var totalEl = document.getElementById('wl-stat-total');
  if (totalEl) totalEl.textContent = n;
}}

function _wlHideEmpty() {{
  var empty = document.getElementById('wl-empty-row');
  if (empty) empty.style.display = 'none';
}}

function _wlCheckEmpty() {{
  var tbody = document.getElementById('wl-tbody');
  if (!tbody) return;
  var rows = tbody.querySelectorAll('.wl-row');
  if (rows.length === 0) {{
    var empty = document.getElementById('wl-empty-row');
    if (empty) empty.style.display = '';
  }}
}}
</script>"""


# ══════════════════════════════════════════════
#  完整 HTML 組裝
# ══════════════════════════════════════════════

def generate_index_html() -> str:
    picks        = get_latest_picks(TOP_N)
    history      = get_history_picks(days=5)
    score_trends = get_all_score_trends(days=7)
    now_str      = datetime.now().strftime("%Y-%m-%d %H:%M")

    today_html     = render_today_section(picks, score_trends)
    history_html   = render_history_section(history)
    winrate_html   = render_winrate_section()
    watchlist_html = render_watchlist_section()

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{SITE_TITLE}</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<style>
:root{{
  --bg:#080c14;--s1:#0d1220;--s2:#131a2e;--s3:#1a2340;
  --border:#1e2d4a;--gold:#e8b84b;--cyan:#29c5c5;
  --text:#d4dff0;--muted:#4a6080;--accent:#4a9eff;
  --font:'Noto Sans TC',sans-serif;
  --mono:'IBM Plex Mono',monospace;
}}
*{{margin:0;padding:0;box-sizing:border-box;}}
html{{scroll-behavior:smooth;}}
body{{background:var(--bg);color:var(--text);font-family:var(--font);min-height:100vh;}}
.container{{max-width:1400px;margin:0 auto;padding:0 20px;}}
.section{{margin-bottom:40px;}}
.section-header{{display:flex;align-items:center;justify-content:space-between;
  margin-bottom:16px;flex-wrap:wrap;gap:8px;}}
.section-title{{font-size:1.1rem;font-weight:700;color:var(--gold);
  font-family:var(--mono);letter-spacing:1px;}}
.section-date{{font-size:.72rem;color:var(--muted);font-family:var(--mono);}}
.navbar{{background:var(--s1);border-bottom:1px solid var(--border);
  padding:14px 0;position:sticky;top:0;z-index:100;}}
.navbar-inner{{display:flex;align-items:center;justify-content:space-between;}}
.navbar-brand{{font-family:var(--mono);font-size:1rem;font-weight:700;color:var(--gold);}}
.navbar-sub{{font-size:.7rem;color:var(--muted);margin-top:2px;}}
.navbar-nav{{display:flex;gap:24px;}}
.navbar-nav a{{color:#9bbfe0;font-size:.85rem;font-weight:600;text-decoration:none;
  transition:color .2s;letter-spacing:.3px;padding-bottom:4px;
  border-bottom:2px solid transparent;}}
.navbar-nav a:hover{{color:#fff;border-bottom-color:#4a6080;}}
.navbar-nav a.nav-active{{color:var(--gold);border-bottom:2px solid var(--gold);}}
.update-badge{{font-size:.65rem;color:var(--muted);font-family:var(--mono);
  background:var(--s2);border:1px solid var(--border);border-radius:4px;padding:3px 8px;}}
.main-content{{padding:28px 0;}}
.today-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;}}
.today-col{{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:16px;}}
.pick-card.hidden{{
  display:none !important;
  margin:0 !important;padding:0 !important;
  border:none !important;height:0 !important;overflow:hidden !important;
}}
.f-btn{{background:transparent;border:1px solid var(--border);color:var(--muted);
  font-size:.72rem;padding:3px 10px;border-radius:5px;cursor:pointer;transition:all .15s;
  font-family:var(--font);white-space:nowrap;}}
.f-btn:hover{{color:var(--text);border-color:#4a6080;}}
.f-btn.active{{background:var(--s3);color:var(--text);border-color:var(--accent);font-weight:600;}}
.data-table{{width:100%;border-collapse:collapse;background:var(--s1);
  border-radius:10px;overflow:hidden;border:1px solid var(--border);}}
.data-table thead tr{{background:var(--s2);border-bottom:2px solid var(--border);}}
.data-table thead th{{padding:10px 12px;text-align:left;font-size:.65rem;
  font-weight:700;letter-spacing:2px;color:var(--muted);text-transform:uppercase;white-space:nowrap;}}
.data-table tbody tr{{border-bottom:1px solid #0f1a2e;transition:background .15s;}}
.data-table tbody tr:hover{{background:rgba(255,255,255,.02);}}
.data-table tbody td{{padding:9px 12px;font-size:.8rem;white-space:nowrap;}}
.tab-btn{{background:transparent;border:none;color:var(--muted);
  font-size:.75rem;padding:4px 12px;border-radius:5px;cursor:pointer;transition:all .2s;
  font-family:var(--font);}}
.tab-btn.active{{background:var(--s3);color:var(--text);font-weight:600;}}
.tab-btn:hover:not(.active){{color:var(--text);}}
.tab-section{{display:none;}}
.tab-section.active{{display:block;}}
.footer{{background:var(--s1);border-top:1px solid var(--border);
  padding:20px 0;text-align:center;font-size:.68rem;color:var(--muted);line-height:2;}}
::-webkit-scrollbar{{width:6px;height:6px;}}
::-webkit-scrollbar-track{{background:var(--bg);}}
::-webkit-scrollbar-thumb{{background:var(--border);border-radius:3px;}}
@media(max-width:900px){{
  .today-grid{{grid-template-columns:1fr;}}
  .navbar-inner{{flex-wrap:wrap;gap:8px;}}
  .navbar-nav{{display:flex;gap:12px;width:100%;justify-content:center;
    padding-top:8px;border-top:1px solid var(--border);}}
  .navbar-nav a{{font-size:.8rem;}}
  .update-badge{{font-size:.6rem;}}
  #today-filters, #history-filters{{gap:8px;}}
  .f-btn{{font-size:.68rem;padding:2px 7px;}}
}}
@media(max-width:600px){{
  .data-table{{font-size:.72rem;}}
  .data-table thead th,.data-table tbody td{{padding:7px 8px;}}
}}
.pick-modal-bg{{
  position:fixed;inset:0;background:rgba(0,0,0,.75);
  display:none;align-items:center;justify-content:center;
  z-index:200;backdrop-filter:blur(3px);
}}
.pick-modal-bg.open{{display:flex;}}
.pick-modal{{
  background:#0d1220;border:1px solid #1e2d4a;border-radius:14px;
  width:500px;max-width:95vw;max-height:90vh;overflow-y:auto;
  box-shadow:0 20px 60px rgba(0,0,0,.6);
}}
.pm-head{{
  padding:16px 20px;border-bottom:1px solid #1e2d4a;
  display:flex;align-items:center;gap:14px;position:sticky;top:0;
  background:#0d1220;z-index:10;
}}
.pm-body{{padding:16px 20px;}}
.pm-close{{
  margin-left:auto;background:transparent;border:none;
  color:#4a6080;font-size:1.2rem;cursor:pointer;padding:2px 6px;
  border-radius:4px;transition:all .15s;
}}
.pm-close:hover{{color:#d4dff0;background:#1a2340;}}
.pm-stat-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:14px;}}
.pm-stat-box{{background:#131a2e;border-radius:8px;padding:8px 10px;}}
.pm-stat-val{{font-family:var(--mono);font-size:1rem;font-weight:700;}}
.pm-stat-lbl{{font-size:.65rem;color:var(--muted);margin-top:2px;}}
.pm-section-lbl{{
  font-size:.68rem;color:var(--muted);letter-spacing:1px;
  text-transform:uppercase;margin:14px 0 8px;
}}
.pm-signal-row{{
  display:flex;align-items:flex-start;gap:8px;
  padding:7px 0;border-bottom:1px solid #0f1a2e;font-size:.78rem;
}}
.pm-signal-row:last-child{{border-bottom:none;}}
.pm-tag{{
  font-size:.68rem;padding:2px 8px;border-radius:4px;
  font-weight:600;white-space:nowrap;
}}
@media(max-width:600px){{
  .pick-modal{{width:100vw;max-height:85vh;border-radius:14px 14px 0 0;}}
  .pick-modal-bg.open{{align-items:flex-end;}}
}}
</style>
</head>
<body>

<nav class="navbar">
  <div class="container navbar-inner">
    <div>
      <div class="navbar-brand">📊 {SITE_TITLE}</div>
      <div class="navbar-sub">{SITE_SUBTITLE}</div>
    </div>
    <nav class="navbar-nav">
      <a href="#" onclick="switchTab('today');return false;" id="nav-today" class="nav-active">今日推薦</a>
      <a href="#" onclick="switchTab('history');return false;" id="nav-history">歷史回測</a>
      <a href="#" onclick="switchTab('winrate');return false;" id="nav-winrate">勝率排行</a>
      <a href="#" onclick="switchTab('watchlist');return false;" id="nav-watchlist">⭐ 自選股</a>
    </nav>
    <div class="update-badge">🕐 {now_str} 更新</div>
  </div>
</nav>

<!-- Pick Modal -->
<div class="pick-modal-bg" id="pick-modal-bg" onclick="if(event.target===this)closePickModal()">
  <div class="pick-modal" onclick="event.stopPropagation()">
    <div class="pm-head">
      <div style="position:relative;width:52px;height:52px;flex-shrink:0;display:flex;align-items:center;justify-content:center;">
        <svg width="52" height="52" viewBox="0 0 52 52" style="position:absolute;">
          <circle cx="26" cy="26" r="22" fill="none" stroke="#1a2340" stroke-width="4"/>
          <circle id="pm-ring-arc" cx="26" cy="26" r="22" fill="none" stroke="#ff4d6d" stroke-width="4"
            stroke-dasharray="138.2 199.5" stroke-dashoffset="34.6" stroke-linecap="round"
            transform="rotate(-90 26 26)"/>
        </svg>
        <span id="pm-score-num" style="font-size:.9rem;font-weight:700;color:#ff4d6d;font-family:'IBM Plex Mono',monospace;z-index:1;">—</span>
      </div>
      <div style="flex:1;min-width:0;">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <a id="pm-sid" href="#" target="_blank"
             style="font-family:'IBM Plex Mono',monospace;font-size:1.15rem;font-weight:700;
                    color:#d4dff0;text-decoration:none;border-bottom:1px dashed #4a6080;">—</a>
          <span id="pm-cat-tag" class="pm-tag"
            style="background:#29c5c518;color:#29c5c5;border:1px solid #29c5c544;">—</span>
        </div>
        <div id="pm-name" style="font-size:.8rem;color:#6a85a8;margin-top:3px;">—</div>
        <div id="pm-verdict" style="font-size:.75rem;font-weight:600;margin-top:3px;color:#ff4d6d;">—</div>
      </div>
      <button class="pm-close" onclick="closePickModal()">✕</button>
    </div>
    <div class="pm-body">
      <div class="pm-section-lbl">🔍 主要評分訊號</div>
      <div style="background:#131a2e;border-radius:8px;padding:8px 12px;" id="pm-signals">
        <div style="color:#4a6080;font-size:.78rem;text-align:center;padding:12px 0;">載入中...</div>
      </div>
      <!-- [修改4] 標題改為「7日收盤價走勢」，圓點=有上榜的日期並標示分數 -->
      <div class="pm-section-lbl">📈 7 日收盤價走勢（圓點為上榜日分數）</div>
      <div style="background:#131a2e;border-radius:8px;padding:12px 14px;">
        <div id="pm-trend-area" style="min-height:96px;"></div>
        <div id="pm-trend-summary" style="font-size:.72rem;color:#6a85a8;margin-top:6px;text-align:center;font-family:'IBM Plex Mono',monospace;"></div>
      </div>
      <div class="pm-section-lbl">📊 技術數值</div>
      <div class="pm-stat-grid">
        <div class="pm-stat-box">
          <div class="pm-stat-val" id="pm-price">—</div>
          <div class="pm-stat-lbl">收盤價</div>
        </div>
        <div class="pm-stat-box">
          <div class="pm-stat-val" id="pm-rsi" style="color:#e8b84b;">—</div>
          <div class="pm-stat-lbl">RSI</div>
        </div>
        <div class="pm-stat-box">
          <div class="pm-stat-val" id="pm-kd" style="color:#29c5c5;">—</div>
          <div class="pm-stat-lbl">KD-K</div>
        </div>
        <div class="pm-stat-box">
          <div class="pm-stat-val" id="pm-vol" style="color:#4a9eff;">—</div>
          <div class="pm-stat-lbl">量比</div>
        </div>
        <div class="pm-stat-box" style="grid-column:span 2;display:flex;align-items:center;justify-content:center;">
          <a id="pm-kline-btn" href="#" target="_blank"
             style="font-size:.78rem;color:#4a9eff;text-decoration:none;
                    border:1px solid #4a9eff44;border-radius:6px;padding:6px 16px;
                    display:inline-block;text-align:center;transition:all .15s;"
             onmouseover="this.style.background='#4a9eff18'" onmouseout="this.style.background='transparent'">
            📈 開啟完整 K 線分析 →
          </a>
        </div>
      </div>
      <div style="margin-top:6px;font-size:.65rem;color:#4a6080;text-align:center;">
        ⚠️ 本評分僅供參考，不構成投資建議。
      </div>
    </div>
  </div>
</div>

<main class="main-content">
  <div class="container">
    <div class="tab-section active" id="tab-today">{today_html}</div>
    <div class="tab-section" id="tab-history">{history_html}</div>
    <div class="tab-section" id="tab-winrate">{winrate_html}</div>
    <div class="tab-section" id="tab-watchlist">{watchlist_html}</div>
  </div>
</main>

<footer class="footer">
  <div class="container">
    <div>⚠️ 本系統由 Python K線評分引擎自動產生，所有分析僅供參考，不構成買賣建議。投資人應自行判斷風險。</div>
    <div style="margin-top:4px;">資料來源：FinMind API ｜ 評分引擎：kline_scorer.py ｜ 最後更新：{now_str}</div>
  </div>
</footer>

<script>
// Tab 切換
function switchTab(tab) {{
  ['today', 'history', 'winrate', 'watchlist'].forEach(function(t) {{
    var sec = document.getElementById('tab-' + t);
    var nav = document.getElementById('nav-' + t);
    if (sec) sec.classList.toggle('active', t === tab);
    if (nav) nav.classList.toggle('nav-active', t === tab);
  }});
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
}}

// 今日推薦篩選
var _todayFilters = {{ cat: '', score: 0, rsi: 'all', vol: 0 }};

document.addEventListener('DOMContentLoaded', function() {{
  document.querySelectorAll('#today-filters .f-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var group = this.dataset.group;
      document.querySelectorAll('#today-filters .f-btn[data-group="' + group + '"]')
        .forEach(function(b) {{ b.classList.remove('active'); }});
      this.classList.add('active');
      var val = this.dataset.val;
      if (group === 'cat')   _todayFilters.cat   = val;
      if (group === 'score') _todayFilters.score  = parseFloat(val);
      if (group === 'rsi')   _todayFilters.rsi   = val;
      if (group === 'vol')   _todayFilters.vol    = parseFloat(val);
      applyTodayFilters();
    }});
  }});
  document.querySelectorAll('#history-filters .f-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var group = this.dataset.group;
      document.querySelectorAll('#history-filters .f-btn[data-group="' + group + '"]')
        .forEach(function(b) {{ b.classList.remove('active'); }});
      this.classList.add('active');
      applyHistoryFilters();
    }});
  }});
  updateHistoryCount();
}});

function applyTodayFilters() {{
  var f = _todayFilters;
  ['ETF','OTC','TSE'].forEach(function(cat) {{
    var col = document.getElementById('col-' + cat);
    if (!col) return;
    if (f.cat && f.cat !== cat) {{ col.style.display = 'none'; return; }}
    col.style.display = '';
    var cards = col.querySelectorAll('.pick-card');
    var visible = 0;
    cards.forEach(function(card) {{
      var score = parseFloat(card.dataset.score || 0);
      var rsi   = parseFloat(card.dataset.rsi || 0);
      var vr    = parseFloat(card.dataset.volratio || 0);
      var ok = true;
      if (score < f.score) ok = false;
      if (f.rsi === '50-70' && !(rsi >= 50 && rsi < 70)) ok = false;
      if (f.rsi === '70+'   && rsi < 70) ok = false;
      if (f.rsi === '50-'   && rsi >= 50) ok = false;
      if (vr < f.vol) ok = false;
      if (ok) {{ card.classList.remove('hidden'); visible++; }}
      else card.classList.add('hidden');
    }});
    var noResult = col.querySelector('.no-result');
    if (noResult) noResult.style.display = visible === 0 ? '' : 'none';
  }});
  var grid = document.querySelector('.today-grid');
  if (grid) grid.style.gridTemplateColumns = f.cat ? '1fr' : 'repeat(3,1fr)';
}}

function resetTodayFilters() {{
  _todayFilters = {{ cat: '', score: 0, rsi: 'all', vol: 0 }};
  document.querySelectorAll('#today-filters .f-btn').forEach(function(btn) {{ btn.classList.remove('active'); }});
  document.querySelectorAll('#today-filters .f-btn[data-val=""]').forEach(function(btn) {{ btn.classList.add('active'); }});
  document.querySelector('#today-filters .f-btn[data-group="score"][data-val="0"]').classList.add('active');
  document.querySelector('#today-filters .f-btn[data-group="rsi"][data-val="all"]').classList.add('active');
  document.querySelector('#today-filters .f-btn[data-group="vol"][data-val="0"]').classList.add('active');
  applyTodayFilters();
}}

// 歷史回測篩選
function applyHistoryFilters() {{
  var kw    = (document.getElementById('history-search').value || '').trim().toLowerCase();
  var cat   = getActiveVal('hcat');
  var score = parseFloat(getActiveVal('hscore') || 0);
  var vol   = parseFloat(getActiveVal('hvol') || 0);
  var pnl   = getActiveVal('hpnl');
  var rows = document.querySelectorAll('#history-body .history-row');
  var shown = 0;
  rows.forEach(function(tr) {{
    var search  = (tr.dataset.search || '').toLowerCase();
    var trCat   = tr.dataset.cat   || '';
    var trScore = parseFloat(tr.dataset.score   || 0);
    var trVr    = parseFloat(tr.dataset.volratio || 0);
    var cells = tr.querySelectorAll('td');
    var t3text = cells[7] ? cells[7].textContent.trim() : '';
    var t3val  = parseFloat(t3text.replace('%','').replace('+',''));
    var ok = true;
    if (kw && !search.includes(kw)) ok = false;
    if (cat && trCat !== cat) ok = false;
    if (trScore < score) ok = false;
    if (trVr < vol) ok = false;
    if (pnl === 'win'  && !(t3val > 0)) ok = false;
    if (pnl === 'loss' && !(t3val < 0)) ok = false;
    tr.style.display = ok ? '' : 'none';
    if (ok) shown++;
  }});
  updateHistoryCount(shown, rows.length);
}}

function getActiveVal(group) {{
  var btn = document.querySelector('#history-filters .f-btn[data-group="' + group + '"].active');
  return btn ? btn.dataset.val : '';
}}

function updateHistoryCount(shown, total) {{
  var el = document.getElementById('history-count');
  if (!el) return;
  if (shown === undefined) {{
    var all = document.querySelectorAll('#history-body .history-row');
    shown = all.length; total = all.length;
  }}
  el.textContent = '顯示 ' + shown + ' / ' + total + ' 筆';
}}

function resetHistoryFilters() {{
  document.getElementById('history-search').value = '';
  document.querySelectorAll('#history-filters .f-btn').forEach(function(btn) {{ btn.classList.remove('active'); }});
  [['hcat',''],['hscore','0'],['hvol','0'],['hpnl','all']].forEach(function(pair) {{
    var btn = document.querySelector('#history-filters .f-btn[data-group="' + pair[0] + '"][data-val="' + pair[1] + '"]');
    if (btn) btn.classList.add('active');
  }});
  applyHistoryFilters();
}}

function filterHistory(q) {{
  document.getElementById('history-search').value = q;
  applyHistoryFilters();
}}

// Modal
var _modalBg = null;
function _getModalBg() {{
  if (!_modalBg) _modalBg = document.getElementById('pick-modal-bg');
  return _modalBg;
}}

function openPickModal(card) {{
  var sid     = card.dataset.sid;
  var name    = card.dataset.name;
  var score   = parseInt(card.dataset.scoreVal || card.dataset.score || 0);
  var col     = card.dataset.color;
  var verdict = card.dataset.verdict;
  var price   = card.dataset.price;
  var rsi     = card.dataset.rsiVal;
  var kd      = card.dataset.kd;
  var vol     = card.dataset.vol;
  var catLbl  = card.dataset.catLabel;
  var catCol  = card.dataset.catColor;

  var trend = [];
  try {{ trend = JSON.parse(card.dataset.trend || '[]'); }} catch(e) {{}}

  var signals = [];
  try {{ signals = JSON.parse((card.dataset.signals || '[]').replace(/&quot;/g,'"')); }} catch(e) {{}}

  document.getElementById('pm-sid').textContent = sid;
  document.getElementById('pm-sid').href = '{KLINE_TOOL_URL}?stock=' + sid;
  document.getElementById('pm-name').textContent = name;
  document.getElementById('pm-verdict').textContent = verdict;
  document.getElementById('pm-verdict').style.color = col;
  document.getElementById('pm-score-num').textContent = score;
  document.getElementById('pm-score-num').style.color = col;
  document.getElementById('pm-cat-tag').textContent = catLbl;
  document.getElementById('pm-cat-tag').style.color = catCol;
  document.getElementById('pm-cat-tag').style.borderColor = catCol + '55';
  document.getElementById('pm-cat-tag').style.background = catCol + '18';

  var circ = Math.round(score / 100 * 276.46 * 10) / 10;
  var gap  = Math.round((276.46 - circ) * 10) / 10;
  document.getElementById('pm-ring-arc').setAttribute('stroke-dasharray', circ + ' ' + gap);
  document.getElementById('pm-ring-arc').setAttribute('stroke', col);

  document.getElementById('pm-price').textContent = price;
  document.getElementById('pm-price').style.color = col;
  document.getElementById('pm-rsi').textContent = rsi;
  document.getElementById('pm-kd').textContent = kd;
  document.getElementById('pm-vol').textContent = vol;

  _drawTrendChart(trend, col, score);
  _renderSignals(signals);

  document.getElementById('pm-kline-btn').href = '{KLINE_TOOL_URL}?stock=' + sid;
  _getModalBg().classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closePickModal() {{
  _getModalBg().classList.remove('open');
  document.body.style.overflow = '';
}}

// [修改4] Modal 大圖：用 close 收盤價畫折線，score 不為 null 的日期加圓點+分數標記
function _drawTrendChart(trend, col, currentScore) {{
  var container = document.getElementById('pm-trend-area');
  if (!trend || trend.length < 2) {{
    container.innerHTML = '<div style="color:#4a6080;font-size:.78rem;text-align:center;padding:20px 0;">歷史資料不足（需至少2天）</div>';
    return;
  }}

  // 支援兩種格式：舊格式 {{d,s}} / 新格式 {{d,s,close}}
  var hasPriceData = trend[0].close !== undefined && trend[0].close !== null;
  // 折線用 close（若有），否則 fallback 到 s（分數）
  var rawValues = trend.map(function(t) {{ return hasPriceData ? t.close : t.s; }});
  var rawScores = trend.map(function(t) {{ return (t.s !== undefined) ? t.s : null; }});
  var rawDates  = trend.map(function(t) {{ return (t.d || t.date || '').slice(5); }});

  // 過濾 null（close 應該不會有，純防呆）
  var validIdx = rawValues.map(function(v,i){{return (v!=null)?i:-1;}}).filter(function(i){{return i>=0;}});
  if (validIdx.length < 2) {{
    container.innerHTML = '<div style="color:#4a6080;font-size:.78rem;text-align:center;padding:20px 0;">歷史資料不足</div>';
    return;
  }}

  var values = validIdx.map(function(i){{return rawValues[i];}});
  var scores = validIdx.map(function(i){{return rawScores[i];}});
  var dates  = validIdx.map(function(i){{return rawDates[i];}});
  var n = values.length;

  var mn = Math.min.apply(null, values) * 0.995;
  var mx = Math.max.apply(null, values) * 1.005;
  if (mx <= mn) mx = mn + 1;

  var W = 400, H = 80, padL = 8, padR = 8, padT = 14, padB = 4;

  function toX(i) {{ return padL + i / (n - 1) * (W - padL - padR); }}
  function toY(v) {{ return padT + (1 - (v - mn) / (mx - mn)) * (H - padT - padB); }}

  var pts = values.map(function(v, i) {{
    return toX(i).toFixed(1) + ',' + toY(v).toFixed(1);
  }}).join(' ');

  var gradId = 'grad-' + Math.random().toString(36).slice(2);
  var lastX = toX(n - 1).toFixed(1);
  var lastY = toY(values[n - 1]).toFixed(1);

  // 首尾收盤價標籤
  var priceLabels = '';
  if (hasPriceData) {{
    priceLabels +=
      '<text x="' + padL + '" y="' + (toY(values[0]) - 4).toFixed(1) + '" font-size="8" fill="#4a6080" text-anchor="middle">' + values[0].toFixed(1) + '</text>' +
      '<text x="' + lastX + '" y="' + (toY(values[n-1]) - 4).toFixed(1) + '" font-size="8" fill="' + col + '" text-anchor="middle" font-weight="700">' + values[n-1].toFixed(1) + '</text>';
  }}

  // 有上榜 score 的日期：打圓點 + 顯示分數
  var scoreDots = '';
  scores.forEach(function(sc, i) {{
    if (sc === null || sc === undefined) return;
    var cx = toX(i).toFixed(1);
    var cy = toY(values[i]).toFixed(1);
    scoreDots += '<circle cx="' + cx + '" cy="' + cy + '" r="3" fill="' + col + '" opacity="0.9"/>';
    var textY = (parseFloat(cy) - 6).toFixed(1);
    scoreDots += '<text x="' + cx + '" y="' + textY + '" font-size="8" fill="' + col + '" text-anchor="middle" font-weight="600">' + sc + '</text>';
  }});

  var dateLabels =
    '<text x="' + padL + '" y="' + (H + 12) + '" font-size="8" fill="#4a6080" text-anchor="middle">' + dates[0] + '</text>' +
    '<text x="' + lastX + '" y="' + (H + 12) + '" font-size="8" fill="' + col + '" text-anchor="middle">' + dates[n-1] + '</text>';

  container.innerHTML =
    '<svg width="100%" viewBox="0 0 ' + W + ' ' + (H + 16) + '" preserveAspectRatio="xMidYMid meet">' +
    '<defs><linearGradient id="' + gradId + '" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0%" stop-color="' + col + '" stop-opacity="0.2"/>' +
    '<stop offset="100%" stop-color="' + col + '" stop-opacity="0"/>' +
    '</linearGradient></defs>' +
    '<polygon points="' + padL + ',' + H + ' ' + pts + ' ' + lastX + ',' + H + '" fill="url(#' + gradId + ')"/>' +
    '<polyline points="' + pts + '" fill="none" stroke="' + col + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
    priceLabels + scoreDots + dateLabels +
    '</svg>';

  var delta = hasPriceData
    ? ((values[n-1] - values[0]) / values[0] * 100).toFixed(2) + '%'
    : ((scores[n-1] || 0) - (scores[0] || 0) > 0 ? '+' : '') + ((scores[n-1] || 0) - (scores[0] || 0)) + ' pts';
  var deltaCol = (values[n-1] >= values[0]) ? '#ff4d6d' : '#4a9eff';
  var labelA = hasPriceData ? values[0].toFixed(1) : (scores[0] || '—');
  var labelB = hasPriceData ? values[n-1].toFixed(1) : (scores[n-1] || '—');

  document.getElementById('pm-trend-summary').innerHTML =
    '<span style="color:#6a85a8;">' + dates[0] + '：' + labelA + '</span>' +
    '&nbsp;→&nbsp;' +
    '<span style="color:' + col + ';font-weight:700;">' + dates[n-1] + '：' + labelB + '</span>' +
    '&nbsp;<span style="color:' + deltaCol + ';">(' + delta + ')</span>';
}}

function _renderSignals(signals) {{
  var container = document.getElementById('pm-signals');
  if (!signals || signals.length === 0) {{
    container.innerHTML = '<div style="color:#4a6080;font-size:.78rem;text-align:center;padding:12px 0;">無訊號資料</div>';
    return;
  }}
  var typeMap = {{
    'bull':    {{ icon: '▲', color: '#ff4d6d' }},
    'bear':    {{ icon: '▼', color: '#00c896' }},
    'neutral': {{ icon: '◆', color: '#6a85a8' }},
  }};
  var catMap = {{
    'ma':'MA','rsi':'RSI','kd':'KD','macd':'MACD','vol':'量能','pattern':'型態','chip':'籌碼',
    '均線':'均線','RSI':'RSI','KD':'KD','MACD':'MACD','量價':'量價','布林':'布林',
    'K線型態':'K線','支撐壓力':'支撐','籌碼':'籌碼','RSI背離':'背離',
  }};
  container.innerHTML = signals.map(function(sig) {{
    var t = typeMap[sig.type] || typeMap['neutral'];
    var catStr = catMap[sig.cat] || sig.cat || '';
    return '<div class="pm-signal-row">' +
      '<span style="color:' + t.color + ';font-size:.9rem;flex-shrink:0;">' + t.icon + '</span>' +
      '<span style="flex:1;color:#d4dff0;">' + (sig.text || '') + '</span>' +
      (catStr ? '<span class="pm-tag" style="background:' + t.color + '18;color:' + t.color + ';border:1px solid ' + t.color + '33;">' + catStr + '</span>' : '') +
      '</div>';
  }}).join('');
}}

document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closePickModal();
}});

// 勝率切換
var _winType = 't3', _winRange = '30d';
function switchWin(type) {{
  _winType = type;
  document.querySelectorAll('[id^="btn-t"]').forEach(function(b) {{ b.classList.remove('active'); }});
  document.getElementById('btn-' + type).classList.add('active');
  _showWinPanel();
}}
function switchRange(range) {{
  _winRange = range;
  ['30d','90d','all'].forEach(function(r) {{
    document.getElementById('btn-' + r).classList.remove('active');
  }});
  document.getElementById('btn-' + range).classList.add('active');
  _showWinPanel();
}}
function _showWinPanel() {{
  document.querySelectorAll('.win-panel').forEach(function(p) {{ p.style.display = 'none'; }});
  var key = _winType + '_' + _winRange;
  var panel = document.getElementById('win-' + key);
  if (panel) panel.style.display = 'block';
}}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════

def generate_all() -> None:
    """生成 docs/index.html"""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    html = generate_index_html()
    out  = DOCS_DIR / "index.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"HTML 生成完成：{out}（{len(html):,} bytes）")


if __name__ == "__main__":
    import sys, logging
    logging.basicConfig(level=logging.INFO)
    sys.path.insert(0, str(Path(__file__).parent))
    generate_all()
    print("Done!")
