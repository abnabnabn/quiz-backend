import asyncio
import json
import os
import random
import uuid
from typing import List, Optional, Dict

import psycopg2
from fastapi import Depends, FastAPI, HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor

# App and middleware setup
app = FastAPI()

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable not set")

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Database connection details
DB_HOST = os.environ.get("DB_HOST", "quizdb")
DB_NAME = "quizdb"
DB_USER = os.environ.get("DB_USER", "quizuser")
DB_PASS = os.environ.get("DB_PASS", "quizpassword")

# Pydantic model for the question response
class Question(BaseModel):
    question: str
    answer: int
    choices: List[str]

# Function to get a database connection
async def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            cursor_factory=RealDictCursor,
        )
        yield conn
    finally:
        conn.close()

class QuestionService:
    def __init__(self, conn: psycopg2.extensions.connection):
        self.conn = conn

    def get_categories(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT category FROM questions")
            return [row["category"] for row in cur.fetchall()]

    def _get_question_count(self, category: str):
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM questions WHERE category = %s", (category,))
            count = cur.fetchone()[0]
            return count

    def _fetch_question(self, category: str, question_index: int):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM questions WHERE category = %s OFFSET %s LIMIT 1",
                (category, question_index),
            )
            question_data = cur.fetchone()

            if not question_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No question found for this index.",
                )

            return question_data

    def _create_question_response(self, question_data, round_number, category):
        choices = [
            question_data["answer"],
            question_data["choice1"],
            question_data["choice2"],
            question_data["choice3"],
        ]
        random.shuffle(choices)

        return {
            "question": question_data["question"],
            "answer": choices.index(question_data["answer"]),
            "choices": choices,
            "round": round_number,
            "category": category
        }

    def init_party(self, player_id: str, category: str):
        party_id = uuid.uuid4()
        game_id = uuid.uuid4()
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO parties (party_id, game_id, player_id) VALUES (%s, %s, %s)",
                (party_id, game_id, player_id),
            )
        return str(party_id)

    def get_game_id(self, party_id: str):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT game_id FROM parties WHERE party_id = %s",
                (party_id,),
            )
            data = cur.fetchone()
            if not data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Party not found.",
                )
            return data["game_id"]

    def get_current_round(self, game_id: str):
        with self.conn.cursor() as cur:
            # Fetch the current round from the game_rounds table
            cur.execute(
                "SELECT MAX(round_number) FROM game_rounds WHERE game_id = %s", (game_id,)
            )
            round_data = cur.fetchone()
            current_round = round_data[0] if round_data[0] is not None else 1
            return current_round

    def increment_round(self, party_id: str):
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE parties SET current_round = current_round + 1 WHERE party_id = %s",
                (party_id,),
            )

    def get_question_for_round(self, game_id: uuid.UUID, round_number: int):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT category, question_index FROM game_rounds WHERE game_id = %s AND round_number = %s",
                (game_id, round_number),
            )
            game_round_data = cur.fetchone()

            if not game_round_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Game round not found.",
                )

            category = game_round_data["category"]
            question_index = game_round_data["question_index"]

        # Fetch and return the question
        question_data = self._fetch_question(category, question_index)
        return self._create_question_response(question_data, round_number, category)

    def get_players_and_scores(self, party_id: str):
        with self.conn.cursor() as cur:
            # Assuming you have a table named 'party_players' to store player information and scores
            cur.execute(
                "SELECT user_id, score FROM party_players WHERE party_id = %s",
                (party_id,),
            )
            players_data = cur.fetchall()
            return [{"user_id": player["user_id"], "score": player["score"]} for player in players_data]

    def increment_player_score(self, party_id: str, user_id: str):
        with self.conn.cursor() as cur:
            # Assuming you have a party_players table with columns: party_id, user_id, score
            cur.execute(
                "UPDATE party_players SET score = score + 1 WHERE party_id = %s AND user_id = %s",
                (party_id, user_id),
            )
# Global dictionary to store party and connection information
party_connections: Dict[str, List[WebSocket]] = {}
joined_players: Dict[str, set] = {}

async def remove_connection(party_id: str, websocket: WebSocket):
    if party_id in party_connections:
        try:
            party_connections[party_id].remove(websocket)
        except ValueError:
            pass

@app.websocket("/ws/{party_id}")
async def websocket_endpoint(websocket: WebSocket, party_id: str):
    await websocket.accept()
    # Access the session from the websocket
    websocket.session = dict(websocket.scope.get("session", {}))

    # Get the user_id from the query parameters
    user_id = websocket.query_params.get("user_id")

    if not user_id or user_id not in joined_players.get(party_id, set()):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return

    if party_id not in party_connections:
        party_connections[party_id] = []
    party_connections[party_id].append(websocket)

    try:
        while True:
            # Receive and process messages from the client
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=10)
                message = json.loads(data)
                if message.get("type") == "answer":
                    await handle_answer(party_id, user_id, message.get("answer_index"), websocket)
            except asyncio.TimeoutError:
                # Send keep-alive message
                await websocket.send_json({"type": "keep_alive"})
            except WebSocketDisconnect:
                raise
            except Exception as e:
                print(f"Error processing message: {e}")

            # Fetch and send game updates to the client
            async with get_db_connection() as conn:
                question_service = QuestionService(conn)
                game_id = question_service.get_game_id(party_id)
                current_round = question_service.get_current_round(game_id)
                question_data = await question_service.get_question_for_round(game_id, current_round)

                question_data["round"] = current_round
                question_data["players"] = question_service.get_players_and_scores(party_id)

            await websocket.send_json({"type": "question", "payload": question_data})

    except WebSocketDisconnect:
        await remove_connection(party_id, websocket)
        print(f"Client disconnected from party {party_id}")
async def handle_answer(party_id: str, user_id: str, answer_index: int, websocket: WebSocket):
    async with get_db_connection() as conn:
        question_service = QuestionService(conn)

        # Check if the player has already answered for this round (using session)
        game_id = question_service.get_game_id(party_id)
        current_round = question_service.get_current_round(game_id)
        answered_key = f"{party_id}-{user_id}-{current_round}-answered"
        if websocket.session.get(answered_key, False):
            await websocket.send_json({"type": "error", "message": "You have already answered this question."})
            return

        question_data = await question_service.get_question_for_round(game_id, current_round)
        correct_answer_index = question_data["answer"]

        if answer_index == correct_answer_index:
            question_service.increment_player_score(party_id, user_id)
            await websocket.send_json({"type": "answer_result", "message": "Correct answer!"})
        else:
            await websocket.send_json({"type": "answer_result", "message": "Incorrect answer."})

        # Mark the player as having answered for this round
        websocket.session[answered_key] = True

@app.post("/api/party/init", response_model=str)
async def init_party(
    conn: psycopg2.extensions.connection = Depends(get_db_connection),
    player_id: str,
    category: str,
    session_id: str = Depends(SessionMiddleware),
):
    question_service = QuestionService(conn)
    party_id = question_service.init_party(player_id, category)

    # Reset answered status for all players in the session
    for key in list(session_id.keys()):
        if key.startswith(f"{party_id}-") and key.endswith("-answered"):
            del session_id[key]

    # Notify connected clients about the new party
    if party_id in party_connections:
        for websocket in party_connections[party_id]:
            await websocket.send_json({"message": "Party initialized"})

    return party_id

@app.post("/api/party/{party_id}/next_question")
async def next_question(
    conn: psycopg2.extensions.connection = Depends(get_db_connection),
    party_id: str,
    user_id: str,
):
    question_service = QuestionService(conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT player_id FROM parties WHERE party_id = %s", (party_id,)
        )
        party_data = cur.fetchone()
        if not party_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Party not found.",
            )
        if party_data["player_id"] != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the party owner can move to the next question.",
            )

    question_service.increment_round(party_id)

    if party_id in party_connections:
        for websocket in party_connections[party_id]:
            await websocket.send_json({"message": "Next question"})

    return {"message": "Next question triggered"}
@app.post("/api/party/{party_id}/join")
async def join_party(
    conn: psycopg2.extensions.connection = Depends(get_db_connection),
    party_id: str,
    user_id: str,
):

    question_service = QuestionService(conn)

    try:
        # Fetch the game_id for the given party_id
        game_id = question_service.get_game_id(party_id)
    except HTTPException as e:
        if e.status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Party not found.",
            )
        else:
            raise e

    # Add the user to the list of joined players for the party
    if party_id not in joined_players:
        joined_players[party_id] = set()
    joined_players[party_id].add(user_id)

    return {"message": "Joined party successfully", "game_id": game_id}

@app.get("/api/categories", response_model=List[str])
async def get_categories(
    conn: psycopg2.extensions.connection = Depends(get_db_connection),
):
    question_service = QuestionService(conn)
    return question_service.get_categories()
