# \dt+

                                            List of relations
 Schema |        Name          | Type  |  Owner   | Persistence | Access method |    Size    | Description
--------+----------------------+-------+----------+-------------+---------------+------------+-------------
 public | acte_normative       | table | expertap | permanent   | heap          | 8192 bytes |
 public | argumentare_critica  | table | expertap | permanent   | heap          | 4320 kB    |
 public | decizii_cnsc         | table | expertap | permanent   | heap          | 78 MB      |
 public | legislatie_fragmente | table | expertap | permanent   | heap          | 8192 bytes |
 public | llm_settings         | table | expertap | permanent   | heap          | 8192 bytes |
 public | nomenclator_cpv      | table | expertap | permanent   | heap          | 8192 bytes |
(6 rows)


# \d decizii_cnsc
                              Table "public.decizii_cnsc"
         Column          |            Type             | Collation | Nullable | Default
-------------------------+-----------------------------+-----------+----------+---------
 id                      | uuid                        |           | not null |
 filename                | character varying(255)      |           | not null |
 numar_bo                | integer                     |           | not null |
 an_bo                   | integer                     |           | not null |
 numar_decizie           | integer                     |           |          |
 complet                 | character varying(5)        |           |          |
 data_decizie            | timestamp without time zone |           |          |
 tip_contestatie         | character varying(20)       |           | not null |
 coduri_critici          | character varying(10)[]     |           | not null |
 cod_cpv                 | character varying(20)       |           |          |
 cpv_descriere           | text                        |           |          |
 cpv_categorie           | character varying(50)       |           |          |
 cpv_clasa               | character varying(200)      |           |          |
 cpv_source              | character varying(20)       |           |          |
 solutie_filename        | character varying(1)        |           |          |
 solutie_contestatie     | character varying(20)       |           |          |
 motiv_respingere        | character varying(50)       |           |          |
 data_initiere_procedura | timestamp without time zone |           |          |
 data_raport_procedura   | timestamp without time zone |           |          |
 numar_anunt_participare | character varying(50)       |           |          |
 valoare_estimata        | numeric(15,2)               |           |          |
 moneda                  | character varying(3)        |           | not null |
 criteriu_atribuire      | character varying(100)      |           |          |
 numar_oferte            | integer                     |           |          |
 contestator             | character varying(500)      |           |          |
 autoritate_contractanta | character varying(500)      |           |          |
 intervenienti           | json                        |           |          |
 text_integral           | text                        |           | not null |
 parse_warnings          | json                        |           |          |
 created_at              | timestamp without time zone |           | not null | now()
 updated_at              | timestamp without time zone |           | not null | now()
Indexes:
    "decizii_cnsc_pkey" PRIMARY KEY, btree (id)
    "decizii_cnsc_filename_key" UNIQUE CONSTRAINT, btree (filename)
    "ix_decizii_bo_unique" UNIQUE, btree (an_bo, numar_bo)
    "ix_decizii_complet" btree (complet)
    "ix_decizii_cpv" btree (cod_cpv)
    "ix_decizii_critici" gin (coduri_critici)
    "ix_decizii_data" btree (data_decizie)
    "ix_decizii_fulltext" gin (text_integral gin_trgm_ops)
    "ix_decizii_solutie" btree (solutie_contestatie)
    "ix_decizii_tip" btree (tip_contestatie)
Referenced by:
    TABLE "argumentare_critica" CONSTRAINT "argumentare_critica_decizie_id_fkey" FOREIGN KEY (decizie_id) REFERENCES decizii_cnsc(id) ON DELETE CASCADE


# \d argumentare_critica
                            Table "public.argumentare_critica"
          Column           |            Type             | Collation | Nullable | Default
---------------------------+-----------------------------+-----------+----------+---------
 id                        | uuid                        |           | not null |
 decizie_id                | uuid                        |           | not null |
 cod_critica               | character varying(10)       |           | not null |
 ordine_in_decizie         | integer                     |           |          |
 argumente_contestator     | text                        |           |          |
 jurisprudenta_contestator | text[]                      |           |          |
 argumente_ac              | text                        |           |          |
 jurisprudenta_ac          | text[]                      |           |          |
 argumente_intervenienti   | json                        |           |          |
 elemente_retinute_cnsc    | text                        |           |          |
 argumentatie_cnsc         | text                        |           |          |
 jurisprudenta_cnsc        | text[]                      |           |          |
 castigator_critica        | character varying(20)       |           | not null |
 embedding_id              | uuid                        |           |          |
 embedding                 | vector(2000)                |           |          |
 created_at                | timestamp without time zone |           | not null | now()
Indexes:
    "argumentare_critica_pkey" PRIMARY KEY, btree (id)
    "ix_arg_castigator" btree (castigator_critica)
    "ix_arg_critica" btree (cod_critica)
    "ix_arg_decizie" btree (decizie_id)
    "ix_arg_embedding_hnsw" hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')
Foreign-key constraints:
    "argumentare_critica_decizie_id_fkey" FOREIGN KEY (decizie_id) REFERENCES decizii_cnsc(id) ON DELETE CASCADE


# \d nomenclator_cpv
                           Table "public.nomenclator_cpv"
       Column        |            Type             | Collation | Nullable | Default
---------------------+-----------------------------+-----------+----------+---------
 cod_cpv             | character varying(20)       |           | not null |
 descriere           | text                        |           | not null |
 categorie_achizitii | character varying(50)       |           |          |
 clasa_produse       | character varying(200)      |           |          |
 cod_parinte         | character varying(20)       |           |          |
 nivel               | integer                     |           |          |
 created_at          | timestamp without time zone |           | not null | now()
Indexes:
    "nomenclator_cpv_pkey" PRIMARY KEY, btree (cod_cpv)
    "ix_cpv_categorie" btree (categorie_achizitii)
    "ix_cpv_clasa" btree (clasa_produse)


# \d acte_normative
                            Table "public.acte_normative"
     Column      |            Type             | Collation | Nullable |       Default
-----------------+-----------------------------+-----------+----------+--------------------
 id              | uuid                        |           | not null | gen_random_uuid()
 tip_act         | character varying(30)       |           | not null |
 numar           | integer                     |           | not null |
 an              | integer                     |           | not null |
 titlu           | text                        |           |          |
 data_publicare  | date                        |           |          |
 created_at      | timestamp without time zone |           | not null | now()
Indexes:
    "acte_normative_pkey" PRIMARY KEY, btree (id)
    "ix_acte_unique" UNIQUE, btree (tip_act, numar, an)
Referenced by:
    TABLE "legislatie_fragmente" CONSTRAINT "legislatie_fragmente_act_id_fkey" FOREIGN KEY (act_id) REFERENCES acte_normative(id) ON DELETE CASCADE


# \d legislatie_fragmente
                          Table "public.legislatie_fragmente"
     Column       |            Type             | Collation | Nullable |       Default
------------------+-----------------------------+-----------+----------+--------------------
 id               | uuid                        |           | not null | gen_random_uuid()
 act_id           | uuid                        |           | not null |
 numar_articol    | integer                     |           | not null |
 articol          | character varying(30)       |           | not null |
 alineat          | integer                     |           |          |
 alineat_text     | character varying(20)       |           |          |
 litera           | character varying(5)        |           |          |
 text_fragment    | text                        |           | not null |
 articol_complet  | text                        |           |          |
 citare           | character varying(150)      |           | not null |
 capitol          | character varying(500)      |           |          |
 sectiune         | character varying(500)      |           |          |
 keywords         | tsvector                    |           |          |
 embedding        | vector(2000)                |           |          |
 created_at       | timestamp without time zone |           | not null | now()
Indexes:
    "legislatie_fragmente_pkey" PRIMARY KEY, btree (id)
    "ix_frag_unique" UNIQUE, btree (act_id, numar_articol, COALESCE(alineat, 0), COALESCE(litera::text, ''::text))
    "ix_frag_act" btree (act_id)
    "ix_frag_citare" btree (act_id, citare)
    "ix_frag_embedding_hnsw" hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')
    "ix_frag_keywords" gin (keywords)
    "ix_frag_lookup" btree (act_id, numar_articol, alineat, litera)
Foreign-key constraints:
    "legislatie_fragmente_act_id_fkey" FOREIGN KEY (act_id) REFERENCES acte_normative(id) ON DELETE CASCADE


# \d llm_settings
                            Table "public.llm_settings"
       Column        |            Type             | Collation | Nullable | Default
---------------------+-----------------------------+-----------+----------+---------
 id                  | integer                     |           | not null | 1
 active_provider     | character varying(30)       |           | not null | 'gemini'::character varying
 active_model        | character varying(100)      |           |          |
 gemini_api_key_enc  | text                        |           |          |
 anthropic_api_key_enc | text                      |           |          |
 openai_api_key_enc  | text                        |           |          |
 groq_api_key_enc    | text                        |           |          |
 openrouter_api_key_enc | text                     |           |          |
 updated_at          | timestamp without time zone |           | not null | now()
Indexes:
    "llm_settings_pkey" PRIMARY KEY, btree (id)


# \dx
                                    List of installed extensions
  Name   | Version |   Schema   |                            Description
---------+---------+------------+-------------------------------------------------------------------
 pg_trgm | 1.6     | public     | text similarity measurement and index searching based on trigrams
 plpgsql | 1.0     | pg_catalog | PL/pgSQL procedural language
 vector  | 0.8.1   | public     | vector data type and ivfflat and hnsw access methods
(3 rows)



# \d search_scopes
                            Table "public.search_scopes"
     Column       |            Type             | Collation | Nullable |       Default
------------------+-----------------------------+-----------+----------+--------------------
 id               | uuid                        |           | not null | gen_random_uuid()
 name             | character varying(100)      |           | not null |
 description      | text                        |           |          |
 filters          | jsonb                       |           | not null | '{}'::jsonb
 decision_count   | integer                     |           |          | 0
 created_at       | timestamp without time zone |           | not null | now()
 updated_at       | timestamp without time zone |           | not null | now()
Indexes:
    "search_scopes_pkey" PRIMARY KEY, btree (id)


---

# Ultima sincronizare cu producția: 2026-03-09

# Changelog Schema Producție

| Data | Comanda SQL | Executat de | Verificat |
|------|-------------|-------------|-----------|
| 2026-03-07 | `CREATE TABLE acte_normative (...)` + seed data 6 acte | Utilizator | DA |
| 2026-03-07 | `CREATE TABLE legislatie_fragmente (...)` + 7 indexuri (incl. HNSW, GIN, UNIQUE) | Utilizator | DA |
| 2026-03-07 | `DROP TABLE IF EXISTS citate_verbatim CASCADE` | Utilizator | DA |
| 2026-03-07 | `DROP TABLE IF EXISTS sectiuni_decizie CASCADE` | Utilizator | DA |
| 2026-03-07 | `DROP TABLE IF EXISTS referinte_articole CASCADE` | Utilizator | DA |
| 2026-03-07 | `DROP INDEX IF EXISTS ix_decizii_cnsc_solutie_contestatie` (duplicate of ix_decizii_solutie) | Utilizator | DA |
| 2026-03-08 | `CREATE TABLE llm_settings (id INTEGER PRIMARY KEY DEFAULT 1, active_provider VARCHAR(30) NOT NULL DEFAULT 'gemini', active_model VARCHAR(100), gemini_api_key_enc TEXT, anthropic_api_key_enc TEXT, openai_api_key_enc TEXT, updated_at TIMESTAMP NOT NULL DEFAULT now()); INSERT INTO llm_settings (id, active_provider) VALUES (1, 'gemini');` | Utilizator | DA |
| 2026-03-09 | `ALTER TABLE llm_settings ADD COLUMN groq_api_key_enc TEXT;` | Utilizator | DA |
| 2026-03-09 | `ALTER TABLE llm_settings ADD COLUMN openrouter_api_key_enc TEXT;` | Utilizator | DA |
| 2026-03-09 | `CREATE TABLE search_scopes (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name VARCHAR(100) NOT NULL, description TEXT, filters JSONB NOT NULL DEFAULT '{}', decision_count INTEGER DEFAULT 0, created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now());` | Utilizator | DA |
