"""
html_generator.py  ── PATCHED (修改 1 & 2)
=================
靜態 HTML 生成器：從 DB 資料生成 docs/index.html

修改記錄：
  [修改1] render_pick_card：中文名稱移到代號正下方（flex-direction:column）
  [修改2] generate_index_html：navbar 連結變亮 + 三個 section 改成 Tab 切換（點一頁只顯示一項）
"""

import json
import logging
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from config import DOCS_DIR, DOCS_DATA_DIR, SITE_TITLE, SITE_SUBTITLE, KLINE_TOOL_URL, TOP_N
from db_manager import get_latest_picks, get_history_picks, get_win_rate_stats

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════
#  輔助函數（unchanged）
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
#  [修改1] 代號+名稱改為直排：代號在上，名稱在下
# ══════════════════════════════════════════════

def render_pick_card(p: dict) -> str:
    sc   = p.get("kline_score", 0)
    col  = score_color(sc)
    bg   = score_bg(sc)
    sid  = p.get("stock_id", "")
    name = p.get("stock_name", "")
    circ = round(sc / 100 * 276.46, 1)
    gap  = round(276.46 - circ, 1)

    rsi_str = f'{p["rsi"]:.1f}' if p.get("rsi") else "—"
    kd_str  = f'{p["kd_k"]:.1f}' if p.get("kd_k") else "—"
    vr_str  = f'{p["vol_ratio"]:.2f}x' if p.get("vol_ratio") else "—"

    return f"""
<div class="pick-card" style="background:{bg};border:1px solid {col}33;border-radius:10px;padding:10px 14px;margin-bottom:6px;display:flex;align-items:center;gap:12px;">
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
  <!-- 主體資訊：[修改1] flex-direction:column → 代號在上，名稱在下 -->
  <div style="flex:1;min-width:0;">
    <div style="display:flex;flex-direction:column;gap:3px;">
      <a href="{kline_url(sid)}" target="_blank"
         style="font-family:'IBM Plex Mono',monospace;font-size:.95rem;font-weight:700;color:#d4dff0;text-decoration:none;border-bottom:1px dashed #4a6080;"
         title="點擊開啟K線分析">{sid}</a>
      <span style="font-size:.72rem;color:#6a85a8;">{name}</span>
    </div>
    <div style="font-size:.7rem;color:{col};font-weight:600;margin-top:3px;">{p.get('verdict','')}</div>
  </div>
  <!-- 數值欄 -->
  <div style="text-align:right;flex-shrink:0;">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:.88rem;color:#d4dff0;">{p.get('close_price','—'):.2f}</div>
    <div style="font-size:.62rem;color:#4a6080;margin-top:2px;">RSI {rsi_str} ｜ K {kd_str}</div>
    <div style="font-size:.62rem;color:#e8b84b;">量比 {vr_str}</div>
  </div>
</div>"""


def render_today_section(picks: dict[str, list[dict]]) -> str:
    all_dates = [p["date"] for cat_list in picks.values() for p in cat_list if p.get("date")]
    latest_date = max(all_dates) if all_dates else "—"

    cols_html = ""
    for cat in ("ETF", "OTC", "TSE"):
        cat_picks = picks.get(cat, [])
        col_color = cat_color(cat)
        cards = "".join(render_pick_card(p) for p in cat_picks) if cat_picks else \
                '<div style="color:#4a6080;font-size:.8rem;padding:20px 0;">暫無資料</div>'

        cols_html += f"""
<div class="today-col">
  <div class="col-header" style="border-bottom:2px solid {col_color};margin-bottom:12px;padding-bottom:8px;">
    <span style="font-size:.9rem;font-weight:700;color:{col_color};">{cat_label(cat)}</span>
    <span style="font-size:.68rem;color:#4a6080;margin-left:8px;">{len(cat_picks)} 支</span>
  </div>
  {cards}
</div>"""

    return f"""
<section class="section" id="today">
  <div class="section-header">
    <div class="section-title">📈 今日推薦</div>
    <div class="section-date">資料日期：{latest_date}</div>
  </div>
  <div class="today-grid">
    {cols_html}
  </div>
</section>"""


# ══════════════════════════════════════════════
#  歷史回測表格（unchanged）
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

        rows_html += f"""
<tr class="history-row" data-search="{sid} {name}">
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
    <input id="history-search" type="text" placeholder="搜尋股票代號或名稱..."
      style="background:#0d1220;border:1px solid #1e2d4a;border-radius:6px;padding:6px 12px;
             color:#d4dff0;font-size:.8rem;outline:none;width:200px;"
      oninput="filterHistory(this.value)">
  </div>
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
#  歷史勝率排行（unchanged）
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
#  [修改2] navbar 連結變亮 + Tab 切換（單頁模式）
# ══════════════════════════════════════════════

def generate_index_html() -> str:
    picks   = get_latest_picks(TOP_N)
    history = get_history_picks(days=5)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    today_html   = render_today_section(picks)
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
/* [修改2] navbar 連結明顯亮化 */
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

/* ── [修改2] Tab sections (single-page navigation) ── */
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
}}
@media(max-width:600px){{
  .data-table{{font-size:.72rem;}}
  .data-table thead th,.data-table tbody td{{padding:7px 8px;}}
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
    <!-- [修改2] onclick 切換 Tab，active 樣式明顯 -->
    <nav class="navbar-nav">
      <a href="#" onclick="switchTab('today');return false;" id="nav-today" class="nav-active">今日推薦</a>
      <a href="#" onclick="switchTab('history');return false;" id="nav-history">歷史回測</a>
      <a href="#" onclick="switchTab('winrate');return false;" id="nav-winrate">勝率排行</a>
    </nav>
    <div class="update-badge">🕐 {now_str} 更新</div>
  </div>
</nav>

<!-- ── Main ── -->
<main class="main-content">
  <div class="container">

    <!-- [修改2] 每個 section 外包 .tab-section div，點擊 navbar 才顯示 -->
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
// ── [修改2] Tab 切換（單頁模式）──
function switchTab(tab) {{
  ['today', 'history', 'winrate'].forEach(function(t) {{
    var sec = document.getElementById('tab-' + t);
    var nav = document.getElementById('nav-' + t);
    if (sec) sec.classList.toggle('active', t === tab);
    if (nav) nav.classList.toggle('nav-active', t === tab);
  }});
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
}}

// ── 歷史回測搜尋 ──
function filterHistory(q) {{
  var rows = document.querySelectorAll('#history-body .history-row');
  var kw = q.trim().toLowerCase();
  rows.forEach(function(tr) {{
    var search = (tr.dataset.search || '').toLowerCase();
    tr.style.display = (!kw || search.includes(kw)) ? '' : 'none';
  }});
}}

// ── 勝率切換 ──
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
