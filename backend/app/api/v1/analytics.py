"""Analytics API endpoints — CNSC Panel Profiles, Outcome Prediction, Decision Comparison."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, case, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import cache_get_json, cache_set_json
from app.db.session import get_session, is_db_available
from app.models.decision import DecizieCNSC, ArgumentareCritica

router = APIRouter()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. CNSC Panel Profile
# ---------------------------------------------------------------------------

@router.get("/panel/{complet}")
async def get_panel_profile(
    complet: str,
    session: AsyncSession = Depends(get_session),
):
    """Get comprehensive statistics for a CNSC panel (C1-C20).

    Returns win rates, CPV domains, criticism code tendencies, and yearly trends.
    """
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Baza de date indisponibilă")

    complet = complet.upper()

    # Check cache (10 min TTL)
    cache_key = f"expertap:analytics:panel:{complet}"
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return cached

    # --- Overall stats ---
    base_filter = and_(
        DecizieCNSC.complet == complet,
        DecizieCNSC.solutie_contestatie.isnot(None),
    )

    total_q = await session.execute(
        select(func.count()).select_from(DecizieCNSC).where(base_filter)
    )
    total = total_q.scalar() or 0
    if total == 0:
        raise HTTPException(status_code=404, detail=f"Completul {complet} nu a fost găsit")

    # Ruling distribution
    ruling_q = await session.execute(
        select(DecizieCNSC.solutie_contestatie, func.count().label("cnt"))
        .where(base_filter)
        .group_by(DecizieCNSC.solutie_contestatie)
    )
    rulings = {r.solutie_contestatie: r.cnt for r in ruling_q}
    admis = rulings.get("ADMIS", 0) + rulings.get("ADMIS_PARTIAL", 0)
    win_rate = round(admis / total * 100, 1) if total > 0 else 0

    # --- By contest type ---
    type_q = await session.execute(
        select(
            DecizieCNSC.tip_contestatie,
            DecizieCNSC.solutie_contestatie,
            func.count().label("cnt"),
        )
        .where(base_filter)
        .group_by(DecizieCNSC.tip_contestatie, DecizieCNSC.solutie_contestatie)
    )
    by_type_raw = {}
    for r in type_q:
        tip = r.tip_contestatie or "necunoscut"
        if tip not in by_type_raw:
            by_type_raw[tip] = {"total": 0, "admis": 0, "admis_partial": 0, "respins": 0}
        by_type_raw[tip]["total"] += r.cnt
        if r.solutie_contestatie == "ADMIS":
            by_type_raw[tip]["admis"] += r.cnt
        elif r.solutie_contestatie == "ADMIS_PARTIAL":
            by_type_raw[tip]["admis_partial"] += r.cnt
        elif r.solutie_contestatie == "RESPINS":
            by_type_raw[tip]["respins"] += r.cnt

    by_type = []
    for tip, stats in by_type_raw.items():
        t = stats["total"]
        w = stats["admis"] + stats["admis_partial"]
        by_type.append({
            "type": tip,
            **stats,
            "win_rate": round(w / t * 100, 1) if t > 0 else 0,
        })

    # --- Top CPV domains ---
    cpv_q = await session.execute(
        select(
            func.substring(DecizieCNSC.cod_cpv, 1, 3).label("cpv_group"),
            DecizieCNSC.cpv_categorie,
            func.count().label("cnt"),
            func.sum(case(
                (DecizieCNSC.solutie_contestatie.in_(["ADMIS", "ADMIS_PARTIAL"]), 1),
                else_=0,
            )).label("wins"),
        )
        .where(and_(base_filter, DecizieCNSC.cod_cpv.isnot(None)))
        .group_by("cpv_group", DecizieCNSC.cpv_categorie)
        .order_by(func.count().desc())
        .limit(10)
    )
    top_cpv = [
        {
            "cpv_group": r.cpv_group,
            "categorie": r.cpv_categorie,
            "total": r.cnt,
            "wins": r.wins,
            "win_rate": round(r.wins / r.cnt * 100, 1) if r.cnt > 0 else 0,
        }
        for r in cpv_q
    ]

    # --- Criticism code tendencies (from ArgumentareCritica) ---
    critici_q = await session.execute(
        select(
            ArgumentareCritica.cod_critica,
            func.count().label("cnt"),
            func.sum(case(
                (ArgumentareCritica.castigator_critica == "contestator", 1),
                else_=0,
            )).label("contestator_wins"),
            func.sum(case(
                (ArgumentareCritica.castigator_critica == "autoritate", 1),
                else_=0,
            )).label("autoritate_wins"),
            func.sum(case(
                (ArgumentareCritica.castigator_critica == "partial", 1),
                else_=0,
            )).label("partial"),
        )
        .join(DecizieCNSC, ArgumentareCritica.decizie_id == DecizieCNSC.id)
        .where(DecizieCNSC.complet == complet)
        .group_by(ArgumentareCritica.cod_critica)
        .order_by(func.count().desc())
        .limit(15)
    )
    criticism_stats = [
        {
            "code": r.cod_critica,
            "total": r.cnt,
            "contestator_wins": r.contestator_wins,
            "autoritate_wins": r.autoritate_wins,
            "partial": r.partial,
            "contestator_win_rate": round(r.contestator_wins / r.cnt * 100, 1) if r.cnt > 0 else 0,
        }
        for r in critici_q
    ]

    # --- Yearly trend ---
    year_q = await session.execute(
        select(
            func.extract("year", DecizieCNSC.data_decizie).label("year"),
            func.count().label("cnt"),
            func.sum(case(
                (DecizieCNSC.solutie_contestatie.in_(["ADMIS", "ADMIS_PARTIAL"]), 1),
                else_=0,
            )).label("wins"),
        )
        .where(and_(base_filter, DecizieCNSC.data_decizie.isnot(None)))
        .group_by("year")
        .order_by("year")
    )
    yearly_trend = [
        {
            "year": int(r.year),
            "total": r.cnt,
            "wins": r.wins,
            "win_rate": round(r.wins / r.cnt * 100, 1) if r.cnt > 0 else 0,
        }
        for r in year_q
    ]

    result = {
        "complet": complet,
        "total_decisions": total,
        "rulings": rulings,
        "win_rate": win_rate,
        "by_type": by_type,
        "top_cpv": top_cpv,
        "criticism_stats": criticism_stats,
        "yearly_trend": yearly_trend,
    }

    await cache_set_json(cache_key, result, ttl_seconds=600)
    return result


@router.get("/panels")
async def list_panels(session: AsyncSession = Depends(get_session)):
    """List all CNSC panels with summary stats for the overview page."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Baza de date indisponibilă")

    cache_key = "expertap:analytics:panels_list"
    cached = await cache_get_json(cache_key)
    if cached is not None:
        return cached

    q = await session.execute(
        select(
            DecizieCNSC.complet,
            func.count().label("total"),
            func.sum(case(
                (DecizieCNSC.solutie_contestatie.in_(["ADMIS", "ADMIS_PARTIAL"]), 1),
                else_=0,
            )).label("wins"),
        )
        .where(and_(
            DecizieCNSC.complet.isnot(None),
            DecizieCNSC.solutie_contestatie.isnot(None),
        ))
        .group_by(DecizieCNSC.complet)
        .order_by(DecizieCNSC.complet)
    )

    panels = [
        {
            "complet": r.complet,
            "total": r.total,
            "wins": r.wins,
            "win_rate": round(r.wins / r.total * 100, 1) if r.total > 0 else 0,
        }
        for r in q
    ]

    await cache_set_json(cache_key, panels, ttl_seconds=600)
    return panels


# ---------------------------------------------------------------------------
# 2. Outcome Predictor
# ---------------------------------------------------------------------------

class PredictRequest(BaseModel):
    """Input for outcome prediction."""
    coduri_critici: list[str] = Field(..., description="Criticism codes (e.g. ['D3', 'R2'])")
    cod_cpv: Optional[str] = Field(None, description="CPV code (e.g. '45310000-3')")
    tip_procedura: Optional[str] = Field(None, description="Procedure type")
    criteriu_atribuire: Optional[str] = Field(None, description="Award criteria")
    complet: Optional[str] = Field(None, description="CNSC panel (C1-C20)")
    tip_contestatie: Optional[str] = Field(None, description="documentatie or rezultat")


@router.post("/predict-outcome")
async def predict_outcome(
    request: PredictRequest,
    session: AsyncSession = Depends(get_session),
):
    """Predict ADMIS/RESPINS probability based on case parameters.

    Combines historical statistics with LLM reasoning.
    """
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Baza de date indisponibilă")

    stats = {}

    # --- Per-criticism win rates ---
    for code in request.coduri_critici:
        q = await session.execute(
            select(
                ArgumentareCritica.castigator_critica,
                func.count().label("cnt"),
            )
            .where(ArgumentareCritica.cod_critica == code)
            .group_by(ArgumentareCritica.castigator_critica)
        )
        code_stats = {r.castigator_critica: r.cnt for r in q}
        total = sum(code_stats.values())
        wins = code_stats.get("contestator", 0) + code_stats.get("partial", 0)
        stats[f"critica_{code}"] = {
            "total": total,
            "contestator_wins": code_stats.get("contestator", 0),
            "autoritate_wins": code_stats.get("autoritate", 0),
            "partial": code_stats.get("partial", 0),
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
        }

    # --- Overall by CPV group (3-digit) ---
    if request.cod_cpv:
        cpv_prefix = request.cod_cpv[:3]
        q = await session.execute(
            select(
                DecizieCNSC.solutie_contestatie,
                func.count().label("cnt"),
            )
            .where(and_(
                DecizieCNSC.cod_cpv.startswith(cpv_prefix),
                DecizieCNSC.solutie_contestatie.isnot(None),
            ))
            .group_by(DecizieCNSC.solutie_contestatie)
        )
        cpv_stats = {r.solutie_contestatie: r.cnt for r in q}
        cpv_total = sum(cpv_stats.values())
        cpv_wins = cpv_stats.get("ADMIS", 0) + cpv_stats.get("ADMIS_PARTIAL", 0)
        stats["cpv_domain"] = {
            "cpv_prefix": cpv_prefix,
            "total": cpv_total,
            "win_rate": round(cpv_wins / cpv_total * 100, 1) if cpv_total > 0 else 0,
        }

    # --- Panel-specific rate ---
    if request.complet:
        q = await session.execute(
            select(
                DecizieCNSC.solutie_contestatie,
                func.count().label("cnt"),
            )
            .where(and_(
                DecizieCNSC.complet == request.complet.upper(),
                DecizieCNSC.solutie_contestatie.isnot(None),
            ))
            .group_by(DecizieCNSC.solutie_contestatie)
        )
        panel_stats = {r.solutie_contestatie: r.cnt for r in q}
        panel_total = sum(panel_stats.values())
        panel_wins = panel_stats.get("ADMIS", 0) + panel_stats.get("ADMIS_PARTIAL", 0)
        stats["panel"] = {
            "complet": request.complet.upper(),
            "total": panel_total,
            "win_rate": round(panel_wins / panel_total * 100, 1) if panel_total > 0 else 0,
        }

    # --- Procedure type rate ---
    if request.tip_procedura:
        q = await session.execute(
            select(
                DecizieCNSC.solutie_contestatie,
                func.count().label("cnt"),
            )
            .where(and_(
                DecizieCNSC.tip_procedura == request.tip_procedura,
                DecizieCNSC.solutie_contestatie.isnot(None),
            ))
            .group_by(DecizieCNSC.solutie_contestatie)
        )
        proc_stats = {r.solutie_contestatie: r.cnt for r in q}
        proc_total = sum(proc_stats.values())
        proc_wins = proc_stats.get("ADMIS", 0) + proc_stats.get("ADMIS_PARTIAL", 0)
        stats["procedure"] = {
            "tip_procedura": request.tip_procedura,
            "total": proc_total,
            "win_rate": round(proc_wins / proc_total * 100, 1) if proc_total > 0 else 0,
        }

    # --- Composite prediction ---
    # Weighted average of all dimensions (criticism codes weighted most)
    weights_and_rates = []
    for code in request.coduri_critici:
        s = stats.get(f"critica_{code}", {})
        if s.get("total", 0) >= 3:
            weights_and_rates.append((3.0, s["win_rate"]))  # Highest weight
    if "cpv_domain" in stats and stats["cpv_domain"]["total"] >= 5:
        weights_and_rates.append((1.5, stats["cpv_domain"]["win_rate"]))
    if "panel" in stats and stats["panel"]["total"] >= 10:
        weights_and_rates.append((2.0, stats["panel"]["win_rate"]))
    if "procedure" in stats and stats["procedure"]["total"] >= 5:
        weights_and_rates.append((1.0, stats["procedure"]["win_rate"]))

    if weights_and_rates:
        total_weight = sum(w for w, _ in weights_and_rates)
        weighted_rate = sum(w * r for w, r in weights_and_rates) / total_weight
    else:
        # Global fallback
        q = await session.execute(
            select(func.count()).select_from(DecizieCNSC)
            .where(DecizieCNSC.solutie_contestatie.in_(["ADMIS", "ADMIS_PARTIAL"]))
        )
        global_wins = q.scalar() or 0
        q2 = await session.execute(
            select(func.count()).select_from(DecizieCNSC)
            .where(DecizieCNSC.solutie_contestatie.isnot(None))
        )
        global_total = q2.scalar() or 1
        weighted_rate = round(global_wins / global_total * 100, 1)

    predicted_outcome = "ADMIS" if weighted_rate >= 50 else "RESPINS"
    confidence = abs(weighted_rate - 50) / 50  # 0-1 scale, 1 = very confident

    # --- LLM reasoning ---
    reasoning = None
    try:
        from app.services.llm.factory import get_active_llm_provider
        llm = await get_active_llm_provider(session)

        stats_summary = []
        for code in request.coduri_critici:
            s = stats.get(f"critica_{code}", {})
            if s:
                stats_summary.append(
                    f"- Critica {code}: {s.get('total', 0)} cazuri, "
                    f"rata de câștig contestator: {s.get('win_rate', 0)}%"
                )
        if "panel" in stats:
            p = stats["panel"]
            stats_summary.append(
                f"- Completul {p['complet']}: {p['total']} decizii, "
                f"rata de admitere: {p['win_rate']}%"
            )
        if "cpv_domain" in stats:
            c = stats["cpv_domain"]
            stats_summary.append(
                f"- Domeniu CPV {c['cpv_prefix']}*: {c['total']} decizii, "
                f"rata de admitere: {c['win_rate']}%"
            )

        prompt = (
            f"Ești un expert în achiziții publice din România. Pe baza statisticilor "
            f"istorice ale CNSC de mai jos, explică în 3-5 propoziții de ce o contestație "
            f"cu aceste caracteristici are o probabilitate de {weighted_rate:.0f}% de a fi admisă.\n\n"
            f"Caracteristici caz:\n"
            f"- Coduri critici: {', '.join(request.coduri_critici)}\n"
            f"{'- CPV: ' + request.cod_cpv + chr(10) if request.cod_cpv else ''}"
            f"{'- Complet: ' + request.complet + chr(10) if request.complet else ''}"
            f"{'- Tip procedură: ' + request.tip_procedura + chr(10) if request.tip_procedura else ''}"
            f"\nStatistici istorice:\n" + "\n".join(stats_summary) +
            f"\n\nPredicție: {predicted_outcome} ({weighted_rate:.0f}%)\n"
            f"Explică concis factorii care influențează această predicție."
        )

        reasoning = await llm.complete(prompt, temperature=0.3, max_tokens=500)
    except Exception as e:
        logger.warning("predict_llm_reasoning_failed", error=str(e))
        reasoning = None

    return {
        "prediction": {
            "outcome": predicted_outcome,
            "probability": round(weighted_rate, 1),
            "confidence": round(confidence, 2),
        },
        "stats": stats,
        "reasoning": reasoning,
        "input": request.model_dump(),
    }


# ---------------------------------------------------------------------------
# 3. Decision Comparison
# ---------------------------------------------------------------------------

class CompareRequest(BaseModel):
    """Input for decision comparison."""
    decision_ids: list[str] = Field(..., min_length=2, max_length=3,
                                     description="2-3 decision IDs to compare")


@router.post("/compare")
async def compare_decisions(
    request: CompareRequest,
    session: AsyncSession = Depends(get_session),
):
    """Compare 2-3 decisions side-by-side with LLM analysis of divergences."""
    if not is_db_available():
        raise HTTPException(status_code=503, detail="Baza de date indisponibilă")

    # Load decisions with their argumentation
    decisions_data = []
    for dec_id in request.decision_ids:
        dec_q = await session.execute(
            select(DecizieCNSC).where(DecizieCNSC.id == dec_id)
        )
        dec = dec_q.scalar_one_or_none()
        if not dec:
            raise HTTPException(status_code=404, detail=f"Decizia {dec_id} nu a fost găsită")

        # Load argumentation
        arg_q = await session.execute(
            select(ArgumentareCritica)
            .where(ArgumentareCritica.decizie_id == dec_id)
            .order_by(ArgumentareCritica.ordine_in_decizie)
        )
        args = list(arg_q.scalars().all())

        decisions_data.append({
            "id": str(dec.id),
            "numar_bo": f"BO{dec.an_bo}_{dec.numar_bo}",
            "complet": dec.complet,
            "data_decizie": dec.data_decizie.isoformat() if dec.data_decizie else None,
            "cod_cpv": dec.cod_cpv,
            "cpv_descriere": dec.cpv_descriere,
            "tip_contestatie": dec.tip_contestatie,
            "tip_procedura": dec.tip_procedura,
            "criteriu_atribuire": dec.criteriu_atribuire,
            "solutie": dec.solutie_contestatie,
            "motiv_respingere": dec.motiv_respingere,
            "rezumat": dec.rezumat,
            "obiect_contract": dec.obiect_contract,
            "argumentari": [
                {
                    "cod_critica": a.cod_critica,
                    "argumente_contestator": a.argumente_contestator[:500] if a.argumente_contestator else None,
                    "argumente_ac": a.argumente_ac[:500] if a.argumente_ac else None,
                    "argumentatie_cnsc": a.argumentatie_cnsc[:500] if a.argumentatie_cnsc else None,
                    "castigator": a.castigator_critica,
                }
                for a in args
            ],
        })

    # --- LLM comparative analysis ---
    llm_analysis = None
    try:
        from app.services.llm.factory import get_active_llm_provider
        llm = await get_active_llm_provider(session)

        # Build comparison context
        dec_summaries = []
        for d in decisions_data:
            s = f"**{d['numar_bo']}** (Complet {d['complet']}, {d['solutie']})\n"
            s += f"  CPV: {d['cod_cpv']} — {d['cpv_descriere'] or 'N/A'}\n"
            s += f"  Tip: {d['tip_contestatie']}, Procedură: {d['tip_procedura'] or 'N/A'}\n"
            s += f"  Rezumat: {d['rezumat'] or 'N/A'}\n"
            for a in d["argumentari"]:
                s += f"  Critica {a['cod_critica']}: câștigător={a['castigator']}\n"
                if a["argumentatie_cnsc"]:
                    s += f"    CNSC: {a['argumentatie_cnsc'][:300]}\n"
            dec_summaries.append(s)

        prompt = (
            "Ești un expert în jurisprudența CNSC din România. Analizează comparativ "
            f"următoarele {len(decisions_data)} decizii și identifică:\n"
            "1. Similitudini în abordarea CNSC\n"
            "2. Divergențe în raționament — de ce au avut rezultate diferite?\n"
            "3. Factori determinanți care au influențat rezultatul\n"
            "4. Lecții practice pentru un avocat\n\n"
            "Decizii:\n\n" + "\n---\n".join(dec_summaries) +
            "\n\nRăspunde în română, structurat pe cele 4 puncte. Fii concis și practic."
        )

        llm_analysis = await llm.complete(prompt, temperature=0.2, max_tokens=1500)
    except Exception as e:
        logger.warning("compare_llm_analysis_failed", error=str(e))

    return {
        "decisions": decisions_data,
        "analysis": llm_analysis,
    }
