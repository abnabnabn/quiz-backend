-- Create the tables
CREATE TABLE questions (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    choice1 TEXT NOT NULL,
    choice2 TEXT NOT NULL,
    choice3 TEXT NOT NULL,
    category TEXT NOT NULL
);

CREATE TABLE parties (
    party_id UUID PRIMARY KEY,
    game_id UUID NOT NULL,
    player_id TEXT NOT NULL
);

CREATE TABLE game_rounds (
    game_id UUID NOT NULL,
    round_number INTEGER NOT NULL,
    category TEXT NOT NULL,
    question_index INTEGER NOT NULL,
    PRIMARY KEY (game_id, round_number)
);

CREATE TABLE party_players (
    party_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (party_id, user_id)
);


-- Load data from all JSON files in the directory
DO
$do$
DECLARE
  json_file TEXT;
  quesion_data jsonb;
BEGIN
  -- Loop through all files in the directory
  FOR json_file IN SELECT pg_ls_dir('/docker-entrypoint-initdb.d/')
  LOOP
    -- Only process files with .json extension
    IF json_file LIKE '%.json' THEN
      -- Read the JSON file content
      quesion_data := pg_read_file('/docker-entrypoint-initdb.d/' || json_file)::jsonb;

      -- Insert data into the table
      INSERT INTO questions (question, answer, choice1, choice2, choice3, category)
      SELECT
        (jsonb_array_elements(quesion_data))->>'question',
        (jsonb_array_elements(quesion_data))->>'answer',
        (jsonb_array_elements(quesion_data))->>'choice1',
        (jsonb_array_elements(quesion_data))->>'choice2',
        (jsonb_array_elements(quesion_data))->>'choice3',
        (jsonb_array_elements(quesion_data))->>'category';
    END IF;
  END LOOP;
END
$do$;
SELECT COUNT(*) FROM questions;

