"""Tests for the CNSC decision parser."""

import pytest
from datetime import datetime

from app.services.parser import CNSCDecisionParser, parse_decision_text


class TestCNSCDecisionParser:
    """Tests for CNSCDecisionParser."""

    @pytest.fixture
    def parser(self):
        """Create a parser instance."""
        return CNSCDecisionParser()

    def test_parse_text_extracts_case_number(self, parser, sample_decision_text):
        """Parser should extract case number from text."""
        result = parser.parse_text(sample_decision_text)
        assert result.case_number == "1234/C1/567"

    def test_parse_text_extracts_date(self, parser, sample_decision_text):
        """Parser should extract date from text."""
        result = parser.parse_text(sample_decision_text)
        assert result.date == datetime(2024, 1, 15)
        assert result.year == 2024

    def test_parse_text_extracts_admis_ruling(self, parser, sample_decision_text):
        """Parser should correctly identify ADMIS ruling."""
        result = parser.parse_text(sample_decision_text)
        assert result.ruling == "ADMIS"

    def test_parse_text_extracts_respins_ruling(self, parser, sample_respins_decision_text):
        """Parser should correctly identify RESPINS ruling."""
        result = parser.parse_text(sample_respins_decision_text)
        assert result.ruling == "RESPINS"

    def test_parse_text_extracts_cpv_codes(self, parser, sample_decision_text):
        """Parser should extract CPV codes from text."""
        result = parser.parse_text(sample_decision_text)
        assert "45233140-2" in result.cpv_codes

    def test_parse_text_extracts_criticism_codes(self, parser, sample_decision_text):
        """Parser should extract criticism codes from text."""
        result = parser.parse_text(sample_decision_text)
        assert "D1" in result.criticism_codes
        assert "D3" in result.criticism_codes

    def test_parse_text_extracts_contestator(self, parser, sample_decision_text):
        """Parser should extract contestator name."""
        result = parser.parse_text(sample_decision_text)
        assert "CONSTRUCTII MODERNE" in result.contestator

    def test_parse_text_extracts_authority(self, parser, sample_decision_text):
        """Parser should extract authority name."""
        result = parser.parse_text(sample_decision_text)
        assert "Primăria" in result.authority

    def test_parse_text_generates_external_id(self, parser, sample_decision_text):
        """Parser should generate a unique external ID."""
        result = parser.parse_text(sample_decision_text)
        assert result.external_id is not None
        assert len(result.external_id) > 0

    def test_parse_text_stores_full_text(self, parser, sample_decision_text):
        """Parser should store the full text."""
        result = parser.parse_text(sample_decision_text)
        assert result.full_text == sample_decision_text

    def test_parse_text_generates_title(self, parser, sample_decision_text):
        """Parser should generate a descriptive title."""
        result = parser.parse_text(sample_decision_text)
        assert result.title is not None
        assert "[ADMIS]" in result.title

    def test_parse_decision_text_convenience_function(self, sample_decision_text):
        """Convenience function should work the same as parser method."""
        result = parse_decision_text(sample_decision_text)
        assert result.ruling == "ADMIS"
        assert result.case_number == "1234/C1/567"


class TestParserEdgeCases:
    """Tests for parser edge cases."""

    @pytest.fixture
    def parser(self):
        return CNSCDecisionParser()

    def test_empty_text(self, parser):
        """Parser should handle empty text gracefully."""
        result = parser.parse_text("")
        assert result.external_id is not None
        assert result.ruling is None
        assert result.cpv_codes == []

    def test_text_without_metadata(self, parser):
        """Parser should handle text without standard metadata."""
        result = parser.parse_text("Random text without any CNSC format.")
        assert result.external_id is not None
        assert result.ruling is None

    def test_partial_ruling(self, parser):
        """Parser should detect partial (in parte) rulings."""
        text = "Consiliul admite în parte contestația..."
        result = parser.parse_text(text)
        assert result.ruling == "PARTIAL"

    def test_multiple_cpv_codes(self, parser):
        """Parser should extract all CPV codes."""
        text = "CPV: 45233140-2, 45233141-9, 72220000-3"
        result = parser.parse_text(text)
        assert len(result.cpv_codes) == 3

    def test_cpv_code_deduplication(self, parser):
        """Parser should deduplicate CPV codes."""
        text = "CPV: 45233140-2 mentioned here and 45233140-2 again"
        result = parser.parse_text(text)
        assert result.cpv_codes.count("45233140-2") == 1

    def test_date_numeric_format(self, parser):
        """Parser should extract dates in numeric format."""
        text = "Data: 15.01.2024"
        result = parser.parse_text(text)
        assert result.date == datetime(2024, 1, 15)

    def test_source_file_in_result(self, parser):
        """Parser should include source file in result."""
        result = parser.parse_text("Some text", source_file="test.txt")
        assert result.source_file == "test.txt"
