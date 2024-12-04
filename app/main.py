import asyncio
import json
import os
import uuid
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
import psycopg2
import redis
from pydantic import BaseModel

# Load environment variables
redis_host = os.getenv("REDIS_HOST", "redis")
redis_port = int(os.getenv("REDIS_PORT", 6379))

# Initialize Redis and FastAPI
redis_client = redis.StrictRedis(host=redis_host, port=redis_port, decode_responses=True)
app = FastAPI()

# In-memory storage for active parties (for this session only)
active_parties = {}

class PartyInitRequest(BaseModel):
    player_id: str
    category: str
    rounds: int
    timeout: int = 30  # Default timeout of 30 seconds for each question

class PartyJoinRequest(BaseModel):
    user_id: str

class PartyMessage(BaseModel):
    event: str
    message: str


def get_db_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME", "quizdb"),
        user=os.getenv("DB_USER", "quizuser"),
        password=os.getenv("DB_PASSWORD", "quizpassword"),
        host=os.getenv("DB_HOST", "quizdb"),
        port=os.getenv("DB_PORT", 5432)
    )


async def initialize_question_pool(category: str, num_questions: int) -> List[dict]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT id, question, answer, choice1, choice2, choice3
                FROM questions WHERE category = %s
                ORDER BY RANDOM() LIMIT %s
            """, (category, num_questions))
            questions = cursor.fetchall()

    if not questions or len(questions) < num_questions:
        raise HTTPException(status_code=400, detail="Not enough questions in the selected category")
    
    return [{
        "id": q[0],
        "question": q[1],
        "choices": [q[2], q[3], q[4], q[5]],
        "answer": q[2]
    } for q in questions]


@app.post("/api/party/init")
async def init_party(request: PartyInitRequest):
    party_id = str(uuid.uuid4())
    game_id = str(uuid.uuid4())

    # Fetch question pool during initialization
    question_pool = await initialize_question_pool(request.category, request.rounds)
    
    # Store party details in active_parties and Redis
    active_parties[party_id] = {
        "game_id": game_id,
        "creator": request.player_id,
        "category": request.category,
        "rounds": request.rounds,
        "timeout": request.timeout,
        "current_round": 1,
        "connections": [],
        "players": {request.player_id: {"score": 0, "category_scores": {}}},
        "question_pool": question_pool
    }
    save_party_to_redis(party_id, active_parties[party_id])

    # Start the first round
    asyncio.create_task(start_round(party_id))

    return JSONResponse({"party_id": party_id})


async def start_round(party_id: str):
    party = load_party_from_redis(party_id)
    if not party:
        return

    if party["current_round"] > party["rounds"]:
        # Game over
        await broadcast_to_party(party_id, {"event": "game_over", "scores": party["players"]})
        delete_party_from_redis(party_id)
        return

    # Use question from the pre-fetched pool
    question = party["question_pool"].pop(0)
    party["current_question"] = question
    await broadcast_to_party(party_id, {
        "event": "new_question",
        "round": party["current_round"],
        "timeout": party["timeout"],
        "question": question["question"],
        "choices": question["choices"]
    })

    # Wait for the timeout
    await asyncio.sleep(party["timeout"])

    if "current_question" in party:
        party.pop("current_question")
        await broadcast_to_party(party_id, {"event": "question_timeout"})
        party["current_round"] += 1
        save_party_to_redis(party_id, party)
        await start_round(party_id)


@app.post("/api/party/{party_id}/join")
async def join_party(party_id: str, request: PartyJoinRequest):
    if party_id not in active_parties:
        raise HTTPException(status_code=404, detail="Party not found")

    party = active_parties[party_id]
    if request.user_id in party["players"]:
        raise HTTPException(status_code=400, detail="User already in party")

    party["players"][request.user_id] = {"score": 0, "category_scores": {}}
    save_party_to_redis(party_id, party)

    return JSONResponse({"message": "Joined party successfully", "game_id": party["game_id"]})


@app.websocket("/ws/{party_id}")
async def websocket_endpoint(websocket: WebSocket, party_id: str):
    await websocket.accept()

    # Add the client to the party's connection list
    if party_id not in active_parties:
        await websocket.close()
        return

    party = active_parties[party_id]
    party["connections"].append(websocket)

    # Send the current question state
    if "current_question" in party:
        await websocket.send_json({
            "event": "new_question",
            "round": party["current_round"],
            "question": party["current_question"]["question"],
            "choices": party["current_question"]["choices"]
        })

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("event") == "answer":
                user_id = data.get("user_id")
                answer = data.get("answer")

                if user_id not in party["players"]:
                    continue

                correct_answer = party["current_question"]["answer"]
                if answer == correct_answer:
                    party["players"][user_id]["score"] += 1

                # Update performance by category
                category = party["category"]
                party["players"][user_id]["category_scores"][category] = party["players"][user_id]["category_scores"].get(category, 0) + 1

                save_party_to_redis(party_id, party)
                await broadcast_to_party(party_id, {
                    "event": "score_update",
                    "user_id": user_id,
                    "score": party["players"][user_id]["score"]
                })
                
    except WebSocketDisconnect:
        party["connections"].remove(websocket)
        if not party["connections"]:
            # Remove party if no players are connected
            delete_party_from_redis(party_id)
        await websocket.close()


async def broadcast_to_party(party_id: str, message: dict):
    party = active_parties.get(party_id)
    if party:
        for websocket in party["connections"]:
            await websocket.send_json(message)


def save_party_to_redis(party_id, party_data):
    redis_client.set(f"party:{party_id}", json.dumps(party_data))


def load_party_from_redis(party_id):
    data = redis_client.get(f"party:{party_id}")
    return json.loads(data) if data else None


def delete_party_from_redis(party_id):
    redis_client.delete(f"party:{party_id}")

