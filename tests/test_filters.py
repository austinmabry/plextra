from plextra.config import Filters
from plextra.filters import (
    describe,
    evaluate,
    external_id,
    item_year,
    sort_items,
)


def movie(**overrides):
    base = {
        "title": "Arrival",
        "year": 2016,
        "ids": {"trakt": 1, "tmdb": 329865, "imdb": "tt2543164"},
        "runtime": 116,
        "country": "us",
        "language": "en",
        "genres": ["science-fiction", "drama"],
        "rating": 8.1,
        "votes": 42000,
        "released": "2016-11-11",
    }
    base.update(overrides)
    return base


def show(**overrides):
    base = {
        "title": "Severance",
        "year": 2022,
        "ids": {"trakt": 2, "tvdb": 371980, "tmdb": 95396},
        "runtime": 50,
        "country": "us",
        "language": "en",
        "network": "Apple TV+",
        "genres": ["drama", "science-fiction"],
        "rating": 8.6,
        "votes": 9000,
        "first_aired": "2022-02-18T08:00:00.000Z",
    }
    base.update(overrides)
    return base


class TestDefaults:
    def test_empty_filters_pass_everything(self):
        assert evaluate(movie(), "movie", Filters()) is None
        assert evaluate(show(), "show", Filters()) is None

    def test_recent_titles_are_not_silently_dropped(self):
        """traktarr's stock config blacklisted anything after 2019."""
        assert evaluate(movie(year=2025), "movie", Filters()) is None

    def test_missing_metadata_passes_when_unfiltered(self):
        sparse = {"title": "Mystery", "ids": {"tmdb": 5}}
        assert evaluate(sparse, "movie", Filters()) is None


class TestYear:
    def test_below_min_year(self):
        reason = evaluate(movie(year=1994), "movie", Filters(min_year=2000))
        assert "before 2000" in reason

    def test_above_max_year(self):
        reason = evaluate(movie(year=2024), "movie", Filters(max_year=2020))
        assert "after 2020" in reason

    def test_inside_range(self):
        assert evaluate(movie(year=2016), "movie", Filters(min_year=2000, max_year=2020)) is None

    def test_missing_year_only_matters_when_filtering(self):
        item = movie(year=None, released=None)
        assert evaluate(item, "movie", Filters()) is None
        assert evaluate(item, "movie", Filters(min_year=2000)) == "no release year on Trakt"

    def test_show_year_falls_back_to_first_aired(self):
        assert item_year(show(year=None)) == 2022


class TestRuntime:
    def test_under_min(self):
        assert "under 60m" in evaluate(movie(runtime=40), "movie", Filters(min_runtime=60))

    def test_over_max(self):
        assert "over 100m" in evaluate(movie(runtime=180), "movie", Filters(max_runtime=100))

    def test_within_bounds(self):
        assert evaluate(movie(), "movie", Filters(min_runtime=60, max_runtime=200)) is None


class TestCountryAndLanguage:
    def test_empty_allows_anything(self):
        assert evaluate(movie(country="fr"), "movie", Filters()) is None

    def test_allowed_value_passes(self):
        assert evaluate(movie(country="us"), "movie", Filters(allowed_countries=["us", "gb"])) is None

    def test_disallowed_value_blocked(self):
        reason = evaluate(movie(country="fr"), "movie", Filters(allowed_countries=["us"]))
        assert "country is FR" in reason

    def test_missing_value_blocked_when_a_list_is_set(self):
        reason = evaluate(movie(country=None), "movie", Filters(allowed_countries=["us"]))
        assert reason == "no country listed on Trakt"

    def test_ignore_keyword_allows_missing_values(self):
        assert evaluate(movie(country=None), "movie", Filters(allowed_countries=["ignore"])) is None

    def test_matching_is_exact_not_substring(self):
        """traktarr's substring match meant 'us' also matched 'rus'."""
        reason = evaluate(movie(country="rus"), "movie", Filters(allowed_countries=["us"]))
        assert reason is not None

    def test_language_filter(self):
        assert evaluate(movie(language="ja"), "movie", Filters(allowed_languages=["en"])) is not None
        assert evaluate(movie(language="en"), "movie", Filters(allowed_languages=["en"])) is None


class TestGenresNetworksTitles:
    def test_blacklisted_genre(self):
        reason = evaluate(movie(), "movie", Filters(blacklisted_genres=["drama"]))
        assert "blacklisted genre drama" == reason

    def test_genre_case_insensitive(self):
        assert evaluate(movie(), "movie", Filters(blacklisted_genres=["DRAMA"])) is not None

    def test_unrelated_genre_passes(self):
        assert evaluate(movie(), "movie", Filters(blacklisted_genres=["horror"])) is None

    def test_blacklisted_network_for_shows(self):
        reason = evaluate(show(), "show", Filters(blacklisted_networks=["apple"]))
        assert "blacklisted network" in reason

    def test_networks_ignored_for_movies(self):
        assert evaluate(movie(), "movie", Filters(blacklisted_networks=["apple"])) is None

    def test_title_keyword(self):
        reason = evaluate(movie(title="Untitled Sequel"), "movie", Filters(
            blacklisted_title_keywords=["untitled"]
        ))
        assert "untitled" in reason

    def test_blacklisted_id(self):
        reason = evaluate(movie(), "movie", Filters(blacklisted_ids=[329865]))
        assert reason == "blacklisted ID 329865"

    def test_show_id_uses_tvdb(self):
        assert evaluate(show(), "show", Filters(blacklisted_ids=[371980])) is not None
        assert evaluate(show(), "show", Filters(blacklisted_ids=[95396])) is None


class TestRatings:
    def test_min_rating(self):
        assert "under 9" in evaluate(movie(rating=8.1), "movie", Filters(min_rating=9.0))
        assert evaluate(movie(rating=8.1), "movie", Filters(min_rating=8.0)) is None

    def test_min_votes(self):
        assert "under 50000" in evaluate(movie(), "movie", Filters(min_votes=50000))
        assert evaluate(movie(), "movie", Filters(min_votes=1000)) is None


class TestHelpers:
    def test_external_id_per_media_type(self):
        assert external_id(movie(), "movie") == 329865
        assert external_id(show(), "show") == 371980
        assert external_id({"ids": {}}, "movie") is None

    def test_describe(self):
        assert describe(movie()) == "Arrival (2016)"
        assert describe({"title": "Nameless"}) == "Nameless"

    def test_sort_by_votes(self):
        items = [movie(title="A", votes=10), movie(title="B", votes=90)]
        assert [i["title"] for i in sort_items(items, "movie", "votes")] == ["B", "A"]

    def test_sort_by_release_date(self):
        items = [movie(title="A", released="2001-01-01"), movie(title="B", released="2020-01-01")]
        assert [i["title"] for i in sort_items(items, "movie", "released")] == ["B", "A"]

    def test_sort_none_keeps_order(self):
        items = [movie(title="A", votes=1), movie(title="B", votes=99)]
        assert [i["title"] for i in sort_items(items, "movie", "none")] == ["A", "B"]

    def test_sort_tolerates_missing_keys(self):
        items = [movie(title="A", votes=None), movie(title="B", votes=5)]
        assert [i["title"] for i in sort_items(items, "movie", "votes")] == ["B", "A"]
