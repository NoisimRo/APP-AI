-- =============================================================================
-- DROP UNUSED TABLES + FIX DUPLICATE INDEX
-- Run in production on 2026-03-07
-- =============================================================================

-- 1. Drop tables without data (all 3 are empty: 8192 bytes = empty heap)
--    CASCADE drops associated foreign keys and indexes automatically
DROP TABLE IF EXISTS citate_verbatim CASCADE;
DROP TABLE IF EXISTS sectiuni_decizie CASCADE;
DROP TABLE IF EXISTS referinte_articole CASCADE;

-- 2. Fix duplicate index on decizii_cnsc.solutie_contestatie
--    Both ix_decizii_cnsc_solutie_contestatie and ix_decizii_solutie
--    are btree indexes on the same column — drop the redundant one
DROP INDEX IF EXISTS ix_decizii_cnsc_solutie_contestatie;
