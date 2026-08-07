"""The generic parser behind custom lists, RSS import and StevenLu."""

import json

import pytest

from plextra.providers.base import ProviderError
from plextra.providers.payload import parse_any


def ids(items):
    return [i.ids for i in items]


class TestSonarrCustomFormat:
    """{title, tvdbId, tmdbId, imdbId} - Sonarr's own custom list shape."""

    def test_full_record(self):
        payload = [{"title": "Severance", "tvdbId": 371980, "tmdbId": 95396, "imdbId": "tt11280740"}]
        item = parse_any(payload, "show")[0]
        assert item.title == "Severance"
        assert item.ids == {"tvdb": 371980, "tmdb": 95396, "imdb": "tt11280740"}

    def test_zero_ids_are_dropped_not_stored(self):
        """Sonarr sends 0 for "unknown", which must not become a real ID."""
        item = parse_any([{"title": "X", "tvdbId": 1, "tmdbId": 0}], "show")[0]
        assert item.ids == {"tvdb": 1}


class TestStevenLuFormat:
    def test_title_and_imdb_id(self):
        payload = [{"title": "The Matrix", "imdb_id": "tt0133093", "poster_url": "..."}]
        item = parse_any(payload, "movie")[0]
        assert item.title == "The Matrix"
        assert item.ids == {"imdb": "tt0133093"}


class TestMdblistShape:
    def test_split_buckets_pick_the_right_half(self):
        payload = {
            "movies": [{"title": "A movie", "imdb_id": "tt1"}],
            "shows": [{"title": "A show", "tvdb_id": 2}],
        }
        assert [i.title for i in parse_any(payload, "movie")] == ["A movie"]
        assert [i.title for i in parse_any(payload, "show")] == ["A show"]


class TestBareIds:
    def test_list_of_numbers_uses_the_media_type(self):
        assert ids(parse_any([603, 604], "movie")) == [{"tmdb": 603}, {"tmdb": 604}]
        assert ids(parse_any([1, 2], "show")) == [{"tvdb": 1}, {"tvdb": 2}]

    def test_id_hint_overrides_the_default(self):
        assert ids(parse_any([603], "show", id_hint="tmdb")) == [{"tmdb": 603}]

    def test_list_of_imdb_strings(self):
        assert ids(parse_any(["tt0133093", "tt0111161"], "movie")) == [
            {"imdb": "tt0133093"}, {"imdb": "tt0111161"},
        ]

    def test_newline_separated_text(self):
        assert ids(parse_any("tt0133093\ntt0111161\n", "movie")) == [
            {"imdb": "tt0133093"}, {"imdb": "tt0111161"},
        ]

    def test_comma_separated_text(self):
        assert ids(parse_any("603, 604", "movie")) == [{"tmdb": 603}, {"tmdb": 604}]


class TestWrappers:
    @pytest.mark.parametrize("key", ["items", "results", "entries", "data"])
    def test_wrapped_arrays(self, key):
        assert len(parse_any({key: [{"tmdbId": 1}]}, "movie")) == 1

    def test_single_object_is_a_list_of_one(self):
        assert len(parse_any({"title": "Solo", "tmdbId": 1}, "movie")) == 1

    def test_json_supplied_as_text(self):
        assert len(parse_any(json.dumps([{"tmdbId": 1}]), "movie")) == 1


class TestMetadata:
    def test_year_from_a_date_field(self):
        assert parse_any([{"tmdbId": 1, "release_date": "2016-11-11"}], "movie")[0].year == 2016

    def test_year_pulled_out_of_the_title(self):
        item = parse_any([{"tmdbId": 1, "title": "The Matrix (1999)"}], "movie")[0]
        assert (item.title, item.year) == ("The Matrix", 1999)

    def test_genres_as_a_comma_string(self):
        item = parse_any([{"tmdbId": 1, "genres": "Drama, Sci-Fi"}], "movie")[0]
        assert item.genres == ["drama", "sci-fi"]

    def test_genres_as_a_list(self):
        assert parse_any([{"tmdbId": 1, "genres": ["Drama"]}], "movie")[0].genres == ["drama"]

    def test_field_names_are_case_insensitive(self):
        assert parse_any([{"TMDBID": 7}], "movie")[0].ids == {"tmdb": 7}


class TestRss:
    FEED = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>The Matrix (1999)</title>
        <link>https://www.imdb.com/title/tt0133093/</link></item>
      <item><title>Heat</title><guid>tt0113277</guid></item>
    </channel></rss>"""

    def test_imdb_ids_are_pulled_from_links_and_guids(self):
        items = parse_any(self.FEED, "movie")
        assert ids(items) == [{"imdb": "tt0133093"}, {"imdb": "tt0113277"}]

    def test_titles_survive(self):
        assert [i.title for i in parse_any(self.FEED, "movie")] == ["The Matrix", "Heat"]

    def test_atom_entries_too(self):
        feed = """<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><title>Dune</title><link href="https://imdb.com/title/tt1160419/"/></entry>
        </feed>"""
        assert ids(parse_any(feed, "movie")) == [{"imdb": "tt1160419"}]

    def test_malformed_xml_is_reported_clearly(self):
        with pytest.raises(ProviderError, match="could not parse"):
            parse_any("<rss><channel><item>", "movie")


class TestRejects:
    def test_entries_with_no_usable_id_are_dropped(self):
        assert parse_any([{"title": "No IDs here"}], "movie") == []

    def test_empty_payloads(self):
        assert parse_any([], "movie") == []
        assert parse_any("", "movie") == []
        assert parse_any({}, "movie") == []
