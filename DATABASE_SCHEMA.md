# DATABASE_SCHEMA.md — Schema Producție ExpertAP

> **NOTĂ:** Acest fișier este un rezumat structural. Sursa de adevăr completă
> (cu output-uri reale din producție) este **`docs/expertap_db.md`**.
>
> **Ultima sincronizare cu producția:** 2026-03-07
> **Tabele în producție:** 5

---

## Tabele existente în producție (5)

| # | Tabel | Purpose | RAG? |
|---|-------|---------|------|
| 1 | `decizii_cnsc` | Decizii CNSC (tabel principal) | Nu |
| 2 | `argumentare_critica` | Argumentare per critică (RAG principal) | Da (2000-dim) |
| 3 | `nomenclator_cpv` | Nomenclator coduri CPV | Nu |
| 4 | `acte_normative` | Master table acte legislative | Nu |
| 5 | `legislatie_fragmente` | Fragmente legislație (literă/alineat/articol) | Da (2000-dim) |

## Tabele eliminate (2026-03-07)

- `sectiuni_decizie` — goală, funcționalitate acoperită de `argumentare_critica`
- `citate_verbatim` — goală, funcționalitate acoperită de `argumentare_critica`
- `referinte_articole` — goală, va fi reimplementată ulterior cu FK la `legislatie_fragmente`

SQL: `sql/drop_unused_tables.sql`

---

## Relații între tabele (Foreign Keys)

```
decizii_cnsc (1) ──→ (N) argumentare_critica    [ON DELETE CASCADE]
acte_normative (1) ──→ (N) legislatie_fragmente  [ON DELETE CASCADE]
nomenclator_cpv — independent (nu are FK)
```

---

## Embedding Configuration

- **Model:** `gemini-embedding-001` (output nativ: 3072 dim, limitat la 2000)
- **Dimensiuni DB:** `vector(2000)` pe `argumentare_critica` și `legislatie_fragmente`
- **Tip index:** HNSW cu `m=16, ef_construction=64`
- **Metric:** `vector_cosine_ops` (cosine similarity)
- **Limita pgvector:** HNSW suportă maxim 2000 dimensiuni

---

## Istoricul Migrărilor Alembic

| Revision | Data | Descriere |
|----------|------|-----------|
| `20251225_0001` | 2025-12-25 | Schema inițială: 6 tabele core + extensii + indexuri |
| `20260305_0002` | 2026-03-05 | Upgrade IVFFlat → HNSW; adăugat `embedding` pe `sectiuni_decizie` |
| `20260306_0003` | 2026-03-06 | Upgrade vector 768 → 2000 dimensiuni pe toate tabelele |

> **Notă:** Migrările Alembic mai vechi referă tabele eliminate (`sectiuni_decizie`, `citate_verbatim`, `referinte_articole`). Acestea sunt păstrate pentru istoricul migrărilor dar nu mai sunt relevante.

---

## Changelog Schema Producție

> Aici se documentează **fiecare** modificare SQL executată manual în producție.

| Data | Comanda SQL | Executat de | Verificat |
|------|-------------|-------------|-----------|
| 2026-03-07 | `CREATE TABLE acte_normative (...)` + seed data 6 acte | Utilizator | DA |
| 2026-03-07 | `CREATE TABLE legislatie_fragmente (...)` + 7 indexuri | Utilizator | DA |
| 2026-03-07 | `DROP TABLE citate_verbatim, sectiuni_decizie, referinte_articole CASCADE` | Utilizator | PENDING |
| 2026-03-07 | `DROP INDEX ix_decizii_cnsc_solutie_contestatie` (duplicate) | Utilizator | PENDING |
