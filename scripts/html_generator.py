"""
html_generator.py  ── PATCHED (修改 1 & 2 & 3)
=================
靜態 HTML 生成器：從 DB 資料生成 docs/index.html

修改記錄：
  [修改1] render_pick_card：中文名稱移到代號正下方（flex-direction:column）
  [修改2] generate_index_html：navbar 連結變亮 + 三個 section 改成 Tab 切換（點一頁只顯示一項）
  [修改3] 多條件篩選器：今日推薦卡片 + 歷史回測表格均支援分數/類別/RSI/量比篩選
"""

import json
import logging
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from config import DOCS_DIR, DOCS_DATA_DIR, SITE_TITLE, SITE_SUBTITLE, KLINE_TOOL_URL, TOP_N
from db_manager import get_latest_picks, get_history_picks, get_win_rate_stats, get_all_score_trends

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
#  [修改1] 代號+名稱改為直排
#  [修改3] 加入 data-* 屬性供 JS 篩選
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

    # 趨勢資料序列化給 JS
    trend_json = "[]"
    mini_svg = ""
    if trend and len(trend) >= 2:
        scores = [t["score"] for t in trend]
        dates  = [t["date"] for t in trend]
        trend_json = json.dumps([{"d": d, "s": s} for d, s in zip(dates, scores)])

        # 迷你折線 SVG（60x28）
        mn = min(scores) - 5
        mx = max(scores) + 5
        if mx == mn:
            mx = mn + 1
        n = len(scores)
        pts = " ".join(
            f"{round(i / (n - 1) * 56, 1)},{round(26 - (s - mn) / (mx - mn) * 22, 1)}"
            for i, s in enumerate(scores)
        )
        last_x = round((n - 1) / (n - 1) * 56, 1)
        last_y = round(26 - (scores[-1] - mn) / (mx - mn) * 22, 1)
        prev_s = scores[-2] if len(scores) >= 2 else scores[-1]
        arrow  = "↑" if scores[-1] > prev_s else ("↓" if scores[-1] < prev_s else "→")
        tr_col = col if scores[-1] >= prev_s else "#4a9eff"
        delta  = scores[-1] - scores[0]
        delta_str = f"{scores[0]}→{scores[-1]} {arrow}"

        mini_svg = f"""
  <!-- 迷你趨勢折線 -->
  <div style="flex-shrink:0;text-align:center;cursor:pointer;" title="點擊查看詳情">
    <svg width="60" height="28" viewBox="0 0 60 28">
      <polyline points="{pts}"
        fill="none" stroke="{tr_col}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="{last_x}" cy="{last_y}" r="2.5" fill="{tr_col}"/>
    </svg>
    <div style="font-size:.58rem;color:{tr_col};margin-top:1px;font-family:'IBM Plex Mono',monospace;">{delta_str}</div>
  </div>"""
    else:
        mini_svg = '<div style="width:60px;flex-shrink:0;"></div>'

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

  <!-- [修改3] 今日推薦篩選器 -->
  <div id="today-filters" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px;
    padding:12px 16px;background:var(--s1);border:1px solid var(--border);border-radius:10px;align-items:center;">

    <!-- 類別篩選 -->
    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">類別</span>
      <button class="f-btn active" data-group="cat" data-val="">全部</button>
      <button class="f-btn" data-group="cat" data-val="ETF">ETF</button>
      <button class="f-btn" data-group="cat" data-val="OTC">上櫃</button>
      <button class="f-btn" data-group="cat" data-val="TSE">上市</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <!-- 分數篩選 -->
    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">分數</span>
      <button class="f-btn active" data-group="score" data-val="0">全部</button>
      <button class="f-btn" data-group="score" data-val="62">62+</button>
      <button class="f-btn" data-group="score" data-val="78">78+</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <!-- RSI 篩選 -->
    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">RSI</span>
      <button class="f-btn active" data-group="rsi" data-val="all">全部</button>
      <button class="f-btn" data-group="rsi" data-val="50-70">50-70</button>
      <button class="f-btn" data-group="rsi" data-val="70+">70+</button>
      <button class="f-btn" data-group="rsi" data-val="50-">50以下</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <!-- 量比篩選 -->
    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">量比</span>
      <button class="f-btn active" data-group="vol" data-val="0">全部</button>
      <button class="f-btn" data-group="vol" data-val="1.5">1.5x+</button>
      <button class="f-btn" data-group="vol" data-val="2">2x+</button>
    </div>

    <!-- 重設 -->
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
#  [修改3] 加入多條件篩選列
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

  <!-- [修改3] 歷史回測篩選器 -->
  <div id="history-filters" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:16px;
    padding:12px 16px;background:var(--s1);border:1px solid var(--border);border-radius:10px;align-items:center;">

    <!-- 搜尋 -->
    <input id="history-search" type="text" placeholder="搜尋代號或名稱..."
      style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:5px 10px;
             color:var(--text);font-size:.78rem;outline:none;width:150px;"
      oninput="applyHistoryFilters()">

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <!-- 類別 -->
    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">類別</span>
      <button class="f-btn active" data-group="hcat" data-val="">全部</button>
      <button class="f-btn" data-group="hcat" data-val="ETF">ETF</button>
      <button class="f-btn" data-group="hcat" data-val="OTC">上櫃</button>
      <button class="f-btn" data-group="hcat" data-val="TSE">上市</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <!-- 分數 -->
    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">分數</span>
      <button class="f-btn active" data-group="hscore" data-val="0">全部</button>
      <button class="f-btn" data-group="hscore" data-val="62">62+</button>
      <button class="f-btn" data-group="hscore" data-val="78">78+</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <!-- 量比 -->
    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">量比</span>
      <button class="f-btn active" data-group="hvol" data-val="0">全部</button>
      <button class="f-btn" data-group="hvol" data-val="1.5">1.5x+</button>
      <button class="f-btn" data-group="hvol" data-val="2">2x+</button>
    </div>

    <div style="width:1px;height:24px;background:var(--border);"></div>

    <!-- 損益 -->
    <div style="display:flex;gap:4px;align-items:center;">
      <span style="font-size:.68rem;color:var(--muted);margin-right:2px;">T+3</span>
      <button class="f-btn active" data-group="hpnl" data-val="all">全部</button>
      <button class="f-btn" data-group="hpnl" data-val="win">獲利</button>
      <button class="f-btn" data-group="hpnl" data-val="loss">虧損</button>
    </div>

    <!-- 重設 -->
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
#  完整 HTML 組裝
# ══════════════════════════════════════════════

def generate_index_html() -> str:
    picks        = get_latest_picks(TOP_N)
    history      = get_history_picks(days=5)
    score_trends = get_all_score_trends(days=7)
    now_str      = datetime.now().strftime("%Y-%m-%d %H:%M")

    today_html   = render_today_section(picks, score_trends)
    history_html = render_history_section(history)
    winrate_html = render_winrate_section()

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{SITE_TITLE}</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@300;400;500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<style>
/* ── CSS Variables ── */
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

/* ── Layout ── */
.container{{max-width:1400px;margin:0 auto;padding:0 20px;}}
.section{{margin-bottom:40px;}}
.section-header{{display:flex;align-items:center;justify-content:space-between;
  margin-bottom:16px;flex-wrap:wrap;gap:8px;}}
.section-title{{font-size:1.1rem;font-weight:700;color:var(--gold);
  font-family:var(--mono);letter-spacing:1px;}}
.section-date{{font-size:.72rem;color:var(--muted);font-family:var(--mono);}}

/* ── Navbar ── */
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

/* ── Main Content ── */
.main-content{{padding:28px 0;}}

/* ── Today Grid ── */
.today-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;}}
.today-col{{background:var(--s1);border:1px solid var(--border);border-radius:12px;padding:16px;}}

/* ── 篩選隱藏卡片：不佔空間 ── */
.pick-card.hidden{{
  display:none !important;
  margin:0 !important;
  padding:0 !important;
  border:none !important;
  height:0 !important;
  overflow:hidden !important;
}}

/* ── Filter Buttons ── */
.f-btn{{background:transparent;border:1px solid var(--border);color:var(--muted);
  font-size:.72rem;padding:3px 10px;border-radius:5px;cursor:pointer;transition:all .15s;
  font-family:var(--font);white-space:nowrap;}}
.f-btn:hover{{color:var(--text);border-color:#4a6080;}}
.f-btn.active{{background:var(--s3);color:var(--text);border-color:var(--accent);font-weight:600;}}

/* ── Data Table ── */
.data-table{{width:100%;border-collapse:collapse;background:var(--s1);
  border-radius:10px;overflow:hidden;border:1px solid var(--border);}}
.data-table thead tr{{background:var(--s2);border-bottom:2px solid var(--border);}}
.data-table thead th{{padding:10px 12px;text-align:left;font-size:.65rem;
  font-weight:700;letter-spacing:2px;color:var(--muted);text-transform:uppercase;white-space:nowrap;}}
.data-table tbody tr{{border-bottom:1px solid #0f1a2e;transition:background .15s;}}
.data-table tbody tr:hover{{background:rgba(255,255,255,.02);}}
.data-table tbody td{{padding:9px 12px;font-size:.8rem;white-space:nowrap;}}

/* ── Tab Buttons (win rate) ── */
.tab-btn{{background:transparent;border:none;color:var(--muted);
  font-size:.75rem;padding:4px 12px;border-radius:5px;cursor:pointer;transition:all .2s;
  font-family:var(--font);}}
.tab-btn.active{{background:var(--s3);color:var(--text);font-weight:600;}}
.tab-btn:hover:not(.active){{color:var(--text);}}

/* ── Tab sections ── */
.tab-section{{display:none;}}
.tab-section.active{{display:block;}}

/* ── Footer ── */
.footer{{background:var(--s1);border-top:1px solid var(--border);
  padding:20px 0;text-align:center;font-size:.68rem;color:var(--muted);line-height:2;}}

/* ── Scrollbar ── */
::-webkit-scrollbar{{width:6px;height:6px;}}
::-webkit-scrollbar-track{{background:var(--bg);}}
::-webkit-scrollbar-thumb{{background:var(--border);border-radius:3px;}}

/* ── Responsive ── */
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

/* ── Pick Modal ── */
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

<!-- ── Navbar ── -->
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
    </nav>
    <div class="update-badge">🕐 {now_str} 更新</div>
  </div>
</nav>

<!-- ── Pick Modal ── -->
<div class="pick-modal-bg" id="pick-modal-bg" onclick="if(event.target===this)closePickModal()">
  <div class="pick-modal" onclick="event.stopPropagation()">

    <!-- Header -->
    <div class="pm-head">
      <!-- 圓環 -->
      <div style="position:relative;width:52px;height:52px;flex-shrink:0;display:flex;align-items:center;justify-content:center;">
        <svg width="52" height="52" viewBox="0 0 52 52" style="position:absolute;">
          <circle cx="26" cy="26" r="22" fill="none" stroke="#1a2340" stroke-width="4"/>
          <circle id="pm-ring-arc" cx="26" cy="26" r="22" fill="none" stroke="#ff4d6d" stroke-width="4"
            stroke-dasharray="138.2 199.5" stroke-dashoffset="34.6" stroke-linecap="round"
            transform="rotate(-90 26 26)"/>
        </svg>
        <span id="pm-score-num" style="font-size:.9rem;font-weight:700;color:#ff4d6d;font-family:'IBM Plex Mono',monospace;z-index:1;">—</span>
      </div>
      <!-- 股票資訊 -->
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

    <!-- Body -->
    <div class="pm-body">

      <!-- 數值 -->
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

      <!-- 7日分數趨勢 -->
      <div class="pm-section-lbl">📈 7 日分數趨勢</div>
      <div style="background:#131a2e;border-radius:8px;padding:12px 14px;">
        <div id="pm-trend-area" style="min-height:96px;"></div>
        <div id="pm-trend-summary" style="font-size:.72rem;color:#6a85a8;margin-top:6px;text-align:center;font-family:'IBM Plex Mono',monospace;"></div>
      </div>

      <!-- 訊號 -->
      <div class="pm-section-lbl">🔍 主要評分訊號</div>
      <div style="background:#131a2e;border-radius:8px;padding:8px 12px;" id="pm-signals">
        <div style="color:#4a6080;font-size:.78rem;text-align:center;padding:12px 0;">載入中...</div>
      </div>

      <div style="margin-top:14px;font-size:.65rem;color:#4a6080;text-align:center;">
        ⚠️ 本評分僅供參考，不構成投資建議。
      </div>
    </div>
  </div>
</div>

<!-- ── Main ── -->
<main class="main-content">
  <div class="container">

    <div class="tab-section active" id="tab-today">
      {today_html}
    </div>

    <div class="tab-section" id="tab-history">
      {history_html}
    </div>

    <div class="tab-section" id="tab-winrate">
      {winrate_html}
    </div>

  </div>
</main>

<!-- ── Footer ── -->
<footer class="footer">
  <div class="container">
    <div>⚠️ 本系統由 Python K線評分引擎自動產生，所有分析僅供參考，不構成買賣建議。投資人應自行判斷風險。</div>
    <div style="margin-top:4px;">資料來源：FinMind API ｜ 評分引擎：kline_scorer.py ｜ 最後更新：{now_str}</div>
  </div>
</footer>

<script>
// ══════════════════════════════════════════
//  Tab 切換
// ══════════════════════════════════════════
function switchTab(tab) {{
  ['today', 'history', 'winrate'].forEach(function(t) {{
    var sec = document.getElementById('tab-' + t);
    var nav = document.getElementById('nav-' + t);
    if (sec) sec.classList.toggle('active', t === tab);
    if (nav) nav.classList.toggle('nav-active', t === tab);
  }});
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
}}

// ══════════════════════════════════════════
//  [修改3] 今日推薦篩選
// ══════════════════════════════════════════
var _todayFilters = {{ cat: '', score: 0, rsi: 'all', vol: 0 }};

// f-btn 點擊事件（統一綁定）
document.addEventListener('DOMContentLoaded', function() {{
  document.querySelectorAll('#today-filters .f-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var group = this.dataset.group;
      // 同 group 的按鈕取消 active
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

    // 類別欄位整體顯示/隱藏
    if (f.cat && f.cat !== cat) {{
      col.style.display = 'none';
      return;
    }}
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

      if (ok) {{
        card.classList.remove('hidden');
      }} else {{
        card.classList.add('hidden');
      }}
      if (ok) visible++;
    }});

    var noResult = col.querySelector('.no-result');
    if (noResult) noResult.style.display = visible === 0 ? '' : 'none';
  }});

  // 類別篩選時動態調整 grid 欄數，避免空欄撐開
  var grid = document.querySelector('.today-grid');
  if (grid) {{
    grid.style.gridTemplateColumns = f.cat ? '1fr' : 'repeat(3,1fr)';
  }}
}}

function resetTodayFilters() {{
  _todayFilters = {{ cat: '', score: 0, rsi: 'all', vol: 0 }};
  document.querySelectorAll('#today-filters .f-btn').forEach(function(btn) {{
    btn.classList.remove('active');
  }});
  document.querySelectorAll('#today-filters .f-btn[data-val=""]').forEach(function(btn) {{
    btn.classList.add('active');
  }});
  document.querySelector('#today-filters .f-btn[data-group="score"][data-val="0"]').classList.add('active');
  document.querySelector('#today-filters .f-btn[data-group="rsi"][data-val="all"]').classList.add('active');
  document.querySelector('#today-filters .f-btn[data-group="vol"][data-val="0"]').classList.add('active');
  applyTodayFilters();
}}

// ══════════════════════════════════════════
//  [修改3] 歷史回測篩選
// ══════════════════════════════════════════
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

    // T+3 損益讀法：從第8個 td（index 7）
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
  document.querySelectorAll('#history-filters .f-btn').forEach(function(btn) {{
    btn.classList.remove('active');
  }});
  // 重設各 group 的預設 active
  [['hcat',''],['hscore','0'],['hvol','0'],['hpnl','all']].forEach(function(pair) {{
    var btn = document.querySelector(
      '#history-filters .f-btn[data-group="' + pair[0] + '"][data-val="' + pair[1] + '"]'
    );
    if (btn) btn.classList.add('active');
  }});
  applyHistoryFilters();
}}

// ── 舊版 filterHistory 保持相容 ──
function filterHistory(q) {{
  document.getElementById('history-search').value = q;
  applyHistoryFilters();
}}

// ══════════════════════════════════════════
//  個股詳細 Modal
// ══════════════════════════════════════════
var _modalBg = null;

function _getModalBg() {{
  if (!_modalBg) _modalBg = document.getElementById('pick-modal-bg');
  return _modalBg;
}}

function openPickModal(card) {{
  var sid      = card.dataset.sid;
  var name     = card.dataset.name;
  var score    = parseInt(card.dataset.scoreVal || card.dataset.score || 0);
  var col      = card.dataset.color;
  var verdict  = card.dataset.verdict;
  var price    = card.dataset.price;
  var rsi      = card.dataset.rsiVal;
  var kd       = card.dataset.kd;
  var vol      = card.dataset.vol;
  var catLbl   = card.dataset.catLabel;
  var catCol   = card.dataset.catColor;

  var trend = [];
  try {{ trend = JSON.parse(card.dataset.trend || '[]'); }} catch(e) {{}}

  var signals = [];
  try {{ signals = JSON.parse((card.dataset.signals || '[]').replace(/&quot;/g,'"')); }} catch(e) {{}}

  // 填入 header
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

  // 圓環
  var circ = Math.round(score / 100 * 276.46 * 10) / 10;
  var gap  = Math.round((276.46 - circ) * 10) / 10;
  document.getElementById('pm-ring-arc').setAttribute('stroke-dasharray', circ + ' ' + gap);
  document.getElementById('pm-ring-arc').setAttribute('stroke', col);

  // 數值
  document.getElementById('pm-price').textContent = price;
  document.getElementById('pm-price').style.color = col;
  document.getElementById('pm-rsi').textContent = rsi;
  document.getElementById('pm-kd').textContent = kd;
  document.getElementById('pm-vol').textContent = vol;

  // 趨勢折線（大圖）
  _drawTrendChart(trend, col, score);

  // 訊號列表
  _renderSignals(signals);

  // K線連結
  document.getElementById('pm-kline-btn').href = '{KLINE_TOOL_URL}?stock=' + sid;

  _getModalBg().classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closePickModal() {{
  _getModalBg().classList.remove('open');
  document.body.style.overflow = '';
}}

function _drawTrendChart(trend, col, currentScore) {{
  var container = document.getElementById('pm-trend-area');
  if (!trend || trend.length < 2) {{
    container.innerHTML = '<div style="color:#4a6080;font-size:.78rem;text-align:center;padding:20px 0;">歷史資料不足（需至少2天）</div>';
    return;
  }}

  var scores = trend.map(function(t) {{ return t.s; }});
  var dates  = trend.map(function(t) {{ return t.d ? t.d.slice(5) : ''; }}); // MM-DD
  var mn = Math.min.apply(null, scores) - 8;
  var mx = Math.max.apply(null, scores) + 8;
  if (mx <= mn) mx = mn + 1;

  var W = 400, H = 80, pad = 6;
  var n = scores.length;

  function toX(i) {{ return pad + i / (n - 1) * (W - pad * 2); }}
  function toY(s) {{ return H - pad - (s - mn) / (mx - mn) * (H - pad * 2); }}

  var pts = scores.map(function(s, i) {{
    return toX(i).toFixed(1) + ',' + toY(s).toFixed(1);
  }}).join(' ');

  // 漸層填充
  var gradId = 'grad-trend-' + Date.now();
  var lastX = toX(n - 1).toFixed(1);
  var lastY = toY(scores[n - 1]).toFixed(1);

  // 分數標籤（每個點）
  var labels = scores.map(function(s, i) {{
    var x = toX(i).toFixed(1);
    var y = toY(s).toFixed(1);
    var isLast = i === n - 1;
    var textCol = isLast ? col : '#4a6080';
    var fw = isLast ? '700' : '400';
    return '<text x="' + x + '" y="' + (parseFloat(y) - 5).toFixed(1) +
           '" text-anchor="middle" font-size="9" fill="' + textCol + '" font-weight="' + fw + '">' + s + '</text>';
  }}).join('');

  // 日期標籤
  var dateLabels = '';
  if (n >= 2) {{
    dateLabels += '<text x="' + pad + '" y="' + (H + 12) + '" text-anchor="middle" font-size="8" fill="#4a6080">' + dates[0] + '</text>';
    dateLabels += '<text x="' + (W - pad) + '" y="' + (H + 12) + '" text-anchor="middle" font-size="8" fill="' + col + '">' + dates[n-1] + '</text>';
  }}

  container.innerHTML = '<svg width="100%" viewBox="0 0 ' + (W) + ' ' + (H + 16) + '" preserveAspectRatio="xMidYMid meet">' +
    '<defs>' +
    '<linearGradient id="' + gradId + '" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0%" stop-color="' + col + '" stop-opacity="0.18"/>' +
    '<stop offset="100%" stop-color="' + col + '" stop-opacity="0"/>' +
    '</linearGradient></defs>' +
    '<polygon points="' + pad + ',' + H + ' ' + pts + ' ' + lastX + ',' + H + '" fill="url(#' + gradId + ')"/>' +
    '<polyline points="' + pts + '" fill="none" stroke="' + col + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<circle cx="' + lastX + '" cy="' + lastY + '" r="4" fill="' + col + '"/>' +
    labels +
    dateLabels +
    '</svg>';

  // 分數變化摘要
  var delta = scores[n-1] - scores[0];
  var deltaStr = (delta > 0 ? '+' : '') + delta;
  var deltaCol = delta > 0 ? '#ff4d6d' : (delta < 0 ? '#4a9eff' : '#6a85a8');
  document.getElementById('pm-trend-summary').innerHTML =
    '<span style="color:#6a85a8;">7日前：' + scores[0] + '</span>' +
    '&nbsp;&nbsp;→&nbsp;&nbsp;' +
    '<span style="color:' + col + ';font-weight:700;">今日：' + scores[n-1] + '</span>' +
    '&nbsp;&nbsp;<span style="color:' + deltaCol + ';font-size:.85rem;">' + deltaStr + ' pts</span>';
}}

function _renderSignals(signals) {{
  var container = document.getElementById('pm-signals');
  if (!signals || signals.length === 0) {{
    container.innerHTML = '<div style="color:#4a6080;font-size:.78rem;text-align:center;padding:12px 0;">無訊號資料</div>';
    return;
  }}

  var typeMap = {{
    'bull':    {{ icon: '▲', color: '#ff4d6d', label: '偏多' }},
    'bear':    {{ icon: '▼', color: '#00c896', label: '偏空' }},
    'neutral': {{ icon: '◆', color: '#6a85a8', label: '中性' }},
  }};

  var catMap = {{
    'ma':      'MA',
    'rsi':     'RSI',
    'kd':      'KD',
    'macd':    'MACD',
    'vol':     '量能',
    'pattern': '型態',
    'chip':    '籌碼',
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

// ESC 關閉
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') closePickModal();
}});

// ══════════════════════════════════════════
//  勝率切換
// ══════════════════════════════════════════
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
