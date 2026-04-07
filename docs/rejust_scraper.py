#!/usr/bin/env python3
"""
=============================================================================
REJUST.RO - Script de descărcare în masă a hotărârilor judecătorești
=============================================================================

Acest script descarcă hotărâri de pe portalul ReJust (rejust.ro) al CSM
și le salvează în format JSON structurat, inclusiv:
- Toate metadatele disponibile
- Referința de citare completă (câmp distinct)
- Textul complet al hotărârii
- Documentele din același dosar (înlănțuirea fond→apel→recurs)

Cerințe:
    pip install playwright
    playwright install chromium

Utilizare:
    python rejust_scraper.py

Autor: Script generat automat
Data: Aprilie 2026
=============================================================================
"""

import json
import time
import os
import sys
import re
import logging
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ─── CONFIGURARE ────────────────────────────────────────────────────────────

# Filtrele de căutare (modifică conform nevoilor tale)
# Acestea corespund filtrelor pe care le-ai setat pe rejust.ro
SEARCH_PARAMS = {
    "datastart": "2011-01-01",
    "dataend": "2026-04-04",
    "pagesize": 20,
    # Materii, obiecte, soluții - copiază din URL-ul generat de rejust.ro
    # Exemplu pentru "Contencios administrativ - achiziții publice":
    "materii": [4],
    "obiecte": [5150, 5151, 8714, 8692, 5152, 5153, 8721, 8690, 8717, 8724,
                8720, 8719, 8716, 8723, 8715, 8718, 8722],
    "solutii": [103, 999, 101, 203, 102, 308, 303, 306, 105, 309, 311, 104],
}

# Delay între cereri (secunde) - crește dacă apare CAPTCHA frecvent
DELAY_BETWEEN_PAGES = 3     # Între paginile de rezultate
DELAY_BETWEEN_CASES = 4     # Între hotărâri individuale
DELAY_AFTER_CAPTCHA = 30    # Așteptare după CAPTCHA

# Directorul de output
OUTPUT_DIR = "rejust_output"
OUTPUT_FILE = "hotarari.json"
PROGRESS_FILE = "progress.json"
LOG_FILE = "scraper.log"

# ─── LOGGING ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(OUTPUT_DIR, LOG_FILE) if os.path.exists(OUTPUT_DIR) else LOG_FILE, encoding='utf-8')
    ]
)
log = logging.getLogger(__name__)


# ─── FUNCȚII UTILITARE ──────────────────────────────────────────────────────

def build_search_url(page_index: int) -> str:
    """Construiește URL-ul API de căutare cu filtrele configurate."""
    params = f"datastart={SEARCH_PARAMS['datastart']}"
    params += f"&dataend={SEARCH_PARAMS['dataend']}"
    params += f"&pageindex={page_index}"
    params += f"&pagesize={SEARCH_PARAMS['pagesize']}"
    
    for obj_id in SEARCH_PARAMS.get("obiecte", []):
        params += f"&obiecte={obj_id}"
    for mat_id in SEARCH_PARAMS.get("materii", []):
        params += f"&materii={mat_id}"
    for sol_id in SEARCH_PARAMS.get("solutii", []):
        params += f"&solutii={sol_id}"
    
    return f"https://rejust.ro/api/cases/PublicCases?{params}"


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
    return {
        # ── Câmp distinct pentru citare (OBLIGATORIU) ──
        "referinta_citare": build_citation(d),
        
        # ── Identificatori ──
        "cod_rj": d.get("ids", ""),
        "url": f"https://rejust.ro/juris/{d.get('ids', '')}",
        "numar_document": d.get("numar_document", ""),
        "data_document": d.get("data_document", ""),
        "data_document_formatata": format_date(d.get("data_document", "")),
        "cod_ecli": d.get("codECLI", "") or None,
        
        # ── Clasificare juridică ──
        "denumire_instanta": d.get("denumire_instanta", ""),
        "denumire_materie": d.get("denumire_materie", ""),
        "denumire_obiect": d.get("denumire_obiectdosar", ""),
        "denumire_categorie": d.get("denumire_categorie", ""),
        "stadiu_procesual": d.get("denumire_stadiuprocesual", ""),
        "solutie": d.get("denumire_solutie", ""),
        
        # ── Textul complet al hotărârii ──
        "text_hotarare": d.get("docs_contentsPUB", ""),
        
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
                "obiect": r.get("nume_obiect", "")
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
    5. Salvează progresiv în JSON
    """
    
    def __init__(self):
        self.results = []
        self.failed_ids = []
        self.processed_ids = set()
        self.all_case_ids = []
        
        # Creează directorul de output
        Path(OUTPUT_DIR).mkdir(exist_ok=True)
        
        # Încarcă progresul anterior dacă există
        self._load_progress()
    
    def _load_progress(self):
        """Încarcă progresul din sesiunea anterioară."""
        progress_path = os.path.join(OUTPUT_DIR, PROGRESS_FILE)
        output_path = os.path.join(OUTPUT_DIR, OUTPUT_FILE)
        
        if os.path.exists(progress_path):
            with open(progress_path, 'r', encoding='utf-8') as f:
                progress = json.load(f)
            self.processed_ids = set(progress.get("processed_ids", []))
            self.failed_ids = progress.get("failed_ids", [])
            self.all_case_ids = progress.get("all_case_ids", [])
            log.info(f"Progres anterior: {len(self.processed_ids)} procesate, "
                     f"{len(self.failed_ids)} eșuate, "
                     f"{len(self.all_case_ids)} ID-uri totale")
        
        if os.path.exists(output_path):
            with open(output_path, 'r', encoding='utf-8') as f:
                self.results = json.load(f)
            log.info(f"Rezultate anterioare: {len(self.results)} hotărâri")
    
    def _save_progress(self):
        """Salvează progresul curent."""
        progress_path = os.path.join(OUTPUT_DIR, PROGRESS_FILE)
        with open(progress_path, 'w', encoding='utf-8') as f:
            json.dump({
                "processed_ids": list(self.processed_ids),
                "failed_ids": self.failed_ids,
                "all_case_ids": self.all_case_ids,
                "last_update": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def _save_results(self):
        """Salvează rezultatele în JSON."""
        output_path = os.path.join(OUTPUT_DIR, OUTPUT_FILE)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        log.info(f"Salvat {len(self.results)} hotărâri în {output_path}")
    
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
    
    def scrape_all(self):
        """Procesul principal de scraping."""
        with sync_playwright() as p:
            # Lansează browser cu profil persistent (păstrează sesiunea)
            user_data_dir = os.path.join(OUTPUT_DIR, "browser_profile")
            
            browser_context = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=False,  # IMPORTANT: browser vizibil pentru autentificare + CAPTCHA
                viewport={"width": 1280, "height": 800},
                locale="ro-RO"
            )
            
            page = browser_context.pages[0] if browser_context.pages else browser_context.new_page()
            
            # Navighează la rejust.ro
            page.goto("https://rejust.ro/")
            
            # Așteaptă autentificarea utilizatorului
            log.info("=" * 60)
            log.info("📋 INSTRUCȚIUNI:")
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
            log.info(f"De procesat: {len(remaining)} hotărâri (din {len(self.all_case_ids)} total)")
            
            for i, case_id in enumerate(remaining):
                log.info(f"[{i+1}/{len(remaining)}] Descarc: {case_id}")
                
                result = self._navigate_and_capture(page, case_id)
                
                if result and result["caseData"] and result["caseData"].get("ids"):
                    structured = structure_case(result["caseData"], result["relatedCases"])
                    self.results.append(structured)
                    self.processed_ids.add(case_id)
                    log.info(f"  ✅ {structured['referinta_citare'][:80]}...")
                else:
                    self.failed_ids.append(case_id)
                    log.warning(f"  ❌ Eșuat: {case_id}")
                
                # Salvează progresiv la fiecare 10 hotărâri
                if (i + 1) % 10 == 0:
                    self._save_results()
                    self._save_progress()
                    log.info(f"  💾 Progres salvat: {len(self.results)} hotărâri")
                
                time.sleep(DELAY_BETWEEN_CASES)
            
            # Salvare finală
            self._save_results()
            self._save_progress()
            
            log.info("=" * 60)
            log.info(f"✅ FINALIZAT!")
            log.info(f"   Hotărâri descărcate: {len(self.results)}")
            log.info(f"   Eșuate: {len(self.failed_ids)}")
            log.info(f"   Fișier: {os.path.join(OUTPUT_DIR, OUTPUT_FILE)}")
            log.info("=" * 60)
            
            # Retry failed
            if self.failed_ids:
                retry = input(f"\nDorești să reîncerci cele {len(self.failed_ids)} eșuate? (da/nu): ")
                if retry.lower() in ('da', 'yes', 'y'):
                    retry_ids = self.failed_ids.copy()
                    self.failed_ids = []
                    for case_id in retry_ids:
                        log.info(f"  Retry: {case_id}")
                        result = self._navigate_and_capture(page, case_id)
                        if result and result["caseData"] and result["caseData"].get("ids"):
                            structured = structure_case(result["caseData"], result["relatedCases"])
                            self.results.append(structured)
                            self.processed_ids.add(case_id)
                        else:
                            self.failed_ids.append(case_id)
                        time.sleep(DELAY_BETWEEN_CASES)
                    self._save_results()
                    self._save_progress()
            
            browser_context.close()


# ─── ENTRY POINT ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    scraper = RejustScraper()
    scraper.scrape_all()
