"""Pasted lists, files in /config, and the CSV exports people actually have."""

import pytest

from sidecarr import providers
from sidecarr.config import AppConfig, Source
from sidecarr.providers.base import ProviderError
from sidecarr.providers.payload import parse_any

# The real header Letterboxd writes, which carries no ID of any kind.
LETTERBOXD = """Date,Name,Year,Letterboxd URI
2024-01-02,The Matrix,1999,https://boxd.it/2a1c
2024-01-03,Heat,1995,https://boxd.it/2b7d
"""

# IMDb's export, which does carry the IMDb ID plus real metadata.
IMDB = """Const,Created,Title,Title Type,IMDb Rating,Runtime (mins),Year,Genres,Num Votes,URL
tt0133093,2024-01-02,The Matrix,Movie,8.7,136,1999,"Action, Sci-Fi",1900000,https://www.imdb.com/title/tt0133093/
tt0113277,2024-01-02,Heat,Movie,8.3,170,1995,"Crime, Drama",700000,https://www.imdb.com/title/tt0113277/
"""


@pytest.fixture
def provider():
    p = providers.build("text", AppConfig())
    yield p
    p.close()


def source(type_, **options):
    return Source(provider="text", type=type_, options={k: str(v) for k, v in options.items()})


class TestCsvParsing:
    def test_letterboxd_export_keeps_title_and_year(self):
        items = parse_any(LETTERBOXD, "movie")
        assert [(i.title, i.year) for i in items] == [("The Matrix", 1999), ("Heat", 1995)]

    def test_letterboxd_rows_have_no_ids_and_that_is_allowed(self):
        """Letterboxd publishes no IDs, so these resolve by title later."""
        assert all(i.ids == {} for i in parse_any(LETTERBOXD, "movie"))

    def test_imdb_export_keeps_the_imdb_id(self):
        items = parse_any(IMDB, "movie")
        assert [i.ids for i in items] == [{"imdb": "tt0133093"}, {"imdb": "tt0113277"}]

    def test_imdb_export_carries_metadata_the_scraper_cannot(self):
        """The scraped IMDb provider gets IDs only; the export has enough to filter."""
        item = parse_any(IMDB, "movie")[0]
        assert item.year == 1999
        assert item.runtime == 136
        assert item.rating == 8.7
        assert item.votes == 1900000
        assert item.genres == ["action", "sci-fi"]

    def test_title_type_does_not_become_the_title(self):
        assert parse_any(IMDB, "movie")[0].title == "The Matrix"

    def test_a_personal_rating_is_not_read_as_the_title_rating(self):
        csv = "Const,Title,Year,Your Rating\ntt0133093,The Matrix,1999,4\n"
        assert parse_any(csv, "movie")[0].rating is None

    def test_tab_separated_works_too(self):
        tsv = "Title\tYear\tImdb Id\nThe Matrix\t1999\ttt0133093\n"
        assert parse_any(tsv, "movie")[0].ids == {"imdb": "tt0133093"}

    def test_a_tmdb_id_column_is_used(self):
        csv = "Title,Year,TMDb ID\nThe Matrix,1999,603\n"
        assert parse_any(csv, "movie")[0].ids == {"tmdb": 603}

    def test_blank_cells_are_skipped(self):
        csv = "Name,Year,Imdb Id\nThe Matrix,,\n"
        item = parse_any(csv, "movie")[0]
        assert item.title == "The Matrix" and item.year is None

    def test_a_bom_does_not_break_the_header(self):
        assert parse_any("﻿Name,Year\nHeat,1995\n", "movie")[0].title == "Heat"


class TestCsvIsNotOverDetected:
    """These shapes predate CSV support and must keep parsing as before."""

    def test_a_comma_separated_id_list_is_still_ids(self):
        assert [i.ids for i in parse_any("603, 604", "movie")] == [{"tmdb": 603}, {"tmdb": 604}]

    def test_a_newline_separated_id_list_is_still_ids(self):
        assert [i.ids for i in parse_any("tt0133093\ntt0111161\n", "movie")] == [
            {"imdb": "tt0133093"},
            {"imdb": "tt0111161"},
        ]

    def test_json_is_still_json(self):
        assert len(parse_any('[{"tmdbId": 1}]', "movie")) == 1

    def test_headerless_comma_data_is_not_treated_as_csv(self):
        """No recognised column name means this is not an export."""
        assert parse_any("foo,bar\nbaz,qux\n", "movie") == []


class TestTitleOnlyStaysOptIn:
    def test_json_without_ids_is_still_dropped(self):
        assert parse_any([{"title": "No IDs here"}], "movie") == []

    def test_unless_the_caller_asks_for_it(self):
        items = parse_any([{"title": "Heat", "year": 1995}], "movie", allow_title_only=True)
        assert [(i.title, i.year) for i in items] == [("Heat", 1995)]

    def test_a_one_character_title_is_not_worth_searching(self):
        assert parse_any([{"title": "x"}], "movie", allow_title_only=True) == []


class TestPaste:
    def test_a_pasted_csv_becomes_items(self, provider):
        items = provider.fetch(source("paste", content=LETTERBOXD), "movie")
        assert [i.title for i in items] == ["The Matrix", "Heat"]

    def test_a_pasted_column_of_ids_works(self, provider):
        items = provider.fetch(source("paste", content="tt0133093\ntt0111161"), "movie")
        assert len(items) == 2

    def test_an_empty_paste_is_reported(self, provider):
        with pytest.raises(ProviderError, match="Nothing was pasted"):
            provider.fetch(source("paste", content="   "), "movie")

    def test_unusable_content_is_reported(self, provider):
        with pytest.raises(ProviderError, match="Nothing usable"):
            provider.fetch(source("paste", content="just some prose here"), "movie")

    def test_an_oversized_paste_points_at_the_file_source(self, provider):
        huge = "Name,Year\n" + "".join(f"Film {n},2000\n" for n in range(80_000))
        with pytest.raises(ProviderError, match="use the file source"):
            provider.fetch(source("paste", content=huge), "movie")


class TestFile:
    @pytest.fixture(autouse=True)
    def config_dir(self, tmp_path, monkeypatch):
        from sidecarr import settings

        monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
        return tmp_path

    def test_a_file_in_config_is_read(self, provider, config_dir):
        path = config_dir / "letterboxd.csv"
        path.write_text(LETTERBOXD)
        items = provider.fetch(source("file", path=str(path)), "movie")
        assert [i.title for i in items] == ["The Matrix", "Heat"]

    def test_a_subdirectory_is_fine(self, provider, config_dir):
        nested = config_dir / "imports"
        nested.mkdir()
        (nested / "list.csv").write_text(IMDB)
        items = provider.fetch(source("file", path=str(nested / "list.csv")), "movie")
        assert len(items) == 2

    def test_a_path_outside_config_is_refused(self, provider, tmp_path):
        outside = tmp_path.parent / "secrets.csv"
        outside.write_text(IMDB)
        with pytest.raises(ProviderError, match="outside the config volume"):
            provider.fetch(source("file", path=str(outside)), "movie")

    def test_escaping_with_dot_dot_is_refused(self, provider, config_dir):
        with pytest.raises(ProviderError, match="outside the config volume"):
            provider.fetch(source("file", path=f"{config_dir}/../etc/passwd"), "movie")

    def test_a_missing_file_is_reported(self, provider, config_dir):
        with pytest.raises(ProviderError, match="No file at"):
            provider.fetch(source("file", path=str(config_dir / "nope.csv")), "movie")

    def test_an_empty_path_is_reported(self, provider):
        with pytest.raises(ProviderError, match="No file path"):
            provider.fetch(source("file", path=""), "movie")


class TestRegistration:
    def test_it_is_in_the_registry(self):
        assert "text" in providers.PROVIDERS

    def test_it_needs_no_credentials(self):
        assert providers.build("text", AppConfig()).configured() is True

    def test_the_editor_can_render_it(self):
        described = next(p for p in providers.describe_all(AppConfig()) if p["key"] == "text")
        kinds = {f["kind"] for s in described["sources"] for f in s["fields"]}
        assert "textarea" in kinds
