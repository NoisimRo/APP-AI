"""Tests for the CNSC decision parser.

Tests the parser following the established conventions:
- Filename: BO{AN}_{NR_BO}_{COD_CRITICI}_CPV_{COD_CPV}_{SOLUTIE}.txt
- Criticism codes: D1-D7 (documentatie), R1-R7 (rezultat)
- Solution codes: A (Admis), R (Respins), X (Unknown)
"""

import pytest
from datetime import datetime

from app.services.parser import (
    CNSCDecisionParser,
    CriticismCodeType,
    SolutionCode,
    SolutionType,
    SectionType,
    CRITICISM_CODES_LEGEND,
    get_criticism_type,
    get_criticism_description,
    parse_decision_text,
    get_all_criticism_codes,
)


class TestFilenameParser:
    """Tests for filename parsing."""

    @pytest.fixture
    def parser(self):
        """Create a parser instance."""
        return CNSCDecisionParser()

    def test_parse_filename_with_cpv(self, parser, sample_filename_with_cpv):
        """Parser should correctly parse filename with CPV code."""
        meta = parser._parse_filename(sample_filename_with_cpv)

        assert meta.an_bo == 2025
        assert meta.numar_bo == 3855
        assert meta.coduri_critici == ["R2"]
        assert meta.cod_cpv == "55520000-1"
        assert meta.solutie == SolutionCode.ADMIS
        assert meta.tip_contestatie == CriticismCodeType.RESULT

    def test_parse_filename_multiple_critici(self, parser, sample_filename_multiple_critici):
        """Parser should correctly parse filename with multiple criticism codes."""
        meta = parser._parse_filename(sample_filename_multiple_critici)

        assert meta.an_bo == 2025
        assert meta.numar_bo == 1234
        assert meta.coduri_critici == ["D1", "D4"]
        assert meta.cod_cpv == "45233140-2"
        assert meta.solutie == SolutionCode.RESPINS
        assert meta.tip_contestatie == CriticismCodeType.DOCUMENTATION

    def test_parse_filename_no_cpv(self, parser, sample_filename_no_cpv):
        """Parser should correctly parse filename without CPV code."""
        meta = parser._parse_filename(sample_filename_no_cpv)

        assert meta.an_bo == 2024
        assert meta.numar_bo == 5678
        assert meta.coduri_critici == ["R3", "R4"]
        assert meta.cod_cpv is None
        assert meta.solutie == SolutionCode.UNKNOWN
        assert meta.tip_contestatie == CriticismCodeType.RESULT

    def test_parse_invalid_filename(self, parser):
        """Parser should raise ValueError for invalid filename."""
        with pytest.raises(ValueError):
            parser._parse_filename("invalid_filename.txt")

    def test_parse_filename_without_valid_critici(self, parser):
        """Parser should raise ValueError if no valid criticism codes found."""
        with pytest.raises(ValueError):
            parser._parse_filename("BO2025_1234_XXX_A.txt")


class TestCriticismCodes:
    """Tests for criticism codes functionality."""

    def test_get_criticism_type_documentation(self):
        """D* codes should return DOCUMENTATION type."""
        for code in ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "DAL"]:
            assert get_criticism_type(code) == CriticismCodeType.DOCUMENTATION

    def test_get_criticism_type_result(self):
        """R* codes should return RESULT type."""
        for code in ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "RAL"]:
            assert get_criticism_type(code) == CriticismCodeType.RESULT

    def test_get_criticism_type_invalid(self):
        """Invalid codes should raise ValueError."""
        with pytest.raises(ValueError):
            get_criticism_type("X1")

    def test_get_criticism_description(self):
        """Should return correct description for known codes."""
        assert "experiență similară" in get_criticism_description("D1")
        assert "Respingerea ofertei" in get_criticism_description("R2")

    def test_get_criticism_description_unknown(self):
        """Should return 'Cod necunoscut' for unknown codes."""
        assert get_criticism_description("Z9") == "Cod necunoscut"

    def test_criticism_codes_legend_complete(self):
        """Legend should contain all expected codes."""
        expected_codes = [
            "D1", "D2", "D3", "D4", "D5", "D6", "D7", "DAL",
            "R1", "R2", "R3", "R4", "R5", "R6", "R7", "RAL"
        ]
        for code in expected_codes:
            assert code in CRITICISM_CODES_LEGEND

    def test_get_all_criticism_codes(self):
        """Should return a copy of all codes."""
        codes = get_all_criticism_codes()
        assert len(codes) == 16
        assert "D1" in codes
        assert "R7" in codes


class TestTextParser:
    """Tests for text content parsing."""

    @pytest.fixture
    def parser(self):
        return CNSCDecisionParser()

    def test_extract_admis_solution(self, parser, sample_decision_text_admis):
        """Parser should extract ADMIS solution from dispositive."""
        result = parser.parse_text(
            sample_decision_text_admis,
            source_file="BO2025_3855_R2_CPV_55520000-1_A.txt"
        )
        assert result.solutie_contestatie == SolutionType.ADMIS

    def test_extract_respins_solution(self, parser, sample_decision_text_respins):
        """Parser should extract RESPINS solution from dispositive."""
        result = parser.parse_text(
            sample_decision_text_respins,
            source_file="BO2025_1234_D1_D4_CPV_45233140-2_R.txt"
        )
        assert result.solutie_contestatie == SolutionType.RESPINS
        assert result.motiv_respingere == "nefondată"

    def test_extract_admis_partial_solution(self, parser, sample_decision_text_admis_partial):
        """Parser should extract ADMIS_PARTIAL solution from dispositive."""
        result = parser.parse_text(
            sample_decision_text_admis_partial,
            source_file="BO2025_5678_R3_R4_X.txt"
        )
        assert result.solutie_contestatie == SolutionType.ADMIS_PARTIAL

    def test_extract_decision_header(self, parser, sample_decision_text_admis):
        """Parser should extract decision number and panel."""
        result = parser.parse_text(
            sample_decision_text_admis,
            source_file="BO2025_3855_R2_CPV_55520000-1_A.txt"
        )
        assert result.complet == "C8"
        assert result.numar_decizie == 4446

    def test_extract_date(self, parser, sample_decision_text_admis):
        """Parser should extract decision date."""
        result = parser.parse_text(
            sample_decision_text_admis,
            source_file="BO2025_3855_R2_CPV_55520000-1_A.txt"
        )
        assert result.data_decizie == datetime(2025, 12, 10)

    def test_extract_cpv_from_text(self, parser, sample_decision_text_admis):
        """Parser should extract CPV code from text."""
        result = parser.parse_text(
            sample_decision_text_admis,
            source_file="BO2025_3855_R2_CPV_55520000-1_A.txt"
        )
        assert result.cod_cpv == "55520000-1"

    def test_extract_criticism_codes_from_text(self, parser, sample_decision_text_respins):
        """Parser should extract criticism codes from text."""
        result = parser.parse_text(
            sample_decision_text_respins,
            source_file="BO2025_1234_D1_D4_CPV_45233140-2_R.txt"
        )
        assert "D1" in result.coduri_critici
        assert "D4" in result.coduri_critici


class TestContestType:
    """Tests for contest type determination."""

    @pytest.fixture
    def parser(self):
        return CNSCDecisionParser()

    def test_documentation_contest_type(self, parser, sample_decision_text_respins):
        """D* codes should result in DOCUMENTATION contest type."""
        result = parser.parse_text(
            sample_decision_text_respins,
            source_file="BO2025_1234_D1_D4_CPV_45233140-2_R.txt"
        )
        assert result.tip_contestatie == CriticismCodeType.DOCUMENTATION

    def test_result_contest_type(self, parser, sample_decision_text_admis):
        """R* codes should result in RESULT contest type."""
        result = parser.parse_text(
            sample_decision_text_admis,
            source_file="BO2025_3855_R2_CPV_55520000-1_A.txt"
        )
        assert result.tip_contestatie == CriticismCodeType.RESULT


class TestSectionParsing:
    """Tests for section parsing."""

    @pytest.fixture
    def parser(self):
        return CNSCDecisionParser()

    def test_parse_sections(self, parser, sample_decision_text_with_sections):
        """Parser should identify major sections."""
        result = parser.parse_text(
            sample_decision_text_with_sections,
            source_file="BO2025_9999_D1_X.txt"
        )
        sections = parser.parse_sections(result)

        # Should find at least some sections
        section_types = [s.tip_sectiune for s in sections]
        assert len(sections) > 0

        # Should find contestant requests, AC point of view, intervention, and dispositive
        # (depending on markers found in text)


class TestExternalId:
    """Tests for external ID generation."""

    @pytest.fixture
    def parser(self):
        return CNSCDecisionParser()

    def test_external_id_format(self, parser, sample_decision_text_admis):
        """External ID should follow BO{year}_{number} format."""
        result = parser.parse_text(
            sample_decision_text_admis,
            source_file="BO2025_3855_R2_CPV_55520000-1_A.txt"
        )
        assert result.external_id == "BO2025_3855"


class TestTitle:
    """Tests for title generation."""

    @pytest.fixture
    def parser(self):
        return CNSCDecisionParser()

    def test_title_includes_bo_and_number(self, parser, sample_decision_text_admis):
        """Title should include BO year and number."""
        result = parser.parse_text(
            sample_decision_text_admis,
            source_file="BO2025_3855_R2_CPV_55520000-1_A.txt"
        )
        assert "BO2025" in result.title
        assert "3855" in result.title

    def test_title_includes_solution(self, parser, sample_decision_text_admis):
        """Title should include solution."""
        result = parser.parse_text(
            sample_decision_text_admis,
            source_file="BO2025_3855_R2_CPV_55520000-1_A.txt"
        )
        assert "ADMIS" in result.title

    def test_title_includes_critici(self, parser, sample_decision_text_respins):
        """Title should include criticism codes."""
        result = parser.parse_text(
            sample_decision_text_respins,
            source_file="BO2025_1234_D1_D4_CPV_45233140-2_R.txt"
        )
        assert "D1" in result.title or "D4" in result.title


class TestValidation:
    """Tests for validation and reconciliation."""

    @pytest.fixture
    def parser(self):
        return CNSCDecisionParser()

    def test_warning_for_missing_cpv(self, parser):
        """Parser should add warning when CPV is not found."""
        text = "Simple text without CPV code"
        result = parser.parse_text(text, source_file="BO2025_1234_D1_X.txt")
        assert any("CPV" in w for w in result.parse_warnings)

    def test_warning_for_solution_mismatch(self, parser, sample_decision_text_respins):
        """Parser should add warning when filename and text solutions don't match."""
        # Filename says A (Admis) but text says Respins
        result = parser.parse_text(
            sample_decision_text_respins,
            source_file="BO2025_1234_D1_D4_CPV_45233140-2_A.txt"  # Wrong solution in filename
        )
        assert any("mismatch" in w.lower() for w in result.parse_warnings)


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_parse_decision_text(self, sample_decision_text_admis):
        """Convenience function should work like parser method."""
        result = parse_decision_text(
            sample_decision_text_admis,
            source_file="BO2025_3855_R2_CPV_55520000-1_A.txt"
        )
        assert result.solutie_contestatie == SolutionType.ADMIS
        assert result.numar_bo == 3855
