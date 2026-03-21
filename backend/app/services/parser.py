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
    DOCUMENTATION = "documentatie"  # D1-D8, DAL
    RESULT = "rezultat"  # R1-R8, RAL


CRITICISM_CODES_LEGEND = {
    # Critici formulate la Documentația de Atribuire (D)
    "D1": "Cerințe restrictive cu privire la experiența similară, criterii de calificare, specificații tehnice",
    "D2": "Criterii de atribuire și factori de evaluare fără algoritm de calcul, cu algoritm de calcul netransparent sau subiectiv",
    "D3": ("Menționarea în cadrul documentației de atribuire a unor denumiri de tehnologii, produse, mărci, "
           "producători, fără a se utiliza sintagma \u201Esau echivalent\u201D"),
    "D4": ("Lipsa unui răspuns clar, complet și fără ambiguități din partea autorității contractante, "
           "la solicitările de clarificări vizând prevederile documentației de atribuire"),
    "D5": "Forma de constituire a garanției de participare",
    "D6": "Impunerea de clauze contractuale inechitabile sau excesive",
    "D7": "Nedivizarea achiziției pe loturi, în cazul produselor/lucrărilor similare",
    "DAL": "Altele - de precizat",

    # Critici formulate la Rezultatul Procedurii (R)
    "R1": ("Contestații împotriva procesului-verbal al ședinței de deschidere a ofertelor "
           "(neluarea în considerare a garanției de participare, modul de desfășurare a ședinței de deschidere a ofertelor)"),
    "R2": "Respingerea ofertei contestate ca neconformă sau inacceptabilă",
    "R3": "Prețul neobișnuit de scăzut al ofertelor altor participanți la procedura de atribuire",
    "R4": ("Documentele de calificare depuse de alți ofertanți participanți sau modul de punctare/evaluare "
           "a acestora de către autoritatea contractantă"),
    "R5": "Lipsa precizării motivelor de respingere a ofertei în comunicare",
    "R6": ("Lipsa solicitare clarificări referitoare la propunerea tehnică/prețul ofertat "
           "sau aprecierea incorectă a răspunsurilor la clarificări"),
    "R7": "Anularea fără temei legal a procedurii de atribuire de către autoritatea contractantă",
    "RAL": "Altele - de precizat",
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

    # Contract object (extracted from introductory text)
    obiect_contract: Optional[str] = None

    # Procurement metadata (extracted from text)
    criteriu_atribuire: Optional[str] = None
    numar_oferte: Optional[int] = None
    valoare_estimata: Optional[float] = None
    moneda: str = "RON"
    numar_anunt_participare: Optional[str] = None
    data_raport_procedura: Optional[datetime] = None

    # Legislative domain and procedure type
    domeniu_legislativ: Optional[str] = None
    tip_procedura: Optional[str] = None

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

WORD_TO_NUMBER = {
    "o": 1, "un": 1, "una": 1, "două": 2, "trei": 3, "patru": 4,
    "cinci": 5, "șase": 6, "șapte": 7, "opt": 8, "nouă": 9, "zece": 10,
    "unsprezece": 11, "doisprezece": 12,
}

# Canonical forms for award criteria
CRITERIU_CANONICAL = {
    "calitate-preț": "cel mai bun raport calitate-preț",
    "calitate-cost": "cel mai bun raport calitate-cost",
    "costul": "costul cel mai scăzut",
    "prețul": "prețul cel mai scăzut",
}


class CNSCDecisionParser:
    """Parser for CNSC decision text files.

    Extracts structured metadata from raw text files following
    the established naming conventions and logical structure.
    """

    # Filename pattern: BO{AN}_{NR_BO}_{COD_CRITICI}_CPV_{COD_CPV}_{SOLUTIE}.txt
    # Accepts dots, commas, and sub-points in criticism codes
    FILENAME_PATTERN = re.compile(
        r"^BO(\d{4})_(\d+)_([A-Za-z0-9_.,-]+?)_CPV_(\d{8}(?:-\d)?)_([ARX])\.txt$",
        re.IGNORECASE
    )

    # Pattern with CPV marker but no actual CPV code (e.g., _CPV_A.txt)
    FILENAME_PATTERN_CPV_EMPTY = re.compile(
        r"^BO(\d{4})_(\d+)_([A-Za-z0-9_.,-]+?)_CPV_([ARX])\.txt$",
        re.IGNORECASE
    )

    # Alternative pattern without CPV
    FILENAME_PATTERN_NO_CPV = re.compile(
        r"^BO(\d{4})_(\d+)_([A-Za-z0-9_.,-]+)_([ARX])\.txt$",
        re.IGNORECASE
    )

    # Patterns for text extraction
    PATTERNS = {
        # Decision number: "Nr. 3754/C8/4446" -> numar_bo=3754, complet=C8, numar_decizie=4446
        "decision_header": re.compile(
            r"Nr\.?\s*(\d+)/([A-Z]\d+)/(\d+)",
            re.IGNORECASE
        ),

        # Date patterns — used only on header area (first ~500 chars)
        # "Data:" is the most reliable marker (unique to the header)
        # "Ședința publică din" is also reliable
        # Plain "din" is too generic (matches references to other dates)
        "date_text": re.compile(
            r"(?:Data|Ședința\s+publică\s+din)\s*[:\s]*(\d{1,2})\s+"
            r"(ianuarie|februarie|martie|aprilie|mai|iunie|"
            r"iulie|august|septembrie|octombrie|noiembrie|decembrie)\s+"
            r"(\d{4})",
            re.IGNORECASE
        ),
        "date_numeric": re.compile(
            r"(?:Data|Ședința\s+publică\s+din)\s*[:\s]*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})"
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
        "criticism": re.compile(r"\b([DR][1-8]|DAL|RAL)\b", re.IGNORECASE),

        # Legal articles
        "article": re.compile(
            r"art(?:icol)?\.?\s*(\d+)(?:\s*alin(?:eat)?\.?\s*\(?\d+\)?)?",
            re.IGNORECASE
        ),

        # Award criteria — matches patterns like:
        #   "criteriul de atribuire al contractului este „prețul cel mai scăzut""
        #   "criteriul de atribuire „cel mai bun raport calitate-preț""
        #   "criteriul de atribuire fiind „prețul cel mai scăzut""
        # Captures the quoted or unquoted criterion name
        "criteriu_atribuire": re.compile(
            r"criteriu(?:l)?\s+de\s+atribuire\s+(?:[\w\s,]+?\s+)?(?:(?:este|fiind)\s+)?"
            r"[\u201e\"\u00ab]?\s*((?:cel\s+mai\s+bun\s+raport\s+calitate\s*[-\u2013\u2014\s]\s*(?:pre[tț]|cost))"
            r"|(?:costul\s+cel\s+mai\s+sc[aă]zut)"
            r"|(?:pre[tț]ul\s+cel\s+mai\s+sc[aă]zut))\s*[\u201d\"\u00bb]?",
            re.IGNORECASE
        ),

        # Number of offers — multiple patterns
        # "au fost depuse trei oferte", "au depus ofertă 5 operatori"
        # "3 din cele 4 oferte depuse", "cele 4 oferte depuse"
        "numar_oferte_text": re.compile(
            r"au\s+fost\s+depuse\s+(o|două|trei|patru|cinci|șase|șapte|opt|nouă|zece)\s+oferte?",
            re.IGNORECASE
        ),
        "numar_oferte_cifra": re.compile(
            r"au\s+depus\s+ofert[eă]\s+(\d+)\s+operatori",
            re.IGNORECASE
        ),
        "numar_oferte_din_cele": re.compile(
            r"(?:din\s+)?cele\s+(\d+)\s+oferte\s+depuse",
            re.IGNORECASE
        ),
        # "au depus oferte 4 operatori" (inverted order)
        "numar_oferte_inv": re.compile(
            r"au\s+depus\s+oferte\s+(\d+)\s+(?:operatori|participanți)",
            re.IGNORECASE
        ),
        # "cei 3 ofertanți", "2 din cei 3 ofertanți"
        "numar_ofertanti": re.compile(
            r"(?:din\s+)?ce(?:i|lor)\s+(\d+)\s+ofertanți",
            re.IGNORECASE
        ),

        # Valoare estimata — "valoarea estimată a ... este (de) 4.674.769,11 lei"
        # Decimals are optional: "8.403.350 lei" or "928.836,70 lei"
        "valoare_estimata": re.compile(
            r"valoare[a]?\s+(?:total[aă]\s+)?estimat[aă]\s+a\s+"
            r"(?:contractului|procedurii|acordului[\s\-]+cadru"
            r"|achiziției)(?:\s+de\s+\w+)?\s+(?:este\s+(?:de\s+)?)?"
            r"([\d.]+(?:,\d{1,2})?)\s*(lei|RON)",
            re.IGNORECASE
        ),

        # Numar anunt participare — "anunțul de participare (simplificat) nr. CN1082102/10.06.2025"
        "numar_anunt": re.compile(
            r"anunț(?:ul)?\s+de\s+participare\s+(?:simplificat\s+)?nr\.?\s*"
            r"((?:SCN|CN|ADV)\d+(?:/[\d.]+)?)",
            re.IGNORECASE
        ),

        # Raportul procedurii — "raportul procedurii nr. 42145/26.08.2025"
        # Date format: nr/DD.MM.YYYY or nr.../DD.MM.YYYY or complex nr like 2/2135/29.04.2025
        "raport_procedura": re.compile(
            r"raportul\s+procedurii\s+nr\.?\s*(?:[\d/]+/)?(\d{1,2})[./](\d{1,2})[./](\d{4})",
            re.IGNORECASE
        ),

        # Legislative domain detection
        # Legea 99/2016 = achiziții sectoriale
        "legea_99": re.compile(
            r"Leg(?:ea|ii)\s+(?:nr\.?\s*)?99/2016",
            re.IGNORECASE
        ),
        # Legea 100/2016 = concesiuni
        "legea_100": re.compile(
            r"Leg(?:ea|ii)\s+(?:nr\.?\s*)?100/2016",
            re.IGNORECASE
        ),
        # "entitate contractantă" (indicator sectorial)
        "entitate_contractanta": re.compile(
            r"entitate\s+contractant[aă]",
            re.IGNORECASE
        ),
        # "achiziții sectoriale" / "contracte sectoriale"
        "sectoriale": re.compile(
            r"(?:achiziți(?:i|ilor|ilor)|contract(?:e|elor))\s+sectoriale",
            re.IGNORECASE
        ),
        # "concesiune de lucrări" / "concesiune de servicii" / "contract de concesiune"
        "concesiune": re.compile(
            r"concesiun(?:e|i|ea|ii)\s+de\s+(?:lucrări|servicii)|contract\s+de\s+concesiune",
            re.IGNORECASE
        ),

        # Procedure type detection
        "proc_licitatie_deschisa": re.compile(
            r"licitați[ea]\s+deschis[aă]",
            re.IGNORECASE
        ),
        "proc_licitatie_restransa": re.compile(
            r"licitați[ea]\s+restrâns[aă]",
            re.IGNORECASE
        ),
        "proc_negociere_competitiva": re.compile(
            r"negociere(?:a)?\s+competitiv[aă]",
            re.IGNORECASE
        ),
        "proc_dialog_competitiv": re.compile(
            r"dialog(?:ul)?\s+competitiv",
            re.IGNORECASE
        ),
        "proc_parteneriat_inovare": re.compile(
            r"parteneriat(?:ul)?\s+pentru\s+inovare",
            re.IGNORECASE
        ),
        "proc_negociere_fara_publicare": re.compile(
            r"negociere(?:a)?\s+fără\s+publicare\s+prealabil[aă]",
            re.IGNORECASE
        ),
        "proc_negociere_fara_invitatie": re.compile(
            r"negociere(?:a)?\s+fără\s+invitați[ea]\s+prealabil[aă]",
            re.IGNORECASE
        ),
        "proc_negociere_fara_anunt": re.compile(
            r"negociere(?:a)?\s+fără\s+publicare(?:a)?\s+(?:unui\s+)?anunț\s+de\s+concesionare",
            re.IGNORECASE
        ),
        "proc_concurs_solutii": re.compile(
            r"concurs(?:ul)?\s+de\s+soluții",
            re.IGNORECASE
        ),
        "proc_servicii_sociale": re.compile(
            r"procedur[aă]\s+(?:de\s+)?atribuire\s+aplicabil[aă]\s+(?:în\s+cazul\s+)?servicii(?:lor)?\s+sociale",
            re.IGNORECASE
        ),
        "proc_simplificata": re.compile(
            r"procedur[aă]\s+simplificat[aă]",
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

        # Stage 4: Extract contract object from introductory text
        self._extract_contract_object(decision, text)

        # Stage 5: Extract solution from dispositive
        self._extract_solution(decision, text)

        # Stage 6: Validate and reconcile
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
        # Try main pattern with full CPV code
        match = self.FILENAME_PATTERN.match(filename)

        if match:
            an_bo = int(match.group(1))
            numar_bo = int(match.group(2))
            critici_str = match.group(3)
            cod_cpv = match.group(4)
            solutie_str = match.group(5).upper()
        else:
            # Try pattern with CPV marker but no actual code (e.g., _CPV_A.txt)
            match = self.FILENAME_PATTERN_CPV_EMPTY.match(filename)
            if match:
                an_bo = int(match.group(1))
                numar_bo = int(match.group(2))
                critici_str = match.group(3)
                cod_cpv = None
                solutie_str = match.group(4).upper()
            else:
                # Try pattern without CPV at all
                match = self.FILENAME_PATTERN_NO_CPV.match(filename)
                if not match:
                    raise ValueError(f"Filename doesn't match expected pattern: {filename}")

                an_bo = int(match.group(1))
                numar_bo = int(match.group(2))
                critici_str = match.group(3)
                cod_cpv = None
                solutie_str = match.group(4).upper()

        # Parse criticism codes (e.g., "D1_D4" -> ["D1", "D4"])
        # "NA" means no specific criticism codes — valid for some decisions
        coduri_critici = self._parse_criticism_codes_from_filename(critici_str)

        if not coduri_critici and critici_str.upper() != "NA":
            raise ValueError(f"No valid criticism codes found in: {critici_str}")

        # Determine contest type from criticism codes (or from solutie if NA)
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

        Handles various formats found in real CNSC filenames:
            "R2" -> ["R2"]
            "D1_D4" -> ["D1", "D4"]
            "R3_R4" -> ["R3", "R4"]
            "R2.2.2,R4" -> ["R2", "R4"]  (sub-points normalized to base code)
            "R4.3" -> ["R4"]  (sub-point stripped)
            "RA" -> ["RAL"]  (shorthand expanded)
            "DA" -> ["DAL"]  (shorthand expanded)
        """
        # Split by underscore first, then by comma
        raw_parts = critici_str.upper().split("_")
        parts = []
        for raw in raw_parts:
            parts.extend(raw.split(","))

        # Normalize and filter valid codes
        valid_codes = []
        seen = set()
        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Expand shorthands: RA -> RAL, DA -> DAL
            if part == "RA":
                part = "RAL"
            elif part == "DA":
                part = "DAL"

            # Check for exact match first (D1-D8, R1-R8, DAL, RAL)
            if re.match(r"^[DR][1-8]$|^DAL$|^RAL$", part):
                if part not in seen:
                    valid_codes.append(part)
                    seen.add(part)
                continue

            # Handle sub-points: R2.2.2 -> R2, R4.3 -> R4, D1.1 -> D1
            base_match = re.match(r"^([DR])(\d)", part)
            if base_match:
                base_code = base_match.group(1) + base_match.group(2)
                if re.match(r"^[DR][1-8]$", base_code) and base_code not in seen:
                    valid_codes.append(base_code)
                    seen.add(base_code)

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

        # Extract date — search only in header area (first 500 chars)
        # to avoid matching dates from referenced laws/documents in the body
        decision.data_decizie = self._extract_date(text[:500])

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

        # Extract award criterion and number of offers
        decision.criteriu_atribuire = self._extract_criteriu_atribuire(text)
        decision.numar_oferte = self._extract_numar_oferte(text)

        # Extract procurement metadata
        val, mon = self._extract_valoare_estimata(text)
        decision.valoare_estimata = val
        if mon:
            decision.moneda = mon
        decision.numar_anunt_participare = self._extract_numar_anunt(text)
        decision.data_raport_procedura = self._extract_data_raport(text)

        # Extract legislative domain and procedure type
        decision.domeniu_legislativ = self._extract_domeniu_legislativ(text)
        decision.tip_procedura = self._extract_tip_procedura(text)

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

    def _extract_criteriu_atribuire(self, text: str) -> Optional[str]:
        """Extract award criterion from decision text.

        Returns one of 4 canonical forms:
        - "cel mai bun raport calitate-preț"
        - "cel mai bun raport calitate-cost"
        - "costul cel mai scăzut"
        - "prețul cel mai scăzut"
        """
        match = self.PATTERNS["criteriu_atribuire"].search(text)
        if match:
            raw = match.group(1).strip().lower()
            # Normalize to canonical form
            raw_normalized = re.sub(r"\s+", " ", raw)
            raw_normalized = re.sub(r"[–—]", "-", raw_normalized)
            if "calitate" in raw_normalized and "cost" in raw_normalized:
                return "cel mai bun raport calitate-cost"
            elif "calitate" in raw_normalized and "preț" in raw_normalized:
                return "cel mai bun raport calitate-preț"
            elif "costul" in raw_normalized:
                return "costul cel mai scăzut"
            elif "prețul" in raw_normalized:
                return "prețul cel mai scăzut"
        return None

    def _extract_numar_oferte(self, text: str) -> Optional[int]:
        """Extract number of offers submitted from decision text.

        Looks for patterns like:
        - "au fost depuse trei oferte"
        - "au depus ofertă 5 operatori economici"
        - "cele 4 oferte depuse"
        """
        # Pattern 1: "au fost depuse trei/patru/... oferte"
        match = self.PATTERNS["numar_oferte_text"].search(text)
        if match:
            word = match.group(1).lower()
            return WORD_TO_NUMBER.get(word)

        # Pattern 2: "au depus ofertă 5 operatori"
        match = self.PATTERNS["numar_oferte_cifra"].search(text)
        if match:
            return int(match.group(1))

        # Pattern 3: "cele 4 oferte depuse" / "3 din cele 4 oferte depuse"
        match = self.PATTERNS["numar_oferte_din_cele"].search(text)
        if match:
            return int(match.group(1))

        # Pattern 4: "au depus oferte 4 operatori" (inverted)
        match = self.PATTERNS["numar_oferte_inv"].search(text)
        if match:
            return int(match.group(1))

        # Pattern 5: "cei 3 ofertanți" / "2 din cei 3 ofertanți"
        match = self.PATTERNS["numar_ofertanti"].search(text)
        if match:
            return int(match.group(1))

        return None

    def _extract_valoare_estimata(self, text: str) -> tuple[Optional[float], Optional[str]]:
        """Extract estimated value and currency from decision text.

        Returns (value, currency) tuple. Value is float, currency is "RON" or "lei".
        Skips anonymized values (dots replacing digits).
        """
        match = self.PATTERNS["valoare_estimata"].search(text)
        if match:
            raw_value = match.group(1)  # e.g., "4.674.769,11"
            currency = match.group(2).upper()  # "lei" or "RON"
            # Normalize: "4.674.769,11" -> 4674769.11
            try:
                normalized = raw_value.replace(".", "").replace(",", ".")
                value = float(normalized)
                # Normalize currency
                if currency == "LEI":
                    currency = "RON"
                return value, currency
            except ValueError:
                pass
        return None, None

    def _extract_numar_anunt(self, text: str) -> Optional[str]:
        """Extract SEAP participation notice number.

        Patterns:
        - "anunțul de participare nr. CN1082102/10.06.2025"
        - "anunțul de participare simplificat nr. SCN1156905/09.12.2025"
        """
        match = self.PATTERNS["numar_anunt"].search(text)
        if match:
            anunt = match.group(1).strip()
            # Skip anonymized: "nr. ...." or "nr......"
            if "." not in anunt.split("/")[0]:  # dots in the SCN part means anonymized
                return anunt
            # Check the SCN number part (before /) is actually a number
            prefix = anunt.split("/")[0] if "/" in anunt else anunt
            if any(c.isdigit() for c in prefix):
                return anunt
        return None

    def _extract_data_raport(self, text: str) -> Optional[datetime]:
        """Extract procedure report date from decision text.

        Looks for "raportul procedurii nr. XXXXX/DD.MM.YYYY"
        """
        match = self.PATTERNS["raport_procedura"].search(text)
        if match:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
            try:
                return datetime(year, month, day)
            except ValueError:
                pass
        return None

    def _extract_domeniu_legislativ(self, text: str) -> str:
        """Detect legislative domain from decision text.

        Priority:
        1. Explicit reference to Legea 100/2016 → concesiuni
        2. Explicit reference to Legea 99/2016 → achizitii_sectoriale
        3. "concesiune de lucrări/servicii" → concesiuni
        4. "achiziții/contracte sectoriale" or "entitate contractantă" → achizitii_sectoriale
        5. Default → achizitii_publice (Legea 98/2016)

        Note: Legea 101/2016 (remedii) mentions all three domains in its title,
        so we exclude occurrences within the standard Legea 101 preamble.
        """
        # Remove ALL references to Legea 101/2016 full title which mentions
        # "contracte sectoriale" and "concesiune de lucrări/servicii" — false positives.
        cleaned = re.sub(
            r"Leg(?:ea|ii)\s+(?:nr\.?\s*)?101/2016\s+privind\s+remediile.*?"
            r"Contestați(?:i|ilor)",
            "",
            text,
            flags=re.IGNORECASE | re.DOTALL
        )
        # Remove the standard legal enumeration "concesiune de lucrări și concesiune
        # de servicii" which appears in Legea 101 citations, ANAP references, etc.
        # Note: both ș (U+0219) and ş (U+015F, cedilla) variants exist in texts.
        search_text = re.sub(
            r"(?:contracte(?:lor)?(?:/acorduri(?:lor)?[\s\-]*cadru)?\s+)?sectoriale\s+"
            r"[sșş]i\s+a\s+(?:contractelor\s+)?(?:de\s+)?concesiun[eă]\s+"
            r"de\s+lucrări\s+[sșş]i\s+(?:de\s+)?concesiun[eă]\s+de\s+servicii",
            "",
            cleaned,
            flags=re.IGNORECASE
        )
        # Also catch standalone "concesiune de lucrări și concesiune de servicii"
        search_text = re.sub(
            r"concesiun[eă]\s+de\s+lucrări\s+[sșş]i\s+(?:de\s+)?concesiun[eă]\s+de\s+servicii",
            "",
            search_text,
            flags=re.IGNORECASE
        )

        # Check for Legea 100/2016 (concesiuni)
        if self.PATTERNS["legea_100"].search(search_text):
            return "concesiuni"

        # Check for Legea 99/2016 (sectoriale)
        if self.PATTERNS["legea_99"].search(search_text):
            return "achizitii_sectoriale"

        # Check for "concesiune de lucrări/servicii" (excluding Legea 101 preamble)
        if self.PATTERNS["concesiune"].search(search_text):
            return "concesiuni"

        # Check for "achiziții/contracte sectoriale"
        if self.PATTERNS["sectoriale"].search(search_text):
            return "achizitii_sectoriale"

        # Check for "entitate contractantă" (strong indicator of sectorial)
        if self.PATTERNS["entitate_contractanta"].search(text):
            return "achizitii_sectoriale"

        # Default: achiziții publice (Legea 98/2016)
        return "achizitii_publice"

    def _extract_tip_procedura(self, text: str) -> Optional[str]:
        """Extract procedure type from decision text.

        Searches for procedure type mentions like "procedura simplificată",
        "licitație deschisă", etc. Returns canonical code.
        """
        # Map pattern keys to canonical procedure codes
        procedure_patterns = [
            ("proc_licitatie_deschisa", "licitatie_deschisa"),
            ("proc_licitatie_restransa", "licitatie_restransa"),
            ("proc_negociere_competitiva", "negociere_competitiva"),
            ("proc_dialog_competitiv", "dialog_competitiv"),
            ("proc_parteneriat_inovare", "parteneriat_inovare"),
            ("proc_negociere_fara_publicare", "negociere_fara_publicare"),
            ("proc_negociere_fara_invitatie", "negociere_fara_invitatie"),
            ("proc_negociere_fara_anunt", "negociere_fara_anunt"),
            ("proc_concurs_solutii", "concurs_solutii"),
            ("proc_servicii_sociale", "servicii_sociale"),
            ("proc_simplificata", "procedura_simplificata"),
        ]

        for pattern_key, code in procedure_patterns:
            if self.PATTERNS[pattern_key].search(text):
                return code

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

    @staticmethod
    def _is_anonymized(text: str) -> bool:
        """Check if text is anonymized (dots/ellipsis/placeholders replacing real content).

        CNSC decisions are frequently anonymized with patterns like:
        - "......... SRL", "... SRL", "..... S.A."
        - "SC (...) S.R.L.", "(...) SRL", "SC (…) S.A."
        - ".........", "...", "……"
        - "XXXXXXX", "_____"
        """
        if not text:
            return True
        cleaned = text.strip().rstrip(".,;:-–—")
        # Remove common prefixes/suffixes (SC, SRL, SA, etc.) to check core
        cleaned_core = re.sub(
            r"^\s*S\.?C\.?\s*", "", cleaned, flags=re.IGNORECASE,
        )
        cleaned_core = re.sub(
            r"\s*(?:S\.?R\.?L\.?|S\.?A\.?|S\.?C\.?|S\.?N\.?C\.?|R\.?A\.?)\s*$",
            "", cleaned_core, flags=re.IGNORECASE,
        ).strip()
        if not cleaned_core:
            return True
        # Detect parenthesized placeholders: (...), (…), (xxx), (___)
        if re.fullmatch(r"[(\[{][\s.…·•_x*]+[)\]}]", cleaned_core, re.IGNORECASE):
            return True
        # Count anonymization chars vs alphanumeric
        anon_chars = sum(1 for c in cleaned_core if c in ".…·•_()[]{}*")
        alnum = sum(1 for c in cleaned_core if c.isalnum())
        # If more anonymization chars than alphanumeric, it's anonymized
        if anon_chars > alnum:
            return True
        # Pure dots/ellipsis/x-es/underscores
        if re.fullmatch(r"[.\s…·•x_*()]+", cleaned_core, re.IGNORECASE):
            return True
        return False

    def _clean_party_name(self, name: str) -> str | None:
        """Clean up party name. Returns None if anonymized."""
        name = name.strip()
        name = re.sub(r"\s+", " ", name)
        name = name[:500]  # Limit length
        if self._is_anonymized(name):
            return None
        return name

    # Pattern 1: Quoted contract object near "având ca obiect" or similar markers
    # Matches: având ca obiect: „Servicii de pază..." or având ca obiect „Echipamente..."
    _CONTRACT_OBJECT_QUOTED = re.compile(
        r"(?:având\s+ca\s+obiect|obiectul\s+(?:contractului|achiziției|acordului[\s-]+cadru)"
        r"|obiect\s+(?:al\s+)?(?:contractului|achiziției))"
        r"[:\s]*"
        "[\u201e\"«]"  # opening quote: „ " «
        r"(.{5,500}?)"
        "[\u201d\u201c\"»]",  # closing quote: " " " »
        re.IGNORECASE,
    )

    # Pattern 2: Unquoted contract object after marker, delimited by period/comma+keyword
    _CONTRACT_OBJECT_UNQUOTED = re.compile(
        r"(?:"
        r"obiectul\s+(?:contractului|achiziției|acordului[\s-]+cadru)"
        r"|obiect\s+(?:al\s+)?(?:contractului|achiziției)"
        r"|privind\s+(?:achiziția\s+de|furnizarea\s+de|prestarea\s+de|execuția)"
        r"|în\s+vederea\s+(?:încheierii|atribuirii)\s+(?:(?:unui\s+)?(?:acord|acordului)[\s-]+"
        r"cadru|contractului)(?:\s+(?:de|pentru)\s+)?"
        r"(?:furnizare|servicii|lucrări|prestare|execuția)?"
        r")"
        r"[:\s]+(.{10,300}?)"
        r"(?:,\s*(?:cod|CPV|finanțat|în\s+valoare|estimat|organizată|publicat|înregistrat|lotul|lot\s|loturile)"
        r"|\.\s|,\s+s-a\s+solicitat)",
        re.IGNORECASE,
    )

    # Pattern 3: "având ca obiect" as standalone — look for the next meaningful text
    _CONTRACT_OBJECT_FALLBACK = re.compile(
        r"având\s+ca\s+obiect[:\s]+"
        r"(.{10,200}?)"
        r"(?:,\s*(?:cod|CPV|s-a\s+solicitat|anunț|publicat)|[.]\s)",
        re.IGNORECASE,
    )

    def _extract_contract_object(self, decision: ParsedDecision, text: str) -> None:
        """Extract contract object description from introductory text.

        Searches the first 5000 characters for common patterns like:
        - "având ca obiect: «Servicii de...»"
        - "obiectul contractului: ..."
        - "în vederea încheierii acordului-cadru de furnizare ..."

        This is purely regex-based (no LLM). The extracted text can be used
        for CPV deduction via embedding similarity against nomenclator_cpv.
        """
        # Normalize whitespace (line breaks → spaces) for better regex matching
        intro = re.sub(r"\s+", " ", text[:5000])

        # Strategy 1: Look for quoted text near markers (most reliable)
        match = self._CONTRACT_OBJECT_QUOTED.search(intro)
        if match:
            obj = match.group(1).strip()
            if len(obj) >= 5 and not self._is_anonymized(obj):
                decision.obiect_contract = obj
                return

        # Strategy 2: Unquoted text after specific markers, delimited by keywords
        match = self._CONTRACT_OBJECT_UNQUOTED.search(intro)
        if match:
            obj = match.group(1).strip()
            if len(obj) >= 10 and not self._is_anonymized(obj):
                decision.obiect_contract = obj
                return

        # Strategy 3: Broadest fallback — "având ca obiect" + any text until delimiter
        match = self._CONTRACT_OBJECT_FALLBACK.search(intro)
        if match:
            obj = match.group(1).strip()
            # Clean: remove leading quotes if partial
            obj = obj.lstrip('\u201e\u201c"«')
            if len(obj) >= 10 and not self._is_anonymized(obj):
                decision.obiect_contract = obj

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
