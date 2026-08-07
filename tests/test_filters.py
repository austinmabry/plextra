from plextra.config import Filters
from plextra.filters import evaluate, sort_items
from plextra.providers.base import MediaItem


def movie(**overrides):
    base = dict(
        ids={"trakt": 1, "tmdb": 329865, "imdb": "tt2543164"},
        title="Arrival",
        year=2016,
        runtime=116,
        country="us",
        language="en",
        genres=["science-fiction", "drama"],
        rating=8.1,
        votes=42000,
        released="2016-11-11",
    )
    base.update(overrides)
    return MediaItem(**base)


def show(**overrides):
    base = dict(
        ids={"trakt": 2, "tvdb": 371980, "tmdb": 95396},
        title="Severance",
        year=2022,
        runtime=50,
        country="us",
        language="en",
        network="Apple TV+",
        genres=["drama", "science-fiction"],
        rating=8.6,
        votes=9000,
        released="2022-02-18",
    )
    base.update(overrides)
    return MediaItem(**base)


class TestDefaults:
    def test_empty_filters_pass_everything(self):
        assert evaluate(movie(), "movie", Filters()) is None
        assert evaluate(show(), "show", Filters()) is None

    def test_recent_titles_are_not_silently_dropped(self):
        """traktarr's stock config blacklisted anything after 2019."""
        assert evaluate(movie(year=2025), "movie", Filters()) is None

    def test_metadata_poor_item_passes_when_unfiltered(self):
        """An IMDb list gives little more than an ID; that is fine unfiltered."""
        sparse = MediaItem(ids={"imdb": "tt0133093"}, title="The Matrix")
        assert evaluate(sparse, "movie", Filters()) is None

    def test_item_with_neither_title_nor_id_is_rejected(self):
        assert evaluate(MediaItem(), "movie", Filters()) == "no title and no ID"


class TestYear:
    def test_below_min_year(self):
        assert "before 2000" in evaluate(movie(year=1994), "movie", Filters(min_year=2000))

    def test_above_max_year(self):
        assert "after 2020" in evaluate(movie(year=2024), "movie", Filters(max_year=2020))

    def test_inside_range(self):
        assert evaluate(movie(), "movie", Filters(min_year=2000, max_year=2020)) is None

    def test_missing_year_only_matters_when_filtering(self):
        item = movie(year=None)
        assert evaluate(item, "movie", Filters()) is None
        reason = evaluate(item, "movie", Filters(min_year=2000), "IMDb")
        assert reason == "no release year from IMDb"


class TestRuntime:
    def test_under_min(self):
        assert "under 60m" in evaluate(movie(runtime=40), "movie", Filters(min_runtime=60))

    def test_over_max(self):
        assert "over 100m" in evaluate(movie(runtime=180), "movie", Filters(max_runtime=100))

    def test_within_bounds(self):
        assert evaluate(movie(), "movie", Filters(min_runtime=60, max_runtime=200)) is None

    def test_missing_runtime_names_the_provider(self):
        """TMDb list endpoints carry no runtime, and that should be visible."""
        reason = evaluate(movie(runtime=None), "movie", Filters(min_runtime=60), "TMDb")
        assert reason == "no runtime from TMDb"


class TestCountryAndLanguage:
    def test_empty_allows_anything(self):
        assert evaluate(movie(country="fr"), "movie", Filters()) is None

    def test_allowed_value_passes(self):
        assert evaluate(movie(country="us"), "movie", Filters(allowed_countries=["us", "gb"])) is None

    def test_disallowed_value_blocked(self):
        assert "country is FR" in evaluate(movie(country="fr"), "movie", Filters(allowed_countries=["us"]))

    def test_missing_value_blocked_when_a_list_is_set(self):
        reason = evaluate(movie(country=None), "movie", Filters(allowed_countries=["us"]), "Plex")
        assert reason == "no country from Plex"

    def test_ignore_keyword_allows_missing_values(self):
        assert evaluate(movie(country=None), "movie", Filters(allowed_countries=["ignore"])) is None

    def test_matching_is_exact_not_substring(self):
        """traktarr's substring match meant 'us' also matched 'rus'."""
        assert evaluate(movie(country="rus"), "movie", Filters(allowed_countries=["us"])) is not None

    def test_language_filter(self):
        assert evaluate(movie(language="ja"), "movie", Filters(allowed_languages=["en"])) is not None
        assert evaluate(movie(language="en"), "movie", Filters(allowed_languages=["en"])) is None


class TestGenresNetworksTitles:
    def test_blacklisted_genre(self):
        assert evaluate(movie(), "movie", Filters(blacklisted_genres=["drama"])) == "blacklisted genre drama"

    def test_genre_case_insensitive(self):
        assert evaluate(movie(), "movie", Filters(blacklisted_genres=["DRAMA"])) is not None

    def test_unrelated_genre_passes(self):
        assert evaluate(movie(), "movie", Filters(blacklisted_genres=["horror"])) is None

    def test_missing_genres_names_the_provider(self):
        reason = evaluate(movie(genres=[]), "movie", Filters(blacklisted_genres=["horror"]), "IMDb")
        assert reason == "no genres from IMDb"

    def test_blacklisted_network_for_shows(self):
        assert "blacklisted network" in evaluate(show(), "show", Filters(blacklisted_networks=["apple"]))

    def test_networks_ignored_for_movies(self):
        assert evaluate(movie(), "movie", Filters(blacklisted_networks=["apple"])) is None

    def test_title_keyword(self):
        reason = evaluate(
            movie(title="Untitled Sequel"), "movie", Filters(blacklisted_title_keywords=["untitled"])
        )
        assert "untitled" in reason

    def test_blacklisted_id_uses_tmdb_for_movies(self):
        assert evaluate(movie(), "movie", Filters(blacklisted_ids=[329865])) == "blacklisted ID 329865"

    def test_blacklisted_id_uses_tvdb_for_shows(self):
        assert evaluate(show(), "show", Filters(blacklisted_ids=[371980])) is not None

    def test_blacklist_ignores_ids_the_target_does_not_use(self):
        """A show is keyed by TVDb, so a TMDb number must not match it."""
        assert evaluate(show(), "show", Filters(blacklisted_ids=[999999])) is None


class TestRatings:
    def test_min_rating(self):
        assert "under 9" in evaluate(movie(rating=8.1), "movie", Filters(min_rating=9.0))
        assert evaluate(movie(rating=8.1), "movie", Filters(min_rating=8.0)) is None

    def test_min_votes(self):
        assert "under 50000" in evaluate(movie(), "movie", Filters(min_votes=50000))
        assert evaluate(movie(), "movie", Filters(min_votes=1000)) is None

    def test_missing_rating_names_the_provider(self):
        reason = evaluate(movie(rating=None), "movie", Filters(min_rating=7.0), "a custom list")
        assert reason == "no rating from a custom list"


class TestMediaItem:
    def test_target_id_per_media_type(self):
        assert movie().target_id("movie") == 329865
        assert show().target_id("show") == 371980
        assert MediaItem(ids={"imdb": "tt1"}).target_id("movie") is None

    def test_imdb_id_rejects_junk(self):
        assert MediaItem(ids={"imdb": "tt0133093"}).imdb_id == "tt0133093"
        assert MediaItem(ids={"imdb": "12345"}).imdb_id is None
        assert MediaItem().imdb_id is None

    def test_label(self):
        assert movie().label == "Arrival (2016)"
        assert MediaItem(title="Nameless").label == "Nameless"


class TestSorting:
    def test_sort_by_votes(self):
        items = [movie(title="A", votes=10), movie(title="B", votes=90)]
        assert [i.title for i in sort_items(items, "movie", "votes")] == ["B", "A"]

    def test_sort_by_release_date(self):
        items = [movie(title="A", released="2001-01-01"), movie(title="B", released="2020-01-01")]
        assert [i.title for i in sort_items(items, "movie", "released")] == ["B", "A"]

    def test_sort_none_keeps_order(self):
        items = [movie(title="A", votes=1), movie(title="B", votes=99)]
        assert [i.title for i in sort_items(items, "movie", "none")] == ["A", "B"]

    def test_sort_tolerates_missing_values(self):
        items = [movie(title="A", votes=None), movie(title="B", votes=5)]
        assert [i.title for i in sort_items(items, "movie", "votes")] == ["B", "A"]
