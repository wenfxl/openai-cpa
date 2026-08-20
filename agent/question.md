# PR #181 後端變更套用 + PR 關閉任務

## 需求描述
使用者（= PR #181 作者 s12ryt）要求：
1. 將 PR #181「SSO-only 模式」的後端變更套用至本地 main 工作目錄
2. 之後關閉 PR #181 並刪除遠端分支

## 背景發現
- commit `312bf1f feat: 增加只拿SSO模式 (SSO-only mode)` 已存在於本地 `feat/sso-only-mode` 分支與遠端 `remotes/fork/feat/sso-only-mode`
- 但 HEAD 在 main 分支 `e5662e6`，不含此 commit
- `feat/sso-only-mode` 的 parent 是 `8a3497a`，main 已在 `8a3497a` 之後大改 4 個後端檔案：
  - config.example.yaml +4 行、static/js/app.js +7 行
  - utils/config.py +91 行、utils/grok_auth/register.py +153 行
  - tests/test_grok_web_pre_oauth_import.py 未修改

## 決策摘要

### 套用方式
- ❌ 不用 cherry-pick：patch 基於 `8a3497a`，main 已大改相同區域，context 不匹配，預期多處衝突；index.html 必衝突（本地已美化）
- ❌ 不用 git checkout：會覆蓋 main 新修改，丟失資料
- ✅ 手動 edit 4 個檔案（main 修改過的）：config.example.yaml / static/js/app.js / utils/config.py / utils/grok_auth/register.py
- ✅ `git checkout 312bf1f -- tests/test_grok_web_pre_oauth_import.py`（main 未修改，直接套用）

### commit/push 策略
- 本地 commit，不 push（不觸發 CI、風險最低、保留使用者決定權）

### PR 關閉方式
- state_reason=completed（代碼已手動套用）
- 刪除遠端 feat/sso-only-mode 分支
- 在 PR 留言說明代碼已手動整合

## 異動檔案
| 檔案 | 異動內容 | 套用方式 |
|------|----------|----------|
| `config.example.yaml` | grok2api 區段加 `sso_only_mode: false` + 註解 | 手動 edit（第 346 行後） |
| `static/js/app.js` | grokDefaults 加 `sso_only_mode: false` | 手動 edit（第 1692 行） |
| `utils/config.py` | `GROK2API_SSO_ONLY_MODE` 變數 + global + 載入 | 手動 edit（第 370/560/931 行） |
| `utils/grok_auth/register.py` | `run()` 加 `sso_only` 參數 + is_sso_only 分支 | 手動 edit（第 126/231 行） |
| `tests/test_grok_web_pre_oauth_import.py` | mock + 4 個 SSO-only 測試 | `git checkout 312bf1f --` |
| `index.html` | SSO-only checkbox（purple 配色） | 已於美化任務完成 |

## PR #181 變更內容（從 get_diff 取得）

### config.example.yaml（+2）
```yaml
  # SSO-only 模式：注册后只拿 SSO 并先行导入 Grok Web，不执行 Build OAuth（不转换 access_token）
  sso_only_mode: false
```

### static/js/app.js（+2-1）
grokDefaults 物件 `import_sso_as_grok_web: false` 後加 `, sso_only_mode: false`

### utils/config.py（+5）
1. 第 370 行後：`GROK2API_SSO_ONLY_MODE: bool = False`
2. 第 560 行 global 宣告加 `GROK2API_SSO_ONLY_MODE`
3. 第 931 行區塊後：
```python
GROK2API_SSO_ONLY_MODE = safe_bool(
    _grok2api.get("sso_only_mode", False)
)
```

### utils/grok_auth/register.py（+21）
1. `run()` 簽名加 `sso_only: Optional[bool] = None` 參數
2. `_import_grok_web_before_oauth(sso, email, run_ctx)` 後加：
```python
# 判断是否启用 SSO-only 模式（参数优先，其次读取配置）
is_sso_only = sso_only if sso_only is not None else bool(getattr(cfg, "GROK2API_SSO_ONLY_MODE", False))

# 风控检测（由 DISCARD_ON_DOWNGRADE 控制，两种模式都保留）
```
3. 風控檢測之後、`complete_build_oauth` 之前加：
```python
if is_sso_only:
    # SSO-only 模式：跳过 OAuth device flow，直接返回简化 JSON
    _log("SSO-only 模式已开启，跳过 Build OAuth", email)
    sso_record = {
        "email": email,
        "password": password,
        "sso": sso,
        "status": "grok_sso",
        "provider": "grok",
    }
    sso_json_str = json.dumps(sso_record, ensure_ascii=False)
    _log_success("SSO-only 模式完成", email)
    return sso_json_str, password
```

### tests/test_grok_web_pre_oauth_import.py（+126-1）
- mock core_engine / hero_sms / auth_core
- `_run_sso_only` helper
- 4 個測試：test_sso_only_skips_build_oauth_and_imports_grok_web / test_sso_only_with_grok_web_failure / test_sso_only_disabled_grok_web_import / test_sso_only_with_risk_check

## 驗收標準
1. 5 個後端檔案套用 sso_only 變更，main 既有修改不受影響
2. pytest test_grok_web_pre_oauth_import.py 全部通過（4 個 SSO-only + 3 個既有測試）
3. 本地 commit 建立成功（不 push）
4. PR #181 關閉（state_reason=completed）+ 遠端分支刪除
5. 本地 feat/sso-only-mode 分支刪除

## 注意事項
- main 大改了 utils/config.py（+91 行）和 utils/grok_auth/register.py（+153 行），手動 edit 需確認精確插入位置
- 測試環境因 auth_core DLL 無法載入，測試檔已含 mock
- index.html 的 sso_only_mode checkbox 已於前一輪美化任務手動新增（purple 配色，第 4524-4531 行）
