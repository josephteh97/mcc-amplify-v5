"""Step 2b regression tests — primary VLM + checker (PLAN.md §5.4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.classify.cache     import JudgeCache
from backend.classify.llm_judge import (
    CLASSIFIER_LLM_CONF_MIN,
    DEFAULT_CACHE_PATH,
    classify_llm,
    is_ollama_reachable,
    parse_response,
    render_thumbnail_png,
)
from backend.classify.types     import ClassifierTier, DrawingClass


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE   = REPO_ROOT / "tests" / "fixtures" / "sample_uploaded_documents"

fixture_required = pytest.mark.skipif(
    not FIXTURE.exists(),
    reason="reference fixture symlink missing — see PLAN.md §3.1",
)


# ── parse_response ──────────────────────────────────────────────────────────

def test_parse_response_canonical():
    raw = "CLASS: STRUCT_PLAN_OVERALL\nCONFIDENCE: 0.95\nREASON: grid bubbles around perimeter, sparse columns and beams."
    cls, conf, reason = parse_response(raw)
    assert cls == "STRUCT_PLAN_OVERALL"
    assert conf == 0.95
    assert reason and "grid bubbles" in reason


def test_parse_response_lowercase_keys():
    raw = "class: discard\nconfidence: 1.0\nreason: room labels visible"
    cls, conf, reason = parse_response(raw)
    assert cls == "DISCARD"
    assert conf == 1.0


def test_parse_response_extra_text():
    raw = "Looking at this drawing:\n\nCLASS: ELEVATION\nCONFIDENCE: 0.8\nREASON: external view with horizontal level lines"
    cls, conf, _ = parse_response(raw)
    assert cls == "ELEVATION"
    assert conf == 0.8


def test_parse_response_empty():
    assert parse_response("") == (None, None, None)


def test_parse_response_partial():
    cls, conf, reason = parse_response("CLASS: SECTION")
    assert cls == "SECTION"
    assert conf is None
    assert reason is None


# ── JudgeCache ──────────────────────────────────────────────────────────────

def test_cache_round_trip(tmp_path: Path):
    cache = JudgeCache(tmp_path / "cache.sqlite")
    assert cache.get("hash-a", "primary-model") is None
    cache.put("hash-a", "primary-model", "DISCARD", 0.95, "rooms visible", "raw=…")
    j = cache.get("hash-a", "primary-model")
    assert j is not None
    assert j.drawing_class == "DISCARD"
    assert j.confidence    == 0.95


def test_cache_keyed_by_model(tmp_path: Path):
    cache = JudgeCache(tmp_path / "cache.sqlite")
    cache.put("h", "primary",  "STRUCT_PLAN_OVERALL", 0.9, "a", "r1")
    cache.put("h", "checker",  "DISCARD",             0.8, "b", "r2")
    assert cache.get("h", "primary").drawing_class  == "STRUCT_PLAN_OVERALL"
    assert cache.get("h", "checker").drawing_class  == "DISCARD"


def test_cache_overwrites_on_repeat_put(tmp_path: Path):
    cache = JudgeCache(tmp_path / "cache.sqlite")
    cache.put("h", "m", "STRUCT_PLAN_OVERALL", 0.9, "first", "r1")
    cache.put("h", "m", "DISCARD",             0.8, "second","r2")
    j = cache.get("h", "m")
    assert j.drawing_class == "DISCARD"
    assert j.reason        == "second"


# ── classify_llm with mocked HTTP — primary + checker combination rule ──────

def _fake_call(verdicts: dict) -> callable:
    """Return a side_effect that maps model name → response string.

    `verdicts` keys are model names; values are the raw text the model returns.
    """
    def _impl(image_b64, model, host, timeout):
        return verdicts.get(model, "")
    return _impl


@fixture_required
def test_combination_agree_accepts(tmp_path: Path):
    """Primary and checker pick the same class → accept, max confidence."""
    pdf = next(FIXTURE.rglob("*.pdf"))
    cache = JudgeCache(tmp_path / "cache.sqlite")

    verdicts = {
        "primary": "CLASS: STRUCT_PLAN_OVERALL\nCONFIDENCE: 0.85\nREASON: structural plan, sparse",
        "checker": "CLASS: STRUCT_PLAN_OVERALL\nCONFIDENCE: 0.95\nREASON: structural plan, columns at grid intersections",
    }
    with patch("backend.classify.llm_judge.is_ollama_reachable", return_value=True), \
         patch("backend.classify.llm_judge._call_ollama", side_effect=_fake_call(verdicts)):
        r = classify_llm(pdf, 0, "h", cache=cache,
                         primary_model="primary", checker_model="checker")
    assert r is not None
    assert r.drawing_class == DrawingClass.STRUCT_PLAN_OVERALL
    assert r.confidence    == 0.95
    assert r.tier          == ClassifierTier.LLM
    assert r.signals.get("checker_agreed") is True


@fixture_required
def test_combination_disagree_unresolved(tmp_path: Path):
    """Primary and checker disagree → UNRESOLVED, both verdicts preserved."""
    pdf = next(FIXTURE.rglob("*.pdf"))
    cache = JudgeCache(tmp_path / "cache.sqlite")

    verdicts = {
        "primary": "CLASS: STRUCT_PLAN_OVERALL\nCONFIDENCE: 0.9\nREASON: structural",
        "checker": "CLASS: DISCARD\nCONFIDENCE: 0.85\nREASON: rooms visible",
    }
    with patch("backend.classify.llm_judge.is_ollama_reachable", return_value=True), \
         patch("backend.classify.llm_judge._call_ollama", side_effect=_fake_call(verdicts)):
        r = classify_llm(pdf, 0, "h", cache=cache,
                         primary_model="primary", checker_model="checker")
    assert r is not None
    assert r.drawing_class == DrawingClass.UNKNOWN
    assert r.tier          == ClassifierTier.UNRESOLVED
    assert r.signals["checker_agreed"] is False
    assert r.signals["primary"]["class"] == "STRUCT_PLAN_OVERALL"
    assert r.signals["checker"]["class"] == "DISCARD"


@fixture_required
def test_combination_primary_fails_falls_to_unresolved(tmp_path: Path):
    """Primary model returns nothing parseable → UNRESOLVED with checker's vote captured."""
    pdf = next(FIXTURE.rglob("*.pdf"))
    cache = JudgeCache(tmp_path / "cache.sqlite")

    verdicts = {
        "primary": "garbled nonsense",
        "checker": "CLASS: SECTION\nCONFIDENCE: 0.9\nREASON: vertical cut",
    }
    with patch("backend.classify.llm_judge.is_ollama_reachable", return_value=True), \
         patch("backend.classify.llm_judge._call_ollama", side_effect=_fake_call(verdicts)):
        r = classify_llm(pdf, 0, "h", cache=cache,
                         primary_model="primary", checker_model="checker", )
    assert r is not None
    assert r.tier == ClassifierTier.UNRESOLVED
    assert r.signals.get("primary_failed") is True
    assert r.signals["checker"]["class"] == "SECTION"


@fixture_required
def test_combination_checker_skipped_when_disabled(tmp_path: Path, monkeypatch):
    """Env disables checker → accept primary alone if confident."""
    pdf = next(FIXTURE.rglob("*.pdf"))
    cache = JudgeCache(tmp_path / "cache.sqlite")
    monkeypatch.setattr("backend.classify.llm_judge.CHECKER_DISABLED", True)

    verdicts = {"primary": "CLASS: ELEVATION\nCONFIDENCE: 0.9\nREASON: levels"}
    with patch("backend.classify.llm_judge.is_ollama_reachable", return_value=True), \
         patch("backend.classify.llm_judge._call_ollama", side_effect=_fake_call(verdicts)) as call_m:
        r = classify_llm(pdf, 0, "h", cache=cache,
                         primary_model="primary", checker_model="checker")
    assert r is not None
    assert r.drawing_class == DrawingClass.ELEVATION
    assert r.signals.get("checker_skipped") is True
    assert call_m.call_count == 1   # only primary called


@fixture_required
def test_cache_hit_avoids_second_http(tmp_path: Path):
    """Re-running the same page with cached primary+checker → no HTTP."""
    pdf = next(FIXTURE.rglob("*.pdf"))
    cache = JudgeCache(tmp_path / "cache.sqlite")
    cache.put("h", "primary", "STRUCT_PLAN_OVERALL", 0.9, "ok", "raw1")
    cache.put("h", "checker", "STRUCT_PLAN_OVERALL", 0.95, "ok", "raw2")

    with patch("backend.classify.llm_judge.is_ollama_reachable", return_value=True), \
         patch("backend.classify.llm_judge._call_ollama") as call_m:
        r = classify_llm(pdf, 0, "h", cache=cache,
                         primary_model="primary", checker_model="checker")
    assert r is not None
    assert r.drawing_class == DrawingClass.STRUCT_PLAN_OVERALL
    assert r.signals["primary"]["cached"] is True
    assert r.signals["checker"]["cached"] is True
    assert call_m.call_count == 0


@fixture_required
def test_classify_llm_returns_none_when_ollama_down(tmp_path: Path):
    pdf   = next(FIXTURE.rglob("*.pdf"))
    cache = JudgeCache(tmp_path / "cache.sqlite")
    with patch("backend.classify.llm_judge.is_ollama_reachable", return_value=False):
        assert classify_llm(pdf, 0, "h", cache=cache) is None


@fixture_required
def test_classify_llm_disabled_via_env(tmp_path: Path, monkeypatch):
    pdf   = next(FIXTURE.rglob("*.pdf"))
    cache = JudgeCache(tmp_path / "cache.sqlite")
    monkeypatch.setattr("backend.classify.llm_judge.LLM_DISABLED", True)
    with patch("backend.classify.llm_judge.is_ollama_reachable") as reach:
        assert classify_llm(pdf, 0, "h", cache=cache) is None
        reach.assert_not_called()


# ── Rendering ───────────────────────────────────────────────────────────────

@fixture_required
def test_render_thumbnail_returns_png_bytes():
    pdf = next(FIXTURE.rglob("*.pdf"))
    png = render_thumbnail_png(pdf, 0, max_px=512)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert 1024 < len(png) < 1_000_000


# ── Live integration (slow, skipped if Ollama is down) ──────────────────────

ollama_required = pytest.mark.skipif(
    not is_ollama_reachable(),
    reason="Ollama not reachable at $OLLAMA_HOST (default http://localhost:11434)",
)


@pytest.mark.slow
@fixture_required
@ollama_required
def test_primary_plus_checker_discards_arch_zone_plans(tmp_path: Path):
    """5 ARCH zone-plans should land DISCARD via primary+checker agreement.

    Real model output is non-deterministic; we accept ≥4/5 acceptances of
    DISCARD. PLAN.md §16: LLM is the catch-all and the UI fallback handles
    residual disagreement."""
    arch_dir = FIXTURE / "02 111 - ENLARGED PLANS"
    if not arch_dir.exists():
        pytest.skip("ARCH zone-plan folder missing from fixture")

    samples = sorted(arch_dir.glob("*.pdf"))[:5]
    cache   = JudgeCache(tmp_path / "cache.sqlite")

    accepted_discard  = 0
    decisions: list[str] = []
    for pdf in samples:
        r = classify_llm(pdf, 0, page_hash=pdf.name, cache=cache)
        if r is None:
            decisions.append(f"{pdf.name} → None")
            continue
        decisions.append(
            f"{pdf.name:50} → {r.drawing_class.value:20} tier={r.tier.value} "
            f"conf={r.confidence:.2f} agreed={r.signals.get('checker_agreed', '?')}"
        )
        if r.drawing_class == DrawingClass.DISCARD and r.tier == ClassifierTier.LLM:
            accepted_discard += 1

    assert accepted_discard >= 4, (
        "Primary+checker should agree on DISCARD for ARCH zones:\n" + "\n".join(decisions)
    )
