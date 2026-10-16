"""Descarga incremental con un cliente falso que imita los filtros de la API."""

from tegchess.config import Config
from tegchess.ingest import sync_user


class FakeClient:
    def __init__(self, games):
        self.games = sorted(games, key=lambda g: g["createdAt"], reverse=True)
        self.calls = []

    def iter_user_games(self, username, *, since=None, until=None, max_games=None,
                        perf_types=None, rated=None, color=None, ascending=False):
        self.calls.append((since, until, max_games, ascending))
        sel = [g for g in self.games
               if (since is None or g["createdAt"] >= since) and (until is None or g["createdAt"] <= until)]
        if ascending:
            sel.reverse()
        yield from sel[:max_games] if max_games else sel


def _cfg(tmp_path, max_games):
    return Config.model_validate({
        "me": "german11",
        "download": {"perf_types": ["blitz"], "max_games": max_games, "rated_only": False},
        "data_dir": str(tmp_path),
    })


def test_incremental_sync(con, tmp_path, raw_games):
    games = sorted(raw_games, key=lambda g: g["createdAt"])
    old, new = games[:-2], games[-2:]

    client = FakeClient(old)
    assert sync_user(client, con, _cfg(tmp_path, 3), "german11") == 3

    # Llegan partidas nuevas: se bajan solo esas.
    client = FakeClient(games)
    assert sync_user(client, con, _cfg(tmp_path, 3), "german11") == 2

    # Subo max_games: completa hacia atrás lo que falta.
    assert sync_user(client, con, _cfg(tmp_path, 10), "german11") == len(games) - 5
    assert con.execute("SELECT count(*) FROM games").fetchone()[0] == len(games)

    # Sin cambios: no baja nada.
    assert sync_user(client, con, _cfg(tmp_path, 10), "german11") == 0
