"""CNSC Decision Parser.

Parses raw CNSC decision text files and extracts structured metadata.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ParsedDecision:
    """Structured representation of a parsed CNSC decision."""

    # Identifiers
    external_id: str
    case_number: Optional[str] = None
    bulletin: Optional[str] = None

    # Dates
    date: Optional[datetime] = None
    year: Optional[int] = None

    # Classification
    ruling: Optional[str] = None  # ADMIS, RESPINS, PARTIAL
    cpv_codes: list[str] = field(default_factory=list)
    criticism_codes: list[str] = field(default_factory=list)

    # Parties
    contestator: Optional[str] = None
    authority: Optional[str] = None
    intervenients: list[str] = field(default_factory=list)

    # Content
    title: Optional[str] = None
    full_text: str = ""

    # Source
    source_file: Optional[str] = None


class CNSCDecisionParser:
    """Parser for CNSC decision text files.

    Extracts structured metadata from raw text files using
    regular expressions and heuristics tailored to the CNSC
    decision format.
    """

    # Patterns for metadata extraction
    PATTERNS = {
        # Case number: e.g., "Nr. 1234/C1/567"
        "case_number": re.compile(
            r"(?:Nr\.|Număr|Dosar)[\s.:]*(\d+[/\-]\w+[/\-]?\d*)",
            re.IGNORECASE,
        ),
        # Date patterns
        "date": re.compile(
            r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})",
        ),
        "date_text": re.compile(
            r"(\d{1,2})\s+(ianuarie|februarie|martie|aprilie|mai|iunie|"
            r"iulie|august|septembrie|octombrie|noiembrie|decembrie)\s+(\d{4})",
            re.IGNORECASE,
        ),
        # CPV codes: e.g., "45233140-2"
        "cpv": re.compile(r"\b(\d{8}(?:-\d)?)\b"),
        # Ruling
        "admis": re.compile(
            r"(?:admite|admis[ăa]?)\s+(?:în\s+parte\s+)?contestația",
            re.IGNORECASE,
        ),
        "respins": re.compile(
            r"(?:respinge|respins[ăa]?)\s+contestația",
            re.IGNORECASE,
        ),
        # Criticism codes (D1-D7 for documentation, R1-R7 for results)
        "criticism_d": re.compile(r"\b(D[1-7])\b", re.IGNORECASE),
        "criticism_r": re.compile(r"\b(R[1-7])\b", re.IGNORECASE),
        # Parties
        "contestator": re.compile(
            r"(?:Contestator|Petent|Reclamant)[:\s]+([^\n,;]+)",
            re.IGNORECASE,
        ),
        "authority": re.compile(
            r"(?:Autoritate\s+contractantă|Intimat)[:\s]+([^\n,;]+)",
            re.IGNORECASE,
        ),
        # Legal articles
        "articles": re.compile(
            r"art(?:icol|\.)\s*(\d+)(?:\s*alin(?:eat|\.)\s*\(?\d+\)?)?",
            re.IGNORECASE,
        ),
    }

    MONTH_MAP = {
        "ianuarie": 1,
        "februarie": 2,
        "martie": 3,
        "aprilie": 4,
        "mai": 5,
        "iunie": 6,
        "iulie": 7,
        "august": 8,
        "septembrie": 9,
        "octombrie": 10,
        "noiembrie": 11,
        "decembrie": 12,
    }

    def parse_file(self, file_path: Path | str) -> ParsedDecision:
        """Parse a CNSC decision from a file.

        Args:
            file_path: Path to the .txt file containing the decision.

        Returns:
            ParsedDecision with extracted metadata.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            ValueError: If the file cannot be parsed.
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Read file content
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Try latin-1 as fallback
            content = file_path.read_text(encoding="latin-1")

        return self.parse_text(content, source_file=str(file_path))

    def parse_text(
        self,
        text: str,
        source_file: Optional[str] = None,
    ) -> ParsedDecision:
        """Parse a CNSC decision from raw text.

        Args:
            text: The raw text content of the decision.
            source_file: Optional source file path for reference.

        Returns:
            ParsedDecision with extracted metadata.
        """
        # Generate external ID from source file or hash
        external_id = self._generate_id(source_file, text)

        decision = ParsedDecision(
            external_id=external_id,
            full_text=text,
            source_file=source_file,
        )

        # Extract metadata
        decision.case_number = self._extract_case_number(text)
        decision.date = self._extract_date(text)
        decision.year = decision.date.year if decision.date else self._extract_year_from_filename(source_file)
        decision.ruling = self._extract_ruling(text)
        decision.cpv_codes = self._extract_cpv_codes(text)
        decision.criticism_codes = self._extract_criticism_codes(text)
        decision.contestator = self._extract_party(text, "contestator")
        decision.authority = self._extract_party(text, "authority")
        decision.title = self._generate_title(decision)
        decision.bulletin = self._extract_bulletin_from_filename(source_file)

        logger.info(
            "decision_parsed",
            external_id=external_id,
            ruling=decision.ruling,
            cpv_count=len(decision.cpv_codes),
        )

        return decision

    def _generate_id(self, source_file: Optional[str], text: str) -> str:
        """Generate a unique ID for the decision."""
        if source_file:
            # Use filename as base
            name = Path(source_file).stem
            # Clean up the name
            name = re.sub(r"[^\w\-]", "_", name)
            return name[:100]

        # Fallback: use hash of content
        import hashlib

        return f"dec_{hashlib.sha256(text.encode()).hexdigest()[:16]}"

    def _extract_case_number(self, text: str) -> Optional[str]:
        """Extract case number from text."""
        match = self.PATTERNS["case_number"].search(text)
        return match.group(1) if match else None

    def _extract_date(self, text: str) -> Optional[datetime]:
        """Extract date from text."""
        # Try text format first (e.g., "15 ianuarie 2024")
        match = self.PATTERNS["date_text"].search(text)
        if match:
            day = int(match.group(1))
            month = self.MONTH_MAP.get(match.group(2).lower(), 1)
            year = int(match.group(3))
            try:
                return datetime(year, month, day)
            except ValueError:
                pass

        # Try numeric format (e.g., "15.01.2024")
        match = self.PATTERNS["date"].search(text)
        if match:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
            try:
                return datetime(year, month, day)
            except ValueError:
                pass

        return None

    def _extract_year_from_filename(self, filename: Optional[str]) -> Optional[int]:
        """Extract year from filename like 'BO2024_xxx.txt'."""
        if not filename:
            return None

        match = re.search(r"BO(\d{4})", filename, re.IGNORECASE)
        if match:
            return int(match.group(1))

        return None

    def _extract_bulletin_from_filename(self, filename: Optional[str]) -> Optional[str]:
        """Extract bulletin from filename."""
        if not filename:
            return None

        match = re.search(r"(BO\d{4})", filename, re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_ruling(self, text: str) -> Optional[str]:
        """Extract ruling (ADMIS/RESPINS) from text."""
        # Check for ADMIS patterns
        if self.PATTERNS["admis"].search(text):
            # Check if partial
            if re.search(r"în\s+parte", text, re.IGNORECASE):
                return "PARTIAL"
            return "ADMIS"

        # Check for RESPINS patterns
        if self.PATTERNS["respins"].search(text):
            return "RESPINS"

        # Try to infer from filename
        return None

    def _extract_cpv_codes(self, text: str) -> list[str]:
        """Extract CPV codes from text."""
        matches = self.PATTERNS["cpv"].findall(text)
        # Deduplicate while preserving order
        seen = set()
        return [x for x in matches if not (x in seen or seen.add(x))]

    def _extract_criticism_codes(self, text: str) -> list[str]:
        """Extract criticism codes (D1-D7, R1-R7) from text."""
        d_codes = self.PATTERNS["criticism_d"].findall(text)
        r_codes = self.PATTERNS["criticism_r"].findall(text)

        # Normalize to uppercase and deduplicate
        codes = [c.upper() for c in d_codes + r_codes]
        seen = set()
        return [x for x in codes if not (x in seen or seen.add(x))]

    def _extract_party(self, text: str, party_type: str) -> Optional[str]:
        """Extract party name from text."""
        pattern = self.PATTERNS.get(party_type)
        if not pattern:
            return None

        match = pattern.search(text)
        if match:
            name = match.group(1).strip()
            # Clean up the name
            name = re.sub(r"\s+", " ", name)
            return name[:500]  # Limit length

        return None

    def _generate_title(self, decision: ParsedDecision) -> str:
        """Generate a title for the decision."""
        parts = []

        if decision.bulletin:
            parts.append(decision.bulletin)

        if decision.case_number:
            parts.append(f"Nr. {decision.case_number}")

        if decision.ruling:
            parts.append(f"[{decision.ruling}]")

        if decision.contestator and decision.authority:
            parts.append(f"{decision.contestator} vs. {decision.authority}")

        return " - ".join(parts) if parts else decision.external_id


# Singleton instance for convenience
parser = CNSCDecisionParser()


def parse_decision_file(file_path: Path | str) -> ParsedDecision:
    """Parse a CNSC decision file.

    Convenience function that uses the singleton parser instance.
    """
    return parser.parse_file(file_path)


def parse_decision_text(text: str, source_file: Optional[str] = None) -> ParsedDecision:
    """Parse CNSC decision text.

    Convenience function that uses the singleton parser instance.
    """
    return parser.parse_text(text, source_file)
