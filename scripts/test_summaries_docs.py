#!/usr/bin/env python3
"""Compare OLD vs NEW summary prompt input on sample decisions from docs/.

Shows what text the LLM would receive with:
- OLD approach: first 5000 chars of raw text (header + contestant arguments)
- NEW approach: CNSC analysis sections extracted from the text

No LLM API key needed — just shows the prompt inputs side by side.

Usage:
    python scripts/test_summaries_docs.py
"""

import sys
import re
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services.parser import parse_decision_text

DOCS_DIR = Path(__file__).parent.parent / "docs"


def extract_cnsc_sections(text: str) -> dict:
    """Extract CNSC analysis sections from raw decision text using regex.

    Mimics what ArgumentareCritica fields contain after LLM analysis.
    """
    result = {
        "elemente_retinute_cnsc": "",
        "argumentatie_cnsc": "",
        "dispozitiv": "",
    }

    # Pattern for CNSC analysis section
    # Common headers: "CONSILIUL", "Analizând", "În drept", "Examinând"
    patterns_cnsc = [
        r'(?:Analizând|Examinând|În ceea ce privește|Față de cele|Prin urmare|Consiliul reține|În drept|CONSILIUL)\s*[,:]?\s*(.*?)(?=\n\s*(?:PENTRU ACESTE MOTIVE|Pentru aceste motive|DECIDE|D I S P U N E|DISPUNE|În temeiul))',
        r'(?:Consiliul constată|Consiliul reține|Consiliul apreciază)\s*(.*?)(?=\n\s*(?:PENTRU ACESTE MOTIVE|Pentru aceste motive|DECIDE|D I S P U N E))',
    ]

    for pattern in patterns_cnsc:
        matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
        if matches:
            # Take the longest match as the main CNSC reasoning
            longest = max(matches, key=len)
            result["argumentatie_cnsc"] = longest.strip()[:3000]
            break

    # Extract dispositive (final decision)
    disp_patterns = [
        r'(?:PENTRU ACESTE MOTIVE|Pentru aceste motive).*?(?:DECIDE|D I S P U N E|DISPUNE)\s*[:\n]\s*(.*?)$',
        r'(?:DECIDE|D I S P U N E|DISPUNE)\s*[:\n]\s*(.*?)$',
    ]

    for pattern in disp_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            result["dispozitiv"] = match.group(1).strip()[:1500]
            break

    # Extract "elemente reținute" - facts before the legal reasoning
    elem_patterns = [
        r'(?:Din actele|Din documentele|Din probele|Prin analiza|Din examinarea|Situația de fapt|Starea de fapt)\s*(.*?)(?=\n\s*(?:Analizând|În drept|Consiliul|În ceea ce|Față de))',
    ]

    for pattern in elem_patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            result["elemente_retinute_cnsc"] = match.group(1).strip()[:2000]
            break

    return result


def main():
    decision_files = sorted(DOCS_DIR.glob("BO*.txt"))
    if not decision_files:
        print("No decision files found in docs/")
        return

    print(f"Found {len(decision_files)} decision files in docs/\n")

    for filepath in decision_files:
        external_id = filepath.stem.split("_CPV")[0]
        print(f"{'='*80}")
        print(f" DECIZIE: {filepath.name}")
        print(f"{'='*80}")

        text = filepath.read_text(encoding="utf-8")
        parsed = parse_decision_text(text, filepath.name)

        print(f"  Contestator: {parsed.contestator or 'N/A'}")
        print(f"  Autoritate:  {parsed.autoritate_contractanta or 'N/A'}")
        print(f"  Soluție:     {parsed.solutie_contestatie or 'N/A'}")
        print(f"  Obiect:      {(parsed.obiect_contract or 'N/A')[:100]}")
        print(f"  Text length: {len(text)} chars")

        # OLD approach: first 5000 chars
        old_text = text[:5000]
        print(f"\n  --- VECHI: Primele 5000 caractere ---")
        print(f"  Conține header:              {'Da' if 'DECIZIE' in old_text[:500] else 'Nu'}")
        print(f"  Conține argumente contest.:  {'Da' if any(kw in old_text for kw in ['contestat', 'solicitat', 'contestator']) else 'Nu'}")
        print(f"  Conține analiză CNSC:        {'Da' if any(kw in old_text for kw in ['Analizând', 'Consiliul reține', 'Consiliul constată', 'Examinând']) else 'Nu'}")
        print(f"  Conține dispozitiv:          {'Da' if any(kw in old_text for kw in ['DECIDE', 'DISPUNE', 'D I S P U N E', 'Admite', 'Respinge']) else 'Nu'}")
        # Show last 200 chars of the 5000 to see where it cuts off
        print(f"  Se termină cu: ...{old_text[-150:].replace(chr(10), ' ')}")

        # NEW approach: CNSC sections
        cnsc = extract_cnsc_sections(text)
        print(f"\n  --- NOU: Câmpuri CNSC extrase ---")
        print(f"  Elemente reținute: {len(cnsc['elemente_retinute_cnsc'])} chars")
        if cnsc["elemente_retinute_cnsc"]:
            print(f"    Preview: {cnsc['elemente_retinute_cnsc'][:200].replace(chr(10), ' ')}...")
        print(f"  Argumentație CNSC: {len(cnsc['argumentatie_cnsc'])} chars")
        if cnsc["argumentatie_cnsc"]:
            print(f"    Preview: {cnsc['argumentatie_cnsc'][:200].replace(chr(10), ' ')}...")
        print(f"  Dispozitiv:        {len(cnsc['dispozitiv'])} chars")
        if cnsc["dispozitiv"]:
            print(f"    Preview: {cnsc['dispozitiv'][:200].replace(chr(10), ' ')}...")

        # Quality verdict
        old_has_cnsc = any(kw in old_text for kw in ['Analizând', 'Consiliul reține', 'Consiliul constată'])
        new_has_cnsc = len(cnsc["argumentatie_cnsc"]) > 100 or len(cnsc["dispozitiv"]) > 50
        print(f"\n  ⚖️  VERDICT:")
        if not old_has_cnsc and new_has_cnsc:
            print(f"    ✅ NOU e mai bun — include analiza CNSC pe care VECHI o ratează")
        elif old_has_cnsc and new_has_cnsc:
            print(f"    🟡 Ambele conțin analiza CNSC — NOU e mai focalizat")
        elif not new_has_cnsc:
            print(f"    ⚠️  Regex-urile nu au extras secțiuni CNSC — în producție LLM-ul extrage mai bine")
        print()

    # Summary stats
    print(f"\n{'='*80}")
    print(f" CONCLUZIE")
    print(f"{'='*80}")
    print(f" Abordarea VECHE trimite primele 5000 chars → header + argumente contestator.")
    print(f" Abordarea NOUĂ trimite elemente_retinute_cnsc + argumentatie_cnsc din DB.")
    print(f" În producție, câmpurile sunt populate de LLM analysis (generate_analysis.py),")
    print(f" nu prin regex — calitatea extragerii e mult mai bună decât ce arată testul.")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
