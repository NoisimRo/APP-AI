"""Training material generator service for ExpertAP.

Generates didactic materials for public procurement training,
grounded in real legislation (legislatie_fragmente) and CNSC
jurisprudence (argumentare_critica) via RAG.
"""

from typing import Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.decision import (
    ArgumentareCritica, DecizieCNSC,
    LegislatieFragment, ActNormativ,
)
from app.services.embedding import EmbeddingService
from app.services.llm.gemini import GeminiProvider

logger = get_logger(__name__)

# --- Material type definitions ---

MATERIAL_TYPES = {
    "speta": {
        "name": "Speță practică",
        "description": "Scenariu realist ce necesită analiză juridică",
    },
    "studiu_caz": {
        "name": "Studiu de caz",
        "description": "Analiză aprofundată a unei situații complexe cu multiple perspective",
    },
    "situational": {
        "name": "Întrebări situaționale",
        "description": "Scenarii decizionale de tip 'Ce ați face dacă...'",
    },
    "palarii": {
        "name": "Pălăriile Gânditoare",
        "description": "Analiza unui scenariu din 6 perspective (de Bono)",
    },
    "dezbatere": {
        "name": "Dezbatere Pro & Contra",
        "description": "Argumente pentru și împotriva unei decizii de achiziție",
    },
    "quiz": {
        "name": "Quiz cu variante",
        "description": "Întrebări cu răspunsuri multiple (A/B/C/D)",
    },
    "joc_rol": {
        "name": "Joc de rol",
        "description": "Scenarii cu roluri și instrucțiuni per participant",
    },
    "erori": {
        "name": "Identificare erori",
        "description": "Document cu greșeli deliberate de identificat",
    },
    "comparativ": {
        "name": "Analiză comparativă",
        "description": "Compararea a două abordări pe aceeași temă",
    },
    "cronologie": {
        "name": "Cronologie procedurală",
        "description": "Ordonarea pașilor într-o procedură de achiziție",
    },
    "program_formare": {
        "name": "Program complet de formare",
        "description": "Program structurat cu multiple materiale didactice variate",
    },
}

DIFFICULTY_LEVELS = {
    "usor": "Ușor — concepte de bază, situații clare, un singur act normativ relevant",
    "mediu": "Mediu — necesită cunoașterea mai multor prevederi legale, situații nuanțate",
    "dificil": "Dificil — scenarii complexe cu interese conflictuale, multiple acte normative",
    "foarte_dificil": "Foarte Dificil — cazuri limită, jurisprudență contradictorie, dispute multi-părți",
}

LENGTH_OPTIONS = {
    "scurt": "~200 cuvinte PER SECȚIUNE (enunț concis, cerințe clare, rezolvare succintă dar completă, note trainer esențiale)",
    "mediu": "~400 cuvinte PER SECȚIUNE (detalii suficiente, argumentare solidă, exemple relevante)",
    "lung": "~800 cuvinte PER SECȚIUNE (analiză detaliată, multiple exemple, argumentare extinsă)",
    "extins": "~1500 cuvinte PER SECȚIUNE (analiză exhaustivă, jurisprudență multiplă, toate perspectivele acoperite)",
}

# --- Type-specific prompt templates ---

MATERIAL_PROMPTS = {
    "speta": """Generează o SPEȚĂ PRACTICĂ pe tema specificată.

Structură obligatorie:
## Enunț
Prezintă situația de fapt într-un mod realist, cu detalii concrete (nume fictive de entități, sume, termene, coduri CPV). Scenariul trebuie să fie plauzibil și ancorat în practica achizițiilor publice din România.

## Cerințe
Formulează 2-4 cerințe/întrebări clare pe care participanții trebuie să le rezolve.

## Rezolvare
Oferă rezolvarea detaliată a fiecărei cerințe, cu:
- Referire la articole de lege exacte (art. X alin. (Y) lit. Z din Legea 98/2016, HG 395/2016, etc.)
- Referire la jurisprudența CNSC relevantă din contextul furnizat
- Argumentație juridică pas cu pas

## Note Trainer
Sfaturi pentru trainer: puncte cheie de subliniat, greșeli frecvente ale participanților, variante de discuție, timp alocat recomandat.""",

    "studiu_caz": """Generează un STUDIU DE CAZ pe tema specificată.

Structură obligatorie:
## Enunț
Prezintă un caz complex și detaliat, cu multiple părți implicate (autoritate contractantă, ofertanți, contestator, CNSC). Include documente, cronologie, sume, termene concrete.

## Cerințe
Formulează 3-5 cerințe de analiză care necesită abordare din mai multe perspective.

## Rezolvare
Analiză aprofundată cu:
- Identificarea problemelor juridice
- Analiza din perspectiva fiecărei părți
- Temeiuri legale exacte (articole, alineate)
- Referire la jurisprudența CNSC relevantă
- Concluzia fundamentată

## Note Trainer
Sugestii de moderare: cum să ghidezi discuția, ce perspective suplimentare pot apărea, cum să gestionezi opiniile divergente, timp alocat per secțiune.""",

    "situational": """Generează ÎNTREBĂRI SITUAȚIONALE pe tema specificată.

Structură obligatorie:
## Enunț
Prezintă 3-5 scenarii situaționale distincte de tipul "Ce ați face dacă..." sau "Cum ați proceda în situația în care...". Fiecare scenariu trebuie să fie realist și să pună participantul într-o poziție decizională concretă.

## Cerințe
Pentru fiecare scenariu, specifică ce trebuie să decidă/argumenteze participantul.

## Rezolvare
Pentru fiecare situație:
- Răspunsul corect/optim cu argumentare
- Acțiunile concrete recomandate
- Temeiuri legale (articole exacte)
- Ce riscuri implică alternativele greșite
- Jurisprudență CNSC relevantă

## Note Trainer
Indicii pentru discuție, scenarii derivate posibile, puncte de evaluare ale răspunsurilor participanților.""",

    "palarii": """Generează un exercițiu cu PĂLĂRIILE GÂNDITOARE (Edward de Bono) pe tema specificată.

Structură obligatorie:
## Enunț
Prezintă un scenariu din achiziții publice care poate fi analizat din multiple perspective. Scenariul trebuie să fie suficient de complex pentru a genera analiză din toate cele 6 perspective.

## Cerințe
Instrucțiuni pentru participanți: împărțirea pe grupe/pălării, timp alocat per pălărie, format de prezentare.

Cele 6 pălării:
- 🟡 **Pălăria Galbenă (Optimism)** — beneficii, avantaje, oportunități
- ⚫ **Pălăria Neagră (Precauție)** — riscuri, probleme, pericole juridice
- 🔴 **Pălăria Roșie (Emoție)** — intuiție, reacții, impactul uman
- ⚪ **Pălăria Albă (Fapte)** — date obiective, informații, legislație aplicabilă
- 🟢 **Pălăria Verde (Creativitate)** — soluții alternative, idei noi
- 🔵 **Pălăria Albastră (Proces)** — organizare, concluzii, pași următori

## Rezolvare
Exemple de analiză completă din perspectiva fiecărei pălării, cu referire la legislație și jurisprudență unde este cazul.

## Note Trainer
Cum să moderezi exercițiul, cât timp per pălărie, cum să sintetizezi concluziile grupurilor.""",

    "dezbatere": """Generează un exercițiu de DEZBATERE PRO & CONTRA pe tema specificată.

Structură obligatorie:
## Enunț
Formulează o teză/afirmație controversată din domeniul achizițiilor publice care poate fi argumentată convingător din ambele părți. Oferă contextul factual necesar.

## Cerințe
Instrucțiuni pentru organizarea dezbaterii: împărțirea în echipe PRO și CONTRA, timp de pregătire, format de prezentare, reguli de dezbatere.

## Rezolvare
### Argumente PRO (cu temeiuri legale)
- Argument 1 + temei legal
- Argument 2 + temei legal
- Jurisprudență CNSC favorabilă

### Argumente CONTRA (cu temeiuri legale)
- Argument 1 + temei legal
- Argument 2 + temei legal
- Jurisprudență CNSC favorabilă

### Concluzie echilibrată

## Note Trainer
Cum să gestionezi dezbaterea, argumente suplimentare de rezervă, cum să concluzionezi constructiv.""",

    "quiz": """Generează un QUIZ CU VARIANTE DE RĂSPUNS pe tema specificată.

Structură obligatorie:
## Enunț
Prezintă contextul general al quiz-ului și instrucțiunile de completare.

## Cerințe
Generează 5-10 întrebări (în funcție de lungime), fiecare cu:
- Textul întrebării (clar, precis)
- 4 variante de răspuns (A, B, C, D)
- O singură variantă corectă (sau specifică dacă sunt mai multe corecte)

Format:
**Întrebarea 1:** [text]
A) [varianta]
B) [varianta]
C) [varianta]
D) [varianta]

## Rezolvare
Pentru fiecare întrebare:
- **Răspuns corect:** [litera]
- **Explicație:** De ce este corect, cu referire la articolul de lege exact
- **De ce sunt greșite celelalte:** Scurtă explicație per variantă incorectă

## Note Trainer
Baremul de notare, timp alocat, cum să folosești quiz-ul (individual vs. echipe), întrebări bonus.""",

    "joc_rol": """Generează un exercițiu de JOC DE ROL pe tema specificată.

Structură obligatorie:
## Enunț
Descrie scenariul general și contextul în care se desfășoară jocul de rol. Include toate detaliile factuale necesare.

## Cerințe
### Roluri și instrucțiuni:
Definește 3-5 roluri (ex: reprezentant autoritate contractantă, ofertant câștigător, ofertant necâștigător, membru comisie evaluare, consilier CNSC).

Pentru fiecare rol:
- **Rol:** [nume]
- **Obiectiv:** Ce trebuie să obțină personajul
- **Informații confidențiale:** Ce știe doar acest personaj
- **Restricții:** Ce nu are voie să facă

### Regulile jocului: timp, etape, format

## Rezolvare
Cum ar trebui să evolueze scenariul, punctele de inflexiune, rezolvarea optimă din perspectivă juridică, cu referire la legislație și jurisprudență.

## Note Trainer
Cum să gestionezi dinamica grupului, intervenții necesare, debriefing post-exercițiu, puncte de evaluare.""",

    "erori": """Generează un exercițiu de IDENTIFICARE ERORI pe tema specificată.

Structură obligatorie:
## Enunț
Prezintă un document/fragment de document din achiziții publice (ex: caiet de sarcini, fișă de date, proces verbal evaluare, raport procedură) care conține erori deliberate. Documentul trebuie să pară autentic.

## Cerințe
Instrucțiuni: participanții trebuie să identifice toate erorile din document, să explice de ce sunt erori și să propună corectarea lor. Specifică numărul total de erori ascunse.

## Rezolvare
Lista completă a erorilor:
- **Eroarea 1:** [localizare] — [descriere] — [articolul de lege încălcat] — [corecția]
- **Eroarea 2:** ... etc.

## Note Trainer
Erori ușor de ratat, erori critice vs. minore, cum să evaluezi completitudinea identificării, discuții suplimentare.""",

    "comparativ": """Generează un exercițiu de ANALIZĂ COMPARATIVĂ pe tema specificată.

Structură obligatorie:
## Enunț
Prezintă două situații/abordări/decizii similare dar cu diferențe importante în domeniul achizițiilor publice. Oferă suficiente detalii pentru fiecare situație.

## Cerințe
Participanții trebuie să:
1. Identifice asemănările și deosebirile
2. Analizeze avantajele/dezavantajele fiecărei abordări
3. Determine care abordare este corectă/optimă și de ce
4. Fundamenteze juridic concluzia

## Rezolvare
### Tabel comparativ
| Criteriu | Situația A | Situația B |
|----------|-----------|-----------|

### Analiză detaliată
- Asemănări
- Deosebiri
- Avantaje/Dezavantaje per abordare
- Concluzie cu temei legal și jurisprudență

## Note Trainer
Nuanțe suplimentare, scenarii în care concluzia s-ar inversa, cum să ghidezi discuția comparativă.""",

    "cronologie": """Generează un exercițiu de CRONOLOGIE PROCEDURALĂ pe tema specificată.

Structură obligatorie:
## Enunț
Prezintă un set de pași/etape dintr-o procedură de achiziție publică, oferite în ordine amestecată. Include termene legale, documente asociate, responsabili.

## Cerințe
Participanții trebuie să:
1. Ordoneze corect pașii procedurii
2. Specifice termenele legale pentru fiecare pas
3. Identifice ce documente se emit la fiecare etapă
4. Precizeze cine este responsabil (autoritate, ANAP, ofertanți, CNSC)

## Rezolvare
Ordinea corectă cu:
- Număr de ordine → Pas → Termen legal → Document emis → Responsabil → Articolul de lege
- Explicație pentru fiecare pas

## Note Trainer
Greșeli frecvente de ordonare, excepții de la termene, cum variază procedura în funcție de tipul achiziției.""",

    "program_formare": """Generează un PROGRAM COMPLET DE FORMARE pe tema specificată.

Ești un designer instrucțional expert în achiziții publice. Creează un program coerent de formare care include
multiple materiale didactice de tipuri variate (spețe, studii de caz, quiz-uri, jocuri de rol, dezbateri, etc.)
alese strategic pentru a livra competențele vizate.

Structură obligatorie:
## Enunț
Prezentarea programului de formare:
- Titlu program
- Obiective de învățare (3-5 obiective concrete, măsurabile)
- Competențe vizate
- Durată estimată
- Public țintă
- Structura pe module/sesiuni

## Cerințe
Pentru FIECARE modul/sesiune din program, generează un material didactic complet:
- Specifică tipul materialului ales (speță, quiz, studiu de caz, joc de rol, dezbatere, etc.) și justifică alegerea
- Include materialul complet cu enunț, instrucțiuni, și rezolvare
- Specifică durata estimată per activitate
- Leagă fiecare activitate de obiectivele de învățare

## Rezolvare
Rezolvările detaliate pentru TOATE materialele din program, cu:
- Temeiuri legale exacte (articole, alineate)
- Jurisprudență CNSC relevantă
- Răspunsuri model pentru fiecare activitate

## Note Trainer
- Agenda detaliată cu timing per activitate
- Sfaturi de facilitare per modul
- Materiale necesare (flipchart, proiector, handout-uri)
- Adaptări pentru diferite niveluri de experiență
- Evaluarea învățării: metode de verificare a competențelor""",
}


class TrainingGenerator:
    """Service for generating training materials grounded in real legal data."""

    def __init__(self, llm_provider: Optional[GeminiProvider] = None):
        self.llm = llm_provider or GeminiProvider(model="gemini-3.1-pro-preview")
        self.embedding_service = EmbeddingService(llm_provider=self.llm)

    async def _search_relevant_context(
        self,
        tema: str,
        session: AsyncSession,
        max_jurisprudenta: int = 5,
        max_legislatie: int = 5,
    ) -> tuple[str, list[str], list[str]]:
        """Search for relevant jurisprudence and legislation.

        Returns:
            Tuple of (context_text, decision_refs, legislation_refs).
        """
        jurisprudence_context = ""
        legislation_context = ""
        decision_refs: list[str] = []
        legislation_refs: list[str] = []

        try:
            # Check if embeddings exist
            has_embeddings = await session.scalar(
                select(func.count())
                .select_from(ArgumentareCritica)
                .where(ArgumentareCritica.embedding.isnot(None))
            )

            if has_embeddings and has_embeddings > 0:
                query_vector = await self.embedding_service.embed_query(tema)

                # Search jurisprudence
                stmt = (
                    select(
                        ArgumentareCritica,
                        ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
                    )
                    .where(ArgumentareCritica.embedding.isnot(None))
                    .order_by("distance")
                    .limit(max_jurisprudenta)
                )

                result = await session.execute(stmt)
                rows = result.all()

                relevant = [
                    (row.ArgumentareCritica, row.distance)
                    for row in rows
                    if row.distance < 0.6
                ]

                if relevant:
                    dec_ids = list({arg.decizie_id for arg, _ in relevant})
                    dec_result = await session.execute(
                        select(DecizieCNSC).where(DecizieCNSC.id.in_(dec_ids))
                    )
                    decisions = {d.id: d for d in dec_result.scalars().all()}
                    decision_refs = [d.external_id for d in decisions.values()]

                    parts = []
                    for arg, dist in relevant:
                        dec = decisions.get(arg.decizie_id)
                        if not dec:
                            continue
                        part = f"Decizia {dec.external_id} (soluție: {dec.solutie_contestatie or 'N/A'}):\n"
                        if arg.argumente_contestator:
                            part += f"  Argumente contestator: {arg.argumente_contestator[:500]}\n"
                        if arg.argumentatie_cnsc:
                            part += f"  Argumentație CNSC: {arg.argumentatie_cnsc[:500]}\n"
                        if arg.castigator_critica and arg.castigator_critica != "unknown":
                            part += f"  Câștigător: {arg.castigator_critica}\n"
                        if arg.jurisprudenta_cnsc:
                            part += f"  Jurisprudență CNSC: {'; '.join(arg.jurisprudenta_cnsc[:3])}\n"
                        parts.append(part)

                    jurisprudence_context = "\n---\n".join(parts)

                # Search legislation
                has_leg_embeddings = await session.scalar(
                    select(func.count())
                    .select_from(LegislatieFragment)
                    .where(LegislatieFragment.embedding.isnot(None))
                )

                if has_leg_embeddings and has_leg_embeddings > 0:
                    leg_stmt = (
                        select(
                            LegislatieFragment,
                            LegislatieFragment.embedding.cosine_distance(query_vector).label("distance"),
                        )
                        .where(LegislatieFragment.embedding.isnot(None))
                        .order_by("distance")
                        .limit(max_legislatie)
                    )

                    leg_result = await session.execute(leg_stmt)
                    leg_rows = leg_result.all()

                    leg_parts = []
                    act_ids = list({row.LegislatieFragment.act_id for row in leg_rows if row.distance < 0.6})

                    acts = {}
                    if act_ids:
                        act_result = await session.execute(
                            select(ActNormativ).where(ActNormativ.id.in_(act_ids))
                        )
                        acts = {a.id: a for a in act_result.scalars().all()}

                    for row in leg_rows:
                        frag = row.LegislatieFragment
                        if row.distance >= 0.6:
                            continue
                        act = acts.get(frag.act_id)
                        act_name = act.denumire if act else "Act necunoscut"
                        citation = f"{frag.citare} din {act_name}" if frag.citare else act_name
                        legislation_refs.append(citation)
                        leg_parts.append(f"{citation}:\n{frag.text_fragment}")

                    legislation_context = "\n---\n".join(leg_parts)

            logger.info(
                "training_context_search",
                tema=tema[:80],
                jurisprudenta_count=len(decision_refs),
                legislatie_count=len(legislation_refs),
            )

        except Exception as e:
            logger.warning("training_context_search_failed", error=str(e))

        context_parts = []
        if jurisprudence_context:
            context_parts.append(
                f"=== JURISPRUDENȚĂ CNSC RELEVANTĂ ===\n{jurisprudence_context}\n=== SFÂRȘIT JURISPRUDENȚĂ ==="
            )
        if legislation_context:
            context_parts.append(
                f"=== LEGISLAȚIE RELEVANTĂ ===\n{legislation_context}\n=== SFÂRȘIT LEGISLAȚIE ==="
            )

        return "\n\n".join(context_parts), decision_refs, legislation_refs

    def _build_system_prompt(
        self,
        tip_material: str,
        nivel_dificultate: str,
        lungime: str,
        context: str,
        public_tinta: str = "",
        program_plan: str = "",
        batch_index: int | None = None,
        batch_total: int | None = None,
    ) -> str:
        """Build the system prompt for material generation."""
        material_info = MATERIAL_TYPES.get(tip_material, MATERIAL_TYPES["speta"])
        nivel_info = DIFFICULTY_LEVELS.get(nivel_dificultate, DIFFICULTY_LEVELS["mediu"])
        lungime_info = LENGTH_OPTIONS.get(lungime, LENGTH_OPTIONS["mediu"])
        material_prompt = MATERIAL_PROMPTS.get(tip_material, MATERIAL_PROMPTS["speta"])

        # Build public tinta section
        public_section = ""
        if public_tinta:
            public_section = f"""
PUBLIC ȚINTĂ SPECIFIC: {public_tinta}
Adaptează limbajul, complexitatea exemplelor și perspectiva materialului pentru acest public specific.
- Dacă publicul include autorități contractante: focusează pe obligații, proceduri, riscuri de neconformitate
- Dacă publicul include operatori economici: focusează pe drepturi, strategii de contestare, greșeli de evitat
- Dacă publicul include organe de control/audit: focusează pe criterii de verificare, nereguli frecvente, bune practici
- Dacă publicul include CNSC: focusează pe interpretare legislativă, jurisprudență, consistență decizională
"""
        else:
            public_section = "\nPUBLIC ȚINTĂ: General — specialiști în achiziții publice (autorități contractante, operatori economici, consultanți).\n"

        # Build program plan section
        program_section = ""
        if program_plan and tip_material == "program_formare":
            program_section = f"""
PLANUL DE FORMARE FURNIZAT DE TRAINER:
{program_plan}

Folosește acest plan ca bază pentru structurarea programului. Respectă modulele, tematicile și competențele vizate.
Alege tipurile de materiale cele mai potrivite pentru fiecare tematică din plan.
"""

        # Build batch section
        batch_section = ""
        if batch_index and batch_total:
            batch_section = f"""
GENERARE ÎN LOT: Acesta este materialul {batch_index} din {batch_total}.
Generează un material DIFERIT de cele anterioare — variază abordarea, scenariul, exemplele, perspectiva.
Fiecare material trebuie să acopere un aspect diferit al temei sau să ofere o perspectivă nouă.
"""

        prompt = f"""Ești un expert în achiziții publice din România și un trainer profesionist cu experiență vastă în formarea specialiștilor. Generezi materiale didactice de cea mai înaltă calitate, ancorate în legislația și jurisprudența reală din România.

REGULI FUNDAMENTALE:
1. Toate materialele trebuie să fie în limba română, cu terminologie juridică corectă
2. Citează DOAR articole de lege care există cu adevărat (Legea 98/2016, HG 395/2016, Legea 101/2016, OUG 34/2006 etc.)
3. Dacă ai jurisprudență CNSC în context, folosește-o activ și citează deciziile specifice
4. NU inventa numere de decizii CNSC sau articole de lege inexistente
5. Materialul trebuie să fie practic, aplicabil, nu doar teoretic
6. Folosește nume fictive pentru entități (ex: S.C. Exemplu S.R.L., Primăria Orașului Model)
{public_section}
TIPUL MATERIALULUI: {material_info['name']} — {material_info['description']}

NIVEL DE DIFICULTATE: {nivel_info}

LUNGIME ȚINTĂ: {lungime_info}

{context}
{program_section}{batch_section}
INSTRUCȚIUNI SPECIFICE PENTRU ACEST TIP DE MATERIAL:
{material_prompt}

IMPORTANT — STRUCTURĂ OBLIGATORIE:
Materialul TREBUIE să conțină EXACT aceste 4 secțiuni, în această ordine:
1. ## Enunț — prezentarea situației/scenariului
2. ## Cerințe — ce trebuie să facă participanții
3. ## Rezolvare — rezolvarea completă cu temeiuri legale exacte (articole, alineate) și jurisprudență CNSC
4. ## Note Trainer — sfaturi pentru formator, puncte cheie, greșeli frecvente, timp alocat

Lungimea țintă ({lungime_info}) se aplică INDEPENDENT la FIECARE secțiune. NU se omite nicio secțiune, ci fiecare este mai concisă. Rezolvarea și Notele Trainer sunt la fel de importante ca Enunțul.

INTERDICȚII STRICTE:
- NU adăuga introduceri, preambuluri sau texte explicative înainte de ## Enunț. Începe DIRECT cu ## Enunț.
- NU adăuga concluzii, rezumate sau texte după ## Note Trainer.
- NU adăuga paragrafe de context general de tipul "Acest material vizează..." sau "Acest test este conceput pentru...". Intră DIRECT în subiect.
- Fiecare cuvânt contează — zero text de umplutură, zero generalități."""

        return prompt

    async def generate(
        self,
        tema: str,
        tip_material: str,
        nivel_dificultate: str,
        lungime: str,
        context_suplimentar: str,
        session: AsyncSession,
        public_tinta: str = "",
        program_plan: str = "",
        batch_index: int | None = None,
        batch_total: int | None = None,
    ) -> dict:
        """Generate a training material (non-streaming).

        Returns:
            Dict with material, rezolvare, note_trainer, legislatie_citata, jurisprudenta_citata, metadata.
        """
        context, decision_refs, legislation_refs = await self._search_relevant_context(
            tema, session
        )

        system_prompt = self._build_system_prompt(
            tip_material, nivel_dificultate, lungime, context,
            public_tinta=public_tinta,
            program_plan=program_plan,
            batch_index=batch_index,
            batch_total=batch_total,
        )

        user_prompt = f"Tema: {tema}\n\nÎncepe DIRECT cu ## Enunț (fără introducere, fără preambul)."
        if context_suplimentar:
            user_prompt += f"\n\nContext suplimentar de la trainer: {context_suplimentar}"

        # Token budget per length (4 sections × words per section × ~1.5 tokens/word)
        token_budgets = {
            "scurt": 4096,     # 4 × ~200 words
            "mediu": 8192,     # 4 × ~400 words
            "lung": 16384,     # 4 × ~800 words
            "extins": 24576,   # 4 × ~1500 words
        }
        max_tokens = token_budgets.get(lungime, 8192)

        response = await self.llm.complete(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.4,
            max_tokens=max_tokens,
        )

        # Strip any preamble text before the first ## heading
        response = self._strip_preamble(response)

        # Parse sections from response
        sections = self._parse_sections(response)

        return {
            "material": sections.get("enunt", response),
            "cerinte": sections.get("cerinte", ""),
            "rezolvare": sections.get("rezolvare", ""),
            "note_trainer": sections.get("note_trainer", ""),
            "full_content": response,
            "legislatie_citata": legislation_refs,
            "jurisprudenta_citata": decision_refs,
            "metadata": {
                "tip": tip_material,
                "tip_name": MATERIAL_TYPES.get(tip_material, {}).get("name", tip_material),
                "nivel": nivel_dificultate,
                "lungime": lungime,
            },
        }

    async def prepare_for_streaming(
        self,
        tema: str,
        tip_material: str,
        nivel_dificultate: str,
        lungime: str,
        context_suplimentar: str,
        session: AsyncSession,
        public_tinta: str = "",
        program_plan: str = "",
        batch_index: int | None = None,
        batch_total: int | None = None,
    ) -> tuple[str, str, dict]:
        """Prepare prompt and system prompt for streaming generation.

        Returns:
            Tuple of (user_prompt, system_prompt, metadata).
        """
        context, decision_refs, legislation_refs = await self._search_relevant_context(
            tema, session
        )

        system_prompt = self._build_system_prompt(
            tip_material, nivel_dificultate, lungime, context,
            public_tinta=public_tinta,
            program_plan=program_plan,
            batch_index=batch_index,
            batch_total=batch_total,
        )

        user_prompt = f"Tema: {tema}\n\nÎncepe DIRECT cu ## Enunț (fără introducere, fără preambul)."
        if context_suplimentar:
            user_prompt += f"\n\nContext suplimentar de la trainer: {context_suplimentar}"

        metadata = {
            "legislatie_citata": legislation_refs,
            "jurisprudenta_citata": decision_refs,
            "tip": tip_material,
            "tip_name": MATERIAL_TYPES.get(tip_material, {}).get("name", tip_material),
            "nivel": nivel_dificultate,
            "lungime": lungime,
        }

        return user_prompt, system_prompt, metadata

    @staticmethod
    def _strip_preamble(text: str) -> str:
        """Remove any text before the first ## heading."""
        import re
        match = re.search(r'^(## )', text, re.MULTILINE)
        if match and match.start() > 0:
            return text[match.start():]
        return text

    @staticmethod
    def _parse_sections(text: str) -> dict:
        """Parse markdown sections from generated text."""
        sections = {}
        current_key = None
        current_lines: list[str] = []

        for line in text.split("\n"):
            line_lower = line.strip().lower()
            if line_lower.startswith("## enun"):
                if current_key:
                    sections[current_key] = "\n".join(current_lines).strip()
                current_key = "enunt"
                current_lines = []
            elif line_lower.startswith("## cerin"):
                if current_key:
                    sections[current_key] = "\n".join(current_lines).strip()
                current_key = "cerinte"
                current_lines = []
            elif line_lower.startswith("## rezolv"):
                if current_key:
                    sections[current_key] = "\n".join(current_lines).strip()
                current_key = "rezolvare"
                current_lines = []
            elif line_lower.startswith("## note"):
                if current_key:
                    sections[current_key] = "\n".join(current_lines).strip()
                current_key = "note_trainer"
                current_lines = []
            else:
                current_lines.append(line)

        if current_key:
            sections[current_key] = "\n".join(current_lines).strip()

        return sections

    @staticmethod
    def get_material_types() -> dict:
        """Return available material types."""
        return MATERIAL_TYPES

    @staticmethod
    def get_difficulty_levels() -> dict:
        """Return available difficulty levels."""
        return DIFFICULTY_LEVELS

    @staticmethod
    def get_length_options() -> dict:
        """Return available length options."""
        return LENGTH_OPTIONS
