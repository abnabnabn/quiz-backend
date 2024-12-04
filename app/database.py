import psycopg2
import os

def get_db_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME", "quizdb"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres"),
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", 5432)
    )
