#!/usr/bin/env python3
"""
=============================================================================
REJUST.RO - Script de descărcare în masă a hotărârilor judecătorești
=============================================================================

Descarcă hotărâri de pe portalul ReJust (rejust.ro) al CSM și le salvează
în format JSON structurat. Include:
- Toate metadatele disponibile (inclusiv dosar_nr extras din text, ECLI fallback)
- Referința de citare completă (câmp distinct)
- Textul complet al hotărârii
- Documentele din același dosar (înlănțuirea fond→apel→recurs)

Filtrare post-fetch:
- Stadiu procesual: doar Fond, Apel, Recurs + căi extraordinare de atac
- Categorie document: doar Hotărâre/Sentință/Decizie (exclude Încheieri)

Output: batch files JSON (500 records/fișier) în rejust_output/

Cerințe:
    pip install playwright
    playwright install chromium

Utilizare:
    python rejust_scraper.py                        # run standard
    python rejust_scraper.py --incremental          # doar de la ultimul run
    python rejust_scraper.py --since 2026-01-01     # de la o dată specifică
    python rejust_scraper.py --max-records 50       # oprește după 50 records
    python rejust_scraper.py --output-dir /tmp/out  # director custom

Autor: ExpertAP
Data: Aprilie 2026
=============================================================================
"""

import argparse
import json
import time
import os
import sys
import re
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ─── CONFIGURARE ────────────────────────────────────────────────────────────

# Filtrele de căutare - 9 soluții substanțiale (fond + apel + recurs)
# Ref: ghid tehnic rejust.ro, secțiunea 5.3
SEARCH_PARAMS = {
    "datastart": "2011-01-01",
    "dataend": "2026-04-04",
    "pagesize": 20,
    # Materii: 4 = Contencios administrativ şi fiscal
    "materii": [4],
    # Obiecte: achiziții publice (toate subtipurile)
    "obiecte": [5150, 5151, 8714, 8692, 5152, 5153, 8721, 8690, 8717, 8724,
                8720, 8719, 8716, 8723, 8715, 8718, 8722],
    # Soluții substanțiale (cu motivare pe fond):
    # Fond:   103=Alte soluții, 101=Admite, 102=Respinge, 999=Nespecificat, 309=Tranzacție
    # Apel:   203=Admitere apel
    # Recurs: 303=Respingere nefondat, 306=Respingere inadmisibil, 308=Alte soluții recurs
    "solutii": [103, 101, 102, 999, 309, 203, 303, 306, 308],
}

# Filtre post-fetch (aplicate după descărcarea datelor Single)
STADII_KEEP = {
    "Recurs", "Fond", "Apel",
    "Contestație în anulare - Recurs",
    "Contestație în anulare - Apel",
    "Revizuire - Recurs",
    "Judecare după casare/desfiinţare",
}

CATEGORII_KEEP = {
    "Hotarâre", "Hotărâre", "Sentință", "Sentinta",
    "Sentinţă civilă", "Decizie", "Decizia",
}

# Delay între cereri (secunde) - crește dacă apare CAPTCHA frecvent
DELAY_BETWEEN_PAGES = 3     # Între paginile de rezultate
DELAY_BETWEEN_CASES = 4     # Între hotărâri individuale
DELAY_AFTER_CAPTCHA = 30    # Așteptare după CAPTCHA

# Output
OUTPUT_DIR = "rejust_output"
PROGRESS_FILE = "progress.json"
LOG_FILE = "scraper.log"
BATCH_SIZE_FILE = 500        # records per output file

# ─── REGEX PATTERNS ────────────────────────────────────────────────────────

DOSAR_NR_PATTERN = re.compile(r'[Dd]osar\s+nr\.?\s*([\d]+/[\d]+/[\d]{4}(?:\*)?)')
ECLI_PATTERN = re.compile(r'(ECLI:RO:\w+:\d{4}:\d{3}\.\S+)')

# ─── LOGGING ────────────────────────────────────────────────────────────────

def setup_logging(output_dir: str):
    """Configure logging to stdout + file."""
    Path(output_dir).mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                os.path.join(output_dir, LOG_FILE), encoding='utf-8'
            )
        ]
    )

log = logging.getLogger(__name__)


# ─── FUNCȚII UTILITARE ──────────────────────────────────────────────────────

def extract_dosar_nr(text: str) -> str:
    """Extrage 'Dosar nr. 1234/3/2025' sau '1234/3/2025*' din text."""
    m = DOSAR_NR_PATTERN.search(text)
    return m.group(1) if m else ""


def extract_ecli(text: str, api_ecli: str | None) -> str:
    """Returnează ECLI din API sau, dacă e null/gol, extrage din text."""
    if api_ecli:
        return api_ecli
    m = ECLI_PATTERN.search(text)
    return m.group(1).rstrip('#') if m else ""


def build_citation(case_data: dict) -> str:
    """
    Construiește referința de citare completă din metadate.
    Format: 'Hotarâre nr. 197/2026 din 02.04.2026 pronunțată de Tribunalul Covasna,
             cod RJ 3g9483gg9 (https://rejust.ro/juris/3g9483gg9)'
    """
    d = case_data
    try:
        dt = datetime.fromisoformat(d["data_document"].replace("Z", ""))
        date_str = dt.strftime("%d.%m.%Y")
    except (ValueError, KeyError):
        date_str = "N/A"
    
    categorie = d.get("denumire_categorie", "Document")
    numar = d.get("numar_document", "")
    instanta = d.get("denumire_instanta", "")
    ids = d.get("ids", "")
    
    nr_part = f" nr. {numar}" if numar else ""
    citation = (
        f"{categorie}{nr_part} din {date_str} "
        f"pronunțată de {instanta}, "
        f"cod RJ {ids} (https://rejust.ro/juris/{ids})"
    )
    return citation


def format_date(iso_date: str) -> str:
    """Convertește data ISO în format românesc DD.MM.YYYY."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", ""))
        return dt.strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return ""


def structure_case(case_data: dict, related_cases: list) -> dict:
    """Structurează datele unei hotărâri în formatul final JSON."""
    d = case_data
    text = d.get("docs_contentsPUB", "")
    return {
        # ── Câmp distinct pentru citare (OBLIGATORIU) ──
        "referinta_citare": build_citation(d),

        # ── Identificatori ──
        "cod_rj": d.get("ids", ""),
        "url": f"https://rejust.ro/juris/{d.get('ids', '')}",
        "numar_document": d.get("numar_document", ""),
        "data_document": d.get("data_document", ""),
        "data_document_formatata": format_date(d.get("data_document", "")),
        "cod_ecli": extract_ecli(text, d.get("codECLI") or None),
        "dosar_nr": extract_dosar_nr(text),

        # ── Clasificare juridică ──
        "denumire_instanta": d.get("denumire_instanta", ""),
        "denumire_materie": d.get("denumire_materie", ""),
        "denumire_obiect": d.get("denumire_obiectdosar", ""),
        "denumire_categorie": d.get("denumire_categorie", ""),
        "stadiu_procesual": d.get("denumire_stadiuprocesual", ""),
        "solutie": d.get("denumire_solutie", ""),

        # ── Textul complet al hotărârii ──
        "text_hotarare": text,

        # ── Înlănțuirea dosarului (fond → apel → recurs) ──
        "documente_dosar": [
            {
                "cod_rj": r.get("id", ""),
                "url": f"https://rejust.ro/juris/{r.get('id', '')}",
                "data_document": r.get("data_document", ""),
                "data_document_formatata": format_date(r.get("data_document", "")),
                "denumire_instanta": r.get("denumire_instanta", ""),
                "denumire_categorie": r.get("denumire_categorie", ""),
                "stadiu_procesual": r.get("nume_stadiu", ""),
                "solutie": r.get("denumire_solutie", ""),
                "materie": r.get("nume_materie", ""),
                "obiect": r.get("nume_obiect", ""),
                "descriere": (
                    f"{r.get('denumire_categorie', '')} din "
                    f"{format_date(r.get('data_document', ''))}, "
                    f"{r.get('denumire_instanta', '')} | "
                    f"Stadiu: {r.get('nume_stadiu', '')}, "
                    f"Soluție: {r.get('denumire_solutie', '')}"
                ),
            }
            for r in related_cases
        ],
        "numar_documente_dosar": len(related_cases)
    }


# ─── CLASA PRINCIPALĂ ───────────────────────────────────────────────────────

class RejustScraper:
    """
    Scraper pentru rejust.ro bazat pe Playwright.

    Strategia:
    1. Deschide browser real, utilizatorul se autentifică manual
    2. Instalează un interceptor pe fetch() pentru a captura răspunsurile API
    3. Navighează prin Blazor.navigateTo() pentru fiecare hotărâre
    4. Capturează Single + PublicRelatedCases
    5. Filtrează post-fetch: stadiu, categorie
    6. Salvează progresiv în batch files JSON (500 records/fișier)
    """

    def __init__(self, output_dir: str = OUTPUT_DIR, max_records: int = 0):
        self.output_dir = output_dir
        self.max_records = max_records
        self.results = []          # current batch buffer
        self.total_saved = 0       # total records across all batch files
        self.failed_ids = []
        self.processed_ids = set()
        self.all_case_ids = []
        self.batch_files = []      # list of written batch filenames

        # Stats counters
        self.stats_skipped_stadiu = Counter()
        self.stats_skipped_categorie = Counter()
        self.stats_instanta = Counter()
        self.stats_stadiu = Counter()
        self.stats_solutie = Counter()

        # Creează directorul de output
        Path(self.output_dir).mkdir(exist_ok=True)

        # Încarcă progresul anterior dacă există
        self._load_progress()

    def _load_progress(self):
        """Încarcă progresul din sesiunea anterioară."""
        progress_path = os.path.join(self.output_dir, PROGRESS_FILE)

        if os.path.exists(progress_path):
            with open(progress_path, 'r', encoding='utf-8') as f:
                progress = json.load(f)
            self.processed_ids = set(progress.get("processed_ids", []))
            self.failed_ids = progress.get("failed_ids", [])
            self.all_case_ids = progress.get("all_case_ids", [])
            self.batch_files = progress.get("batch_files", [])
            self.total_saved = progress.get("total_saved", 0)
            log.info(f"Progres anterior: {len(self.processed_ids)} procesate, "
                     f"{len(self.failed_ids)} eșuate, "
                     f"{len(self.all_case_ids)} ID-uri totale, "
                     f"{self.total_saved} salvate în {len(self.batch_files)} batch-uri")

    def _save_progress(self):
        """Salvează progresul curent."""
        progress_path = os.path.join(self.output_dir, PROGRESS_FILE)
        with open(progress_path, 'w', encoding='utf-8') as f:
            json.dump({
                "processed_ids": list(self.processed_ids),
                "failed_ids": self.failed_ids,
                "all_case_ids": self.all_case_ids,
                "batch_files": self.batch_files,
                "total_saved": self.total_saved,
                "last_update": datetime.now().isoformat(),
                "search_params": SEARCH_PARAMS,
            }, f, ensure_ascii=False, indent=2)

    def _flush_batch(self):
        """Scrie buffer-ul curent într-un fișier batch."""
        if not self.results:
            return
        batch_num = len(self.batch_files)
        filename = f"hotarari_batch_{batch_num:03d}.json"
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        self.batch_files.append(filename)
        self.total_saved += len(self.results)
        log.info(f"  💾 Batch {batch_num}: {len(self.results)} hotărâri → {filename} "
                 f"(total: {self.total_saved})")
        self.results = []

    def _save_results(self):
        """Salvează rezultatele curente (flush batch dacă e nevoie)."""
        if self.results:
            self._flush_batch()
        self._save_progress()
    
    def _install_interceptor(self, page):
        """Instalează interceptorul fetch în pagină."""
        page.evaluate("""() => {
            const _origFetch = window.fetch.__original || window.fetch;
            window._capturedSingle = null;
            window._capturedRelated = null;
            window._capturedSearch = null;
            window._interceptorActive = true;
            
            window.fetch = function(input, init) {
                const url = typeof input === 'string' ? input : (input?.url || '');
                const result = _origFetch.apply(this, arguments);
                
                if(url.includes('/api/cases/Single')) {
                    result.then(r => {
                        const cloned = r.clone();
                        cloned.json().then(data => {
                            window._capturedSingle = data;
                        }).catch(() => {});
                    }).catch(() => {});
                }
                
                if(url.includes('/api/Cases/PublicRelatedCases')) {
                    result.then(r => {
                        const cloned = r.clone();
                        cloned.json().then(data => {
                            window._capturedRelated = data;
                        }).catch(() => {});
                    }).catch(() => {});
                }
                
                if(url.includes('/api/cases/PublicCases')) {
                    result.then(r => {
                        const cloned = r.clone();
                        cloned.json().then(data => {
                            window._capturedSearch = data;
                        }).catch(() => {});
                    }).catch(() => {});
                }
                
                return result;
            };
            window.fetch.__original = _origFetch;
        }""")
    
    def _check_captcha(self, page) -> bool:
        """Verifică dacă apare CAPTCHA Cloudflare Turnstile."""
        try:
            captcha = page.query_selector('.cf-turnstile')
            if captcha and captcha.is_visible():
                return True
        except:
            pass
        return False
    
    def _wait_for_captcha_resolution(self, page):
        """Așteaptă ca utilizatorul să rezolve CAPTCHA-ul."""
        log.warning("⚠️  CAPTCHA detectat! Rezolvă-l manual în browser...")
        while self._check_captcha(page):
            time.sleep(2)
        log.info("✅ CAPTCHA rezolvat, continuu...")
        time.sleep(DELAY_AFTER_CAPTCHA)
        # Reinstalează interceptorul după CAPTCHA
        self._install_interceptor(page)
    
    def _navigate_and_capture(self, page, case_id: str) -> dict:
        """
        Navighează la o hotărâre prin Blazor și capturează datele.
        Returnează dict cu caseData și relatedCases.
        """
        # Reset captured data
        page.evaluate("""() => {
            window._capturedSingle = null;
            window._capturedRelated = null;
        }""")
        
        # Navighează prin Blazor
        page.evaluate(f"""() => {{
            Blazor.navigateTo('/juris/{case_id}');
        }}""")
        
        # Așteaptă datele Single (timeout 20s)
        for _ in range(40):
            time.sleep(0.5)
            
            # Verifică CAPTCHA
            if self._check_captcha(page):
                self._wait_for_captcha_resolution(page)
                # Retry navigation
                return self._navigate_and_capture(page, case_id)
            
            data = page.evaluate("window._capturedSingle")
            if data and data.get("ids"):
                break
        
        single_data = page.evaluate("window._capturedSingle")
        
        if not single_data or not single_data.get("ids"):
            log.warning(f"  ❌ Timeout/eroare pentru {case_id}")
            return None
        
        # Acum capturează related cases prin API direct
        # (Blazor nu le apelează automat de fiecare dată)
        related_data = page.evaluate(f"""() => {{
            return new Promise((resolve) => {{
                window._capturedRelated = null;
                // Trigger the related cases fetch
                const origFetch = window.fetch.__original || window.fetch;
                origFetch('https://rejust.ro/api/Cases/PublicRelatedCases?Id={case_id}')
                    .then(r => r.json())
                    .then(data => resolve(data))
                    .catch(() => resolve([]));
            }});
        }}""")
        
        # Dacă related_data eșuează, încearcă din DOM
        if not related_data or (isinstance(related_data, dict) and related_data.get("StatusCode")):
            related_data = self._extract_related_from_dom(page, case_id)
        
        return {
            "caseData": single_data,
            "relatedCases": related_data if isinstance(related_data, list) else []
        }
    
    def _extract_related_from_dom(self, page, current_id: str) -> list:
        """Extrage documentele legate din DOM-ul paginii."""
        try:
            return page.evaluate(f"""() => {{
                const links = document.querySelectorAll('a[href*="juris/"]');
                const results = [];
                links.forEach(a => {{
                    const href = a.getAttribute('href') || '';
                    const match = href.match(/juris\/([a-z0-9]+)/);
                    if(match) {{
                        const id = match[1];
                        const texts = [];
                        a.querySelectorAll('div, span').forEach(el => texts.push(el.textContent.trim()));
                        results.push({{
                            id: id,
                            _domText: texts.join(' | ')
                        }});
                    }}
                }});
                return results;
            }}""")
        except:
            return []
    
    def collect_all_ids(self, page):
        """
        Colectează toate ID-urile din paginile de rezultate.
        Navighează prin pagina de căutare și extrage ID-urile din DOM.
        """
        if self.all_case_ids:
            log.info(f"ID-uri deja colectate din sesiunea anterioară: {len(self.all_case_ids)}")
            return
        
        log.info("Colectez ID-urile din paginile de rezultate...")
        
        # Navighează la pagina de căutare
        page.evaluate("Blazor.navigateTo('/')")
        time.sleep(3)
        
        all_ids = []
        page_index = 0
        
        while True:
            # Extrage ID-urile din pagina curentă
            ids_on_page = page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="juris/"]');
                const ids = new Set();
                links.forEach(a => {
                    const match = a.href.match(/juris\/([a-z0-9]+)/);
                    if(match) ids.add(match[1]);
                });
                return [...ids];
            }""")
            
            if not ids_on_page:
                break
            
            new_ids = [id for id in ids_on_page if id not in all_ids]
            all_ids.extend(new_ids)
            log.info(f"  Pagina {page_index}: {len(new_ids)} ID-uri noi, total: {len(all_ids)}")
            
            # Click pe "Mai multe rezultate"
            more_btn = page.query_selector('button:has-text("Mai multe rezultate")')
            if not more_btn:
                log.info("Nu mai sunt rezultate.")
                break
            
            more_btn.click()
            time.sleep(DELAY_BETWEEN_PAGES)
            
            # Verifică CAPTCHA
            if self._check_captcha(page):
                self._wait_for_captcha_resolution(page)
            
            page_index += 1
        
        self.all_case_ids = all_ids
        self._save_progress()
        log.info(f"Total ID-uri colectate: {len(all_ids)}")
    
    def _process_case(self, page, case_id: str) -> bool:
        """
        Procesează o singură hotărâre: descarcă, filtrează, structurează.
        Returnează True dacă a fost acceptată și adăugată la results.
        """
        result = self._navigate_and_capture(page, case_id)

        if not result or not result["caseData"] or not result["caseData"].get("ids"):
            self.failed_ids.append(case_id)
            log.warning(f"  ❌ Eșuat: {case_id}")
            return False

        case_data = result["caseData"]

        # ── Filtru post-fetch: stadiu procesual ──
        stadiu = case_data.get("denumire_stadiuprocesual", "")
        if stadiu not in STADII_KEEP:
            log.info(f"  ⏭️  Skip (stadiu: {stadiu})")
            self.processed_ids.add(case_id)
            self.stats_skipped_stadiu[stadiu] += 1
            return False

        # ── Filtru post-fetch: categorie document ──
        categorie = case_data.get("denumire_categorie", "")
        if categorie not in CATEGORII_KEEP:
            log.info(f"  ⏭️  Skip (categorie: {categorie})")
            self.processed_ids.add(case_id)
            self.stats_skipped_categorie[categorie] += 1
            return False

        # ── Structurare și salvare ──
        structured = structure_case(case_data, result["relatedCases"])
        self.results.append(structured)
        self.processed_ids.add(case_id)

        # Stats
        self.stats_instanta[structured["denumire_instanta"]] += 1
        self.stats_stadiu[stadiu] += 1
        self.stats_solutie[structured["solutie"]] += 1

        log.info(f"  ✅ {structured['referinta_citare'][:80]}...")
        return True

    def _print_stats(self):
        """Afișează statisticile run-ului."""
        log.info("=" * 60)
        log.info(f"📊 STATISTICI RUN")
        log.info(f"   Total hotărâri salvate: {self.total_saved}")
        log.info(f"   Batch files: {len(self.batch_files)}")
        log.info(f"   Eșuate: {len(self.failed_ids)}")

        if self.stats_instanta:
            log.info(f"\n   Per instanță (top 10):")
            for inst, cnt in self.stats_instanta.most_common(10):
                log.info(f"     {inst}: {cnt}")

        if self.stats_stadiu:
            log.info(f"\n   Per stadiu:")
            for st, cnt in self.stats_stadiu.most_common():
                log.info(f"     {st}: {cnt}")

        if self.stats_solutie:
            log.info(f"\n   Per soluție (top 10):")
            for sol, cnt in self.stats_solutie.most_common(10):
                log.info(f"     {sol}: {cnt}")

        if self.stats_skipped_stadiu:
            log.info(f"\n   Skipped (stadiu invalid):")
            for st, cnt in self.stats_skipped_stadiu.most_common():
                log.info(f"     {st}: {cnt}")

        if self.stats_skipped_categorie:
            log.info(f"\n   Skipped (categorie invalidă):")
            for cat, cnt in self.stats_skipped_categorie.most_common():
                log.info(f"     {cat}: {cnt}")

        log.info("=" * 60)

    def scrape_all(self):
        """Procesul principal de scraping."""
        with sync_playwright() as p:
            # Lansează browser cu profil persistent (păstrează sesiunea)
            user_data_dir = os.path.join(self.output_dir, "browser_profile")

            browser_context = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,
                viewport={"width": 1280, "height": 800},
                locale="ro-RO"
            )

            page = browser_context.pages[0] if browser_context.pages else browser_context.new_page()

            # Navighează la rejust.ro
            page.goto("https://rejust.ro/")

            # Așteaptă autentificarea utilizatorului
            log.info("=" * 60)
            log.info("INSTRUCȚIUNI:")
            log.info("1. Autentifică-te pe rejust.ro în fereastra browser-ului")
            log.info("2. Setează filtrele de căutare dorite")
            log.info("3. Rezolvă CAPTCHA-ul dacă apare")
            log.info("4. Apasă ENTER în terminal când ești gata")
            log.info("=" * 60)
            input("\n>>> Apasă ENTER când ești autentificat și ai filtrele setate... ")

            # Instalează interceptorul
            self._install_interceptor(page)

            # PASUL 1: Colectează toate ID-urile
            self.collect_all_ids(page)

            # PASUL 2: Descarcă fiecare hotărâre
            remaining = [id for id in self.all_case_ids if id not in self.processed_ids]
            if self.max_records > 0:
                remaining = remaining[:self.max_records]
            log.info(f"De procesat: {len(remaining)} hotărâri "
                     f"(din {len(self.all_case_ids)} total)")

            for i, case_id in enumerate(remaining):
                log.info(f"[{i+1}/{len(remaining)}] Descarc: {case_id}")

                accepted = self._process_case(page, case_id)

                # Flush batch la BATCH_SIZE_FILE records
                if accepted and len(self.results) >= BATCH_SIZE_FILE:
                    self._flush_batch()
                    self._save_progress()

                # Salvează progresiv la fiecare 10 hotărâri
                if (i + 1) % 10 == 0:
                    self._save_progress()

                # Verifică max_records (count doar pe cele acceptate)
                if self.max_records > 0 and self.total_saved + len(self.results) >= self.max_records:
                    log.info(f"Max records ({self.max_records}) atins, opresc.")
                    break

                time.sleep(DELAY_BETWEEN_CASES)

            # Salvare finală
            self._save_results()

            # Statistici
            self._print_stats()

            # Retry failed
            if self.failed_ids:
                retry = input(f"\nDorești să reîncerci cele {len(self.failed_ids)} eșuate? (da/nu): ")
                if retry.lower() in ('da', 'yes', 'y'):
                    retry_ids = self.failed_ids.copy()
                    self.failed_ids = []
                    for case_id in retry_ids:
                        log.info(f"  Retry: {case_id}")
                        self._process_case(page, case_id)
                        time.sleep(DELAY_BETWEEN_CASES)
                    self._save_results()

            browser_context.close()


# ─── ENTRY POINT ────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Descarcă hotărâri judecătorești de pe rejust.ro"
    )
    parser.add_argument(
        "--incremental", action="store_true",
        help="Doar hotărâri noi de la ultimul run (citește datastart din progress.json)"
    )
    parser.add_argument(
        "--since", type=str,
        help="Override data de start (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--output-dir", default=OUTPUT_DIR,
        help=f"Director de output (default: {OUTPUT_DIR})"
    )
    parser.add_argument(
        "--max-records", type=int, default=0,
        help="Oprește după N records acceptate (0=nelimitat)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Setup logging (needs output dir first)
    setup_logging(args.output_dir)

    # Incremental mode: citește ultima dată din progress.json
    if args.incremental:
        progress_path = os.path.join(args.output_dir, PROGRESS_FILE)
        if os.path.exists(progress_path):
            with open(progress_path, 'r', encoding='utf-8') as f:
                progress = json.load(f)
            last_update = progress.get("last_update", "")
            if last_update:
                # Folosește data ultimului run ca datastart
                dt = datetime.fromisoformat(last_update)
                SEARCH_PARAMS["datastart"] = dt.strftime("%Y-%m-%d")
                log.info(f"Mod incremental: datastart={SEARCH_PARAMS['datastart']}")
        else:
            log.info("Mod incremental dar nu există progress.json, rulează complet.")

    # Override --since
    if args.since:
        SEARCH_PARAMS["datastart"] = args.since
        log.info(f"Override datastart: {args.since}")

    scraper = RejustScraper(
        output_dir=args.output_dir,
        max_records=args.max_records,
    )
    scraper.scrape_all()
