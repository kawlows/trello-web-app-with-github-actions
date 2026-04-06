import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlmodel.pool import StaticPool

from main import app, get_db, Base, BoardDB, ListDB, CardDB


# ---------- Test DB setup (in-memory SQLite) ----------

@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )

    with TestingSessionLocal() as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session):
    def get_db_override():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = get_db_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


# ---------- Tests (7 total) ----------

def test_create_board(client: TestClient):
    response = client.post("/api/boards", json={"title": "My Board"})
    data = response.json()

    assert response.status_code == 200
    assert data["title"] == "My Board"
    assert data["id"] is not None
    assert data["lists"] == []


def test_create_board_default_title(client: TestClient):
    # No title provided -> should fall back to "Untitled Board"
    response = client.post("/api/boards", json={})
    data = response.json()

    assert response.status_code == 200
    assert data["title"] == "Untitled Board"
    assert data["id"] is not None


def test_get_boards_list(session, client: TestClient):
    # seed 2 boards directly in DB
    b1 = BoardDB(title="Board A")
    b2 = BoardDB(title="Board B")
    session.add_all([b1, b2])
    session.commit()
    session.refresh(b1)
    session.refresh(b2)

    response = client.get("/api/boards")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, list)
    assert len(data) == 2
    titles = {b["title"] for b in data}
    assert "Board A" in titles
    assert "Board B" in titles


def test_create_list_and_card(session, client: TestClient):
    board = BoardDB(title="Test Board")
    session.add(board)
    session.commit()
    session.refresh(board)

    # create list via API
    response = client.post(
        f"/api/boards/{board.id}/lists", json={"title": "Todo"}
    )
    assert response.status_code == 200
    list_data = response.json()
    list_id = list_data["id"]
    assert list_data["title"] == "Todo"

    # create card via API
    response = client.post(
        f"/api/boards/{board.id}/lists/{list_id}/cards",
        json={"title": "First task"},
    )
    assert response.status_code == 200
    card_data = response.json()
    assert card_data["title"] == "First task"
    assert card_data["id"] is not None

    # get board and check nested data
    response = client.get(f"/api/boards/{board.id}")
    assert response.status_code == 200
    board_data = response.json()
    assert len(board_data["lists"]) == 1
    assert len(board_data["lists"][0]["cards"]) == 1
    assert board_data["lists"][0]["cards"][0]["title"] == "First task"


def test_get_single_board_not_found(client: TestClient):
    # random ID that doesn't exist
    response = client.get("/api/boards/non-existent-id")
    assert response.status_code == 404
    assert response.json()["detail"] == "Board not found"


def test_update_list_title(session, client: TestClient):
    board = BoardDB(title="Board for update")
    session.add(board)
    session.commit()
    session.refresh(board)

    list_obj = ListDB(title="Old Title", board_id=board.id)
    session.add(list_obj)
    session.commit()
    session.refresh(list_obj)

    response = client.put(
        f"/api/boards/{board.id}/lists/{list_obj.id}",
        json={"title": "New Title"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "New Title"

    session.refresh(list_obj)
    assert list_obj.title == "New Title"


def test_move_and_delete_card(session, client: TestClient):
    board = BoardDB(title="Move/Delete Test")
    session.add(board)
    session.commit()
    session.refresh(board)

    list_a = ListDB(title="A", board_id=board.id)
    list_b = ListDB(title="B", board_id=board.id)
    session.add_all([list_a, list_b])
    session.commit()
    session.refresh(list_a)
    session.refresh(list_b)

    card = CardDB(title="To move and delete", list_id=list_a.id)
    session.add(card)
    session.commit()
    session.refresh(card)

    # move via API
    response = client.post(
        f"/api/boards/{board.id}/move-card",
        json={"fromListId": list_a.id, "toListId": list_b.id, "cardId": card.id},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Card moved"

    session.refresh(card)
    assert card.list_id == list_b.id

    # delete via API
    response = client.delete(
        f"/api/boards/{board.id}/lists/{list_b.id}/cards/{card.id}"
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Card deleted"

    deleted = session.get(CardDB, card.id)
    assert deleted is None