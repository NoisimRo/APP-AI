# \dt+

                                            List of relations
 Schema |        Name              | Type  |  Owner   | Persistence | Access method |    Size    | Description
--------+--------------------------+-------+----------+-------------+---------------+------------+-------------
 public | acte_normative           | table | expertap | permanent   | heap          | 8192 bytes |
 public | argumentare_critica      | table | expertap | permanent   | heap          | 4320 kB    |
 public | conversatii              | table | expertap | permanent   | heap          | 8192 bytes |
 public | decizii_cnsc             | table | expertap | permanent   | heap          | 78 MB      |
 public | documente_generate       | table | expertap | permanent   | heap          | 8192 bytes |
 public | legislatie_fragmente     | table | expertap | permanent   | heap          | 8192 bytes |
 public | llm_settings             | table | expertap | permanent   | heap          | 8192 bytes |
 public | mesaje_conversatie       | table | expertap | permanent   | heap          | 8192 bytes |
 public | nomenclator_cpv          | table | expertap | permanent   | heap          | 8192 bytes |
 public | red_flags_salvate        | table | expertap | permanent   | heap          | 8192 bytes |
 public | search_scopes            | table | expertap | permanent   | heap          | 8192 bytes |
 public | spete_anap               | table | expertap | permanent   | heap          | 8192 bytes |
 public | training_materials       | table | expertap | permanent   | heap          | 8192 bytes |
 public | user_context             | table | expertap | permanent   | heap          | 8192 bytes |
 public | users                    | table | expertap | permanent   | heap          | 8192 bytes |
(15 rows)


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
 domeniu_legislativ      | character varying(30)       |           |          |
 tip_procedura           | character varying(80)       |           |          |
 contestator             | character varying(500)      |           |          |
 autoritate_contractanta | character varying(500)      |           |          |
 intervenienti           | json                        |           |          |
 text_integral           | text                        |           | not null |
 obiect_contract         | text                        |           |          |
 rezumat                 | text                        |           |          |
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
    "ix_decizii_domeniu" btree (domeniu_legislativ)
    "ix_decizii_procedura" btree (tip_procedura)
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
 denumire_en         | character varying(200)      |           |          |
 categorie_achizitii | character varying(50)       |           |          |
 clasa_produse       | character varying(200)      |           |          |
 cod_parinte         | character varying(20)       |           |          |
 nivel               | integer                     |           |          |
 created_at          | timestamp without time zone |           | not null | now()
 embedding           | vector(2000)                |           |          |
Indexes:
    "nomenclator_cpv_pkey" PRIMARY KEY, btree (cod_cpv)
    "ix_cpv_categorie" btree (categorie_achizitii)
    "ix_cpv_clasa" btree (clasa_produse)
    "ix_cpv_embedding" hnsw (embedding vector_cosine_ops)


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
    "ix_frag_unique" UNIQUE, btree (act_id, numar_articol, COALESCE(alineat, 0), COALESCE(litera, ''::character varying))
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
 user_id          | uuid                        |           |          |
 created_at       | timestamp without time zone |           | not null | now()
 updated_at       | timestamp without time zone |           | not null | now()
Indexes:
    "search_scopes_pkey" PRIMARY KEY, btree (id)
    "ix_search_scopes_user_id" btree (user_id)
Foreign-key constraints:
    "search_scopes_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE


# \d users
                              Table "public.users"
         Column        |            Type             | Collation | Nullable |       Default
-----------------------+-----------------------------+-----------+----------+--------------------
 id                    | uuid                        |           | not null | gen_random_uuid()
 email                 | character varying(255)      |           |          |
 nume                  | character varying(200)      |           |          |
 password_hash         | character varying(255)      |           |          |
 rol                   | character varying(30)       |           | not null | 'registered'::character varying
 activ                 | boolean                     |           | not null | true
 email_verified        | boolean                     |           | not null | false
 reset_token           | character varying(255)      |           |          |
 reset_token_expires   | timestamp without time zone |           |          |
 verification_code     | character varying(10)       |           |          |
 verification_code_expires | timestamp without time zone |           |          |
 metadata              | jsonb                       |           |          | '{}'::jsonb
 created_at            | timestamp without time zone |           | not null | now()
 updated_at            | timestamp without time zone |           | not null | now()
 last_login            | timestamp without time zone |           |          |
Indexes:
    "users_pkey" PRIMARY KEY, btree (id)
    "users_email_key" UNIQUE CONSTRAINT, btree (email)
    "ix_users_email" btree (email)
    "ix_users_rol" btree (rol)
Referenced by:
    TABLE "conversatii" CONSTRAINT "conversatii_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    TABLE "documente_generate" CONSTRAINT "documente_generate_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    TABLE "red_flags_salvate" CONSTRAINT "red_flags_salvate_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    TABLE "training_materials" CONSTRAINT "training_materials_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    TABLE "user_context" CONSTRAINT "user_context_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE


# \d user_context
                            Table "public.user_context"
     Column     |            Type             | Collation | Nullable |       Default
----------------+-----------------------------+-----------+----------+--------------------
 id             | uuid                        |           | not null | gen_random_uuid()
 user_id        | uuid                        |           | not null |
 fact_type      | character varying(50)       |           | not null | 'general'::character varying
 content        | text                        |           | not null |
 source         | character varying(50)       |           |          |
 importance     | integer                     |           | not null | 5
 active         | boolean                     |           | not null | true
 created_at     | timestamp without time zone |           | not null | now()
 updated_at     | timestamp without time zone |           | not null | now()
Indexes:
    "user_context_pkey" PRIMARY KEY, btree (id)
    "idx_user_context_user_id" btree (user_id)
Foreign-key constraints:
    "user_context_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE


# \d conversatii
                            Table "public.conversatii"
     Column     |            Type             | Collation | Nullable |       Default
----------------+-----------------------------+-----------+----------+--------------------
 id             | uuid                        |           | not null | gen_random_uuid()
 user_id        | uuid                        |           |          |
 titlu          | character varying(200)      |           | not null |
 primul_mesaj   | text                        |           |          |
 numar_mesaje   | integer                     |           | not null | 0
 scope_id       | uuid                        |           |          |
 created_at     | timestamp without time zone |           | not null | now()
 updated_at     | timestamp without time zone |           | not null | now()
Indexes:
    "conversatii_pkey" PRIMARY KEY, btree (id)
    "ix_conv_created" btree (created_at DESC)
    "ix_conv_user" btree (user_id)
Foreign-key constraints:
    "conversatii_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    "conversatii_scope_id_fkey" FOREIGN KEY (scope_id) REFERENCES search_scopes(id) ON DELETE SET NULL
Referenced by:
    TABLE "mesaje_conversatie" CONSTRAINT "mesaje_conversatie_conversatie_id_fkey" FOREIGN KEY (conversatie_id) REFERENCES conversatii(id) ON DELETE CASCADE


# \d mesaje_conversatie
                         Table "public.mesaje_conversatie"
      Column       |            Type             | Collation | Nullable |       Default
-------------------+-----------------------------+-----------+----------+--------------------
 id                | uuid                        |           | not null | gen_random_uuid()
 conversatie_id    | uuid                        |           | not null |
 rol               | character varying(20)       |           | not null |
 continut          | text                        |           | not null |
 citations         | jsonb                       |           |          | '[]'::jsonb
 confidence        | real                        |           |          |
 ordine            | integer                     |           | not null |
 created_at        | timestamp without time zone |           | not null | now()
Indexes:
    "mesaje_conversatie_pkey" PRIMARY KEY, btree (id)
    "ix_msg_conv" btree (conversatie_id, ordine)
Foreign-key constraints:
    "mesaje_conversatie_conversatie_id_fkey" FOREIGN KEY (conversatie_id) REFERENCES conversatii(id) ON DELETE CASCADE


# \d documente_generate
                         Table "public.documente_generate"
      Column        |            Type             | Collation | Nullable |       Default
--------------------+-----------------------------+-----------+----------+--------------------
 id                 | uuid                        |           | not null | gen_random_uuid()
 user_id            | uuid                        |           |          |
 tip_document       | character varying(30)       |           | not null |
 titlu              | character varying(300)      |           | not null |
 continut           | text                        |           | not null |
 referinte_decizii  | text[]                      |           |          |
 metadata           | jsonb                       |           |          | '{}'::jsonb
 created_at         | timestamp without time zone |           | not null | now()
 updated_at         | timestamp without time zone |           | not null | now()
Indexes:
    "documente_generate_pkey" PRIMARY KEY, btree (id)
    "ix_docgen_created" btree (created_at DESC)
    "ix_docgen_tip" btree (tip_document)
    "ix_docgen_user" btree (user_id)
Foreign-key constraints:
    "documente_generate_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE


# \d red_flags_salvate
                         Table "public.red_flags_salvate"
        Column         |            Type             | Collation | Nullable |       Default
-----------------------+-----------------------------+-----------+----------+--------------------
 id                    | uuid                        |           | not null | gen_random_uuid()
 user_id               | uuid                        |           |          |
 titlu                 | character varying(300)      |           | not null |
 text_analizat_preview | text                        |           |          |
 rezultate             | jsonb                       |           | not null |
 total_flags           | integer                     |           | not null | 0
 critice               | integer                     |           | not null | 0
 medii                 | integer                     |           | not null | 0
 scazute               | integer                     |           | not null | 0
 created_at            | timestamp without time zone |           | not null | now()
 updated_at            | timestamp without time zone |           | not null | now()
Indexes:
    "red_flags_salvate_pkey" PRIMARY KEY, btree (id)
    "ix_rf_created" btree (created_at DESC)
    "ix_rf_user" btree (user_id)
Foreign-key constraints:
    "red_flags_salvate_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE


# \d training_materials
                          Table "public.training_materials"
       Column        |            Type             | Collation | Nullable |       Default
---------------------+-----------------------------+-----------+----------+--------------------
 id                  | uuid                        |           | not null | gen_random_uuid()
 user_id             | uuid                        |           |          |
 tip_material        | character varying(30)       |           | not null |
 tema                | text                        |           | not null |
 nivel_dificultate   | character varying(20)       |           | not null |
 lungime             | character varying(20)       |           | not null |
 full_content        | text                        |           | not null |
 material            | text                        |           |          |
 cerinte             | text                        |           |          |
 rezolvare           | text                        |           |          |
 note_trainer        | text                        |           |          |
 legislatie_citata   | text[]                      |           |          |
 jurisprudenta_citata| text[]                      |           |          |
 metadata            | jsonb                       |           |          | '{}'::jsonb
 created_at          | timestamp without time zone |           | not null | now()
 updated_at          | timestamp without time zone |           | not null | now()
Indexes:
    "training_materials_pkey" PRIMARY KEY, btree (id)
    "ix_tm_created" btree (created_at DESC)
    "ix_tm_tip" btree (tip_material)
    "ix_tm_user" btree (user_id)
Foreign-key constraints:
    "training_materials_user_id_fkey" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE


# \d spete_anap
                              Table "public.spete_anap"
       Column        |            Type             | Collation | Nullable |      Default
---------------------+-----------------------------+-----------+----------+--------------------
 id                  | uuid                        |           | not null | gen_random_uuid()
 numar_speta         | integer                     |           | not null |
 versiune            | integer                     |           | not null | 1
 data_publicarii     | timestamp without time zone |           | not null |
 categorie           | character varying(200)      |           | not null |
 intrebare           | text                        |           | not null |
 raspuns             | text                        |           | not null |
 taguri              | text[]                      |           |          |
 embedding           | vector(2000)                |           |          |
 created_at          | timestamp without time zone |           | not null | now()
Indexes:
    "spete_anap_pkey" PRIMARY KEY, btree (id)
    "spete_anap_numar_speta_key" UNIQUE CONSTRAINT, btree (numar_speta)
    "ix_spete_categorie" btree (categorie)
    "ix_spete_embedding_hnsw" hnsw (embedding vector_cosine_ops)
    "ix_spete_taguri" gin (taguri)


---

# Ultima sincronizare cu producția: 2026-04-04

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
| 2026-03-15 | `CREATE TABLE users (...)` + 2 indexuri (email, rol) — pregătit pentru auth multi-user viitor | Utilizator | DA |
| 2026-03-15 | `CREATE TABLE conversatii (...)` + 2 indexuri (user_id, created_at DESC) + FK users, search_scopes | Utilizator | DA |
| 2026-03-15 | `CREATE TABLE mesaje_conversatie (...)` + 1 index (conversatie_id, ordine) + FK conversatii CASCADE | Utilizator | DA |
| 2026-03-15 | `CREATE TABLE documente_generate (...)` + 3 indexuri (user_id, tip, created_at DESC) + FK users | Utilizator | DA |
| 2026-03-15 | `CREATE TABLE red_flags_salvate (...)` + 2 indexuri (user_id, created_at DESC) + FK users | Utilizator | DA |
| 2026-03-15 | `CREATE TABLE training_materials (...)` + 3 indexuri (user_id, tip, created_at DESC) + FK users | Utilizator | DA |
| 2026-03-16 | `ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)` | Utilizator | DA |
| 2026-03-16 | `ALTER TABLE users ADD COLUMN last_login TIMESTAMP` | Utilizator | DA |
| 2026-03-16 | `ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT false` | Utilizator | DA |
| 2026-03-16 | `ALTER TABLE users ADD COLUMN reset_token VARCHAR(255)` | Utilizator | DA |
| 2026-03-16 | `ALTER TABLE users ADD COLUMN reset_token_expires TIMESTAMP` | Utilizator | DA |
| 2026-03-17 | `ALTER TABLE users ADD COLUMN verification_code VARCHAR(10)` | Utilizator | DA |
| 2026-03-17 | `ALTER TABLE users ADD COLUMN verification_code_expires TIMESTAMP` | Utilizator | DA |
| 2026-03-17 | `ALTER TABLE search_scopes ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE CASCADE;` | Utilizator | DA |
| 2026-03-17 | `CREATE INDEX ix_search_scopes_user_id ON search_scopes(user_id);` | Utilizator | DA |
| 2026-03-20 | `ALTER TABLE decizii_cnsc ADD COLUMN obiect_contract TEXT;` | Utilizator | DA |
| 2026-03-20 | `ALTER TABLE decizii_cnsc ADD COLUMN rezumat TEXT;` | Utilizator | DA |
| 2026-03-20 | `ALTER TABLE nomenclator_cpv ADD COLUMN embedding vector(2000);` | Utilizator | DA |
| 2026-03-20 | `CREATE INDEX ix_cpv_embedding ON nomenclator_cpv USING hnsw (embedding vector_cosine_ops);` | Utilizator | DA |
| 2026-03-21 | `ALTER TABLE decizii_cnsc ADD COLUMN domeniu_legislativ VARCHAR(30);` | Utilizator | DA |
| 2026-03-21 | `ALTER TABLE decizii_cnsc ADD COLUMN tip_procedura VARCHAR(80);` | Utilizator | DA |
| 2026-03-21 | `COMMENT ON COLUMN decizii_cnsc.domeniu_legislativ IS 'achizitii_publice (L98/2016), achizitii_sectoriale (L99/2016), concesiuni (L100/2016)';` | Utilizator | DA |
| 2026-03-21 | `COMMENT ON COLUMN decizii_cnsc.tip_procedura IS 'licitatie_deschisa, licitatie_restransa, negociere_competitiva, dialog_competitiv, parteneriat_inovare, negociere_fara_publicare, concurs_solutii, servicii_sociale, procedura_simplificata';` | Utilizator | DA |
| 2026-03-21 | `CREATE INDEX ix_decizii_domeniu ON decizii_cnsc (domeniu_legislativ);` | Utilizator | DA |
| 2026-03-21 | `CREATE INDEX ix_decizii_procedura ON decizii_cnsc (tip_procedura);` | Utilizator | DA |
| 2026-04-02 | `CREATE TABLE spete_anap (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), numar_speta INTEGER NOT NULL UNIQUE, versiune INTEGER NOT NULL DEFAULT 1, data_publicarii TIMESTAMP NOT NULL, categorie VARCHAR(200) NOT NULL, intrebare TEXT NOT NULL, raspuns TEXT NOT NULL, taguri TEXT[], embedding vector(2000), created_at TIMESTAMP NOT NULL DEFAULT now());` + 3 indexuri (categorie, taguri GIN, embedding HNSW) | Utilizator | DA |
| 2026-04-04 | `CREATE TABLE user_context (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE, fact_type VARCHAR(50) NOT NULL DEFAULT 'general', content TEXT NOT NULL, source VARCHAR(50), importance INTEGER NOT NULL DEFAULT 5, active BOOLEAN NOT NULL DEFAULT true, created_at TIMESTAMP NOT NULL DEFAULT now(), updated_at TIMESTAMP NOT NULL DEFAULT now()); CREATE INDEX idx_user_context_user_id ON user_context(user_id);` — Sprint 3: Memorie persistentă AI (4.4) | Utilizator | DA |
