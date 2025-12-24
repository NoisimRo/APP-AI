"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def sample_filename_with_cpv():
    """Sample filename with CPV code."""
    return "BO2025_3855_R2_CPV_55520000-1_A.txt"


@pytest.fixture
def sample_filename_multiple_critici():
    """Sample filename with multiple criticism codes."""
    return "BO2025_1234_D1_D4_CPV_45233140-2_R.txt"


@pytest.fixture
def sample_filename_no_cpv():
    """Sample filename without CPV code."""
    return "BO2024_5678_R3_R4_X.txt"


@pytest.fixture
def sample_decision_text_admis():
    """Sample CNSC decision text with ADMIS ruling."""
    return """
    CONSILIUL NAȚIONAL DE SOLUȚIONARE A CONTESTAȚIILOR

    DECIZIE
    Nr. 3855/C8/4446
    Data: 10 decembrie 2025

    Contestator: S.C. CONSTRUCTII MODERNE S.R.L.
    Autoritate contractantă: Primăria Municipiului București

    În fapt:
    Contestatorul a participat la procedura de achiziție publică având ca obiect
    "Servicii de cantină" - CPV 55520000-1.

    S-au invocat următoarele critici:
    - R2: Respingerea ofertei ca neconformă

    Analizând documentația, Consiliul constată că:

    Conform art. 210 din Legea 98/2016, autoritatea contractantă avea obligația
    de a asigura o concurență reală.

    PENTRU ACESTE MOTIVE

    CONSILIUL DECIDE:

    Admite contestația formulată de S.C. CONSTRUCTII MODERNE S.R.L.
    """


@pytest.fixture
def sample_decision_text_respins():
    """Sample CNSC decision text with RESPINS ruling."""
    return """
    CONSILIUL NAȚIONAL DE SOLUȚIONARE A CONTESTAȚIILOR

    DECIZIE
    Nr. 1234/C5/2222
    Data: 15 ianuarie 2025

    Contestator: S.C. TECH SOLUTIONS S.R.L.
    Autoritate contractantă: Consiliul Județean Constanța

    În fapt:
    Contestatorul a participat la procedura de achiziție publică având ca obiect
    "Lucrări drumuri" - CPV 45233140-2.

    S-au invocat următoarele critici:
    - D1: Cerințe de calificare restrictive
    - D4: Lipsa răspuns la clarificări

    Analizând documentația, Consiliul constată că autoritatea contractantă
    a respectat prevederile legale.

    CONSILIUL DECIDE:

    Respinge, ca nefondată, contestația formulată de S.C. TECH SOLUTIONS S.R.L.
    """


@pytest.fixture
def sample_decision_text_admis_partial():
    """Sample CNSC decision text with ADMIS PARTIAL ruling."""
    return """
    CONSILIUL NAȚIONAL DE SOLUȚIONARE A CONTESTAȚIILOR

    DECIZIE
    Nr. 5678/C3/3333
    Data: 20 februarie 2025

    Contestator: S.C. SERVICII PUBLICE S.R.L.

    S-au invocat următoarele critici:
    - R3: Preț neobișnuit de scăzut
    - R4: Documente calificare

    CONSILIUL DECIDE:

    Admite, în parte, contestația formulată de S.C. SERVICII PUBLICE S.R.L.
    """


@pytest.fixture
def sample_decision_text_with_sections():
    """Sample CNSC decision with all major sections."""
    return """
    CONSILIUL NAȚIONAL DE SOLUȚIONARE A CONTESTAȚIILOR

    DECIZIE
    Nr. 9999/C1/1111
    Data: 5 martie 2025

    Contestatorul solicită anularea procedurii de achiziție.

    Prin contestație se arată că documentația de atribuire conține cerințe restrictive.

    Punct de vedere al Autorității Contractante:

    Autoritatea contractantă a formulat punct de vedere prin care solicită
    respingerea contestației ca nefondată.

    Cerere de intervenție:

    S.C. COMPETITOR S.R.L. a formulat cerere de intervenție în sprijinul AC.

    PENTRU ACESTE MOTIVE

    CONSILIUL DECIDE:

    Respinge, ca nefondată, contestația.
    """
