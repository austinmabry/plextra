import pytest

from sidecarr.clients.trakt import TraktClient, TraktError, parse_list_url


class TestParseListUrl:
    @pytest.mark.parametrize(
        "value",
        [
            "https://trakt.tv/users/someone/lists/my-list",
            "http://trakt.tv/users/someone/lists/my-list",
            "trakt.tv/users/someone/lists/my-list?sort=rank",
            "/users/someone/lists/my-list",
            "someone/my-list",
        ],
    )
    def test_accepted_forms(self, value):
        assert parse_list_url(value) == ("someone", "my-list")

    def test_rejects_nonsense(self):
        with pytest.raises(TraktError):
            parse_list_url("just-a-word")

    def test_rejects_empty(self):
        with pytest.raises(TraktError):
            parse_list_url("")


class TestNormalise:
    """Trakt returns at least three different shapes for list endpoints."""

    normalise = staticmethod(TraktClient._normalise)

    def test_bare_objects(self):
        raw = [{"title": "Arrival", "year": 2016, "ids": {"trakt": 1, "tmdb": 9}}]
        assert self.normalise(raw, "movie", "popular")[0]["title"] == "Arrival"

    def test_wrapped_objects(self):
        raw = [{"watchers": 40, "movie": {"title": "Dune", "ids": {"trakt": 2}}}]
        assert self.normalise(raw, "movie", "trending")[0]["title"] == "Dune"

    def test_watchlist_entries(self):
        raw = [{"rank": 1, "type": "movie", "movie": {"title": "Heat", "ids": {"trakt": 3}}}]
        assert self.normalise(raw, "movie", "watchlist")[0]["title"] == "Heat"

    def test_show_key(self):
        raw = [{"show": {"title": "Severance", "ids": {"trakt": 4}}}]
        assert self.normalise(raw, "show", "watchlist")[0]["title"] == "Severance"

    def test_movie_key_ignored_for_shows(self):
        raw = [{"movie": {"title": "Dune", "ids": {"trakt": 5}}}]
        assert self.normalise(raw, "show", "watchlist") == []

    def test_person_credits_unwrap_cast(self):
        raw = {
            "cast": [
                {"character": "Neo", "movie": {"title": "The Matrix", "ids": {"trakt": 6}}},
            ],
            "crew": {},
        }
        assert self.normalise(raw, "movie", "person")[0]["title"] == "The Matrix"

    def test_person_credits_drop_self_and_narrator_roles(self):
        raw = {
            "cast": [
                {"character": "Himself", "movie": {"title": "Doc", "ids": {"trakt": 7}}},
                {"character": "Narrator", "movie": {"title": "Nature", "ids": {"trakt": 8}}},
                {"character": "", "movie": {"title": "Uncredited", "ids": {"trakt": 9}}},
                {"character": "Ripley", "movie": {"title": "Alien", "ids": {"trakt": 10}}},
            ]
        }
        titles = [i["title"] for i in self.normalise(raw, "movie", "person")]
        assert titles == ["Alien"]

    def test_duplicates_removed_by_trakt_id(self):
        raw = [
            {"movie": {"title": "Dune", "ids": {"trakt": 11}}},
            {"movie": {"title": "Dune", "ids": {"trakt": 11}}},
        ]
        assert len(self.normalise(raw, "movie", "list")) == 1

    def test_items_without_titles_skipped(self):
        raw = [{"rank": 1}, {"movie": {"title": "Real", "ids": {"trakt": 12}}}]
        assert len(self.normalise(raw, "movie", "list")) == 1

    def test_unexpected_payload_returns_empty(self):
        assert self.normalise("nope", "movie", "list") == []
        assert self.normalise(None, "movie", "list") == []


class TestResolveSource:
    """Source type -> endpoint mapping, without touching the network."""

    def client(self):
        from sidecarr.config import TraktConfig

        return TraktClient(TraktConfig(client_id="abc", client_secret="def"))

    def resolve(self, source_type, media="movie", **kwargs):
        from sidecarr.config import Source

        with self.client() as trakt:
            return trakt._resolve(Source(type=source_type, **kwargs), media)

    def test_watchlist_needs_auth(self):
        path, needs_auth, paged = self.resolve("watchlist")
        assert path == "/users/{account}/watchlist/movies"
        assert needs_auth and paged

    def test_shows_use_the_shows_endpoints(self):
        path, _, _ = self.resolve("trending", media="show")
        assert path == "/shows/trending"

    def test_custom_list(self):
        path, needs_auth, _ = self.resolve(
            "list", list_url="https://trakt.tv/users/me/lists/faves"
        )
        assert path == "/users/me/lists/faves/items/movies"
        assert needs_auth is False  # public lists work unauthenticated

    def test_custom_list_with_account_authenticates(self):
        _, needs_auth, _ = self.resolve(
            "list", list_url="me/faves", account="me"
        )
        assert needs_auth is True

    def test_person_is_slugified(self):
        path, _, _ = self.resolve("person", person="Denis Villeneuve")
        assert path == "/people/denis-villeneuve/movies"

    def test_watched_uses_period(self):
        path, _, _ = self.resolve("watched", period="yearly")
        assert path == "/movies/watched/yearly"

    def test_boxoffice_is_movies_only(self):
        with pytest.raises(TraktError):
            self.resolve("boxoffice", media="show")

    def test_unknown_type_is_rejected(self):
        """Now that many providers exist, the API validates against the provider."""
        from sidecarr.config import Source

        source = Source(provider="trakt", type="nonsense")
        with self.client() as trakt:
            with pytest.raises(TraktError, match="Unknown Trakt source"):
                trakt._resolve(source, "movie")
