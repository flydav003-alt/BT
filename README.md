# 台股 K線評分排名報告與回測系統

> 每日自動更新的 GitHub Pages 靜態網站，整合 ETF × 上櫃 × 上市三大類別的 K線評分排名與歷史勝率回測。

---

## 📁 專案目錄結構

```
taiwan-kline-dashboard/
├── .github/
│   └── workflows/
│       ├── run_scoring.yml              # 主 Workflow（自動觸發）
│       └── notify_from_source_repo.yml  # 給來源 Repo 使用的模板
├── scripts/
│   ├── kline_scorer.py    # K線評分引擎（核心）
│   ├── main.py            # 主執行腳本
│   ├── db_manager.py      # SQLite 資料庫操作
│   ├── html_generator.py  # 靜態 HTML 生成器
│   └── config.py          # 集中設定檔
├── data/
│   ├── input/             # 三份每日 CSV（由來源 Repo 自動更新）
│   │   ├── etf.csv
│   │   ├── otc.csv
│   │   └── tse.csv
│   └── history.db         # SQLite 歷史資料庫
├── docs/                  # GitHub Pages 根目錄
│   ├── index.html         # 主頁面（自動生成）
│   ├── data/
│   │   └── dashboard.json # 資料 JSON（自動生成）
│   └── .nojekyll
├── requirements.txt
└── README.md
```

---

## 🚀 部署步驟

### Step 1：Fork / Clone 本 Repo

```bash
git clone https://github.com/YOUR_USERNAME/taiwan-kline-dashboard.git
cd taiwan-kline-dashboard
```

### Step 2：設定 GitHub Pages

1. 進入 Repo → **Settings** → **Pages**
2. Source 選 **Deploy from a branch**
3. Branch 選 **main**，資料夾選 **/ (docs)**
4. 點 **Save**
5. 等待約 1 分鐘後，網址會出現：`https://YOUR_USERNAME.github.io/taiwan-kline-dashboard/`

### Step 3：設定 GitHub Secrets

進入 Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret 名稱 | 說明 |
|------------|------|
| `FINMIND_TOKEN` | FinMind API Token（無 token 也能跑，但建議設定） |

### Step 4：設定來源 Repo（三個外部 Repo）

每個來源 Repo 需要做以下設定：

**4-1. 在「主 Repo」生成 Personal Access Token（PAT）**

1. GitHub → 右上角頭像 → **Settings** → **Developer settings**
2. **Personal access tokens** → **Tokens (classic)** → **Generate new token**
3. 勾選 **repo**（完整 repo 權限）
4. 複製 token

**4-2. 在各「來源 Repo」設定 Secret**

| Secret 名稱 | 說明 |
|------------|------|
| `MAIN_REPO_TOKEN` | 上方生成的 PAT |

**4-3. 在各「來源 Repo」加入 Workflow**

將 `.github/workflows/notify_from_source_repo.yml` 複製到來源 Repo，並修改：

```yaml
# ETF Repo → event-type: csv-etf-updated
# OTC Repo → event-type: csv-otc-updated
# TSE Repo → event-type: csv-tse-updated

repository: YOUR_GITHUB_USERNAME/taiwan-kline-dashboard  # 改成你的主 Repo
```

---

## ⚙️ config.py 設定說明

```python
# CSV 欄位名稱（依你的實際 CSV 調整）
CSV_COL_STOCK_ID   = "stock_id"        # 股票代號欄位名稱
CSV_COL_NAME       = "stock_name"      # 中文名稱欄位（無則填 None）
CSV_COL_COMP_SCORE = "composite_score" # 綜合分數欄位（排序用）

# 每欄顯示幾支
TOP_N = 8

# K線分析連結（點擊股票代號跳轉）
KLINE_TOOL_URL = "https://flydav003-alt.github.io/k-line/"
```

---

## 🔧 手動執行

### 本地測試

```bash
# 安裝相依套件
pip install -r requirements.txt

# 執行完整流程
cd scripts
python main.py

# 只處理特定類別
python main.py --category ETF

# 只補回測資料
python main.py --backfill-only

# 只重新生成 HTML
python main.py --regen-html
```

### 在 GitHub Actions 手動觸發

1. 進入 Repo → **Actions** → **台股K線評分 - 每日更新**
2. 點 **Run workflow**
3. 選擇類別（留空=全部）和模式
4. 點 **Run workflow**

---

## 🔄 自動觸發機制

| 觸發方式 | 說明 |
|---------|------|
| 來源 Repo push CSV | `repository_dispatch` 事件 → 對應類別自動更新 |
| 每日 18:30（台灣時間）| `schedule` 定時 → 補填 T+3/T+5 回測資料 |
| 手動 | `workflow_dispatch` → 可指定類別和模式 |

### 並發保護

```yaml
concurrency:
  group: dashboard-update
  cancel-in-progress: false  # 排隊等待，不取消
```

三個 Repo 同時 push → workflow 自動排隊，依序執行，資料完整不遺失。

---

## 📊 資料庫 Schema

```sql
-- 每日推薦（含回測）
daily_picks (
  date, category(ETF/OTC/TSE), rank, stock_id, stock_name,
  kline_score, verdict, close_price, rsi, kd_k, vol_ratio,
  strategy_used, top_signals,
  t3_date, t3_price, t3_pnl,   -- T+3 回測
  t5_date, t5_price, t5_pnl    -- T+5 回測
)

-- 股票名稱對照
stock_names (stock_id, stock_name)

-- 執行紀錄
run_log (run_at, category, status, stocks_cnt, message)
```

---

## ❓ 常見問題

**Q：為什麼 T+3/T+5 價格是空的？**
A：需要等待 3~5 個交易日後，系統自動補填。每日 18:30 的定時排程會自動補齊。

**Q：CSV 欄位名稱不同怎麼辦？**
A：修改 `scripts/config.py` 中的 `CSV_COL_STOCK_ID`、`CSV_COL_NAME`、`CSV_COL_COMP_SCORE`。

**Q：不想用 FinMind Token 怎麼辦？**
A：直接不設定 `FINMIND_TOKEN` Secret 即可，系統會使用免費額度（300次/小時），18支股票 = 36次請求，完全夠用。

**Q：如何增加顯示股票數？**
A：修改 `config.py` 的 `TOP_N = 8`。

---

## ⚠️ 免責聲明

本系統所有分析內容僅供參考，不構成任何買賣建議。投資人應自行評估風險，作者不對任何投資損失負責。
