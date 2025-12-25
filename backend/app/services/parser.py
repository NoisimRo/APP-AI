"""CNSC Decision Parser.

Parses raw CNSC decision text files and extracts structured metadata
following the established naming conventions and structure.

Filename Convention:
    BO{AN}_{NR_BO}_{COD_CRITICI}_CPV_{COD_CPV}_{SOLUTIE}.txt

Example:
    BO2025_3855_R2_CPV_55520000-1_A.txt
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# CRITICISM CODES LEGEND
# =============================================================================

class CriticismCodeType(str, Enum):
    """Type of criticism - determines contest type."""
    DOCUMENTATION = "documentatie"  # D1-D7, DAL
    RESULT = "rezultat"  # R1-R7, RAL


CRITICISM_CODES_LEGEND = {
    # Critici la Documentația de Atribuire (D)
    "D1": "Cerințe restrictive: experiență similară, criterii calificare, specificații tehnice",
    "D2": "Criterii atribuire/factori evaluare fără algoritm calcul sau cu algoritm netransparent/subiectiv",
    "D3": "Denumiri tehnologii/produse/marci/producatori fara sintagma 'sau echivalent'",
    "D4": "Lipsa răspuns clar/complet la solicitările de clarificări privind documentația",
    "D5": "Forma de constituire a garanției de participare",
    "D6": "Clauze contractuale inechitabile sau excesive",
    "D7": "Nedivizarea achiziției pe loturi (produse/lucrări similare)",
    "DAL": "Altele (documentație) - necesită extragere din text",

    # Critici la Rezultatul Procedurii (R)
    "R1": "Contestații contra PV ședință deschidere (garanție participare, mod desfășurare)",
    "R2": "Respingerea ofertei contestatorului ca neconformă sau inacceptabilă",
    "R3": "Preț neobișnuit de scăzut al ofertelor altor participanți",
    "R4": "Documente calificare ale altor ofertanți / mod de punctare-evaluare",
    "R5": "Lipsa precizării motivelor de respingere în comunicare",
    "R6": "Lipsa solicitare clarificări propunere tehnică/preț sau apreciere incorectă răspunsuri",
    "R7": "Anularea fără temei legal a procedurii de către AC",
    "RAL": "Altele (rezultat) - necesită extragere din text",
}


def get_criticism_type(code: str) -> CriticismCodeType:
    """Determine contest type from criticism code."""
    code_upper = code.upper()
    if code_upper.startswith("D"):
        return CriticismCodeType.DOCUMENTATION
    elif code_upper.startswith("R"):
        return CriticismCodeType.RESULT
    else:
        raise ValueError(f"Unknown criticism code prefix: {code}")


def get_criticism_description(code: str) -> str:
    """Get description for a criticism code."""
    return CRITICISM_CODES_LEGEND.get(code.upper(), "Cod necunoscut")


# =============================================================================
# SOLUTION CODES
# =============================================================================

class SolutionCode(str, Enum):
    """Solution codes from filename."""
    ADMIS = "A"
    RESPINS = "R"
    UNKNOWN = "X"


class SolutionType(str, Enum):
    """Detailed solution types extracted from text."""
    ADMIS = "ADMIS"
    ADMIS_PARTIAL = "ADMIS_PARTIAL"
    RESPINS = "RESPINS"
    UNKNOWN = "UNKNOWN"


REJECTION_REASONS = [
    "nefondată",
    "tardivă",
    "lipsită de interes",
    "inadmisibilă",
    "rămasă fără obiect",
]


# =============================================================================
# SECTION TYPES
# =============================================================================

class SectionType(str, Enum):
    """Logical sections of a CNSC decision."""
    HEADER = "antet"
    CONTESTANT_REQUESTS = "solicitari_contestator"
    PROCEDURE_HISTORY = "istoric"
    AC_POINT_OF_VIEW = "punct_vedere_ac"
    INTERVENTION = "interventie"
    CNSC_ANALYSIS = "analiza_cnsc"
    DISPOSITIVE = "dispozitiv"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class FilenameMetadata:
    """Metadata extracted from filename."""
    an_bo: int  # Year (e.g., 2025)
    numar_bo: int  # Bulletin number (e.g., 3855)
    coduri_critici: list[str]  # Criticism codes (e.g., ['R2'] or ['D1', 'D4'])
    cod_cpv: Optional[str]  # CPV code if present (e.g., '55520000-1')
    solutie: SolutionCode  # Solution (A, R, X)
    tip_contestatie: CriticismCodeType  # Deduced from criticism codes
    filename: str  # Original filename


@dataclass
class TextMetadata:
    """Metadata extracted from decision text."""
    numar_decizie: Optional[int] = None  # Decision number (e.g., 4446)
    complet: Optional[str] = None  # Panel (e.g., C8)
    data_decizie: Optional[datetime] = None  # Decision date

    # Detailed solution from "CONSILIUL DECIDE:"
    solutie_contestatie: Optional[SolutionType] = None
    motiv_respingere: Optional[str] = None

    # CPV if not in filename
    cod_cpv_text: Optional[str] = None
    cpv_source: Literal["filename", "text_explicit", "dedus"] = "filename"


@dataclass
class DecisionSection:
    """A section of the decision."""
    tip_sectiune: SectionType
    ordine: int
    text_sectiune: str
    numar_intervenient: Optional[int] = None  # For INTERVENTION sections


@dataclass
class ParsedDecision:
    """Complete structured representation of a parsed CNSC decision."""

    # Identifiers (from filename)
    filename: str
    numar_bo: int
    an_bo: int

    # Metadata from text
    numar_decizie: Optional[int] = None
    complet: Optional[str] = None
    data_decizie: Optional[datetime] = None

    # Contest type (deduced from criticism codes)
    tip_contestatie: CriticismCodeType = CriticismCodeType.DOCUMENTATION

    # Classification
    coduri_critici: list[str] = field(default_factory=list)

    # CPV
    cod_cpv: Optional[str] = None
    cpv_source: Literal["filename", "text_explicit", "dedus"] = "filename"

    # Solution from filename
    solutie_filename: SolutionCode = SolutionCode.UNKNOWN

    # Detailed solution from text
    solutie_contestatie: Optional[SolutionType] = None
    motiv_respingere: Optional[str] = None

    # Parties (extracted from text)
    contestator: Optional[str] = None
    autoritate_contractanta: Optional[str] = None
    intervenienti: list[str] = field(default_factory=list)

    # Full content
    text_integral: str = ""

    # Sections (populated by section parser)
    sectiuni: list[DecisionSection] = field(default_factory=list)

    # Metadata
    source_file: Optional[str] = None
    parse_warnings: list[str] = field(default_factory=list)

    @property
    def external_id(self) -> str:
        """Generate unique external ID."""
        return f"BO{self.an_bo}_{self.numar_bo}"

    @property
    def title(self) -> str:
        """Generate descriptive title."""
        parts = [f"BO{self.an_bo}", f"Nr. {self.numar_bo}"]

        if self.coduri_critici:
            parts.append(f"[{'+'.join(self.coduri_critici)}]")

        if self.solutie_contestatie:
            parts.append(f"[{self.solutie_contestatie.value}]")
        elif self.solutie_filename != SolutionCode.UNKNOWN:
            sol_map = {SolutionCode.ADMIS: "ADMIS", SolutionCode.RESPINS: "RESPINS"}
            parts.append(f"[{sol_map.get(self.solutie_filename, 'X')}]")

        return " - ".join(parts)


# =============================================================================
# PARSER IMPLEMENTATION
# =============================================================================

class CNSCDecisionParser:
    """Parser for CNSC decision text files.

    Extracts structured metadata from raw text files following
    the established naming conventions and logical structure.
    """

    # Filename pattern: BO{AN}_{NR_BO}_{COD_CRITICI}_CPV_{COD_CPV}_{SOLUTIE}.txt
    FILENAME_PATTERN = re.compile(
        r"^BO(\d{4})_(\d+)_([A-Za-z0-9_]+?)(?:_CPV_(\d{8}(?:-\d)?))?_([ARX])\.txt$",
        re.IGNORECASE
    )

    # Alternative pattern without CPV
    FILENAME_PATTERN_NO_CPV = re.compile(
        r"^BO(\d{4})_(\d+)_([A-Za-z0-9_]+)_([ARX])\.txt$",
        re.IGNORECASE
    )

    # Patterns for text extraction
    PATTERNS = {
        # Decision number: "Nr. 3754/C8/4446" -> numar_bo=3754, complet=C8, numar_decizie=4446
        "decision_header": re.compile(
            r"Nr\.?\s*(\d+)/([A-Z]\d+)/(\d+)",
            re.IGNORECASE
        ),

        # Date patterns
        "date_text": re.compile(
            r"(?:Data|din)\s*[:\s]*(\d{1,2})\s+"
            r"(ianuarie|februarie|martie|aprilie|mai|iunie|"
            r"iulie|august|septembrie|octombrie|noiembrie|decembrie)\s+"
            r"(\d{4})",
            re.IGNORECASE
        ),
        "date_numeric": re.compile(
            r"(?:Data|din)\s*[:\s]*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})"
        ),

        # CPV codes: "45233140-2"
        "cpv": re.compile(r"\b(\d{8}-\d)\b"),

        # Solution patterns in dispositive
        "admis_integral": re.compile(
            r"Admite(?:,?\s+în\s+totalitate)?\s+contestația",
            re.IGNORECASE
        ),
        "admis_partial": re.compile(
            r"Admite,?\s+în\s+parte,?\s+contestația",
            re.IGNORECASE
        ),
        "respins": re.compile(
            r"Respinge,?\s+(?:ca\s+)?(\w+),?\s+contestația",
            re.IGNORECASE
        ),

        # Parties
        "contestator": re.compile(
            r"(?:Contestator|Petent)[:\s]+([^\n]+?)(?:\n|,\s*în)",
            re.IGNORECASE
        ),
        "authority": re.compile(
            r"(?:Autoritate(?:\s+contractantă)?|Intimat)[:\s]+([^\n]+?)(?:\n|,\s*în)",
            re.IGNORECASE
        ),

        # Criticism codes in text
        "criticism": re.compile(r"\b([DR][1-7]|DAL|RAL)\b", re.IGNORECASE),

        # Legal articles
        "article": re.compile(
            r"art(?:icol)?\.?\s*(\d+)(?:\s*alin(?:eat)?\.?\s*\(?\d+\)?)?",
            re.IGNORECASE
        ),
    }

    # Section markers
    SECTION_MARKERS = {
        SectionType.DISPOSITIVE: [
            "CONSILIUL DECIDE:",
            "CONSILIUL HOTĂRĂȘTE:",
            "PENTRU ACESTE MOTIVE",
        ],
        SectionType.AC_POINT_OF_VIEW: [
            "Punct de vedere",
            "Autoritatea contractantă a formulat punct de vedere",
            "a transmis punct de vedere",
        ],
        SectionType.INTERVENTION: [
            "Cerere de intervenție",
            "a formulat cerere de intervenție",
            "Intervenient",
        ],
        SectionType.CONTESTANT_REQUESTS: [
            "Contestatorul solicită",
            "În contestație se solicită",
            "Prin contestație",
        ],
    }

    MONTH_MAP = {
        "ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4,
        "mai": 5, "iunie": 6, "iulie": 7, "august": 8,
        "septembrie": 9, "octombrie": 10, "noiembrie": 11, "decembrie": 12,
    }

    def parse_file(self, file_path: Path | str) -> ParsedDecision:
        """Parse a CNSC decision from a file.

        Args:
            file_path: Path to the .txt file containing the decision.

        Returns:
            ParsedDecision with extracted metadata.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            ValueError: If the filename doesn't match expected pattern.
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Read file content
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
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
        warnings: list[str] = []

        # Stage 1: Parse filename metadata
        filename_meta = None
        if source_file:
            try:
                filename_meta = self._parse_filename(Path(source_file).name)
            except ValueError as e:
                warnings.append(f"Filename parse warning: {e}")

        # Initialize decision with filename metadata or defaults
        if filename_meta:
            decision = ParsedDecision(
                filename=filename_meta.filename,
                numar_bo=filename_meta.numar_bo,
                an_bo=filename_meta.an_bo,
                coduri_critici=filename_meta.coduri_critici,
                tip_contestatie=filename_meta.tip_contestatie,
                cod_cpv=filename_meta.cod_cpv,
                cpv_source="filename" if filename_meta.cod_cpv else "text_explicit",
                solutie_filename=filename_meta.solutie,
                text_integral=text,
                source_file=source_file,
            )
        else:
            # Fallback for text without valid filename
            decision = ParsedDecision(
                filename=Path(source_file).name if source_file else "unknown.txt",
                numar_bo=0,
                an_bo=0,
                text_integral=text,
                source_file=source_file,
            )

        # Stage 2: Extract metadata from text
        self._extract_text_metadata(decision, text)

        # Stage 3: Extract parties
        self._extract_parties(decision, text)

        # Stage 4: Extract solution from dispositive
        self._extract_solution(decision, text)

        # Stage 5: Validate and reconcile
        self._validate_and_reconcile(decision, warnings)

        decision.parse_warnings = warnings

        logger.info(
            "decision_parsed",
            external_id=decision.external_id,
            tip_contestatie=decision.tip_contestatie.value,
            solutie=decision.solutie_contestatie.value if decision.solutie_contestatie else "X",
            critici=decision.coduri_critici,
        )

        return decision

    def _parse_filename(self, filename: str) -> FilenameMetadata:
        """Parse filename to extract metadata.

        Expected format: BO{AN}_{NR_BO}_{COD_CRITICI}_CPV_{COD_CPV}_{SOLUTIE}.txt

        Examples:
            BO2025_3855_R2_CPV_55520000-1_A.txt
            BO2025_1234_D1_D4_CPV_45233140-2_R.txt
            BO2024_5678_R3_R4_X.txt (no CPV)
        """
        # Try main pattern with CPV
        match = self.FILENAME_PATTERN.match(filename)

        if not match:
            # Try pattern without CPV
            match = self.FILENAME_PATTERN_NO_CPV.match(filename)
            if not match:
                raise ValueError(f"Filename doesn't match expected pattern: {filename}")

            an_bo = int(match.group(1))
            numar_bo = int(match.group(2))
            critici_str = match.group(3)
            cod_cpv = None
            solutie_str = match.group(4).upper()
        else:
            an_bo = int(match.group(1))
            numar_bo = int(match.group(2))
            critici_str = match.group(3)
            cod_cpv = match.group(4)
            solutie_str = match.group(5).upper()

        # Parse criticism codes (e.g., "D1_D4" -> ["D1", "D4"])
        coduri_critici = self._parse_criticism_codes_from_filename(critici_str)

        if not coduri_critici:
            raise ValueError(f"No valid criticism codes found in: {critici_str}")

        # Determine contest type from criticism codes
        tip_contestatie = self._determine_contest_type(coduri_critici)

        # Parse solution code
        solutie = SolutionCode(solutie_str) if solutie_str in "ARX" else SolutionCode.UNKNOWN

        return FilenameMetadata(
            an_bo=an_bo,
            numar_bo=numar_bo,
            coduri_critici=coduri_critici,
            cod_cpv=cod_cpv,
            solutie=solutie,
            tip_contestatie=tip_contestatie,
            filename=filename,
        )

    def _parse_criticism_codes_from_filename(self, critici_str: str) -> list[str]:
        """Parse criticism codes from filename segment.

        Examples:
            "R2" -> ["R2"]
            "D1_D4" -> ["D1", "D4"]
            "R3_R4" -> ["R3", "R4"]
        """
        # Split by underscore
        parts = critici_str.upper().split("_")

        # Filter valid codes
        valid_codes = []
        for part in parts:
            # Check if it's a valid criticism code
            if re.match(r"^[DR][1-7]$|^DAL$|^RAL$", part):
                valid_codes.append(part)

        return valid_codes

    def _determine_contest_type(self, coduri_critici: list[str]) -> CriticismCodeType:
        """Determine contest type from criticism codes.

        D* codes -> documentatie
        R* codes -> rezultat

        If mixed, use the first code's type.
        """
        if not coduri_critici:
            return CriticismCodeType.DOCUMENTATION

        first_code = coduri_critici[0].upper()
        if first_code.startswith("R"):
            return CriticismCodeType.RESULT
        return CriticismCodeType.DOCUMENTATION

    def _extract_text_metadata(self, decision: ParsedDecision, text: str) -> None:
        """Extract metadata from decision text."""

        # Extract decision number header: "Nr. 3754/C8/4446"
        match = self.PATTERNS["decision_header"].search(text)
        if match:
            # Validate numar_bo matches filename
            text_numar_bo = int(match.group(1))
            if decision.numar_bo and text_numar_bo != decision.numar_bo:
                decision.parse_warnings.append(
                    f"Numar BO mismatch: filename={decision.numar_bo}, text={text_numar_bo}"
                )

            decision.complet = match.group(2).upper()
            decision.numar_decizie = int(match.group(3))

        # Extract date
        decision.data_decizie = self._extract_date(text)

        # Extract CPV if not from filename
        if not decision.cod_cpv:
            cpv_matches = self.PATTERNS["cpv"].findall(text)
            if cpv_matches:
                decision.cod_cpv = cpv_matches[0]  # First CPV found
                decision.cpv_source = "text_explicit"

        # Extract criticism codes from text if not from filename
        if not decision.coduri_critici:
            text_codes = self.PATTERNS["criticism"].findall(text)
            decision.coduri_critici = list(dict.fromkeys([c.upper() for c in text_codes]))
            if decision.coduri_critici:
                decision.tip_contestatie = self._determine_contest_type(decision.coduri_critici)

    def _extract_date(self, text: str) -> Optional[datetime]:
        """Extract date from text."""
        # Try text format first (e.g., "10 decembrie 2025")
        match = self.PATTERNS["date_text"].search(text)
        if match:
            day = int(match.group(1))
            month = self.MONTH_MAP.get(match.group(2).lower(), 1)
            year = int(match.group(3))
            try:
                return datetime(year, month, day)
            except ValueError:
                pass

        # Try numeric format (e.g., "10.12.2025")
        match = self.PATTERNS["date_numeric"].search(text)
        if match:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
            try:
                return datetime(year, month, day)
            except ValueError:
                pass

        return None

    def _extract_parties(self, decision: ParsedDecision, text: str) -> None:
        """Extract parties from text."""
        # Contestator
        match = self.PATTERNS["contestator"].search(text)
        if match:
            decision.contestator = self._clean_party_name(match.group(1))

        # Authority
        match = self.PATTERNS["authority"].search(text)
        if match:
            decision.autoritate_contractanta = self._clean_party_name(match.group(1))

    def _clean_party_name(self, name: str) -> str:
        """Clean up party name."""
        name = name.strip()
        name = re.sub(r"\s+", " ", name)
        return name[:500]  # Limit length

    def _extract_solution(self, decision: ParsedDecision, text: str) -> None:
        """Extract solution from dispositive section.

        Looks for "CONSILIUL DECIDE:" marker and extracts the ruling.
        """
        # Find dispositive section
        dispositive_start = None
        for marker in self.SECTION_MARKERS[SectionType.DISPOSITIVE]:
            pos = text.find(marker)
            if pos != -1:
                dispositive_start = pos
                break

        if dispositive_start is None:
            return

        dispositive_text = text[dispositive_start:dispositive_start + 2000]

        # Check for partial admission first
        if self.PATTERNS["admis_partial"].search(dispositive_text):
            decision.solutie_contestatie = SolutionType.ADMIS_PARTIAL
            return

        # Check for full admission
        if self.PATTERNS["admis_integral"].search(dispositive_text):
            decision.solutie_contestatie = SolutionType.ADMIS
            return

        # Check for rejection (with reason)
        match = self.PATTERNS["respins"].search(dispositive_text)
        if match:
            decision.solutie_contestatie = SolutionType.RESPINS
            reason = match.group(1).lower()
            if reason in REJECTION_REASONS:
                decision.motiv_respingere = reason
            else:
                # Try to find known reason in context
                for known_reason in REJECTION_REASONS:
                    if known_reason in dispositive_text.lower():
                        decision.motiv_respingere = known_reason
                        break
            return

    def _validate_and_reconcile(self, decision: ParsedDecision, warnings: list[str]) -> None:
        """Validate consistency between filename and text data."""

        # Reconcile solution
        if decision.solutie_filename != SolutionCode.UNKNOWN:
            if decision.solutie_contestatie is None:
                # Use filename solution if text extraction failed
                if decision.solutie_filename == SolutionCode.ADMIS:
                    decision.solutie_contestatie = SolutionType.ADMIS
                elif decision.solutie_filename == SolutionCode.RESPINS:
                    decision.solutie_contestatie = SolutionType.RESPINS
            else:
                # Check consistency
                filename_is_admis = decision.solutie_filename == SolutionCode.ADMIS
                text_is_admis = decision.solutie_contestatie in (
                    SolutionType.ADMIS, SolutionType.ADMIS_PARTIAL
                )

                if filename_is_admis != text_is_admis:
                    warnings.append(
                        f"Solution mismatch: filename={decision.solutie_filename.value}, "
                        f"text={decision.solutie_contestatie.value}"
                    )

        # Validate required fields
        if not decision.coduri_critici:
            warnings.append("No criticism codes found")

        if not decision.cod_cpv:
            warnings.append("No CPV code found")

    def parse_sections(self, decision: ParsedDecision) -> list[DecisionSection]:
        """Parse decision text into logical sections.

        This is a separate method for Stage 3 of the pipeline.

        Args:
            decision: ParsedDecision with text_integral populated.

        Returns:
            List of DecisionSection objects.
        """
        text = decision.text_integral
        sections = []

        # Find section boundaries
        section_positions = []

        for section_type, markers in self.SECTION_MARKERS.items():
            for marker in markers:
                pos = text.find(marker)
                if pos != -1:
                    section_positions.append((pos, section_type, marker))

        # Sort by position
        section_positions.sort(key=lambda x: x[0])

        # Create sections
        for i, (pos, section_type, marker) in enumerate(section_positions):
            # End position is start of next section or end of text
            end_pos = section_positions[i + 1][0] if i + 1 < len(section_positions) else len(text)

            section_text = text[pos:end_pos].strip()

            sections.append(DecisionSection(
                tip_sectiune=section_type,
                ordine=i + 1,
                text_sectiune=section_text,
            ))

        return sections


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

# Singleton instance
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


def get_all_criticism_codes() -> dict[str, str]:
    """Get the complete legend of criticism codes."""
    return CRITICISM_CODES_LEGEND.copy()
