"""Title matching. Deterministic by design, so it is testable without a model."""

from scraper_agent.evals.matching import match_titles, normalise_title, similarity


# --- normalisation --------------------------------------------------------


def test_normalise_strips_punctuation_and_case():
    assert normalise_title("Men's Strider — Grey!") == "mens strider grey"


def test_apostrophes_are_deleted_not_split():
    """A model dropping the apostrophe must still match exactly."""
    assert normalise_title("Men's") == normalise_title("Mens") == "mens"
    assert normalise_title("Women’s Tree Runner") == "womens tree runner"


def test_normalise_collapses_whitespace():
    assert normalise_title("  Widget   Pro \n") == "widget pro"


def test_normalise_handles_none_and_numbers():
    assert normalise_title(None) == ""
    assert normalise_title(51.77) == "51 77"


# --- the three rules ------------------------------------------------------


def test_exact_match_after_normalisation():
    score, kind = similarity("Men's Strider", "mens strider")
    assert kind == "exact" and score == 1.0


def test_containment_handles_page_vs_api_titles():
    # The listing says "Men's Strider"; the API says the full variant name.
    score, kind = similarity(
        "Men's Strider", "Men's Strider - Medium Grey (Blizzard Sole)"
    )
    assert kind == "containment" and score >= 0.9


def test_single_word_does_not_match_by_containment():
    # "Shoes" must not marry "Wool Runner Shoes For Everyday".
    _, kind = similarity("Shoes", "Wool Runner Shoes For Everyday")
    assert kind != "containment"


def test_containment_requires_contiguity():
    _, kind = similarity("wool everyday", "wool runner shoes for everyday")
    assert kind != "containment"


def test_fuzzy_catches_small_typos():
    score, kind = similarity("Tree Dasher 2", "Tree Dashr 2")
    assert kind == "fuzzy" and score > 0.85


def test_unrelated_titles_score_low():
    score, _ = similarity("Wool Runner", "Kitchen Blender 5000")
    assert score < 0.6


def test_empty_titles_never_match():
    assert similarity("", "anything") == (0.0, "none")
    assert similarity(None, None) == (0.0, "none")


# --- pairing --------------------------------------------------------------


def test_matches_are_paired_by_index():
    result = match_titles(["Tree Dasher", "Wool Runner"], ["Wool Runner", "Tree Dasher"])
    assert result.matched_count == 2
    pairs = {(m.predicted_index, m.truth_index) for m in result.matches}
    assert pairs == {(0, 1), (1, 0)}


def test_unmatched_are_reported_on_both_sides():
    result = match_titles(["Real Product", "Login"], ["Real Product", "Another Thing"])
    assert result.matched_count == 1
    assert result.unmatched_predicted == [1]   # "Login" is a hallucination
    assert result.unmatched_truth == [1]       # "Another Thing" was missed


def test_each_truth_is_consumed_once():
    """Two predictions of one product = one match plus one false positive."""
    result = match_titles(["Wool Runner", "Wool Runner"], ["Wool Runner"])
    assert result.matched_count == 1
    assert result.unmatched_predicted == [1]


def test_threshold_is_respected():
    strict = match_titles(["Tree Dashr 2"], ["Tree Dasher 2"], threshold=0.99)
    assert strict.matched_count == 0
    loose = match_titles(["Tree Dashr 2"], ["Tree Dasher 2"], threshold=0.80)
    assert loose.matched_count == 1


def test_empty_inputs_are_safe():
    assert match_titles([], []).matched_count == 0
    assert match_titles(["x"], []).unmatched_predicted == [0]
    assert match_titles([], ["x"]).unmatched_truth == [0]
