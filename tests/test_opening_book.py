import chess

from tegchess.opening_book import book, family, name_for


def _epds(sans: str) -> list[str]:
    board, out = chess.Board(), []
    for san in sans.split():
        board.push_san(san)
        out.append(board.epd())
    return out


def test_book_loads_lichess_names():
    assert len(book()) > 3000
    eco, name, ply = name_for(_epds("e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6"))
    assert (eco, name, ply) == ("B90", "Sicilian Defense: Najdorf Variation", 10)
    assert family(name) == "Sicilian Defense"


def test_transpositions_get_the_same_name():
    a = name_for(_epds("d4 Nf6 c4 e6 Nf3 d5"))
    b = name_for(_epds("Nf3 Nf6 c4 e6 d4 d5"))
    assert a[:2] == b[:2]


def test_unknown_start_and_empty():
    assert name_for([]) is None
