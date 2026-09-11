# Riset Lengkap: TestSprite MCP Server

> **Objek riset:** <https://www.testsprite.com/solutions/mcp> — "MCP for AI Coding Agents"
> **Tanggal riset:** 8 September 2026
> **Metode:** Penggalian langsung terhadap 30+ halaman (situs utama, docs.testsprite.com via indeks `llms.txt` berisi 82 halaman, GitHub org, npm registry, blog, halaman pricing/changelog/hackathon/about/enterprise/use-cases) ditambah pencarian komunitas & press pihak ketiga.
> **Catatan:** Riset ini direncanakan memakai ≥5 subagent researcher, namun seluruh subagent gagal karena kendala konfigurasi lingkungan ("Model provider is not configured"), sehingga seluruh riset dikerjakan langsung secara paralel dengan WebFetch/WebSearch/npm registry API dengan cakupan yang identik.

---

## Daftar Isi

1. [Ringkasan Eksekutif (TL;DR)](#1-ringkasan-eksekutif-tldr)
2. [Profil Perusahaan](#2-profil-perusahaan)
3. [Halaman Solusi MCP — Bedah Total](#3-halaman-solusi-mcp--bedah-total)
4. [8 MCP Tools — Referensi Lengkap dengan Parameter](#4-8-mcp-tools--referensi-lengkap-dengan-parameter)
5. [Workflow & Konsep Inti](#5-workflow--konsep-inti)
6. [Instalasi per IDE/Client (Verbatim)](#6-instalasi-per-ideclient-verbatim)
7. [Package npm @testsprite/testsprite-mcp](#7-package-npm-testspitetestsprite-mcp)
8. [Jenis Tes & Teknologi yang Didukung](#8-jenis-tes--teknologi-yang-didukung)
9. [Healing, Observability & Dashboard](#9-healing-observability--dashboard)
10. [Deploy, Monitoring & Integrasi GitHub/CI-CD](#10-deploy-monitoring--integrasi-githubci-cd)
11. [Contoh Hasil Nyata (Demo E-commerce)](#11-contoh-hasil-nyata-demo-e-commerce)
12. [Biaya, Kredit & Performance](#12-biaya-kredit--performance)
13. [Keamanan & Compliance](#13-keamanan--compliance)
14. [Pricing Plans](#14-pricing-plans)
15. [Changelog Lengkap (Agu 2025 – Apr 2026)](#15-changelog-lengkap-agu-2025--apr-2026)
16. [Ekosistem Produk TestSprite (4 Surface)](#16-ekosistem-produk-testsprite-4-surface)
17. [GitHub, Blog & Hackathon](#17-github-blog--hackathon)
18. [Review & Suara Komunitas](#18-review--suara-komunitas)
19. [Analisis: Kekuatan vs Keterbatasan](#19-analisis-kekuatan-vs-keterbatasan)
20. [Daftar Semua Sumber](#20-daftar-semua-sumber)

---

## 1. Ringkasan Eksekutif (TL;DR)

**TestSprite MCP Server** adalah *testing agent* otonom yang berjalan di dalam IDE Anda sebagai **MCP (Model Context Protocol) server** — standar terbuka untuk menghubungkan aplikasi AI ke sistem eksternal. Ia menjembatani AI coding assistant (Cursor, Claude Code, VS Code/Copilot, Windsurf, Trae, Antigravity, Codex) dengan *testing engine* TestSprite sehingga seluruh siklus — membaca PRD → menganalisis kode → membuat test plan → menulis test code → mengeksekusi di cloud → mendiagnosis kegagalan → mengirim saran perbaikan balik ke coding agent — terjadi otomatis tanpa keluar dari IDE.

**Klaim utama:** menaikkan akurasi kode hasil AI dari **42% → 92–93%** fitur terpenuhi (berdasarkan benchmark internal CoderCup), akurasi kode **90%+**, pengujian **10x lebih cepat** (jam → menit), dan **121% peningkatan akurasi**. Pengguna cukup mengetik satu prompt: *"Help me test this project with TestSprite."*

**Fakta kunci:**

| Aspek | Detail |
|---|---|
| Produk | MCP Server (plugin npm `@testsprite/testsprite-mcp`), plus Web Portal, CLI open-source (Apache-2.0), CI/CD |
| Jumlah tools MCP | **8 core tools** (`testsprite_bootstrap` … `testsprite_check_account_info`) |
| Prasyarat | Node.js ≥ 22, akun TestSprite gratis, API key |
| Harga | Free $0 (150 kredit/bln) → Starter $19/bln (400 kredit) → Standard $69/bln (1.600 kredit) → Enterprise custom |
| Mode testing | `codebase` (full sweep, 20–50 tes, 10–20 menit) dan `diff` (git diff saja, 3–10 tes, 3–5 menit) |
| Perusahaan | TestSprite, Seattle AS, didirikan 2023 oleh Yunhao Jiao; total funding ±$9,66 juta |
| Eksekusi | Cloud sandbox terisolasi & ephemeral (localhost di-expose via tunnel milik MCP server) |
| Lisensi plugin | BUSL-1.1 (plugin MCP); CLI terpisah Apache-2.0 |

---

## 2. Profil Perusahaan

- **Nama:** TestSprite · **HQ:** Seattle, WA, Amerika Serikat · **Didirikan:** 2023 oleh **Yunhao Jiao** (CEO/penulis post blog utama).
- **Funding:**
  - **Pre-seed $1,5 juta (2024)** — otomatisasi testing API & web app ([GeekWire 2024](https://www.geekwire.com/2024/seattle-startup-testsprite-which-automates-testing-of-apis-and-web-apps-raises-1-5m/)).
  - **Seed $6,7 juta (Oktober 2025)** dipimpin **Trilogy Equity Partners** — positioning sebagai *"testing backbone for AI-generated code"* ([GeekWire 2025](https://www.geekwire.com/2025/seattle-startup-testsprite-raises-6-7m-to-become-testing-backbone-for-ai-generated-code/)).
  - Total pelaporan pihak ketiga (Tracxn): ±**$9,66 juta** dalam 3 ronde.
- **Skala klaim:** 100.000+ developer, 50.000+ tim; badge **Product Hunt #1 Product of the Day** (TestSprite 3.0, 22 Mei 2026); TestSprite 2.1 (Maret 2026) 414+ upvotes.
- **Misi (halaman About):** "We're Making Software Testing Easier" — memberi developer "kemampuan seorang software test engineer khusus" lewat otomasi.
- **Rekrutmen terbuka:** Marketing Manager, Marketing Assistant, Senior AI Engineer, Software Engineer Full Stack, Customer Support Specialist (hybrid Seattle/remote).
- **Saluran resmi:** [docs.testsprite.com](https://docs.testsprite.com/) · [github.com/testsprite](https://github.com/testsprite) (107 followers, verified owner) · Discord `discord.com/invite/GXWFjCe4an` · YouTube `@testsprite` · X `@test_sprite` · LinkedIn `/company/testsprite` · `contact@testsprite.com` · Calendly `calendly.com/contact-hmul/schedule`.

---

## 3. Halaman Solusi MCP — Bedah Total

### 3.1 Hero

- **Judul halaman:** "MCP for AI Coding Agents | TestSprite".
- **Headline:** *"Let Your AI Code, We'll Make It Work."*
- **Sub-headline:** Mengubah kode hasil AI menjadi software yang benar-benar berfungsi, via autonomous testing agent yang tertanam di IDE.
- **Klaim angka:**
  - "TestSprite MCP boosts your AI coding accuracy to **over 90%**" — *All automatic. All behind the scenes.*
  - Badge "Boost Accuracy at **121%**".
  - Grafik batang: **42% → 93%** (label "With Coding Agent Only" vs "With TestSprite", metrik *Features Delivered*); teks body menyebut "42% → 92% autonomous feature delivery through continuous verification" *(catatan: ada inkonsistensi kecil 92% vs 93% di halaman)*.
- **CTA:** "Install Now" (muncul 3×), "Contact Us"/"Schedule a Call" (Calendly), plus media **"MCP 10-Minute Demo Video"**.

### 3.2 Demo di halaman: Flight Booking Flow

*"From scattered scripts to a working flight booking flow powered by autonomous testing"* — ditampilkan dalam 4 tab: **Test Cases Generated by TestSprite MCP · Initial Test Execution Results · Manual Testing Outcome · Code After MCP Feedback**. Berikut 15 test case yang dipamerkan:

1. `TC001_User_Login_Success`
2. `TC002_User_Login_Failure_with_Incorrect_Credentials`
3. `TC003_Hotel_Search_Returns_Matching_Result`
4. `TC004_Flight_Search_Returns_Matching_Results`
5. `TC005_Booking_Simulation_for_Hotel`
6. `TC006_Booking_Simulation_for_Flight`
7. `TC007_Submit_and_Display_Hotel_Review`
8. `TC008_Submit_and_Display_Flight_Review`
9. `TC009_Booking_History_Displays_All_Past_Bookings`
10. `TC010_Admin_Add_and_Delete_Hotel`
11. `TC011_Admin_Add_and_Delete_Flight`
12. `TC012_UI_Layout_and_Responsiveness_Check`
13. `TC013_Application_Offline_Usage_and_Local_Data_Persistence`
14. `TC014_Invalid_Review_Submission_Handling`
15. `TC015_Invalid_Admin_Input_Handling`

### 3.3 Cara Pakai MCP — 4 Tahap

1. **CONFIGURING TESTS — "Understands What You Want":** set pengaturan dasar/info proyek; mem-parsing PRD atau intent bahasa alami untuk menentukan apa yang diuji.
2. **CREATING DOCUMENTATIONS — "Validates What Your Agent Wrote":** menganalisis codebase bersama coding agent; otomatis menghasilkan PRD ternormalisasi, test plan, test case, dan test code.
3. **EXECUTING TESTS — "Runs & Diagnoses the Results":** menjalankan test di lingkungan cloud remote; menandai kegagalan + akar masalah; memverifikasi output sesuai desain.
4. **GENERATING REPORTS — "Closes the Loop":** mengirim feedback terstruktur & test report ke coding agent dengan saran perbaikan presisi.

### 3.4 Next Steps (setelah loop pertama)

- **Organize with Test List** — kelompokkan tes ke list yang bisa dilacak.
- **Set Up Monitoring** — otomatisasi eksekusi terjadwal.
- **Schedule Recurring Tests** — pemeriksaan reliabilitas otomatis yang konsisten.
- **Track Progress Over Time** — visualisasi historis hasil untuk melihat tren & stabilitas.

### 3.5 FAQ di halaman (1 dari 5 terbuka)

- **"What is MCP Server for AI testing?"** — *"The MCP (Model Context Protocol) Server is an AI-powered testing agent built into your development workflow. It integrates with your IDE and your coding assistant, reads your product requirements (PRD), analyzes your codebase, automatically generates and executes comprehensive tests — covering logic, edge cases, error-handling, performance, and more. It then provides detailed feedback and even suggests fixes, enabling you to move from manual test writing to a fully AI-automated quality loop."*
- 4 pertanyaan lain (advanced AI testing agent untuk IDE, siapa penggunanya, cara terbaik menaikkan akurasi kode AI, solusi full-stack paling komprehensif) ter-listing namun jawabannya collapsed/tidak dirender.

### 3.6 Penutup

Tagline: *"Ship at AI speed. Sign off with evidence."* — tombol Install Now & Schedule a Call. Tidak ada fine print/disclaimer angka pada halaman ini.

---

## 4. 8 MCP Tools — Referensi Lengkap dengan Parameter

Sumber: [docs.testsprite.com/mcp/core/tools](https://docs.testsprite.com/mcp/core/tools.md). Server mengekspos **tepat 8 tools**; seluruh aksi lain mengalir lewat mereka.

### 4.1 `testsprite_bootstrap`
Menyiapkan lingkungan testing & konfigurasi awal (deteksi struktur proyek, verifikasi app jalan di port, menentukan scope, membuka configuration portal, memberi `next_action` agar AI bisa menyalakan app bila perlu).

| Parameter | Tipe | Keterangan |
|---|---|---|
| `localPort` | number | Port aplikasi berjalan (default `5173`) |
| `pathname` | string | Path tertentu yang tak terjangkau via navigasi antar halaman |
| `type` | string | `"frontend"` atau `"backend"` |
| `projectPath` | string | Path absolut direktori proyek |
| `testScope` | string | `"codebase"` atau `"diff"` |

```js
testsprite_bootstrap({ localPort: 3000, type: "frontend", projectPath: "/Users/dev/my-project", testScope: "codebase" })
```

### 4.2 `testsprite_generate_code_summary`
Memindai codebase → ringkasan arsitektur. Output file `code_summary.json` (analisis arsitektur, deteksi framework/dependency, feature mapping, entry points). Prasyarat langkah berikutnya.

| Parameter | Tipe | Keterangan |
|---|---|---|
| `projectRootPath` | string | Path absolut root proyek |

### 4.3 `testsprite_generate_standardized_prd`
Menghasilkan **PRD ternormalisasi** khas TestSprite → file `standard_prd.json` berisi product overview/goals, user story + acceptance criteria, functional requirements, technical specs. Menjadi fondasi test planning.

| Parameter | Tipe | Keterangan |
|---|---|---|
| `projectPath` | string | Path absolut root proyek |

### 4.4 `testsprite_generate_frontend_test_plan`
Test plan frontend → `frontend_test_plan.json` (UI interaction, form validation, navigasi/routing, visual regression, auth flow bila `needLogin`). Otomatis lanjut ke eksekusi.

| Parameter | Tipe | Keterangan |
|---|---|---|
| `projectPath` | string | Path absolut root proyek |
| `needLogin` | boolean | Butuh autentikasi? (default `true`) |

### 4.5 `testsprite_generate_backend_test_plan`
Test plan backend → `backend_test_plan.json` (API endpoints, functional integration, operasi database, auth/authorization, error handling). Otomatis lanjut ke eksekusi.

| Parameter | Tipe | Keterangan |
|---|---|---|
| `projectPath` | string | Path absolut root proyek |

### 4.6 `testsprite_generate_code_and_execute`
Menulis test code nyata (Playwright, Cypress, dll.), menjalankannya ke live app, lalu menghasilkan: file per-test, hasil, **bug report + saran perbaikan**, `testsprite_tests/tmp/test_results.json`, `tmp/report_prompt.json`, serta `TestSprite_MCP_Test_Report.md` & `.html` (demo docs juga menyebut varian `.pdf`), lengkap screenshot/video.

| Parameter | Tipe | Keterangan |
|---|---|---|
| `projectName` | string | Nama proyek (biasanya nama direktori root) |
| `projectPath` | string | Path absolut root proyek |
| `testIds` | array | ID test spesifik (mis. `["TC001","TC002"]`); `[]` = semua |
| `additionalInstruction` | string | Instruksi tambahan (mis. "Focus on checkout flow and payment security") |

```js
testsprite_generate_code_and_execute({
  projectName: "my-ecommerce-app",
  projectPath: "/Users/dev/my-project",
  testIds: [],
  additionalInstruction: "Focus on checkout flow and payment security"
})
```

### 4.7 `testsprite_open_test_result_dashboard`
Membuka kembali dashboard untuk meninjau/mengedit run lampau (tanpa parameter): riwayat suite & hasil, rekaman eksekusi, breakdown per langkah, browse/filter/sort, edit test case in-place, re-run test hasil editan.

### 4.8 `testsprite_check_account_info`
Cek nama akun, email, plan langganan, dan sisa kredit terkait API key (tanpa parameter). Mengembalikan `firstName`, `lastName`, `emailAddress`, `subPlan`, `credits`. Jika key invalid → status + tautan pembuatan key baru (MCP server harus direstart setelah ganti key).

### 4.9 Rantai workflow resmi

```
testsprite_bootstrap → baca PRD user → generate_code_summary → generate_standardized_prd
→ (frontend | backend)_test_plan sesuai tipe proyek → generate_code_and_execute
→ (opsional) modify tests / apply fixes di IDE → rerun
```

**Best practices terdokumentasi:** selalu pakai path absolut; pastikan app **berjalan** di port yang ditentukan sebelum eksekusi; kredensial login diisi via configuration portal; manfaatkan `testIds` untuk run inkremental; `additionalInstruction` untuk konteks fokus.

---

## 5. Workflow & Konsep Inti

### 5.1 Workflow 8 langkah (docs overview)

1. **Reads User PRD** — memahami requirement & goals.
2. **Analyzes Your Code** — memindai struktur, fitur, implementasi.
3. **Generates TestSprite PRD** — PRD ternormalisasi.
4. **Creates Test Plans** — test case komprehensif dari PRD + kode.
5. **Generates Test Code** — script eksekusi (Playwright, Cypress, dll.).
6. **Executes Tests** — di lingkungan cloud aman.
7. **Provides Results** — report detail + insight actionable.
8. **Enables Fixes** — IDE memakai analisis untuk patch otomatis.

### 5.2 Dua jalur utama pembuatan tes

**A. Proyek baru (9 langkah):** bootstrap (`testScope:"codebase"`) → baca PRD user → `generate_code_summary` → `generate_standardized_prd` → buat test plan (frontend/backend) → `generate_code_and_execute` → eksekusi cloud (sandbox → install dependency → run → kumpulkan hasil → buat report) → analisis hasil → **AI memperbaiki kode** dengan prompt:
```
Help me fix the codebase based on these test results
```
(AI IDE membaca `test_results.json` + `report_prompt.json`, menganalisis stderr/visual, membuat patch, lalu re-run untuk validasi — TestSprite menganalisis; coding agent Anda yang mengeksekusi perbaikan.)

**B. Perubahan baru / diff-based (6 langkah, shift-left):** bootstrap `testScope:"diff"` (analisis git diff sejak commit terakhir) → code summary diff-aware (`changed_features`, `dependencies_added`, `files_changed`, `lines_added/removed`) → update PRD (boleh diskip untuk bugfix kecil) → test plan tertarget → generate & execute → review laporan diff (regresi + saran). Contoh prompt: *"Test my new changes related to the Stripe payment features."* / *"Test my in-progress payment feature changes"*.

**Perbandingan scope (angka resmi docs):**

| Scope | Jumlah tes tipikal | Waktu eksekusi | Use case |
|---|---|---|---|
| `codebase` | 20–50 tes | 10–20 menit | Setup awal, rilis mayor |
| `diff` | 3–10 tes | 3–5 menit | Saat development, validasi PR (±3–5× lebih cepat) |

### 5.3 Test lifecycle 7 tahap

1. **Discover & Understand** → 2. **Plan** → 3. **Generate** → 4. **Execute** (artifacts: log, trace, screenshot, video) → 5. **Analyze** (klasifikasi kegagalan: *product bug* vs *test fragility* vs *environment*) → 6. **Heal & Maintain** → 7. **Report & Integrate** (sinyal untuk CI/CD quality gate). Dideskripsikan sebagai *"continuous and IDE-native"*.

### 5.4 Glosarium (Key Terms)

| Istilah | Definisi |
|---|---|
| **Generate** | Membuat tes pertama kali dari PRD & proyek |
| **Regenerate** | Membangun ulang dari nol setelah perubahan besar |
| **Run** | Eksekusi tes yang baru dibuat |
| **Rerun** | Eksekusi ulang tes yang sudah ada tanpa modifikasi |
| **Healing** | Perbaikan otomatis/semi-otomatis tes rapuh yang gagal karena perubahan non-fungsional (bukan menyembunyikan bug) |
| **Test Scope** | `codebase` (seluruh proyek) vs `diff` (berbasis git diff) |
| **PRD** | Dokumen requirement asli (boleh catatan informal, spesifikasi formal, ticket Jira, design doc) |
| **Normalized PRD** | Format PRD standar TestSprite (overview, fitur + acceptance criteria, user flow, konteks teknis) |
| **Test Plan** | Daftar terstruktur test case (ID TC001…, deskripsi, kategori, prioritas, expected outcome) disimpan sebagai `frontend_test_plan.json` / `backend_test_plan.json` |

### 5.5 Menambah & memodifikasi tes

- **Add Extra Tests:** tambahkan case manual di akhir file test plan (format: `id`, `title`, `description`, `category`, `priority`, `steps[] {type: action|assertion, description}`) lalu prompt *"Rerun the xth test for me using testsprite_generate_code_and_execute"*. Berguna untuk impor tes dari sumber lain (AI IDE seperti Cursor/Trae bisa membantu reformat).
- **Modify or Update Tests:** dashboard edit interaktif per langkah — klik langkah untuk melihat *snapshot* momen tersebut; 4 kapabilitas: ganti interaction type (Click/Navigate/Input/Scroll/Assert + selector), ubah nilai input, atur timeout, pilih elemen lain secara visual (point-and-click, tanpa CSS/XPath manual). **Smart Regeneration**: pilih *"Update this step only"* atau *"Update this and following steps"*; kustomisasi dipertahankan.
- **Import Existing Tests** (migrasi dari platform lain, mis. Playwright/Cypress/Jest/Postman): masukkan ke test plan (pertahankan ID/judul) → regenerate test code → run. Praktik terbaik: simpan tes di repo, map env var via portal, konsisten penamaan.

### 5.6 Perbandingan positioning (docs)

| Aspek | Testing Tradisional | TestSprite MCP Server |
|---|---|---|
| Pembuatan test case | Manual | AI otomatis |
| Setup | Framework kompleks | Hampir nol |
| Debugging | Debug gagal manual | Menganalisis & bantu perbaiki |
| Integrasi | Terpisah dari development | Di dalam workflow coding |
| Coverage | Terbatas, edge case terlewat | Otomatis komprehensif |

### 5.7 FAQ docs (11 pertanyaan — poin penting)

- **Portal vs MCP?** Portal = antarmuka visual/kolaborasi; MCP = testing di dalam IDE.
- **Kecepatan:** Portal 2–3 menit ke tes pertama; MCP 1–2 menit install/konfigurasi; mayoritas tes selesai **10–20 menit**.
- **Data:** source code **tidak pernah disimpan** di server mereka; enkripsi in-transit; sandbox cloud terisolasi; analisis MCP terjadi **lokal**; klaim SOC 2.
- **Jika gagal:** report detail, analisis akar masalah AI, saran perbaikan, **patch otomatis via MCP Server**, re-test sederhana.
- Klaim "5000+ Enterprise Customers" dan "90% reduction" waktu testing manual muncul di FAQ ini (belum terverifikasi pihak ketiga).

---

## 6. Instalasi per IDE/Client (Verbatim)

Sumber: [docs.testsprite.com/mcp/getting-started/installation](https://docs.testsprite.com/mcp/getting-started/installation.md) & [blog setup post](https://www.testsprite.com/blog/how-to-set-up-the-testsprite-mcp-server-in-your-ide).

**Prasyarat:** IDE kompatibel — **Trae, Cursor, Claude Code, Windsurf, VS Code, GitHub Copilot, Antigravity** (blog menambahkan konteks Codex) · akun TestSprite (gratis) · **Node.js ≥ 22** (`node --version`) · API key (dashboard → Settings → **API Keys** → *New API Key*; format tampil `sk_test_****...****abcd`; hanya ditampilkan sekali; bisa di-revoke, efek langsung).

**Konfigurasi standar (Cursor / Windsurf / umum):**

```json
{
  "mcpServers": {
    "TestSprite": {
      "command": "npx",
      "args": ["@testsprite/testsprite-mcp@latest"],
      "env": {
        "API_KEY": "your-api-key"
      }
    }
  }
}
```

**Cursor**
- *One-click:* deep link `cursor://anysphere.cursor-deeplink/mcp/install?name=TestSprite&config=...` → tempel API key.
- *Manual:* Settings (⌘⇧J) → **Tools & Integration** → **Add custom MCP** → tempel config standar. Sukses = titik hijau di ikon TestSprite.
- ⚠️ Sandbox: mode default "Run in Sandbox" Cursor **memblokir** TestSprite → Settings → Chat → Auto-Run → Auto-Run Mode → **"Ask Everytime"** atau **"Run Everything"**.

**Claude Code** (scoped per direktori proyek)
```bash
cd /path/to/your/project
claude mcp add TestSprite --env API_KEY=your_api_key -- npx @testsprite/testsprite-mcp@latest
claude mcp list   # harus: TestSprite: npx @testsprite/testsprite-mcp@latest - ✓ Connected
```

**VS Code** (Command Palette ⌘⇧P → **MCP: Add Server** → Command (stdio) → `npx @testsprite/testsprite-mcp@latest`, nama `TestSprite`):
```json
{
  "servers": {
    "testsprite": {
      "command": "npx",
      "args": ["-y", "@testsprite/testsprite-mcp@latest"],
      "env": { "API_KEY": "your-api-key" }
    }
  }
}
```
Klik tombol `start` pada entri di `mcp.json`.

**Antigravity:** panel agent → `...` → **Manage MCP Servers** → **View raw config** → tambahkan ke `mcp_config.json` (config standar).

**Trae:** AI Sidebar → AI Management → **MCP → Add → Add from Marketplace** → cari **TestSprite** → masukkan API key → pilih **Builder with MCP**. *(Ini rute yang dipakai tim Trae/ByteDance — mereka juga memberi testimoni.)*

**Instalasi global opsional (blog):** `npm install -g @testsprite/testsprite-mcp@latest` (lebih andal daripada npx yang me-resolve ulang tiap run).

**Verifikasi & troubleshoot:** prompt tes `"Help me test this project with TestSprite."` → configuration portal terbuka di browser. Pastikan app jalan (`npm run dev` frontend port 3000/5173/8080; backend `node index.js` port 8000/3001/4000; `python app.py` port 5000/8000). Cek versi Node (npx bisa gagal senyap di Node lama), spasi berlebih di API key, dan restart IDE setelah menyimpan config. Uninstall = hapus config + restart IDE.

**Alur first test (±10 menit):** siapkan proyek (`frontend/`, `backend/`, README) → prompt *"Can you test this project with TestSprite?"* (drag-drop folder proyek opsional) → isi **Testing Configuration** di browser: (1) tipe **Frontend/Backend** & scope **Codebase/Code Diff**, (2) kredensial akun tes (frontend user/pass; backend **Basic/Bearer/API-key/None**), (3) URL aplikasi (mis. `http://localhost:5173`), (4) **upload PRD — wajib**, boleh draft/kasalah kualitas, AI akan menormalisasinya → workflow otomatis → hasil di `testsprite_tests/` → minta AI memperbaiki: *"Please fix the codebase based on TestSprite testing results."*

---

## 7. Package npm `@testsprite/testsprite-mcp`

Data dari npm registry API (akses langsung npmjs.com diblokir 403):

| Properti | Nilai |
|---|---|
| Nama | `@testsprite/testsprite-mcp` |
| Deskripsi | "The TestSprite MCP plugin brings fully automated software testing into your coding workflow. It connects your IDE's AI assistant (like Cursor or Copilot) with TestSprite's intelligent testing engine to automatically generate, execute, and debug integratio…" |
| Versi terbaru | **0.0.43** (dimodifikasi terakhir 3 Sep 2026) |
| Rilis pertama | 13 Juli 2025 (42 versi terpublikasi) |
| Binary | `testsprite-mcp-plugin` → `dist/index.js` |
| Lisensi | **BUSL-1.1** (Business Source License — bukan open source; berbeda dari CLI yang Apache-2.0) |
| Keywords | testing, automation, ai, cursor, vscode, mcp, testsprite |
| Dependency | `@modelcontextprotocol/sdk`, `axios`, `commander`, `express`, `ws`, `simple-git`, `zod`, `yaml`, `open`, `conf`, `cli-progress`, `http-proxy`, `socks`, `proper-lockfile`, `markdown-it`, `multer`, `form-data`, `jschardet`, `iconv-lite`, `fs-extra`, `lodash`, `@llmcode/yamux-ts` — konsisten dengan fitur tunnel localhost→cloud, git diff, dashboard web lokal |

---

## 8. Jenis Tes & Teknologi yang Didukung

### 8.1 Frontend — Business-Flow E2E (6 kategori)

1. **User Journey Navigation** — multi-step workflow, transisi halaman, deep linking, history, route guard.
2. **Form Flows & Validation** — validasi input, pesan error, dependensi antar field, submission, persistensi data.
3. **Visual States & Layouts** — rendering komponen, responsive, loading/empty state, aksesibilitas.
4. **Interactive Components & Stateful UI** — dropdown, modal, tab, akordeon, drag-and-drop, real-time update.
5. **Authorization & Auth Flows** — login/logout, protected route, role-based visibility, session, token refresh.
6. **Error Handling (UI)** — toast, dialog, inline error, graceful degradation.

### 8.2 Backend — API & Integration (7 kategori)

1. **Functional API Workflows** — perilaku endpoint, orkestrasi multi-langkah, integrasi antar service.
2. **Contract & Schema Validation** — schema request/response, tipe data, field wajib, versi API.
3. **Error Handling & Resilience** — status code, retry, backoff, timeout.
4. **Authorization & Authentication** — validasi token, RBAC, scope, sesi.
5. **Boundary & Edge Cases** — limit payload, pagination, null/empty, input malformed.
6. **Data Integrity & Persistence** — konsistensi, transaksi, idempotency, constraint DB.
7. **Security Testing** — injection, XSS, CSRF, auth bypass, kebocoran data, misconfiguration.

*(Halaman solusi backend juga menyebut: load/performance testing, concurrency testing, response content testing.)*

### 8.3 Framework & bahasa (docs MCP overview)

- **Frontend:** React, Vue, Angular, Svelte, Next.js, Vite, Vanilla JS/TypeScript.
- **Backend:** Node.js, Python, Java, Go, Express.js, FastAPI, Spring Boot, REST API, GraphQL.
- **Test framework yang dihasilkan:** Playwright (utama; contoh kode Python async + headless Chromium), Cypress, Jest; klien API sesuai bahasa.
- **Mekanik eksekusi:** "Playwright/Cypress untuk UI; language-appropriate clients untuk API" — dipilih otomatis oleh agent.

---

## 9. Healing, Observability & Dashboard

### 9.1 Observability — sinyal yang dikumpulkan

- **Artifacts eksekusi:** screenshot & video (jalur UI), console log, network trace, HTTP request/response dengan header & payload.
- **Hasil terstruktur:** status per-test + timestamp + durasi, assertion & lokasi kegagalan, kategorisasi (functional/error handling/auth/boundary/edge/concurrency/UI-UX).
- **Context files:** code summary, PRD ternormalisasi, test plan, detail environment (framework, port, config). Semua tersimpan human-readable + machine-readable JSON di `testsprite_tests/`.

### 9.2 Klasifikasi akar masalah (4 kategori)

1. **Product Bug** — perilaku menyimpang dari PRD/plan.
2. **Test Fragility** — locator drift, timing mismatch, transient state.
3. **Environment Issue** — service down, port salah, kredensial hilang.
4. **Contract Violation** — schema response melanggar jaminan API.

Deteksi flakiness: langkah identik gagal di titik waktu berbeda, pass/fail bergantian tanpa perubahan kode, sensitif terhadap latensi/animasi/spinner → dijawab dengan stable wait, selector resilien, flow idempoten.

### 9.3 Strategi auto-heal

- **UI:** selector role/label/text-first (bukan CSS/XPath), deterministic wait (network idle, element visible), aksi ditunda sampai ready state.
- **Data & state:** data/fixture deterministik, reset state antar tes, cleanup sesi bocor.
- **Env & config:** panduan terarah untuk masalah port/start app, saran kredensial/env var di portal.
- **API contract:** assertion schema dikencangkan ke kontrak aktual, penambahan kasus boundary nullability/pagination.
- **Kebijakan:** *"Healing is applied automatically when safe; otherwise, it's proposed for review in your IDE."* Alur perbaikan 5 langkah: Execute & Collect → Analyze & Classify → Decide Healing Path → Verify (re-run) → Report (anotasi healed vs approved).

### 9.4 Test Progress Dashboard

Terbuka otomatis saat `testsprite_generate_code_and_execute` berjalan:
- **Real-time monitoring** (running / passed / issue), pencarian (nama/ID) & filter (status, tanggal dibuat, terakhir dimodifikasi), sort (created date, last execution, alfabetis).
- **Test detail:** rekaman video setiap run, pass/fail + pesan error, breakdown per langkah (Navigate/Input/Click/Scroll/Assert).
- **Riwayat:** buka kembali kapan saja via `testsprite_open_test_result_dashboard` (contoh prompt: *"open test result dashboard of testsprite cases"*) → telusuri suite historis, tonton rekaman, edit, dan re-run test.
- Di Web Portal, hasil MCP tampil di **All Tests → MCP Tests** (nama, waktu eksekusi, durasi, status Passed/Failed/In Progress, tipe Frontend/Backend/Mixed) — dibagikan sebagai URL dengan kontrol akses (changelog v2.0.9) dan proyek MCP bisa dihapus dari dashboard (v2.0.19).
- Changelog v2.0.19–v2.1.1 juga menyebut: **halaman local execution** berjendela popup dengan hasil real-time, **Test Modification Dashboard** (edit langkah, re-run dari langkah mana pun, "Add More Cases" setelah eksekusi, status persisten antar sesi), tool validasi API key & status akun.

---

## 10. Deploy, Monitoring & Integrasi GitHub/CI-CD

### 10.1 Deploy tests (web portal)

Syarat: tes sudah dibuat & dijalankan lokal via MCP; akses portal MCP Tests; URL aplikasi ter-deploy. Langkah: buka proyek → tombol **"Deploy Tests"** pada suite → pilih tes (individu/massal; mulai dari subset kritis) → konfirmasi → masukkan URL target (`https://your-app.com` atau `https://staging.your-app.com`; pastikan URL & kredensial auth dapat diakses) → pantau real-time di **All Tests** (log, visualisasi, screenshot, tren performa).

### 10.2 Continuous monitoring

Dua langkah: deploy → konfigurasikan jadwal. Fitur: eksekusi otomatis **harian/mingguan/bulanan** (opsional **per jam** untuk aplikasi kritis), **proactive alert** saat gagal/degradasi performa, **historical tracking** tren, jadwal bisa pause/ubah/hapus. Best practices: mulai dari tes stabil (hindari alert fatigue), frekuensi sesuai kekritisan, channel notifikasi berbeda per severity, validasi di non-production dulu, jaga tes tetap mutakhir.

### 10.3 GitHub integration — dua jalur

> **Prasyarat kritis:** *"TestSprite MCP Server is required first. The GitHub integration only runs tests — it does not generate them."* Test hasil MCP (folder `testsprite_tests/`) harus di-commit ke repo.

**GitHub App (rekomendasi)** — alur: deteksi PR → tunggu platform deploy (Vercel/Netlify/Render/Railway/Fly.io) preview → jalankan tes TestSprite ke preview URL → posting komentar PR berisi tabel hasil + link detail.
Setup: (1) hubungkan platform deploy (Vercel/Netlify/Render/Fly.io); (2) portal web → Settings → **GitHub App** → *Connect With GitHub App* → otorisasi → pilih repo; (3) pengaturan per repo: **Run on PRs**, **Include Draft PRs**, **Blocking PR** (blokir merge saat gagal); (4) manajemen: Add Connection / Remove / Manage in GitHub.

**GitHub Action** — untuk pipeline custom. Generate API key → simpan sebagai secret `TESTSPRITE_API_KEY` → workflow:

```yaml
name: TestSprite CI
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  test:
    needs: deploy
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: TestSprite Action
        uses: TestSprite/run-action@v1
        with:
          testsprite-api-key: ${{ secrets.TESTSPRITE_API_KEY }}
          base_url: ${{ needs.deploy.outputs.url }}
          blocking: 'true'   # gagalkan build jika tes tidak lulus
```

Docs menyertakan template lengkap deploy-preview + test untuk 5 platform: **Vercel** (secrets `VERCEL_TOKEN`/`VERCEL_ORG_ID`/`VERCEL_PROJECT_ID`), **Render** (`RENDER_API_KEY`, `RENDER_SERVICE_ID`, action `JorgeLNJunior/render-deploy@v1.5.0`), **Railway** (`RAILWAY_API_TOKEN`, `RAILWAY_PROJECT_ID`, action `ayungavis/railway-preview-deploy@v1.0.2` + job cleanup saat PR tertutup), **Netlify** (`NETLIFY_AUTH_TOKEN`, `NETLIFY_SITE_ID`, action `jsmrcaga/action-netlify-deploy@v2.0.0`), **Fly.io** (`FLY_API_TOKEN`, `superfly/flyctl-actions/setup-flyctl@master`).

| | GitHub App (direkomendasikan) | GitHub Action |
|---|---|---|
| Cocok untuk | Tim di Vercel/Netlify/Render/managed platform | Pipeline CI/CD custom |
| Setup | Beberapa klik via portal | YAML + repo secrets |
| Deteksi deploy | Otomatis | Manual dalam workflow |
| Konfigurasi | Web Portal | File workflow repo |

*(Halaman pricing juga menegaskan "IDE plugin via MCP (Claude Code, Codex)" dan "GitHub Action / CI integration" tersedia di **semua tier**.)*

---

## 11. Contoh Hasil Nyata (Demo E-commerce)

Dari [docs /learn/mcp-demo](https://docs.testsprite.com/learn/mcp-demo.md) — proyek e-commerce nyata: React 18 + TypeScript + Vite + Material UI, backend Node.js lokal, testing Playwright (Python).

- **Prompt:** *"Can you test this e-commerce project with TestSprite?"*
- **PRD ternormalisasi yang dihasilkan:** 5 core goals, 11 key features (Authentication System, Product Catalog, dst.), 7 user flows, 10 validation criteria, code summary (deteksi TypeScript/React/Vite/MUI/React Router).
- **Test plan:** 16 test case — kategori Functional (8), Error Handling (3), Security (3), UI (2). Contoh TC001 "Login success with valid test credentials" (functional, High), TC002 "Login failure with invalid credentials" (error handling, High), TC003 "Redirect unauthenticated user from protected routes" (security, High).
- **Test code:** Python async Playwright — headless Chromium (`--window-size=1280,720 --disable-dev-shm-usage`), context timeout 5.000 ms, `page.goto` timeout 10.000 ms ke `http://localhost:5174`, locator XPath, assertion judul & menu, cleanup di blok `finally`.
- **Hasil:** 16 total — 10 passed (62,5%), 6 failed (37,5%). Contoh kegagalan: TC002 *(High)* "System redirects to Product Catalog instead of showing error message" → saran: tampilkan error login yang semestinya; TC009 *(High)* "Admin dashboard page became empty after form submission" → saran: error boundary + validasi response backend.
- **Laporan:** 73% requirement teruji; tabel per-requirement (mis. Admin Product Management 3 tes 0 lulus; Purchase & Checkout 3/3 lulus); risiko: kegagalan fungsi admin kritis, error handling login, responsive belum tuntas.
- **Struktur file yang dihasilkan** (`testsprite_tests/`):
  - `tmp/` → `config.json`, `code_summary.json`, `report_prompt.json`, `test_results.json`, `prd_files/`
  - Root → `standard_prd.json`, `testsprite_frontend_test_plan.json`
  - Report → `TestSprite_MCP_Test_Report` (.md, .html, .pdf)
  - 16 script Playwright `TC001`–`TC016`
- Video demo resmi: "Get started with TestSprite MCP Server" (YouTube ID `W4AdVj407RU`) + demo 10 menit di halaman solusi — setup di dalam Cursor, PRD hilang dibuat otomatis, bug dideteksi & diselesaikan "tanpa intervensi manusia dalam satu loop", klaim 90%+ akurasi tanpa prompt manual.

---

## 12. Biaya, Kredit & Performance

- **Kredit** adalah mata uang kuota bulanan (lihat [Pricing](#14-pricing-plans)). Tool `testsprite_check_account_info` menampilkan sisa kredit sebelum run — pola yang direkomendasikan docs.
- **Kontrol biaya** (docs cost & performance — tanpa angka kredit-per-aksi): gunakan `testScope:"diff"` untuk iterasi harian; `testIds` untuk subset; **reports-first** (baca laporan dulu sebelum re-eksekusi); rerun (tanpa re-planning) untuk validasi perbaikan; kelola retensi artifact (screenshot/video) selaras jendela retensi CI.
- **Tips performa:** pastikan app cepat siap di `localPort` (pre-warm/seed DB), selector role/label-based untuk mengurangi retry/timeout, tes independen agar paralel di CI, fixture deterministik (hindari retry flaky yang lambat).
- **Default yang disarankan:** PR → diff scope + `testIds` tertarget; main branch → full run harian + **retensi artifact 7 hari**; hotfix → rerun terfokus.
- Angka performa klaim di berbagai halaman: URL → 50–100 tes E2E pertama dalam **±10 menit**; siklus testing 10–20 menit; diff scope 3–5 menit; benchmark manual pembanding "2–4 jam per E2E test" (Diffie *State of E2E Testing 2026*, Capgemini WQ Report 2024–25); suite 60 tes ≈ 3–6 minggu kerja manual.

---

## 13. Keamanan & Compliance

**Prinsip:** least privilege (kredensial minimum untuk tes), isolasi (sandbox + ephemeral environment), transparansi (laporan human-readable + log machine-readable).

- **API key & kredensial:** diatur via portal (bukan hardcode di tes), dukungan env var & secret injection; key workspace-scoped, revocable kapan pun; ditampilkan penuh sekali saja.
- **Artifact:** screenshot/video/log di `testsprite_tests/`; retensi dapat dikonfigurasi di CI; nilai sensitif dapat **dimask** di log/report.
- **Auth:** validasi gated route & role-based UI (frontend); token/scope/permission divalidasi dalam tes (backend); akun tes least-privilege + rotasi berkala.
- **Compliance:** halaman enterprise menyatakan **SOC 2 compliant** ("audited regularly; report provided to reviewers on request"); traceability requirement→test untuk audit; segregation of duties ("TestSprite analyzes, IDE applies fixes with approval"); setiap run tercatat siapa pemicu, target, kesimpulan (attribution); tidak ada instalasi di infrastruktur customer — staging/preview default, akses production **opt-in**.
- **Data (FAQ docs):** source code tidak disimpan di server mereka; enkripsi in-transit; analisis MCP lokal; eksekusi di cloud sandbox terisolasi.
- **Belum tersedia (dinyatakan eksplisit di halaman enterprise):** SSO, SCIM, data residency & retention — diakui sebagai roadmap, perusahaan berkomitmen memberi tanggal.
- Sertifikasi lain (ISO dsb.) tidak disebut; arsitektur detail sandbox tidak dipublikasikan.

---

## 14. Pricing Plans

Sumber: [testsprite.com/pricing](https://www.testsprite.com/pricing). Toggle tahunan: hemat 30% (angka tahunan tidak dirinci).

| | **Free** | **Starter** | **Standard** (Recommended) | **Enterprise** |
|---|---|---|---|---|
| Harga | $0 | **$0 bulan pertama**, lalu **$19/bln** | **$69/bln** | Custom (Calendly) |
| Kredit/bulan | 150 | 400 | 1.600 | Custom |
| Test Lists | 1 | 5 | Unlimited | Custom |
| Test Schedules | — | 5 | Unlimited | Custom |
| Model AI | GPT-5.4 Mini / Claude Sonnet 4.6 "atau serupa" | GPT-5.5 / Claude Opus 4.7 "atau serupa" + **Proprietary TestSprite Model** | sama seperti Starter | + **Custom AI model training**, API access |
| Fitur testing | Basic, workflow front+back otomatis, CLI | + Advanced, enhanced generation, custom config, **backend integration test chains**, **auto-healing rerun**, upload file tes 75 MB/proyek | sama + upload 300 MB | sama + limit custom |
| Optimisasi eksekusi | ✔ semua tier: eksekusi lebih cepat/efisien, skalabel proyek besar, **IDE plugin via MCP (Claude Code, Codex)**, **GitHub Action/CI**, test scheduling | ✔ | ✔ | ✔ |
| Support | Community | Priority | Priority | Dedicated + onboarding khusus |

Catatan: jumlah seat, jumlah proyek, biaya kredit per aksi tidak dipublikasikan. FAQ docs menambahkan: plan individual = beberapa proyek dalam limit pemakaian; plan tim = proyek bersama. **Plugin MCP tersedia mulai tier gratis.**

---

## 15. Changelog Lengkap (Agu 2025 – Apr 2026)

Sumber: [testsprite.com/changelog](https://www.testsprite.com/changelog).

| Versi | Tanggal | Sorotan |
|---|---|---|
| **v2.0.7** | 1 Agu 2025 | Onboarding refresh; **tool re-run** MCP; tes MCP tampil di Recent Created Tests; pesan error API key lebih jelas |
| **v2.0.9** | 1 Sep 2025 | **Test sharing** MCP (URL + kontrol akses); dukungan file frontend; re-run frontend via MCP; redesign test report |
| **v2.0.13** | 1 Okt 2025 | Overhaul dashboard total; **section MCP Tests** di dashboard; parameter `pathname` (target halaman spesifik); tool **"rerun with prompt"** |
| **v2.0.17** | 1 Nov 2025 | Yearly billing default; rebranding logo di seluruh permukaan MCP |
| **v2.0.18** | 1 Des 2025 | **Dukungan Linux** (semua OS utama) |
| **v2.0.19** | 1 Jan 2026 | **Local execution page** (popup + hasil real-time); hapus proyek MCP dari dashboard |
| **v2.1.0** | 1 Feb 2026 | **GitHub App integration** (settings, overview GitHub Action, hasil per tes); **Test Modification Dashboard** (edit semua langkah, re-run dari langkah mana pun, "Add More Cases", status persist antar sesi); engine code summary upgrade |
| **v2.1.1** | 1 Mar 2026 | Light mode + auto-detect; self-service **billing portal**; MCP dashboard rebrand + toggle tema; stabilitas koneksi; konsolidasi tool eksekusi; **tool validasi API key & status akun** |
| **v3.0.0** | 24 Apr 2026 | Engine mayor: **+40% akurasi** di proyek tersulit; **Feature Map** editable dari PRD; **Files & Fixtures** (CSV/JSON/PDF/image, auto-wired); **parallel exploration fleet** agent frontend; backend rebuild (dependency chains multi-layer, Dynamic Variables, Auto Cleanup, Auto-Auth, Smart Rerun, Data Flow view); pengumuman **TestSprite CLI** open-source menyusul |

Rilis besar terkait produk secara umum: **TestSprite 2.0 + MCP Server** diluncurkan 15 Juli 2025 (blog: baseline AI 42% → 93% setelah satu loop MCP dengan Cursor + Claude 3.5 Sonnet); **TestSprite 2.1** (5 Mar 2026); **TestSprite 3.0** (22 Mei 2026, #1 Product of the Day); **CLI open-source** (11 Jun 2026, liputan SiliconAngle).

---

## 16. Ekosistem Produk TestSprite (4 Surface)

> "One Engine, Every Surface" — CLI untuk agent · IDE via MCP · CI setiap merge · Dashboard no-code. Ketiganya berbagi backend, proyek, tes, dan run yang sama.

| Surface | Peran | Ciri khas | Batasan |
|---|---|---|---|
| **Web Portal** | Untuk manusia (QA, tim non-IDE) | Setup visual, upload PRD, eksplorasi, dashboard, scheduling, billing, manajemen tim; workflow 7 langkah; GitHub App; API keys | Tidak scriptable/agent-driven |
| **MCP Server** | Integrasi IDE untuk loop localhost | Dipanggil sebagai tool dalam sesi; **memiliki tunnel yang mengekspos localhost ke cloud runner** | Session-scoped; tidak scriptable; tidak untuk CI |
| **CLI** (`testsprite`) | Scriptable, agent-safe, CI | `--output json` + exit code (0/1) kontrak stabil; failure bundle atomik (langkah gagal + tetangga, DOM-as-text, hipotesis akar masalah, target fix); `--dry-run` offline; `testsprite setup` memasang *verification skill* ke agent (Claude Code default; `--agent codex/cursor/...`) | Tanpa target localhost (itu tugas MCP); tanpa manajemen jadwal/billing |
| **CI/CD** | Gerbang setiap merge | GitHub Action `TestSprite/run-action@v1`; full suite re-check per PR; hasil diposting ke PR | Bergantung deploy preview |

**Highlight Web Portal (pelengkap MCP):** spec-driven (plan dari PRD + eksplorasi live app + gap keduanya); UI testing Playwright+Python dengan Feature Exploration (Beta) & Auto-Heal; API testing dengan endpoint discovery, Dependency Chains, Auto-Cleanup, **Auto-Auth (Pro)**, Dynamic Variables, Data Flow view; dashboard Create Tests / All Tests / Test Lists / Monitoring / Settings.

**Highlight solusi frontend (halaman produk):** parallel agent exploration (banyak agent memetakan UI sekaligus), auto-heal 5 langkah (Run→Failed, Detect UI drift→Flagged, Auto-heal→Re-decided, Re-run→Passed, Commit fix→Healed), preview web hasil, analisis kode per tes, revisi bahasa alami via chat.

**Highlight solusi backend (halaman produk):** auto authentication (tes di belakang login otomatis), integration testing lintas service/DB/API pihak ketiga, stateful API flows (chaining nilai, dynamic variables, auto-cleanup data). 9 tipe tes: functional, auth, load/performance, security, boundary, response content, error handling, edge case, concurrency.

**Halaman enterprise:** klaim 12–25% regresi fitur per perubahan AI; suite E2E pertama manual $8.000–$16.000 waktu tergaji vs ±10 menit via TestSprite; named environments (staging/preview/production masing-masing dengan login & target default); version history/lineage per tes; test lists lintas proyek terjadwal; integrasi **Linear, Jira, Slack, Figma**; agentic chat plain-language dengan *"every write is proposed before it happens"*.

**Use-case directory:** 24+ artikel perbandingan SEO (TestSprite vs Applitools/Functionize/Katalon/Mabl/Octomind/QA.tech/Sauce Labs/TestMu/Testsigma/Virtuoso; serial Momentic/testRigor/mabl di blog), tersedia dalam 10 bahasa (EN, JA, zh-Hant, ES, PT, FR, DE, RU, AR, HI). Halaman khusus *"Software Testing MCP"* menegaskan 4 pilar: Save What You Broke / Understand What You Want / Validate What You Have / Suggest What You Need.

---

## 17. GitHub, Blog & Hackathon

### 17.1 GitHub org [github.com/testsprite](https://github.com/testsprite) (5 repo publik)

| Repo | Deskripsi | Bahasa | Bintang | Lisensi |
|---|---|---|---|---|
| **testsprite-cli** | CLI resmi — AI-powered automated testing dari terminal | TypeScript | 3.161 ⭐ (157 fork) | Apache-2.0 |
| **testsprite-action** | Jalankan tes TestSprite di GitHub Actions via CLI (composite action) | — | 0 | MIT |
| **Docs** | Sumber dokumentasi | MDX | 39 ⭐ | — |
| **CoderCup** | Benchmark publik berkelanjutan untuk AI coding agents, "dipimpin wasit TestSprite" | TypeScript | 23 ⭐ | Apache-2.0 |
| **run-action** | GitHub Action E2E tests TestSprite (`TestSprite/run-action@v1`) | JavaScript | 3 ⭐ | MIT |

### 17.2 Blog (27 post terindeks; 23 kategori Software Testing oleh Zeshi Du)

Paling relevan ke MCP: *"How to Set Up the TestSprite MCP Server in Your IDE"* (20 Agu 2026) · *"Introducing TestSprite 2.0 — AI That Tests, Fixes, and Delivers"* (15 Jul 2025, kelahiran MCP Server) · *"Introducing TestSprite 2.1"* (5 Mar 2026) · *"Building the Testing Backbone: Announcing Our Seed Round"* (30 Okt 2025) · *"What Is TestSprite and How Does It Test a Project Automatically?"* · serial Momentic (7 post) · *"TestSprite Pricing: Which Plan Fits a Solo Developer?"* · *"How to Run Your First Automated Test With TestSprite"*.

### 17.3 Hackathon Season 3 — "Build the Loop" (closed, winners announced)

- **Konsep:** coding agent (Claude Code/Codex/Antigravity) menulis kode; TestSprite CLI bertindak sebagai **checker** yang menjalankan tes nyata ke app live dan mengembalikan verdict yang dipatuhi agent. "A loop without a real checker hallucinates progress."
- **Hadiah:** pool **$5.000** — Project Awards $3.000 (5 pemenang) + CLI Improvement Bonus $2.000 (habis).
- **Loop 4 langkah:** Write → Verify (CLI) → Fix → Verify Again (tes lulus "dibank", berulang).
- **Aturan kunci:** repo publik + **LOOP.md ditulis agent** (satu kalimat per iterasi; tanpa log = gugur) + README + URL live; `testsprite setup` untuk onboarding; Node ≥ 20; CI/CD gating = bonus +5.
- **Rubrik:** Project Quality 40 / Loop Quality 40 / Innovation 20 (+5 CI) / Engagement bonus. Timeline 30 Jun – 15 Jul (2026); pemenang diumumkan di X & Discord.
- **CoderCup** (codercup.ai): benchmark publik — Claude Code, OpenAI Codex, Google Antigravity membangun app 10 fase dinilai terhadap 16–22 test plan E2E; metrik yang dikutip berulang: self-evolve 0%→80%, regresi 12–25%, requirement terpenuhi 42%→92%.

---

## 18. Review & Suara Komunitas

### 18.1 Review developer pihak ketiga (DEV.to, skor 8/10 — paling substantif)

Uji pada expense tracker multi-mata-uang (Next.js + Node.js; pengguna US/Indonesia/Jepang; USD/IDR/JPY):
- **Hasil:** setup <10 menit; auto-detect stack/route/endpoint/skema DB; ±15 menit menghasilkan 23 flow UI + 14 tes API + 6 edge case konversi mata uang tanpa YAML; patch otonom bekerja ujung-ke-ujung (rekomendasi fix → Cursor menerapkan → loop tertutup); menangkap bug nyata (currency switcher tidak persist saat refresh); CI/CD jalan di PR; 2 regresi tertangkap dalam 2 minggu.
- **Kritik konkret:** (1) **tidak locale-aware** — tes auto-generated mengasumsikan en-US (MM/DD/YYYY), tanpa variasi regional, tanggal bisa "lulus" padahal salah render untuk user Indonesia; (2) **format mata uang/angka tidak divalidasi di UI** (1,234,567 vs Rp 1.234.567 lolos semua tes) — perlu assertion manual untuk fintech/e-commerce.
- **Vonis:** "the real deal for the agentic development workflow", "dramatically raises the floor" kualitas produksi; akan jadi "no-brainer" bila ada locale-aware test generation.

### 18.2 Lain-lain

- **Agent Finder:** "the best autonomous testing agent for developers shipping AI-generated code".
- **Skywork AI:** deep-dive yang mengutip klaim 42% → 93%.
- **Video YouTube komunitas:** "TestSprite Review – 2025 | This AI Agent Running Your Software Testing" (cover MCP + Cursor); "The AI Agent That Guarantees Your Code Works".
- **Testimoni resmi:** Tim **Trae (ByteDance)** — *"Pretty cool MCP from TestSprite team!"*; Malcolm Y. (Genrex), Jeffery Z. (Princeton Pharmatech), Michel F. (Astronuts), Jakub K. (Parcel AI), Bo L. (QA Engineer, **Luckin Coffee** — generasi tes kaya, kode terbaca, debugging online).
- **Proyek komunitas "Built with TestSprite"** (Discover): Nora (OS belajar pixel-art, 55 iterasi loop write→verify→fix), PolyDub (dubbing real-time ~1,5 detik), Mantiz (deteksi agent curang di test suite), CinePurr, Sharetopus, Timeora (penjadwalan bahasa alami EN/**Indonesia**), TeamDynamics — dua terakhir oleh @bagusardin_ (developer Indonesia).
- **Press:** GeekWire (seed $6,7 jt; pre-seed $1,5 jt), SiliconAngle (peluncuran CLI open-source, 11 Jun 2026), Product Hunt (#1 Product of the Day 22 Mei 2026 untuk 3.0; 2.1 = 414+ upvotes).

---

## 19. Analisis: Kekuatan vs Keterbatasan

### Kekuatan

1. **Loop tertutup sejati di dalam IDE** — dari PRD sampai saran patch balik ke coding agent, via 8 tool yang dirancang compositional (`testIds`, `additionalInstruction`, `testScope`).
2. **Dua scope cerdas** — `diff` 3–5 menit untuk loop development cepat; `codebase` untuk onboarding/rilis.
3. **Observability kaya** — video rekaman, screenshot per langkah, DOM snapshot, klasifikasi akar masalah 4 kategori, failure bundle atomik.
4. **Auto-heal dengan kebijakan aman** — otomatis bila aman, usulan review bila mengubah perilaku; bukan penyembunyi bug.
5. **Eksekusi cloud terisolasi** — tidak perlu setup runner lokal; tunnel localhost dimiliki MCP server.
6. **Kontinuitas lintas surface** — tes MCP muncul di Web Portal, bisa di-share, dijadwalkan, dan dijadikan gerbang PR (GitHub App/Action di 5 platform deploy).
7. **Migrasi non-destruktif** — impor tes Playwright/Cypress/Jest/Postman yang sudah ada.
8. **Entry point murah** — tier gratis 150 kredit + plugin MCP di semua tier; CLI open source Apache-2.0.

### Keterbatasan & risiko

1. **Angka klaim (42→93%, 90%+, 121%) bersumber internal** (CoderCup/studi kasus sendiri) — belum ada audit independen; ada inkonsistensi kecil 92% vs 93% di halaman yang sama.
2. **Gap locale/internasionalisasi** (temuan review DEV.to) — asumsi en-US pada tanggal/format mata uang; krusial untuk aplikasi Indonesia/fintech.
3. **Klaim "5000+ Enterprise Customers"** di FAQ docs berbeda jauh skala dengan badge "50.000+ tim" di homepage — belum terverifikasi pihak ketiga.
4. **Plugin MCP berlisensi BUSL-1.1** (bukan open source) — berbeda dari CLI; perlu perhatian jika kebijakan organisasi melarang dependensi non-OSS.
5. **Belum ada SSO/SCIM/data residency** (dinyatakan sendiri di halaman enterprise) — relevan untuk adopsi enterprise besar.
6. **Dependensi runtime tinggi** — Node ≥ 22, app harus berjalan di port yang dikenal, kredensial tes via portal; Claude Code scoped per direktori proyek.
7. **Biaya per aksi tidak transparan** — konsumsi kredit per tool/run tidak dipublikasikan; hanya kuota bulanan.
8. **MCP server tidak untuk CI** — session-scoped; CI harus lewat CLI/GitHub Action (by design, tapi perlu dipahami).

---

## 20. Daftar Semua Sumber

**Situs utama TestSprite**
- Halaman solusi MCP: <https://www.testsprite.com/solutions/mcp>
- Homepage: <https://www.testsprite.com/>
- Pricing: <https://www.testsprite.com/pricing> · Changelog: <https://www.testsprite.com/changelog>
- Backend: <https://www.testsprite.com/solutions/backend> · Frontend: <https://www.testsprite.com/solutions/frontend>
- Enterprise: <https://www.testsprite.com/enterprise> · About: <https://www.testsprite.com/about>
- Use-cases: <https://www.testsprite.com/use-cases> · Software Testing MCP: <https://www.testsprite.com/use-cases/en/software-testing-mcp>
- Blog: <https://www.testsprite.com/blog> · Hackathon: <https://www.testsprite.com/hackathon-s3>

**Dokumentasi (docs.testsprite.com)**
- Indeks lengkap 82 halaman: <https://docs.testsprite.com/llms.txt>
- MCP: [introduction](https://docs.testsprite.com/mcp/getting-started/introduction.md) · [overview](https://docs.testsprite.com/mcp/getting-started/overview.md) · [installation](https://docs.testsprite.com/mcp/getting-started/installation.md) · [first-test](https://docs.testsprite.com/mcp/getting-started/first-test.md)
- MCP tools: <https://docs.testsprite.com/mcp/core/tools.md>
- Konsep: [key-terms](https://docs.testsprite.com/mcp/concepts/key-terms.md) · [test-type-lifecycle](https://docs.testsprite.com/mcp/concepts/test-type-lifecycle.md) · [healing-observability](https://docs.testsprite.com/mcp/concepts/healing-observability.md)
- Core: [create-tests-new-project](https://docs.testsprite.com/mcp/core/create-tests-new-project.md) · [create-tests-new-feature](https://docs.testsprite.com/mcp/core/create-tests-new-feature.md) · [add-extra-tests](https://docs.testsprite.com/mcp/core/add-extra-tests.md) · [test-progress-dashboard](https://docs.testsprite.com/mcp/core/test-progress-dashboard.md) · [modify-tests](https://docs.testsprite.com/mcp/core/modify-tests.md) · [deploy-to-production](https://docs.testsprite.com/mcp/core/deploy-to-production.md) · [continuous-monitoring](https://docs.testsprite.com/mcp/core/continuous-monitoring.md)
- Integrasi & maintenance: [github-integration](https://docs.testsprite.com/mcp/integrations/github-integration.md) · [cost-performance](https://docs.testsprite.com/mcp/maintenance/cost-performance.md) · [security-compliance](https://docs.testsprite.com/mcp/maintenance/security-compliance.md) · [migration](https://docs.testsprite.com/mcp/maintenance/migration-from-other-platforms.md)
- Learn: [mcp-demo](https://docs.testsprite.com/learn/mcp-demo.md) · [FAQ](https://docs.testsprite.com/learn/faq.md) · Admin: [api-keys](https://docs.testsprite.com/web-portal/admin/api-keys.md)
- CLI: [overview](https://docs.testsprite.com/cli/getting-started/overview.md)

**Package & repositori**
- npm: `@testsprite/testsprite-mcp` (registry.npmjs.org; akses 3 Sep 2026)
- GitHub org: <https://github.com/testsprite> — testsprite-cli, testsprite-action, Docs, CoderCup, run-action

**Pihak ketiga**
- Review DEV.to 8/10: <https://dev.to/shaadow347/testsprite-review-the-autonomous-ai-testing-agent-that-actually-closes-the-agentic-loop-5cg5>
- Agent Finder: <https://agent-finder.co/reviews/testsprite>
- Skywork AI deep-dive: <https://skywork.ai/skypage/en/TestSprite-MCP:-A-Deep-Dive-into-the-AI-Agent-That's-Fixing-AI-Generated-Code/1976129816263454720>
- GeekWire seed: <https://www.geekwire.com/2025/seattle-startup-testsprite-raises-6-7m-to-become-testing-backbone-for-ai-generated-code/> · pre-seed: <https://www.geekwire.com/2024/seattle-startup-testsprite-which-automates-testing-of-apis-and-web-apps-raises-1-5m/>
- Product Hunt: <https://www.producthunt.com/products/testsprite>
- Tracxn (total funding): <https://tracxn.com/d/companies/testsprite/__RawssYtjJcs9XTubf-s4VnhZ-yyNFkObB90H-UmBX2A>
- Video: YouTube @testsprite (intro MCP `W4AdVj407RU`; review komunitas `noJIUcB-Yb0`, `Ii4iByza908`)
- Komunitas: Discord <https://discord.com/invite/GXWFjCe4an> · X [@test_sprite](https://x.com/test_sprite) · LinkedIn [/company/testsprite](https://linkedin.com/company/testsprite)

---

*Dokumen ini disusun dari 33 sumber primer/sekunder yang di-fetch langsung pada 8 September 2026. Angka klaim (akurasi, jumlah pengguna, penghematan waktu) adalah klaim vendor kecuali disebutkan berasal dari pihak ketiga.*
