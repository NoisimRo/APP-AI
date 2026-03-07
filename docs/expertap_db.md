# \dt+

                                            List of relations
 Schema |        Name         | Type  |  Owner   | Persistence | Access method |    Size    | Description 
--------+---------------------+-------+----------+-------------+---------------+------------+-------------
 public | argumentare_critica | table | expertap | permanent   | heap          | 4320 kB    | 
 public | citate_verbatim     | table | expertap | permanent   | heap          | 8192 bytes | 
 public | decizii_cnsc        | table | expertap | permanent   | heap          | 78 MB      | 
 public | nomenclator_cpv     | table | expertap | permanent   | heap          | 8192 bytes | 
 public | referinte_articole  | table | expertap | permanent   | heap          | 8192 bytes | 
 public | sectiuni_decizie    | table | expertap | permanent   | heap          | 8192 bytes | 
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
    "ix_decizii_cnsc_solutie_contestatie" btree (solutie_contestatie)
    "ix_decizii_complet" btree (complet)
    "ix_decizii_cpv" btree (cod_cpv)
    "ix_decizii_critici" gin (coduri_critici)
    "ix_decizii_data" btree (data_decizie)
    "ix_decizii_fulltext" gin (text_integral gin_trgm_ops)
    "ix_decizii_solutie" btree (solutie_contestatie)
    "ix_decizii_tip" btree (tip_contestatie)
Referenced by:
    TABLE "argumentare_critica" CONSTRAINT "argumentare_critica_decizie_id_fkey" FOREIGN KEY (decizie_id) REFERENCES decizii_cnsc(id) ON DELETE CASCADE
    TABLE "citate_verbatim" CONSTRAINT "citate_verbatim_decizie_id_fkey" FOREIGN KEY (decizie_id) REFERENCES decizii_cnsc(id) ON DELETE CASCADE
    TABLE "referinte_articole" CONSTRAINT "referinte_articole_decizie_id_fkey" FOREIGN KEY (decizie_id) REFERENCES decizii_cnsc(id) ON DELETE CASCADE
    TABLE "sectiuni_decizie" CONSTRAINT "sectiuni_decizie_decizie_id_fkey" FOREIGN KEY (decizie_id) REFERENCES decizii_cnsc(id) ON DELETE CASCADE


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
Referenced by:
    TABLE "citate_verbatim" CONSTRAINT "citate_verbatim_argumentare_id_fkey" FOREIGN KEY (argumentare_id) REFERENCES argumentare_critica(id) ON DELETE SET NULL
    TABLE "referinte_articole" CONSTRAINT "referinte_articole_argumentare_id_fkey" FOREIGN KEY (argumentare_id) REFERENCES argumentare_critica(id) ON DELETE SET NULL


# \d sectiuni_decizie
                          Table "public.sectiuni_decizie"
       Column       |            Type             | Collation | Nullable | Default 
--------------------+-----------------------------+-----------+----------+---------
 id                 | uuid                        |           | not null | 
 decizie_id         | uuid                        |           | not null | 
 tip_sectiune       | character varying(50)       |           | not null | 
 ordine             | integer                     |           | not null | 
 numar_intervenient | integer                     |           |          | 
 text_sectiune      | text                        |           | not null | 
 embedding_id       | uuid                        |           |          | 
 embedding          | vector(2000)                |           |          | 
 created_at         | timestamp without time zone |           | not null | now()
Indexes:
    "sectiuni_decizie_pkey" PRIMARY KEY, btree (id)
    "ix_sectiuni_decizie" btree (decizie_id)
    "ix_sectiuni_embedding_hnsw" hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')
    "ix_sectiuni_tip" btree (tip_sectiune)
Foreign-key constraints:
    "sectiuni_decizie_decizie_id_fkey" FOREIGN KEY (decizie_id) REFERENCES decizii_cnsc(id) ON DELETE CASCADE
Referenced by:
    TABLE "citate_verbatim" CONSTRAINT "citate_verbatim_sectiune_id_fkey" FOREIGN KEY (sectiune_id) REFERENCES sectiuni_decizie(id) ON DELETE SET NULL


# \d citate_verbatim
                        Table "public.citate_verbatim"
     Column     |            Type             | Collation | Nullable | Default 
----------------+-----------------------------+-----------+----------+---------
 id             | uuid                        |           | not null | 
 decizie_id     | uuid                        |           | not null | 
 sectiune_id    | uuid                        |           |          | 
 argumentare_id | uuid                        |           |          | 
 text_verbatim  | text                        |           | not null | 
 pozitie_start  | integer                     |           |          | 
 pozitie_end    | integer                     |           |          | 
 tip_citat      | character varying(30)       |           |          | 
 embedding_id   | uuid                        |           |          | 
 embedding      | vector(2000)                |           |          | 
 created_at     | timestamp without time zone |           | not null | now()
Indexes:
    "citate_verbatim_pkey" PRIMARY KEY, btree (id)
    "ix_citate_decizie" btree (decizie_id)
    "ix_citate_embedding_hnsw" hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')
    "ix_citate_tip" btree (tip_citat)
Foreign-key constraints:
    "citate_verbatim_argumentare_id_fkey" FOREIGN KEY (argumentare_id) REFERENCES argumentare_critica(id) ON DELETE SET NULL
    "citate_verbatim_decizie_id_fkey" FOREIGN KEY (decizie_id) REFERENCES decizii_cnsc(id) ON DELETE CASCADE
    "citate_verbatim_sectiune_id_fkey" FOREIGN KEY (sectiune_id) REFERENCES sectiuni_decizie(id) ON DELETE SET NULL


# \d referinte_articole
                         Table "public.referinte_articole"
       Column        |            Type             | Collation | Nullable | Default 
---------------------+-----------------------------+-----------+----------+---------
 id                  | uuid                        |           | not null | 
 decizie_id          | uuid                        |           | not null | 
 argumentare_id      | uuid                        |           |          | 
 act_normativ        | character varying(50)       |           | not null | 
 articol             | character varying(30)       |           | not null | 
 tip_referinta       | character varying(20)       |           |          | 
 text_citat          | text                        |           |          | 
 invocat_de          | character varying(20)       |           |          | 
 argument_castigator | boolean                     |           |          | 
 created_at          | timestamp without time zone |           | not null | now()
Indexes:
    "referinte_articole_pkey" PRIMARY KEY, btree (id)
    "ix_ref_articol" btree (act_normativ, articol)
    "ix_ref_castigator" btree (argument_castigator)
    "ix_ref_decizie" btree (decizie_id)
    "ix_ref_invocat" btree (invocat_de)
Foreign-key constraints:
    "referinte_articole_argumentare_id_fkey" FOREIGN KEY (argumentare_id) REFERENCES argumentare_critica(id) ON DELETE SET NULL
    "referinte_articole_decizie_id_fkey" FOREIGN KEY (decizie_id) REFERENCES decizii_cnsc(id) ON DELETE CASCADE


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


# \dx
                                    List of installed extensions
  Name   | Version |   Schema   |                            Description                            
---------+---------+------------+-------------------------------------------------------------------
 pg_trgm | 1.6     | public     | text similarity measurement and index searching based on trigrams
 plpgsql | 1.0     | pg_catalog | PL/pgSQL procedural language
 vector  | 0.8.1   | public     | vector data type and ivfflat and hnsw access methods
(3 rows)

