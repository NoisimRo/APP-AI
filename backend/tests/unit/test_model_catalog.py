"""Teste pentru alegerea automată a modelelor (fără apeluri de rețea)."""

from app.services.llm import anthropic_models as claude
from app.services.llm import gemini_models as gemini


class TestGeminiFamily:
    def test_classic_families(self):
        assert gemini._family("gemini-3.1-pro-preview") == "pro"
        assert gemini._family("gemini-2.5-flash-lite") == "flash-lite"
        assert gemini._family("gemini-3-flash-preview") == "flash"

    def test_new_naming_and_suffixes(self):
        assert gemini._family("gemini-4-argon") == "argon"
        assert gemini._family("gemini-4-argon-preview") == "argon"
        assert gemini._family("gemini-2.5-flash-preview-09-2025") == "flash"
        assert gemini._family("gemini-3.1-pro-preview-customtools") == "pro-customtools"


class TestGeminiReplacement:
    AVAILABLE = [
        "gemini-4-hydrogen", "gemini-4-argon", "gemini-4-neon",
        "gemini-3.1-pro", "gemini-3.5-pro-preview", "gemini-3-flash",
        "gemini-2.5-flash-lite",
    ]

    def test_preview_retired_prefers_stable_same_generation(self):
        assert gemini.pick_replacement("gemini-3.1-pro-preview", self.AVAILABLE) == "gemini-3.1-pro"

    def test_same_family_new_naming(self):
        assert gemini.pick_replacement("gemini-4-argon-preview", self.AVAILABLE) == "gemini-4-argon"

    def test_vanished_family_stays_on_same_generation(self):
        assert gemini.pick_replacement("gemini-4-carbon", self.AVAILABLE).startswith("gemini-4-")

    def test_no_candidates(self):
        assert gemini.pick_replacement("gemini-3.1-pro", ["gemini-3.1-pro"]) is None


class TestGeminiThinking:
    def test_unknown_models_default_to_thinking(self):
        assert gemini.is_thinking_model("gemini-4-neon")
        assert gemini.is_thinking_model("gemini-9-future")

    def test_legacy_non_thinking(self):
        assert not gemini.is_thinking_model("gemini-2.0-flash")

    def test_metadata_wins(self):
        gemini._info["gemini-x-test"] = gemini.GeminiModelInfo("gemini-x-test", 1, 1, thinking=False)
        try:
            assert not gemini.is_thinking_model("gemini-x-test")
        finally:
            gemini._info.pop("gemini-x-test")


class TestClaudeReplacement:
    AVAILABLE = ["claude-opus-6", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"]

    def test_version_parsing(self):
        assert claude.model_version("claude-opus-4-6") == (4, 6)
        assert claude.model_version("claude-opus-5") == (5,)
        assert claude.model_family("claude-fable-5-2") == "fable"

    def test_same_family_newest(self):
        assert claude.pick_replacement("claude-opus-4-6", self.AVAILABLE) == "claude-opus-6"
        assert claude.pick_replacement("claude-sonnet-4-6", self.AVAILABLE) == "claude-sonnet-5-5"

    def test_vanished_family_newest_overall(self):
        assert claude.pick_replacement("claude-fable-5-2", self.AVAILABLE) == "claude-opus-6"
