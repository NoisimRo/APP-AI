# DATABASE_SCHEMA.md — Schema Producție ExpertAP

> **NOTĂ:** Acest fișier este un rezumat structural. Sursa de adevăr completă
> (cu output-uri reale din producție) este **`docs/expertap_db.md`**.
>
> **Ultima sincronizare cu producția:** 2026-03-07
> **Tabele în producție:** 6 (fără tabele legislație — încă de creat)

---

## Tabele existente în producție (6)

| # | Tabel | Purpose | RAG? |
|---|-------|---------|------|
| 1 | `decizii_cnsc` | Decizii CNSC (tabel principal) | Nu |
| 2 | `argumentare_critica` | Argumentare per critică (RAG principal) | Da (2000-dim) |
| 3 | `sectiuni_decizie` | Secțiuni decizie | Da (2000-dim) |
| 4 | `citate_verbatim` | Citate exacte din decizii | Da (2000-dim) |
| 5 | `referinte_articole` | Referințe la articole de lege | Nu |
| 6 | `nomenclator_cpv` | Nomenclator coduri CPV | Nu |

## Tabele de creat (legislație)

| # | Tabel | Purpose | RAG? |
|---|-------|---------|------|
| 7 | `acte_normative` | Master table acte legislative | Nu |
| 8 | `legislatie_fragmente` | Fragmente legislație (literă/alineat/articol) | Da (2000-dim) |

SQL complet: `sql/create_legislation_tables.sql`

---

## Relații între tabele (Foreign Keys)

```
decizii_cnsc (1) ──→ (N) argumentare_critica    [ON DELETE CASCADE]
decizii_cnsc (1) ──→ (N) sectiuni_decizie       [ON DELETE CASCADE]
decizii_cnsc (1) ──→ (N) citate_verbatim        [ON DELETE CASCADE]
decizii_cnsc (1) ──→ (N) referinte_articole      [ON DELETE CASCADE]

argumentare_critica (1) ──→ (N) referinte_articole   [ON DELETE SET NULL]
sectiuni_decizie (1) ──→ (N) citate_verbatim        [ON DELETE SET NULL]
argumentare_critica (1) ──→ (N) citate_verbatim      [ON DELETE SET NULL]

acte_normative (1) ──→ (N) legislatie_fragmente     [ON DELETE CASCADE]
nomenclator_cpv — independent (nu are FK)
```

---

## Embedding Configuration

- **Model:** `gemini-embedding-001` (output nativ: 3072 dim, limitat la 2000)
- **Dimensiuni DB:** `vector(2000)` pe toate coloanele de embedding
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

---

## Changelog Schema Producție

> Aici se documentează **fiecare** modificare SQL executată manual în producție.

| Data | Comanda SQL | Executat de | Verificat |
|------|-------------|-------------|-----------|
| _2026-03-07_ | _Document creat din cod + migrări — necesită validare vs producție_ | Claude | NU |

<!--
TEMPLATE pentru modificări viitoare:
| YYYY-MM-DD | `ALTER TABLE ... ;` | Utilizator | DA/NU |
-->
