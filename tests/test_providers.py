"""Per-provider conversion, reference parsing and self-description.

The HTTP layer is exercised in test_provider_http.py; these check the parts that
turn an upstream payload into a MediaItem, which is where the per-site quirks
live.
"""

import pytest

from sidecarr import providers
from sidecarr.config import AppConfig, Source
from sidecarr.providers.base import MediaItem, ProviderError, parse_year, set_id


@pytest.fixture
def config():
    return AppConfig()


class TestRegistry:
    def test_every_provider_builds(self, config):
        for key in providers.PROVIDERS:
            assert providers.build(key, config).key == key

    def test_unknown_provider_names_the_known_ones(self, config):
        with pytest.raises(ProviderError, match="Unknown list source"):
            providers.build("not-a-real-provider", config)

    def test_source_keys_are_unique_within_a_provider(self, config):
        for key in providers.PROVIDERS:
            provider = providers.build(key, config)
            keys = [s.key for s in provider.source_types]
            assert len(keys) == len(set(keys)), key

    def test_every_source_declares_at_least_one_media_type(self, config):
        for key in providers.PROVIDERS:
            for source in providers.build(key, config).source_types:
                assert source.media, f"{key}/{source.key}"

    def test_providers_needing_keys_say_how_to_get_them(self, config):
        for key in ("trakt", "tmdb", "mdblist", "plex"):
            provider = providers.build(key, config)
            assert not provider.configured()
            assert provider.setup_hint, key


class TestMediaItemHelpers:
    def test_set_id_drops_blanks_and_zeroes(self):
        ids = {}
        for value in (None, "", 0, "0"):
            set_id(ids, "tmdb", value)
        assert ids == {}

    def test_set_id_requires_tt_prefix_for_imdb(self):
        ids = {}
        set_id(ids, "imdb", "12345")
        set_id(ids, "imdb", "tt12345")
        assert ids == {"imdb": "tt12345"}

    def test_set_id_coerces_numeric_strings(self):
        ids = {}
        set_id(ids, "tmdb", "603")
        assert ids == {"tmdb": 603}

    @pytest.mark.parametrize(
        "value,expected",
        [(2016, 2016), ("2016-11-11", 2016), ("2016", 2016), (None, None), ("", None), (12, None)],
    )
    def test_parse_year(self, value, expected):
        assert parse_year(value) == expected

    def test_dedupe_prefers_ids_over_titles(self):
        items = [
            MediaItem(ids={"tmdb": 1}, title="One"),
            MediaItem(ids={"tmdb": 1}, title="One, renamed"),
            MediaItem(ids={"tmdb": 2}, title="Two"),
        ]
        assert len(providers.dedupe(items)) == 2

    def test_dedupe_falls_back_to_title_and_year(self):
        items = [MediaItem(title="Solo", year=2018), MediaItem(title="Solo", year=2018)]
        assert len(providers.dedupe(items)) == 1


class TestTmdb:
    def provider(self, config):
        config.tmdb.api_key = "key"
        return providers.build("tmdb", config)

    def test_needs_an_api_key(self, config):
        assert not providers.build("tmdb", config).configured()
        config.tmdb.api_key = "key"
        assert providers.build("tmdb", config).configured()

    def test_converts_a_movie_result(self, config):
        provider = self.provider(config)
        provider._genres["movie"] = {18: "drama", 878: "science fiction"}
        item = provider._items(
            [{
                "id": 329865, "title": "Arrival", "release_date": "2016-11-11",
                "genre_ids": [18, 878], "original_language": "en",
                "vote_average": 7.6, "vote_count": 15000,
            }],
            "movie",
        )[0]

        assert item.ids == {"tmdb": 329865}
        assert (item.title, item.year) == ("Arrival", 2016)
        assert item.genres == ["drama", "science fiction"]
        assert (item.language, item.rating, item.votes) == ("en", 7.6, 15000)

    def test_converts_a_show_result(self, config):
        provider = self.provider(config)
        provider._genres["tv"] = {}
        item = provider._items(
            [{"id": 95396, "name": "Severance", "first_air_date": "2022-02-18",
              "origin_country": ["US"]}],
            "show",
        )[0]
        assert (item.title, item.year, item.country) == ("Severance", 2022, "us")

    def test_entries_without_an_id_are_dropped(self, config):
        provider = self.provider(config)
        provider._genres["movie"] = {}
        assert provider._items([{"title": "No ID"}], "movie") == []

    @pytest.mark.parametrize(
        "given,expected",
        [("420", 420), ("  420 ", 420), ("https://www.themoviedb.org/collection/10-star-wars", 10)],
    )
    def test_numeric_ids_tolerate_pasted_urls(self, given, expected):
        from sidecarr.providers.tmdb import _number

        assert _number(given, "collection ID") == expected

    def test_non_numeric_id_is_rejected(self):
        from sidecarr.providers.tmdb import _number

        with pytest.raises(ProviderError, match="numeric TMDb"):
            _number("star-wars", "collection ID")

    def test_movies_need_no_id_resolution(self, config):
        """Radarr keys off TMDb, which is what TMDb already gave us."""
        item = MediaItem(ids={"tmdb": 1})
        self.provider(config).resolve_ids(item, "movie")
        assert item.ids == {"tmdb": 1}


class TestMdblist:
    def provider(self, config):
        config.mdblist.api_key = "key"
        return providers.build("mdblist", config)

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("https://mdblist.com/lists/someone/my-list", ("slug", "someone/my-list")),
            ("mdblist.com/lists/someone/my-list?sort=rank", ("slug", "someone/my-list")),
            ("someone/my-list", ("slug", "someone/my-list")),
            ("12345", ("id", "12345")),
        ],
    )
    def test_list_reference_forms(self, given, expected):
        from sidecarr.providers.mdblist import parse_list_ref

        assert parse_list_ref(given) == expected

    def test_bad_reference_is_reported(self):
        from sidecarr.providers.mdblist import parse_list_ref

        with pytest.raises(ProviderError, match="not an MDBList list"):
            parse_list_ref("just-a-word")

    def test_converts_items(self, config):
        item = self.provider(config)._items(
            [{"title": "Arrival", "imdb_id": "tt2543164", "tvdb_id": 0,
              "release_year": 2016, "mediatype": "movie"}],
            "movie",
        )[0]
        assert item.ids == {"imdb": "tt2543164"}
        assert (item.title, item.year) == ("Arrival", 2016)

    def test_a_mixed_list_keeps_only_the_wanted_kind(self, config):
        entries = [
            {"title": "A movie", "imdb_id": "tt1", "mediatype": "movie"},
            {"title": "A show", "tvdb_id": 2, "mediatype": "show"},
        ]
        provider = self.provider(config)
        assert [i.title for i in provider._items(entries, "movie")] == ["A movie"]
        assert [i.title for i in provider._items(entries, "show")] == ["A show"]

    def test_split_response_shape(self, config):
        payload = {"movies": [{"title": "M", "imdb_id": "tt1"}], "shows": []}
        assert [i.title for i in self.provider(config)._items(payload, "movie")] == ["M"]


class TestPlex:
    def test_needs_a_token(self, config):
        assert not providers.build("plex", config).configured()
        config.plex.token = "tok"
        assert providers.build("plex", config).configured()

    def test_reads_ids_out_of_plex_guids(self, config):
        from sidecarr.providers.plex import PlexProvider

        item = PlexProvider._items(
            [{
                "type": "movie", "title": "Arrival", "year": 2016, "duration": 6960000,
                "Guid": [{"id": "imdb://tt2543164"}, {"id": "tmdb://329865"}, {"id": "tvdb://12"}],
                "Genre": [{"tag": "Drama"}],
            }],
            "movie",
        )[0]

        assert item.ids == {"imdb": "tt2543164", "tmdb": 329865, "tvdb": 12}
        assert item.genres == ["drama"]
        assert item.runtime == 116  # milliseconds -> minutes

    def test_wrong_media_type_is_skipped(self, config):
        from sidecarr.providers.plex import PlexProvider

        entries = [{"type": "show", "title": "S", "Guid": [{"id": "tvdb://1"}]}]
        assert PlexProvider._items(entries, "movie") == []
        assert len(PlexProvider._items(entries, "show")) == 1

    def test_entries_without_guids_are_dropped(self, config):
        from sidecarr.providers.plex import PlexProvider

        assert PlexProvider._items([{"type": "movie", "title": "No guids"}], "movie") == []


class TestImdb:
    @pytest.mark.parametrize(
        "given", ["ls123456789", "https://www.imdb.com/list/ls123456789/", "  ls123456789 "]
    )
    def test_list_id_forms(self, given):
        from sidecarr.providers.imdb import _list_id

        assert _list_id(given) == "ls123456789"

    def test_bad_list_id_is_reported(self):
        from sidecarr.providers.imdb import _list_id

        with pytest.raises(ProviderError, match="not an IMDb list"):
            _list_id("my-favourites")

    def test_extracts_titles_from_embedded_json(self):
        from sidecarr.providers.imdb import _extract

        html = """<script type="application/json">
        {"a": {"items": [
          {"id": "tt0133093", "titleText": {"text": "The Matrix"},
           "releaseYear": {"year": 1999}},
          {"id": "tt0111161", "titleText": {"text": "The Shawshank Redemption"},
           "releaseYear": {"year": 1994}}
        ]}}</script>"""
        items = _extract(html)
        assert [(i.title, i.year) for i in items] == [
            ("The Matrix", 1999), ("The Shawshank Redemption", 1994),
        ]

    def test_falls_back_to_bare_ids(self):
        from sidecarr.providers.imdb import _extract

        items = _extract('<a href="/title/tt0133093/">x</a><a href="/title/tt0111161/">y</a>')
        assert [i.ids for i in items] == [{"imdb": "tt0133093"}, {"imdb": "tt0111161"}]
        assert all(i.title == "" for i in items)

    def test_chart_media_mismatch_is_explained(self, config):
        provider = providers.build("imdb", config)
        source = Source(provider="imdb", type="chart", options={"chart": "toptv"})
        with pytest.raises(ProviderError, match="shows chart"):
            provider.fetch(source, "movie")


class TestCustom:
    def test_requires_a_url(self, config):
        provider = providers.build("custom", config)
        with pytest.raises(ProviderError, match="needs a URL"):
            provider.fetch(Source(provider="custom", type="url"), "movie")

    def test_rejects_non_http_urls(self, config):
        provider = providers.build("custom", config)
        source = Source(provider="custom", type="url", options={"url": "file:///etc/passwd"})
        with pytest.raises(ProviderError, match="not an http"):
            provider.fetch(source, "movie")


class TestStevenLu:
    def test_is_movies_only(self, config):
        provider = providers.build("stevenlu", config)
        assert provider.media_types() == {"movie"}
        with pytest.raises(ProviderError, match="movies only"):
            provider.fetch(Source(provider="stevenlu", type="popular"), "show")


class TestArrInstance:
    def test_needs_url_and_key(self, config):
        provider = providers.build("arr", config)
        with pytest.raises(ProviderError, match="URL and API key"):
            provider.fetch(Source(provider="arr", type="library"), "movie")


class TestSourceOptions:
    def test_named_fields_win_over_options(self):
        source = Source(list_url="named", options={"list_url": "optional"})
        assert source.get("list_url") == "named"

    def test_options_are_read_when_no_named_field_exists(self):
        assert Source(options={"company_id": "420"}).get("company_id") == "420"

    def test_missing_key_returns_the_default(self):
        assert Source().get("nope", "fallback") == "fallback"

    def test_provider_defaults_to_trakt(self):
        assert Source().provider == "trakt"
