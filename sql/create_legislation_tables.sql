-- =============================================================================
-- LEGISLATION TABLES FOR ExpertAP
-- Run in production AFTER creating docs/expertap_db.md backup
-- =============================================================================

-- =============================================================================
-- TABLE 1: acte_normative
-- Master table for legislative acts (normalizare, nu string repetitiv)
-- =============================================================================
CREATE TABLE acte_normative (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tip_act VARCHAR(30) NOT NULL,              -- Lege, HG, OUG, OG, Directivă
    numar INTEGER NOT NULL,                     -- 98, 395, 99, 100, 101
    an INTEGER NOT NULL,                        -- 2016
    titlu TEXT,                                 -- titlul complet al actului normativ
    data_publicare DATE,                        -- data publicării în MO
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

-- Unique: un singur act per (tip, număr, an)
CREATE UNIQUE INDEX ix_acte_unique ON acte_normative (tip_act, numar, an);


-- =============================================================================
-- TABLE 2: legislatie_fragmente
-- Fragmente de legislație la GRANULARITATE MAXIMĂ
-- Un rând per cea mai mică unitate juridică independentă:
--   - Literă (dacă articolul/alineatul are litere)
--   - Alineat (dacă nu are litere)
--   - Articol (dacă nu are alineate)
--
-- Embedding pe fragment, dar articol_complet pentru context RAG
-- =============================================================================
CREATE TABLE legislatie_fragmente (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- FK către actul normativ (nu string!)
    act_id UUID NOT NULL REFERENCES acte_normative(id) ON DELETE CASCADE,

    -- Identificare articol
    numar_articol INTEGER NOT NULL,             -- 2, 178 (numeric, pt sortare)
    articol VARCHAR(30) NOT NULL,               -- "art. 2", "art. 178"

    -- Identificare alineat (NULL = articolul nu are alineate)
    alineat INTEGER,                            -- 1, 2, 3...
    alineat_text VARCHAR(20),                   -- "alin. (1)", "alin. (2)"

    -- Identificare literă (NULL = alineatul nu are litere, sau e la nivel de alineat/articol)
    litera VARCHAR(5),                          -- "a", "b", "c", "a1"...

    -- Textul ACESTUI fragment specific
    text_fragment TEXT NOT NULL,

    -- Textul COMPLET al articolului (toate alineatele + literele)
    -- Util pentru RAG: modelul vede contextul vecin
    articol_complet TEXT,

    -- Citare canonică (fără actul normativ, acela e via FK)
    -- Ex: "art. 2 alin. (2) lit. a)", "art. 1", "art. 178 alin. (3)"
    citare VARCHAR(150) NOT NULL,

    -- Context în structura legii
    capitol VARCHAR(500),                       -- "I - Dispoziții generale"
    sectiune VARCHAR(500),                      -- "1 - Obiect, scop și principii"

    -- Full-text search pentru căutare juridică
    keywords TSVECTOR,

    -- Vector embedding (2000 dims - limita pgvector HNSW)
    embedding vector(2000),

    -- Timestamps
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

-- =============================================================================
-- INDEXES
-- =============================================================================

-- Unicitate robustă: un singur rând per (act, articol, alineat, literă)
-- COALESCE rezolvă problema NULL != NULL în PostgreSQL
CREATE UNIQUE INDEX ix_frag_unique
    ON legislatie_fragmente (act_id, numar_articol, COALESCE(alineat, 0), COALESCE(litera, ''));

-- FK lookup
CREATE INDEX ix_frag_act ON legislatie_fragmente (act_id);

-- Lookup rapid pentru query juridic: "art. X alin. Y lit. Z din Legea 98"
CREATE INDEX ix_frag_lookup
    ON legislatie_fragmente (act_id, numar_articol, alineat, litera);

-- Căutare pe citare (pentru display/verificare)
CREATE INDEX ix_frag_citare ON legislatie_fragmente (act_id, citare);

-- Full-text search pe keywords (GIN)
CREATE INDEX ix_frag_keywords ON legislatie_fragmente USING gin (keywords);

-- HNSW vector index pentru semantic search (cosine similarity)
CREATE INDEX ix_frag_embedding_hnsw ON legislatie_fragmente
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);


-- =============================================================================
-- SEED DATA: Acte normative principale
-- =============================================================================
INSERT INTO acte_normative (tip_act, numar, an, titlu) VALUES
    ('Lege', 98, 2016, 'Legea nr. 98/2016 privind achizițiile publice'),
    ('HG', 395, 2016, 'HG nr. 395/2016 - Normele metodologice de aplicare a Legii 98/2016'),
    ('Lege', 99, 2016, 'Legea nr. 99/2016 privind achizițiile sectoriale'),
    ('Lege', 100, 2016, 'Legea nr. 100/2016 privind concesiunile de lucrări și servicii'),
    ('Lege', 101, 2016, 'Legea nr. 101/2016 privind remediile și căile de atac'),
    ('HG', 394, 2016, 'HG nr. 394/2016 - Normele metodologice de aplicare a Legii 99/2016')
ON CONFLICT DO NOTHING;
