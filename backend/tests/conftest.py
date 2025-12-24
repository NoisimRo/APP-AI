"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def sample_decision_text():
    """Sample CNSC decision text for testing."""
    return """
    CONSILIUL NAȚIONAL DE SOLUȚIONARE A CONTESTAȚIILOR

    Decizia Nr. 1234/C1/567
    Data: 15 ianuarie 2024

    Contestator: S.C. CONSTRUCTII MODERNE S.R.L.
    Autoritate contractantă: Primăria Municipiului București

    În fapt:
    Contestatorul a participat la procedura de achiziție publică având ca obiect
    "Lucrări de reabilitare drumuri" - CPV 45233140-2.

    S-au invocat următoarele critici:
    - D1: Cerințe de calificare restrictive
    - D3: Criterii de atribuire subiective

    Analizând documentația, Consiliul constată că:

    Conform art. 210 din Legea 98/2016, autoritatea contractantă avea obligația
    de a asigura o concurență reală.

    PENTRU ACESTE MOTIVE

    CONSILIUL NAȚIONAL DE SOLUȚIONARE A CONTESTAȚIILOR

    DECIDE:

    Admite contestația formulată de S.C. CONSTRUCTII MODERNE S.R.L.
    """


@pytest.fixture
def sample_respins_decision_text():
    """Sample rejected CNSC decision text for testing."""
    return """
    CONSILIUL NAȚIONAL DE SOLUȚIONARE A CONTESTAȚIILOR

    Decizia Nr. 5678/C2/890
    Data: 20 februarie 2024

    Contestator: S.C. TECH SOLUTIONS S.R.L.
    Autoritate contractantă: Consiliul Județean Constanța

    În fapt:
    Contestatorul a participat la procedura de achiziție publică având ca obiect
    "Servicii de consultanță IT" - CPV 72220000-3.

    Critica R2: Evaluarea ofertei tehnice

    Analizând documentația, Consiliul constată că autoritatea contractantă
    a respectat prevederile legale.

    DECIDE:

    Respinge contestația formulată de S.C. TECH SOLUTIONS S.R.L.
    """
