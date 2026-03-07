# DATABASE_SCHEMA.md — Schema Producție ExpertAP

> **SURSA DE ADEVĂR** pentru schema bazei de date din producție.
> Orice modificare SQL executată în producție TREBUIE reflectată aici imediat.
>
> **Ultima sincronizare cu producția:** 2026-03-07 (reconstruit din models + migrări Alembic)
> **Validat manual de utilizator:** NU — necesită verificare `\dt+` / `\d <table>` în producție

---

## Extensii PostgreSQL

```sql
CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector pentru embeddings
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- trigram pentru full-text search
```

---

## 1. `decizii_cnsc` — Decizii CNSC (tabel principal)

| Coloană | Tip | Nullable | Default | Observații |
|---------|-----|----------|---------|------------|
| `id` | UUID | NOT NULL | `uuid_generate_v4()` | PK |
| `filename` | VARCHAR(255) | NOT NULL | — | UNIQUE |
| `numar_bo` | INTEGER | NOT NULL | — | |
| `an_bo` | INTEGER | NOT NULL | — | |
| `numar_decizie` | INTEGER | NULL | — | |
| `complet` | VARCHAR(5) | NULL | — | C1-C20 |
| `data_decizie` | TIMESTAMP | NULL | — | |
| `tip_contestatie` | VARCHAR(20) | NOT NULL | `'documentatie'` | `'documentatie'` sau `'rezultat'` |
| `coduri_critici` | VARCHAR(10)[] | NOT NULL | `'{}'` | ARRAY, ex: `{'R2'}`, `{'D1','D4'}` |
| `cod_cpv` | VARCHAR(20) | NULL | — | |
| `cpv_descriere` | TEXT | NULL | — | |
| `cpv_categorie` | VARCHAR(50) | NULL | — | Furnizare / Servicii / Lucrări |
| `cpv_clasa` | VARCHAR(200) | NULL | — | |
| `cpv_source` | VARCHAR(20) | NULL | — | `'filename'`, `'text_explicit'`, `'dedus'` |
| `solutie_filename` | VARCHAR(1) | NULL | — | A, R, X |
| `solutie_contestatie` | VARCHAR(20) | NULL | — | ADMIS, ADMIS_PARTIAL, RESPINS |
| `motiv_respingere` | VARCHAR(50) | NULL | — | nefondată, tardivă, etc. |
| `data_initiere_procedura` | TIMESTAMP | NULL | — | |
| `data_raport_procedura` | TIMESTAMP | NULL | — | |
| `numar_anunt_participare` | VARCHAR(50) | NULL | — | |
| `valoare_estimata` | NUMERIC(15,2) | NULL | — | |
| `moneda` | VARCHAR(3) | NOT NULL | `'RON'` | |
| `criteriu_atribuire` | VARCHAR(100) | NULL | — | |
| `numar_oferte` | INTEGER | NULL | — | |
| `contestator` | VARCHAR(500) | NULL | — | |
| `autoritate_contractanta` | VARCHAR(500) | NULL | — | |
| `intervenienti` | JSON | NULL | `'[]'` | |
| `text_integral` | TEXT | NOT NULL | — | |
| `parse_warnings` | JSON | NULL | `'[]'` | |
| `created_at` | TIMESTAMP | NOT NULL | `now()` | |
| `updated_at` | TIMESTAMP | NOT NULL | `now()` | auto-update on change |

**Indexuri:**

| Nume index | Coloane | Tip | Unic |
|------------|---------|-----|------|
| `decizii_cnsc_pkey` | `id` | btree | DA |
| `ix_decizii_cnsc_filename` | `filename` | btree | DA |
| `ix_decizii_bo_unique` | `an_bo, numar_bo` | btree | DA |
| `ix_decizii_tip` | `tip_contestatie` | btree | NU |
| `ix_decizii_critici` | `coduri_critici` | GIN | NU |
| `ix_decizii_cpv` | `cod_cpv` | btree | NU |
| `ix_decizii_solutie` | `solutie_contestatie` | btree | NU |
| `ix_decizii_data` | `data_decizie` | btree | NU |
| `ix_decizii_complet` | `complet` | btree | NU |
| `ix_decizii_fulltext` | `text_integral` | GIN (gin_trgm_ops) | NU |

---

## 2. `argumentare_critica` — Argumentare per critică (RAG principal)

| Coloană | Tip | Nullable | Default | Observații |
|---------|-----|----------|---------|------------|
| `id` | UUID | NOT NULL | `uuid_generate_v4()` | PK |
| `decizie_id` | UUID | NOT NULL | — | FK → `decizii_cnsc.id` ON DELETE CASCADE |
| `cod_critica` | VARCHAR(10) | NOT NULL | — | D1, R2, etc. |
| `ordine_in_decizie` | INTEGER | NULL | — | |
| `argumente_contestator` | TEXT | NULL | — | |
| `jurisprudenta_contestator` | TEXT[] | NULL | `'{}'` | ARRAY |
| `argumente_ac` | TEXT | NULL | — | |
| `jurisprudenta_ac` | TEXT[] | NULL | `'{}'` | ARRAY |
| `argumente_intervenienti` | JSON | NULL | — | `[{"nr": 1, "argumente": "...", "jurisprudenta": [...]}]` |
| `elemente_retinute_cnsc` | TEXT | NULL | — | |
| `argumentatie_cnsc` | TEXT | NULL | — | |
| `jurisprudenta_cnsc` | TEXT[] | NULL | `'{}'` | ARRAY |
| `castigator_critica` | VARCHAR(20) | NOT NULL | `'unknown'` | `contestator`, `autoritate`, `partial`, `unknown` |
| `embedding_id` | UUID | NULL | — | |
| `embedding` | vector(2000) | NULL | — | RAG embedding |
| `created_at` | TIMESTAMP | NOT NULL | `now()` | |

**Indexuri:**

| Nume index | Coloane | Tip | Unic |
|------------|---------|-----|------|
| `argumentare_critica_pkey` | `id` | btree | DA |
| `ix_arg_decizie` | `decizie_id` | btree | NU |
| `ix_arg_critica` | `cod_critica` | btree | NU |
| `ix_arg_castigator` | `castigator_critica` | btree | NU |
| `ix_arg_embedding_hnsw` | `embedding` | HNSW (vector_cosine_ops, m=16, ef_construction=64) | NU |

---

## 3. `sectiuni_decizie` — Secțiuni decizie

| Coloană | Tip | Nullable | Default | Observații |
|---------|-----|----------|---------|------------|
| `id` | UUID | NOT NULL | `uuid_generate_v4()` | PK |
| `decizie_id` | UUID | NOT NULL | — | FK → `decizii_cnsc.id` ON DELETE CASCADE |
| `tip_sectiune` | VARCHAR(50) | NOT NULL | — | antet, solicitari_contestator, etc. |
| `ordine` | INTEGER | NOT NULL | — | |
| `numar_intervenient` | INTEGER | NULL | — | |
| `text_sectiune` | TEXT | NOT NULL | — | |
| `embedding_id` | UUID | NULL | — | |
| `embedding` | vector(2000) | NULL | — | Adăugat în migrarea 0002, redimensionat în 0003 |
| `created_at` | TIMESTAMP | NOT NULL | `now()` | |

**Indexuri:**

| Nume index | Coloane | Tip | Unic |
|------------|---------|-----|------|
| `sectiuni_decizie_pkey` | `id` | btree | DA |
| `ix_sectiuni_decizie` | `decizie_id` | btree | NU |
| `ix_sectiuni_tip` | `tip_sectiune` | btree | NU |
| `ix_sectiuni_embedding_hnsw` | `embedding` | HNSW (vector_cosine_ops, m=16, ef_construction=64) | NU |

---

## 4. `citate_verbatim` — Citate exacte din decizii

| Coloană | Tip | Nullable | Default | Observații |
|---------|-----|----------|---------|------------|
| `id` | UUID | NOT NULL | `uuid_generate_v4()` | PK |
| `decizie_id` | UUID | NOT NULL | — | FK → `decizii_cnsc.id` ON DELETE CASCADE |
| `sectiune_id` | UUID | NULL | — | FK → `sectiuni_decizie.id` ON DELETE SET NULL |
| `argumentare_id` | UUID | NULL | — | FK → `argumentare_critica.id` ON DELETE SET NULL |
| `text_verbatim` | TEXT | NOT NULL | — | |
| `pozitie_start` | INTEGER | NULL | — | |
| `pozitie_end` | INTEGER | NULL | — | |
| `tip_citat` | VARCHAR(30) | NULL | — | `argumentatie_cnsc`, `dispozitiv`, `referinta_legala` |
| `embedding_id` | UUID | NULL | — | |
| `embedding` | vector(2000) | NULL | — | |
| `created_at` | TIMESTAMP | NOT NULL | `now()` | |

**Indexuri:**

| Nume index | Coloane | Tip | Unic |
|------------|---------|-----|------|
| `citate_verbatim_pkey` | `id` | btree | DA |
| `ix_citate_decizie` | `decizie_id` | btree | NU |
| `ix_citate_tip` | `tip_citat` | btree | NU |
| `ix_citate_embedding_hnsw` | `embedding` | HNSW (vector_cosine_ops, m=16, ef_construction=64) | NU |

---

## 5. `referinte_articole` — Referințe la articole de lege

| Coloană | Tip | Nullable | Default | Observații |
|---------|-----|----------|---------|------------|
| `id` | UUID | NOT NULL | `uuid_generate_v4()` | PK |
| `decizie_id` | UUID | NOT NULL | — | FK → `decizii_cnsc.id` ON DELETE CASCADE |
| `argumentare_id` | UUID | NULL | — | FK → `argumentare_critica.id` ON DELETE SET NULL |
| `act_normativ` | VARCHAR(50) | NOT NULL | — | ex: `L98/2016`, `HG395/2016` |
| `articol` | VARCHAR(30) | NOT NULL | — | ex: `art. 210`, `art. 196 alin. (2)` |
| `tip_referinta` | VARCHAR(20) | NULL | — | `trimitere`, `citat_partial`, `citat_integral` |
| `text_citat` | TEXT | NULL | — | |
| `invocat_de` | VARCHAR(20) | NULL | — | `contestator`, `ac`, `intervenient`, `cnsc` |
| `argument_castigator` | BOOLEAN | NULL | — | |
| `created_at` | TIMESTAMP | NOT NULL | `now()` | |

**Indexuri:**

| Nume index | Coloane | Tip | Unic |
|------------|---------|-----|------|
| `referinte_articole_pkey` | `id` | btree | DA |
| `ix_ref_decizie` | `decizie_id` | btree | NU |
| `ix_ref_articol` | `act_normativ, articol` | btree | NU |
| `ix_ref_invocat` | `invocat_de` | btree | NU |
| `ix_ref_castigator` | `argument_castigator` | btree | NU |

---

## 6. `nomenclator_cpv` — Nomenclator coduri CPV

| Coloană | Tip | Nullable | Default | Observații |
|---------|-----|----------|---------|------------|
| `cod_cpv` | VARCHAR(20) | NOT NULL | — | PK |
| `descriere` | TEXT | NOT NULL | — | |
| `categorie_achizitii` | VARCHAR(50) | NULL | — | Furnizare / Servicii / Lucrări |
| `clasa_produse` | VARCHAR(200) | NULL | — | |
| `cod_parinte` | VARCHAR(20) | NULL | — | |
| `nivel` | INTEGER | NULL | — | 1-8 granularitate |
| `created_at` | TIMESTAMP | NOT NULL | `now()` | |

**Indexuri:**

| Nume index | Coloane | Tip | Unic |
|------------|---------|-----|------|
| `nomenclator_cpv_pkey` | `cod_cpv` | btree | DA |
| `ix_cpv_categorie` | `categorie_achizitii` | btree | NU |
| `ix_cpv_clasa` | `clasa_produse` | btree | NU |

---

## 7. `articole_legislatie` — Articole legislație (granularitate: alineat)

> **ATENȚIE:** Acest tabel NU are migrare Alembic. A fost creat probabil prin `Base.metadata.create_all()` sau manual.
> Necesită confirmare că există în producție cu exact aceste coloane.

| Coloană | Tip | Nullable | Default | Observații |
|---------|-----|----------|---------|------------|
| `id` | UUID | NOT NULL | `uuid_generate_v4()` | PK |
| `act_normativ` | VARCHAR(100) | NOT NULL | — | `Legea 98/2016`, `HG 395/2016` |
| `numar_articol` | INTEGER | NOT NULL | — | numeric, pt sortare |
| `articol` | VARCHAR(50) | NOT NULL | — | `art. 2`, `art. 178` |
| `alineat` | INTEGER | NULL | — | 1, 2, 3... sau NULL |
| `alineat_text` | VARCHAR(20) | NULL | — | `alin. (1)`, `alin. (2)` |
| `litere` | JSON | NULL | — | `[{"litera": "a", "text": "..."}]` |
| `text_integral` | TEXT | NOT NULL | — | text complet alineat |
| `citare` | VARCHAR(100) | NOT NULL | — | `art. 2 alin. (2)`, `art. 1` |
| `capitol` | VARCHAR(500) | NULL | — | context capitol |
| `sectiune` | VARCHAR(500) | NULL | — | context secțiune |
| `embedding` | vector(2000) | NULL | — | RAG embedding |
| `created_at` | TIMESTAMP | NOT NULL | `now()` | |

**Indexuri:**

| Nume index | Coloane | Tip | Unic |
|------------|---------|-----|------|
| `articole_legislatie_pkey` | `id` | btree | DA |
| `ix_art_act` | `act_normativ` | btree | NU |
| `ix_art_numar` | `act_normativ, numar_articol` | btree | NU |
| `ix_art_citare` | `act_normativ, citare` | btree | DA (UNIQUE) |
| `ix_art_embedding_hnsw` | `embedding` | HNSW (vector_cosine_ops, m=16, ef_construction=64) | NU |

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

articole_legislatie — independent (nu are FK)
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

**Lipsesc migrări pentru:**
- `articole_legislatie` — creat direct, fără Alembic

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
