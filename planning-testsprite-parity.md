# Planning: Menjadikan TestZeus Hercules Se-Powerful TestSprite

> **Dokumen:** Rencana modifikasi `testzeus-hercules` v1.0.1 menuju paritas (dan melampaui) fitur TestSprite MCP
> **Tanggal:** 8 September 2026
> **Basis data:**
> - Riset kompetitor: [`riset-testsprite-mcp.md`](riset-testsprite-mcp.md) (33 sumber: docs TestSprite, pricing, changelog, review pihak ketiga)
> - Scanning codebase mendalam oleh 5 subagent paralel (arsitektur & entry point; kapabilitas testing; LLM & config; output/observability; gap-analysis fitur TestSprite) — semua dengan sitasi `file:line`
> **Sifat:** Dokumen perencanaan — belum ada kode yang diubah.

---

## Daftar Isi

1. [Ringkasan Eksekutif](#1-ringkasan-eksekutif)
2. [Baseline: Hercules Hari Ini](#2-baseline-hercules-hari-ini)
3. [Matriks Gap: TestSprite vs Hercules](#3-matriks-gap-testsprite-vs-hercules)
4. [Prinsip Desain](#4-prinsip-desain)
5. [Arsitektur Target](#5-arsitektur-target)
6. [Roadmap Bertahap](#6-roadmap-bertahap)
7. [Detail Workstream](#7-detail-workstream)
8. [Prioritas Impact vs Effort](#8-prioritas-impact-vs-effort)
9. [Risiko & Mitigasi](#9-risiko--mitigasi)
10. [Definition of Done per Fase](#10-definition-of-done-per-fase)
11. [Yang Sengaja TIDAK Dibuat](#11-yang-sengaja-tidak-dibuat)

---

## 1. Ringkasan Eksekutif

Hercules **tidak mulai dari nol** — justru sebaliknya. Hasil scanning menunjukkan fondasi teknis Hercules di beberapa hal *lebih kuat* dari TestSprite (identifikasi elemen via injeksi `md` + accessibility tree yang segar setiap load = imun terhadap selector rot; 7 nav agent lintas browser/API/security/SQL/MCP; testing visual via LLM vision; sandbox Python). Yang tertinggal bukan mesin testing-nya, melainkan **lapisan produk di sekelilingnya**:

| Kategori gap terbesar | Status kini |
|---|---|
| MCP server hanya 3 tools kasar (subprocess, tanpa streaming, tanpa sesi) | TestSprite: 8 tools compositional |
| Tidak ada lapisan "test intelligence": PRD → test plan terstruktur → traceability | TestSprite: normalized PRD + test plan JSON (TC001…) |
| Exit code tidak mencerminkan pass/fail → CI harus parse XML | TestSprite: native CI gating |
| Bukti kegagalan tersebar (video/screenshot/log di folder berbeda, tanpa bundel) | TestSprite: failure bundle atomik |
| Tidak ada dashboard, riwayat run, monitoring terjadwal, posting hasil ke PR | TestSprite: semuanya ada |
| Eksekusi 100% sekuensial | TestSprite: fleet paralel di cloud |

Rencana ini disusun dalam **5 fase** (Fase 0 quick wins → Fase 4 monitoring & paralel), membangun di atas titik ekstensi yang sudah ada (registry `@tool`, `_HELPER_MAP`, LangGraph state, config manager), dan memanfaatkan dependensi yang **sudah terpasang** (`unstructured[all-docs]` untuk parsing PRD, `sqlalchemy` untuk DB riwayat, `starlette` untuk dashboard) sehingga tambahan dependensi baru minim.

**Posisi kompetitif yang dituju:** "TestSprite-level automation, open-source, self-hosted, tanpa lock-in kredit" — mesin eksekusi lokal penuh (bukan cloud sandbox SaaS), dapat dikendarai coding agent via MCP setipis TestSprite tapi sepenuhnya milik pengguna.

---

## 2. Baseline: Hercules Hari Ini

### 2.1 Arsitektur (hasil scan #1)

```
CLI (testzeus-hercules)                     MCP server (testzeus-hercules-mcp)
 ├─ single: opt/input/test.feature           ├─ generate_gherkin(description)   [subprocess]
 ├─ --bulk: iterasi tests/*                  ├─ run_test(gherkin|description)  [subprocess CLI, 300s]
 ├─ --guided/--test/--dry-run                └─ get_test_results(run_id)       [parse JUnit XML]
 └─ interactive REPL
        │
        ▼
SimpleHercules (LangGraph StateGraph, simple_hercules.py:1072-1088)
 ENTRY → planner ⇄ executor → assertion → END
          │           │
          │           └─ _HELPER_MAP (:537-546) → 7 nav agent:
          │              browser | api | sec | sql | time_keeper | mcp | executor
          └─ state AgentState (:51-79): messages, plan, target_helper, verdict, token/cost
```

- **Planner** (`high_level_planner_agent.py`) menghasilkan JSON ketat: `plan, next_step, target_helper, terminate, final_response, is_assert, assert_summary, is_passed`.
- **Executor** menjalankan loop ReAct nav agent (`_run_nav_agent`, `simple_hercules.py:857-1007`) dengan *stale-tool-call guard* (state berubah → antrean tool dibatalkan, DOM dibaca ulang).
- **Ekstensi baru:** tool baru = file di `core/tools/` + `@tool(agent_names=[...])` (auto-registered); agent baru = subclass `BaseNavAgent` + entri `_HELPER_MAP` + keyword di prompt planner; node graf baru = `_build_graph`.

### 2.2 Kapabilitas testing (hasil scan #2) — sudah kuat

- **Browser:** klik (5 varian + fallback JS untuk SVG/Salesforce), teks/dropdown/slider/date-time, hover+tooltip, drag-and-drop, key combos, upload file, multi-tab (aktivasi via `document.hidden`), dialog handler, captcha (reCAPTCHA v2/v3), clipboard, PDF, geo, uBlock, emulasi device/lokale/timezone, aksesibilitas (axe-core), **shadow DOM + iframe same-origin**, CDP remote (BrowserStack/LambdaTest).
- **Identifikasi elemen (keunggulan):** injeksi atribut `md` numerik + accessibility tree kustom (`get_detailed_accessibility_tree.py`) — LLM menyebut elemen dengan ID `md`, mapping **dibangun ulang tiap state** → tidak ada selector basi (baca: "self-healing" struktural).
- **API/Backend:** `generic_http_api` (httpx, 5 mode auth, semua method/body), SQL SELECT async (postgres/mysql/sqlite), security Nuclei (1 tool per template tag), sandbox Python dengan Playwright ter-inject.
- **Verifikasi:** assert via planner JSON; `get_page_text` (shadow+iframe+alt); **visual compare via LLM vision** (`visual_skill.py`); a11y violation report.
- **Evidence:** screenshot sebelum/sesudah tiap aksi (+bounding-box overlay opsional), video per halaman, Playwright trace zip, network log JSON-lines, console log, interaction log dengan *alternative selectors* (tapi hanya untuk forensik — tidak dipakai retry).

### 2.3 LLM & config (hasil scan #3)

- Satu factory `ChatOpenAI` (`llm_helper.py:76-102`) → semua provider OpenAI-compatible (proxy LiteLLM, Portkey gateway, Azure via gateway); 3 role independen (`planner_agent`/`nav_agent`/`helper_agent`) via `agents_llm_config.json`; 7 nav agent berbagi satu stanza `nav_agent` (limitasi).
- Token usage terlacak 3 bentuk; **cost hanya jika provider melaporkan** (`model_pricing` ada di config tapi tak pernah dipakai).
- Telemetri = Sentry analytics vendor (opt-out `ENABLE_TELEMETRY=0`); **tidak ada OTel span** meski `opentelemetry-proto` ikut terpasang.
- **Bug yang ditemukan:** `mcp_hercules.example.json` pola stdio padahal server streamable-http (klien stdio akan hang); `mcp_server.py` punya blok `if __name__` duplikat (:255 & :302); default nav-rounds tak sinkron (`runner.py:24` = 50 vs `simple_hercules.py:90` = 10).

### 2.4 Output & observability (hasil scan #4)

- JUnit XML per-skenario + merge (`junit_helper.py`), HTML via `junit2html`; properti testcase memuat path bukti + cost/token — tapi bukti **tidak pernah dibundel/di-embed**.
- **Exit code non-zero hanya untuk salah konfigurasi** (`__main__.py:38,148,166`) — test gagal tetap exit 0. CI harus parse XML (seperti `tests/test_base.py:compare_results`).
- Tidak ada: file log runtime, dashboard, DB riwayat, report Markdown/PDF, share URL. "Run terbaru" = sort mtime folder output.
- `frontend/` = 2 halaman screencast CDP vanilla (viewer interaktif + non-interaktif) — bahan bagus untuk fitur replay, tapi bukan dashboard.

### 2.5 Gap fitur TestSprite (hasil scan #5)

| Kapabilitas | Status | Detail |
|---|---|---|
| PRD handling | **PARTIAL** | Hanya description→1 Gherkin (`test_builder.py`); skrip offline `vir_migration_script.py` & `generate_api_functional_gherkin_test.py` (OpenAPI→Gherkin) belum terintegrasi |
| Test plan generation | **PARTIAL** | Tanpa artefak plan terstruktur/matriks coverage/plan editor |
| Codebase analysis | **ABSENT** | Nol hit untuk scan kode/deteksi framework/git diff |
| Auto-heal persisten | **PARTIAL** | Adaptif in-run (prompt + fallback JS + loop detection); tanpa klasifikasi akar masalah, tanpa selector store lintas run |
| Scheduling/monitoring | **ABSENT** | Tidak ada cron/daemon/run berulang |
| CI/GitHub user-facing | **PARTIAL** | CI sendiri + output JUnit/Docker; tanpa PR comment/action untuk pengguna |
| Test lists/suites | **PARTIAL** | `--bulk` sekuensial; tanpa suite bernama/tag/retry policy |
| Cloud/parallel | **PARTIAL** | CDP remote ada; paralelisme nol (semua `for` loop) |
| Chaining antar-tes | **PARTIAL** | Kuat in-run (`store_data`, `store_run_data`, `--dont-close-browser`); tanpa dependency graph/ekspor-impor variabel bernama |

---

## 3. Matriks Gap: TestSprite vs Hercules

Legenda: ✅ ada · 🟡 partial · ❌ tidak ada · **P** prioritas (1 = tertinggi)

| # | Kemampuan TestSprite | TestSprite | Hercules kini | P | Catatan |
|---|---|---|---|---|---|
| 1 | 8 MCP tools compositional (bootstrap/plan/execute/dashboard/…) | ✅ | 🟡 3 tools subprocess kasar | **1** | Inti dari "driven by coding agent" |
| 2 | Normalized PRD (JSON) dari dokumen | ✅ | ❌ | **1** | `unstructured` sudah di deps — parsing PDF/DOCX hampir gratis |
| 3 | Test plan terstruktur (TC001…, kategori, prioritas) | ✅ | ❌ | **1** | Gherkin bisa jadi target kompilasi plan |
| 4 | Diff-scope vs codebase-scope | ✅ | ❌ | 2 | Butuh git integration |
| 5 | Exit code CI + blocking | ✅ | ❌ | **1** | Trivial, dampak besar |
| 6 | Failure bundle atomik (step+DOM+akar masalah+saran fix) | ✅ | 🟡 bukti tersebar | **1** | Bahan sudah direkam semua, tinggal dibundel |
| 7 | Laporan MD/HTML (+PDF) dengan coverage per requirement | ✅ | 🟡 HTML saja | 2 | |
| 8 | Auto-heal + klasifikasi kegagalan 4 kategori | ✅ | 🟡 implicit via md-refresh | 2 | Nilai jual: klasifikasi + saran fix |
| 9 | Dashboard: hasil live, video replay, edit langkah, re-run | ✅ | ❌ | 2 | `starlette` sudah ada; screencast viewer ada |
| 10 | Riwayat run (DB) + tren | ✅ | ❌ (folder saja) | 2 | `sqlalchemy` sudah ada |
| 11 | Scheduled monitoring + alert | ✅ | ❌ | 3 | |
| 12 | GitHub Action + komentar PR + blocking | ✅ | ❌ | 2 | Pola `TestSprite/run-action@v1` bisa ditiru open-source |
| 13 | Test lists / suite bernama / tag | ✅ | 🟡 bulk folder | 3 | |
| 14 | Eksekusi paralel (fleet) | ✅ | ❌ | 3 | stake_id singleton PlaywrightManager memudahkan |
| 15 | Video replay tiap run | ✅ | ✅ (webm) tapi tanpa viewer | — | Tinggal dihubungkan |
| 16 | Multi-auth (Basic/Bearer/API-key) config portal | ✅ | 🟡 via test_data/env | 3 | |
| 17 | Auto-Auth / token refresh | ✅ | ❌ | 4 | |
| 18 | Kredit & cloud sandbox SaaS | ✅ | ❌ (by design) | — | Lihat [§11](#11-yang-sengaja-tidak-dibuat) |

> Kekuatan Hercules yang **tidak dimiliki TestSprite** (pertahankan & tonjolkan): identifikasi elemen tanpa selector-rot, security testing Nuclei, SQL agent, sandbox Python, testing visual LLM, multilingual, axe-core a11y, proxy LiteLLM/Portkey multi-vendor, 100% self-hosted.

---

## 4. Prinsip Desain

1. **Bangun di atas titik ekstensi yang ada** — tool baru lewat `@tool` registry; agent baru lewat `BaseNavAgent` + `_HELPER_MAP`; artefak baru sebagai modul `utils/`, bukan fork arsitektur.
2. **Gherkin tetap bahasa test** — TestSprite punya "normalized PRD + test plan JSON"; Hercules mengadopsi lapisan setara: **TestPlan JSON** (format TC terinspirasi TestSprite) yang **dikompilasi ke Gherkin** untuk eksekusi. Dua arah: Gherkin existing tetap jalan tanpa perubahan.
3. **Lokal dulu, cloud opsional** — semua fitur jalan self-hosted; integrasi grid (BS/LT) via CDP yang sudah ada; tidak ada SaaS wajib.
4. **Bukti = warga kelas satu** — setiap run menghasilkan run-record (DB) + failure bundle (folder tunggal terkonsolidasi) + report MD/HTML.
5. **Backward compatible** — `python -m testzeus_hercules` dengan `.feature` lama harus tetap berperilaku sama (kecuali exit code — lihat Fase 0, dengan flag kompatibilitas `EXIT_CODE_COMPAT=1` untuk yang bergantung pada perilaku lama).
6. **Tanpa dependensi berat baru** — maksimal memanfaatkan `unstructured`, `sqlalchemy`, `starlette`, `aiohttp`, `mcp` yang sudah terpasang.

---

## 5. Arsitektur Target

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            Lapisan KONSUMEN                              │
│  IDE/coding agent (MCP)   CLI (single/bulk/guided)   CI (GitHub Action)  │
└───────────────┬───────────────────┬────────────────────┬─────────────────┘
                │                   │                    │
┌───────────────▼───────────────────▼────────────────────▼─────────────────┐
│                     Lapisan ORKESTRASI BARU (hercules/orchestrator)      │
│  • Project session (bootstrap: URL, kredensial, scope codebase|diff)     │
│  • PRD pipeline: dokumen → NormalizedPRD JSON → TestPlan JSON → Gherkin  │
│  • TestPlan store + coverage matrix + traceability requirement→test      │
│  • Run manager: antrean, paralel per-skenario (stake_id unik), re-run    │
│  • Run history DB (SQLAlchemy: runs, cases, steps, artifacts, healing)   │
│  • Failure bundler + klasifikasi akar masalah (bug/fragility/env/contract)│
│  • Scheduler (monitoring terjadwal + notifikasi)                         │
└───────────────┬──────────────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────────────┐
│            Mesin EKSEKUSI (yang sekarang, nyaris tak diubah)             │
│  SimpleHercules LangGraph + 7 nav agent + tool registry + PlaywrightMgr  │
│  + modul healing baru (selector store, heal proposal)                    │
└───────────────┬──────────────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────────────┐
│                     Lapisan OUTPUT & OBSERVABILITY                        │
│  JUnit XML (tetap) + Report MD/HTML baru + Dashboard web (starlette)     │
│  + video replay viewer (evolusi frontend/) + evidence bundle             │
└──────────────────────────────────────────────────────────────────────────┘
```

**Modul baru (usulan penempatan):**

| Modul | Isi | Ekstensi dari |
|---|---|---|
| `testzeus_hercules/orchestrator/` | session, project_config, run_manager, scheduler | baru |
| `testzeus_hercules/intelligence/` | `prd_parser.py` (unstructured), `normalized_prd.py`, `test_plan.py` (model Pydantic + kompilasi Gherkin), `coverage.py` | baru; menyerap `utils/test_builder.py` & `gherkin_generator.py` |
| `testzeus_hercules/orchestrator/code_scan/` | `code_summary.py` (deteksi framework), `git_diff.py` | baru |
| `testzeus_hercules/healing/` | `selector_store.py`, `failure_classifier.py`, `heal_proposer.py` | baru; konsumsi `browser_logger.get_alternative_selectors` |
| `testzeus_hercules/reporting/` | `markdown_report.py`, `bundle.py` (failure bundle), `run_db.py` (SQLAlchemy models) | baru; menyerap `junit_helper.py` |
| `testzeus_hercules/dashboard/` | app starlette: run list, live progress, replay, plan editor | baru; evolusi `frontend/` |
| `testzeus_hercules/mcp_server.py` | **ditulis ulang** → 9 tools (lihat §7.1) | rewrite |
| `.github/actions/run-tests/` | composite action untuk pengguna | baru |

---

## 6. Roadmap Bertahap

> Estimasi untuk 1 developer penuh waktu, asumsi familiar dengan codebase. Fase boleh overlap.

### Fase 0 — Quick Wins & Perbaikan Fondasi (±1 minggu)

| # | Item | File | Detail |
|---|---|---|---|
| 0.1 | **Exit code CI** | `__main__.py` | Exit 1 bila ada failure/error di hasil merge (kecuali `EXIT_CODE_COMPAT=1`). Update `Makefile`, README, docs/run_guide |
| 0.2 | **Failure bundle** | `reporting/bundle.py` | Per test gagal: kopi screenshot step gagal + tetangga, video, network log, `agent_inner_thoughts.json`, potongan DOM (dari trace), saran fix (dari planner JSON) → `output/run_*/bundles/<test_id>/` + `bundle.md` |
| 0.3 | **Report Markdown** | `reporting/markdown_report.py` | `Hercules_Test_Report.md` per run: ringkasan, tabel per-skenario, link bukti, token/cost |
| 0.4 | Bugfix klien MCP | `mcp_hercules.example.json`, `mcp_server.py` | Contoh config → HTTP benar (atau tambahkan dukungan stdio); hapus blok `__main__` duplikat; sinkronkan default nav-rounds |
| 0.5 | Cost dari `model_pricing` | `llm_helper.py`, `simple_hercules.py` | Hitung biaya sendiri bila provider tak melaporkan (tabel harga per model di config) |
| 0.6 | Log file runtime | `utils/logger.py` | Optional `LOG_FILE_PATH` → `log_files/<run>/run.log` (rotasi sederhana) |

### Fase 1 — MCP Server Paritas 8-Tool (±2–3 minggu)

Tulis ulang `mcp_server.py` dari pattern "subprocess per tool" menjadi **in-process session-based**, tools (nama boleh beda, semantik setara TestSprite):

| Tool Hercules | Padanan TestSprite | Perilaku |
|---|---|---|
| `hercules_configure_session` | `testsprite_bootstrap` | URL app, port, kredensial, tipe (frontend/backend), scope (`codebase`/`diff`), simpan session state; membuka halaman config dashboard bila perlu |
| `hercules_generate_code_summary` | `testsprite_generate_code_summary` | Scan repo (framework, entry point, routes, fitur) → `code_summary.json` |
| `hercules_generate_standardized_prd` | `testsprite_generate_standardized_prd` | Gabungkan PRD user (jika ada) + code summary → `standard_prd.json` |
| `hercules_generate_test_plan` | `frontend/backend_test_plan` | PRD → TestPlan JSON (TC001…, kategori, prioritas, langkah action/assertion) — frontend & backend dalam satu plan ber-tag |
| `hercules_generate_and_execute` | `testsprite_generate_code_and_execute` | TestPlan → Gherkin → jalankan (in-process runner, bukan subprocess) → hasil + report + bundle; dukung `test_ids[]` subset & instruksi tambahan |
| `hercules_open_dashboard` | `testsprite_open_test_result_dashboard` | Buka/muat ulang dashboard run (Fase 3; sementara return URL HTML report) |
| `hercules_get_run_results` | `get_test_results` (diperluas) | Status, kasus, langkah, artefak, klasifikasi kegagalan |
| `hercules_check_config` | `testsprite_check_account_info` | Validasi konfigurasi LLM/browser + uji ping model + tampilkan estimasi biaya |
| `hercules_rerun` *(bonus)* | rerun dengan prompt | Re-run test/case tertentu tanpa re-planning |

Plus: **progress streaming** — notifikasi MCP saat tiap scenario step selesai (ubah `NotificationManager` dari no-op menjadi emiter; lihat `playwright_manager.py:1194-1198` yang sudah stub `pass`).

### Fase 2 — Test Intelligence: PRD & Test Plan (±3–4 minggu, overlap dgn F1)

1. **PRD parser** (`intelligence/prd_parser.py`): `unstructured` → teks dari PDF/DOCX/MD/TXT/URL; chunking; ekstraksi fitur/goal/user-story/acceptance-criteria via LLM (planner model) → `NormalizedPRD` Pydantic.
2. **TestPlan model** (`intelligence/test_plan.py`): `{id: TC001, title, description, category(functional|error_handling|security|ui_ux|performance), priority(high|medium|low), tags[frontend|backend], steps[{type: action|assertion, description}], data_refs[]}` — format sadar-TestSprite agar familiar.
3. **Kompilator Gherkin**: TestPlan → `.feature` (Given/When/Then per step; Examples dari `data_refs`). Skenario positif + negatif + edge case dipicu kategori.
4. **Coverage & traceability**: tabel requirement (dari PRD) ↔ TC; laporan "% requirement teruji" (TestSprite menampilkan angka serupa).
5. **Integrasi skrip lama**: promosikan `helper_scripts/generate_api_functional_gherkin_test.py` (OpenAPI→Gherkin) & `vir_migration_script.py` menjadi tool first-class (`intelligence/openapi_importer.py`).
6. **Diff scope** (`orchestrator/code_scan/git_diff.py`): `git diff` → file/fitur terdampak → filter TC terkait (tag/analisis) → rencana run tertarget.

### Fase 3 — Healing, DB Riwayat, Dashboard (±4–5 minggu)

1. **Run DB** (`reporting/run_db.py`): SQLAlchemy (SQLite default, Postgres opsional) — tabel `runs, test_cases, steps, artifacts, healing_events`; migrasi dari scan folder output lama (iterator).
2. **Klasifikasi kegagalan** (`healing/failure_classifier.py`): 4 kategori ala TestSprite — *product bug / test fragility / environment / contract violation* — heuristik (error patterns per tool) + LLM assist; tersimpan di DB & bundle.
3. **Selector store + heal proposal** (`healing/selector_store.py`): simpan alternative selectors (sudah dihitung `browser_logger.py:262-329` tapi terbuang!) keyed by canonical name (role+name); saat `md` lookup gagal → coba store → ProposalHeal di-report. Ini melengkapi (bukan menggantikan) keunggulan md-refresh.
4. **Dashboard web** (`dashboard/`): starlette app — daftar run (dari DB), halaman run: status per langkah, **video replay** (`<video>` serve file webm + seek ke timestamp step), screenshot viewer, network log viewer, TestPlan editor (edit step → re-run case), tombol re-run. Evolusi `frontend/` screencast untuk mode "live view" run berjalan.
5. **Auto-heal report**: ringkasan heal otomatis vs usulan (butuh approval) per run.

### Fase 4 — CI/CD, Monitoring & Paralel (±3–4 minggu)

1. **GitHub Action pengguna** (`.github/actions/run-tests/action.yml` + publish ke marketplace): input `feature-dir`/`plan-file`/`base-url`/`blocking`; menjalankan container Hercules, upload JUnit + bundle artifact, `blocking: true` → gagalkan job.
2. **Komentar PR**: action step (script kecil `gh api`) posting tabel hasil per-skenario + link artefak — pola TestSprite bot comment.
3. **Scheduler/monitoring** (`orchestrator/scheduler.py`): daemon `hercules-monitor --watch plans/ --interval hourly|daily` → run berulang, bandingkan vs baseline, kirim notifikasi (webhook Slack/Discord/generic + email opsional); konfigurasi `.hercules/monitor.yaml`.
4. **Test lists/suites bernama**: `.hercules/suites.yaml` (daftar test/plan + tag + retry + jadwal) → `hercules run --suite regression`.
5. **Eksekusi paralel**: `run_manager` menjalankan skenario via `asyncio.gather` dengan `stake_id` unik per skenario (PlaywrightManager sudah per-stake singleton) + semaphore konfigurabel (`MAX_PARALLEL_SCENARIOS`); JUnit merge sudah ada. Sharding untuk CI matrix (`--shard i/n`).
6. **Chaining antar-tes**: ekspor/impor variabel bernama antar kasus (`store_data` diperluas key global suite) + dependency sederhana (`depends_on` di suite yaml).

---

## 7. Detail Workstream

### 7.1 Rancangan MCP tools (kontrak interface)

```python
@mcp.tool()
async def hercules_configure_session(
    app_url: str,                      # http://localhost:5173
    project_path: str = "",            # path absolut repo (untuk scan/diff)
    test_type: str = "frontend",       # frontend | backend | mixed
    test_scope: str = "codebase",      # codebase | diff
    credentials: dict | None = None,   # {username, password} atau {api_key, bearer}
    prd_path: str = "",                # opsional, path dokumen PRD
) -> dict   # {session_id, config_summary, next_action}
```

Semua tool lain menerima `session_id`; state sesi disimpan in-process (dan dipersist ke `.hercules/session.json`). Progress event via MCP notifications (FastMCP mendukung context.progress).

### 7.2 Format TestPlan JSON (kompatibel semangat TestSprite)

```json
{
  "meta": {"project": "my-app", "version": "1.0.0", "generated_by": "hercules"},
  "requirements": [
    {"id": "REQ-001", "description": "User can log in", "source": "PRD p.2", "test_ids": ["TC001", "TC002"]}
  ],
  "test_cases": [
    {
      "id": "TC001", "title": "Login success with valid credentials",
      "category": "functional", "priority": "high", "tags": ["frontend"],
      "steps": [
        {"type": "action", "description": "Navigate to the login page"},
        {"type": "action", "description": "Enter valid username and password"},
        {"type": "assertion", "description": "User is redirected to the dashboard"}
      ],
      "data_refs": ["credentials.default"]
    }
  ]
}
```

Kompilasi → Gherkin: 1 test case = 1 Scenario; `assertion` → `Then`; `data_refs` → Examples. Eksekusi memakai pipeline `gherkin_helper.process_feature_file` yang ada.

### 7.3 Failure bundle (layout)

```
output/run_<ts>/
├── Hercules_Test_Report.md          ← ringkasan run (baru)
├── junit/…_result.xml/.html         ← tetap
└── bundles/
    └── TC005_admin_panel/
        ├── bundle.md                ← diagnosis: kategori, akar masalah, saran fix, langkah gagal ± tetangga
        ├── screenshots/step_07_fail.png, step_06.png, step_08.png
        ├── video.webm               ← dengan cue timestamp step gagal
        ├── network.json             ← request/response sekitar kegagalan
        ├── dom_snapshot.html        ← dari Playwright trace
        └── planner_thoughts.json    ← reasoning planner saat kegagalan
```

### 7.4 Perubahan pada mesin eksekusi (minimal)

- `simple_hercules.py`: hook di akhir tiap langkah → emit event ke `NotificationManager` (sudah ada, kini no-op) untuk progress streaming + penyimpanan `steps` ke run DB.
- `runner.py`: dukung instansiasi per-skenario dengan `stake_id` parametrik (untuk paralel) — API `SingleCommandInputRunner(stake_id, command)` sudah mendukung.
- Browser tools: saat aksi gagal, panggil `healing/selector_store.py` lookup sebelum menyerah ke LLM (fallback non-invasif).

---

## 8. Prioritas Impact vs Effort

```
IMPAK TINGGI │  EXIT CODE (0.1)        MCP 8-TOOLS (F1)        DASHBOARD+DB (F3)
             │  FAILURE BUNDLE (0.2)   TEST PLAN+PRD (F2)       GITHUB ACTION (F4.1-2)
             │  MD REPORT (0.3)
             │
IMPAK SEDANG │  COST FIX (0.5)         AUTO-HEAL (F3.3)        PARALEL (F4.5)
             │                         DIFF SCOPE (F2.6)       SCHEDULER (F4.3)
             │                         SUITES (F4.4)
             │
             └───────────────────────────────────────────────────────────────────
               EFFORT KECIL              EFFORT SEDANG            EFFORT BESAR
```

Urutan pengerjaan yang disarankan: **0.1 → 0.2 → 0.4 → F1 (inti) → F2 → 0.3/0.5 → F3 → F4**.

---

## 9. Risiko & Mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Paralel skenario berbagi global state (`get_global_conf`, `StaticLTM`, `PlaywrightManager._instances`) | F4 bisa corrupt hasil | Audit singleton per modul di awal F4; konfigurasi per-run di-clone; mulai dari paralel antar-*proses* (bulk) bila dalam-proses terlalu berisiko |
| Streaming progress via MCP butuh perubahan loop eksekusi | Regresi pada CLI mode | NotificationManager bersifat opt-in listener; jalur CLI tak berubah bila tak ada subscriber |
| Kualitas TestPlan dari PRD jelek (PRD buruk) | TC tidak bermakna | Prompt + validasi skema + always-human-reviewable via plan editor; TestSprite sendiri bilang "PRD draft pun cukup" — normalisasi mengisi kekosongan |
| LLM non-OpenAI-compatible (provider native) | F2 terhambat | Tetap lewat proxy LiteLLM (pola yang sudah resmi didukung); jangan tambah client baru |
| Ukuran PRD besar melebihi context | Parsing gagal | Chunking + ringkasan bertingkat di `prd_parser` (map-reduce per bagian) |
| `unstructured[all-docs]` berat di CI | Image Docker membengkak | Extra `[docs]` opsional; fallback parser murni-Python untuk md/txt/html |
| Scope creep menyalin 100% TestSprite | Fokus hilang | Patuhi §11; setiap PR harus map ke item roadmap bernomor |
| Behaviour exit-code baru memutus pengguna lama | Keluhan | Flag `EXIT_CODE_COMPAT=1` + catatan rilis + major version bump (1.x → 2.0) |

---

## 10. Definition of Done per Fase

- **F0:** CI demo repo gagal (exit ≠ 0) saat test gagal; bundle & MD report dihasilkan di `opt/output/run_*/`; semua test unit + `tests/test_features` hijau; bug MCP diperbaiki.
- **F1:** Cursor/Claude Code dapat `hercules_configure_session` → `generate_test_plan` → `generate_and_execute` → `get_run_results` dalam satu sesi tanpa restart server; 3 tools lama tetap ada (deprecated alias).
- **F2:** Diberi PDF PRD 10 halaman + repo aplikasi → TestPlan JSON ≥15 TC lintas kategori + coverage matrix; kompilasi Gherkin dieksekusi lulus di aplikasi contoh `tests/api_testing_app`.
- **F3:** Dashboard menampilkan ≥10 run terakhir dengan video replay seek-per-step; kegagalan terklasifikasi 4 kategori di DB; selector store terpakai di ≥1 skenario healing.
- **F4:** Action dijalankan di PR dummy → komentar hasil terposting + blocking bekerja; scheduler menjalankan suite nightly 3 malam beruntun tanpa intervensi; 4 skenario paralel selesai ±25% waktu sekuensial.

---

## 11. Yang Sengaja TIDAK Dibuat

1. **Sistem kredit/kuota & billing** — Hercules open-source; "kuota" = konfigurasi pengguna sendiri (mis. `MAX_RUNS_PER_DAY` untuk self-limit).
2. **Cloud sandbox SaaS milik vendor** — eksekusi tetap lokal/infra pengguna (Docker/CDP grid yang sudah ada).
3. **Klon visual dashboard marketing TestSprite** — dashboard Hercules berfokus run-history + replay + plan editor, bukan landing page.
4. **Auto-apply patch ke kode aplikasi pengguna** — Hercules mengusulkan fix di bundle/report; penerapan tetap di tangan coding agent/manusia (sesuai prinsip keamanan TestSprite juga: "TestSprite analyzes, IDE applies fixes with approval").
5. **Model AI proprietary** — tetap BYO-model via LiteLLM/Portkey.

---

## Lampiran A — Jejak ke Sumber Internal

- Arsitektur & entry point: `simple_hercules.py:51-79,537-546,1072-1088`; `__main__.py:24-183`; `core/runner.py`; `mcp_server.py:57-250`
- Kapabilitas: `core/tools/*` (registry `tool_registry.py:129`); md-injection `utils/get_detailed_accessibility_tree.py:48-520`; visual `core/extra_tools/visual_skill.py`; API `core/tools/api_calls.py:97-253`; SQL `core/tools/sql_calls.py`; Nuclei `core/tools/api_sec_calls.py`
- Output: `utils/junit_helper.py:35-308`; trace paths `config.py:1058-1074`; bukti `playwright_manager.py:1083-1192`
- Gap: skrip terabaikan `helper_scripts/generate_api_functional_gherkin_test.py`; alternative selectors terbuang `core/browser_logger.py:262-329`; notification no-op `core/playwright_manager.py:1194-1198`
- Referensi kompetitor: [`riset-testsprite-mcp.md`](riset-testsprite-mcp.md) — 8 tools §4, test types §8, healing §9, CI §10, pricing §14.

## Lampiran B — Bug Kecil yang Ikut Diperbaiki (dari scanning)

1. `mcp_hercules.example.json` stdio vs server streamable-http (klien akan hang) — `mcp_server.py:8-16` vs file contoh.
2. `mcp_server.py` blok `if __name__ == "__main__"` duplikat (:255, :302) + duplikasi print startup.
3. Default `browser_nav_max_chat_round` tidak konsisten: `runner.py:24` (50) vs `simple_hercules.py:90` (10).
4. `LOAD_EXTRA_TOOLS` default `"false"` tapi loader memuat selama nilainya bukan `"false"` — perilaku OK, namun dokumentasi README perlu dipertegas (extra_tools opt-in).
5. `HISTORY.md` hanya berisi kata "Changelog" — isi dengan riwayat rilis nyata saat mulai proyek ini.

---

*Dokumen ini disusun dari 5 laporan scanning codebase paralel (±9.400 baris temuan dengan sitasi file:line) + riset kompetitor 33 sumber. Langkah berikutnya: review & setujui roadmap, lalu mulai Fase 0.*
