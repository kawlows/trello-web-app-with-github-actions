from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from typing import Dict
from sqlalchemy import create_engine, Column, String, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
import uuid

app = FastAPI(title="Trello Clone - SQLite")

# Templates
templates = Jinja2Templates(directory="templates")

# Allow local JS/other ports if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./trello.db"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# SQLAlchemy Models
class BoardDB(Base):
    __tablename__ = "boards"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, index=True)
    lists = relationship("ListDB", back_populates="board", cascade="all, delete-orphan")


class ListDB(Base):
    __tablename__ = "lists"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, index=True)
    board_id = Column(String, ForeignKey("boards.id"))
    board = relationship("BoardDB", back_populates="lists")
    cards = relationship("CardDB", back_populates="list_db", cascade="all, delete-orphan")


class CardDB(Base):
    __tablename__ = "cards"

    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, index=True)
    description = Column(Text, default="")
    color = Column(String, default="#0079bf")
    list_id = Column(String, ForeignKey("lists.id"))
    list_db = relationship("ListDB", back_populates="cards")


Base.metadata.create_all(bind=engine)

# Serialization
def model_to_dict(obj):
    if obj is None:
        return None

    if isinstance(obj, list):
        return [model_to_dict(item) for item in obj]

    result = {}
    for c in obj.__table__.columns:
        value = getattr(obj, c.name)
        result[c.name] = value

    if isinstance(obj, BoardDB):
        result["lists"] = model_to_dict(obj.lists)
    elif isinstance(obj, ListDB):
        result["cards"] = model_to_dict(obj.cards)

    return result

# ROUTES
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/boards")
async def get_boards(db: Session = Depends(get_db)):
    boards = db.query(BoardDB).all()
    return [model_to_dict(board) for board in boards]


@app.post("/api/boards")
async def create_board(board_data: Dict, db: Session = Depends(get_db)):
    title = board_data.get("title") or "Untitled Board"
    new_board = BoardDB(title=title)
    db.add(new_board)
    db.commit()
    db.refresh(new_board)
    return model_to_dict(new_board)


@app.get("/api/boards/{board_id}")
async def get_board(board_id: str, db: Session = Depends(get_db)):
    board = db.query(BoardDB).filter(BoardDB.id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")
    return model_to_dict(board)


@app.post("/api/boards/{board_id}/lists")
async def create_list(board_id: str, list_data: Dict, db: Session = Depends(get_db)):
    board = db.query(BoardDB).filter(BoardDB.id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    title = list_data.get("title") or "New List"
    new_list = ListDB(title=title, board=board)
    db.add(new_list)
    db.commit()
    db.refresh(new_list)
    return model_to_dict(new_list)


@app.put("/api/boards/{board_id}/lists/{list_id}")
async def update_list(board_id: str, list_id: str, list_data: Dict, db: Session = Depends(get_db)):
    list_item = (
        db.query(ListDB)
        .filter(ListDB.id == list_id, ListDB.board_id == board_id)
        .first()
    )

    if not list_item:
        raise HTTPException(status_code=404, detail="List not found")

    # defensive: ignore empty/None titles
    new_title = list_data.get("title")
    if new_title:
        list_item.title = new_title

    db.commit()
    db.refresh(list_item)
    return model_to_dict(list_item)


@app.post("/api/boards/{board_id}/lists/{list_id}/cards")
async def create_card(board_id: str, list_id: str, card_data: Dict, db: Session = Depends(get_db)):
    list_item = (
        db.query(ListDB)
        .filter(ListDB.id == list_id, ListDB.board_id == board_id)
        .first()
    )

    if not list_item:
        raise HTTPException(status_code=404, detail="List not found")

    title = card_data.get("title") or "New Card"
    description = card_data.get("description") or ""

    new_card = CardDB(
        title=title,
        description=description,
        color="#0079bf",
        list_id=list_item.id,
    )

    db.add(new_card)
    db.commit()
    db.refresh(new_card)
    return model_to_dict(new_card)


@app.delete("/api/boards/{board_id}/lists/{list_id}/cards/{card_id}")
async def delete_card(board_id: str, list_id: str, card_id: str, db: Session = Depends(get_db)):
    card = (
        db.query(CardDB)
        .filter(CardDB.id == card_id, CardDB.list_id == list_id)
        .first()
    )

    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    db.delete(card)
    db.commit()
    return {"message": "Card deleted"}


@app.post("/api/boards/{board_id}/move-card")
async def move_card(board_id: str, move_data: Dict, db: Session = Depends(get_db)):
    # Skeptical check: ensure required fields exist
    card_id = move_data.get("cardId")
    to_list_id = move_data.get("toListId")
    if not card_id or not to_list_id:
        raise HTTPException(status_code=400, detail="cardId and toListId are required")

    card = db.query(CardDB).filter(CardDB.id == card_id).first()

    if card:
        card.list_id = to_list_id
        db.commit()
        return {"message": "Card moved"}

    raise HTTPException(status_code=404, detail="Card not found")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
