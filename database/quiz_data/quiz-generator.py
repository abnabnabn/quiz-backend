import os
import yaml
import json
import logging
from datetime import datetime
from google.ai import generativeai as genai

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s: %(message)s',
    filename=f'quiz_generation_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
)

def generate_system_prompt(category_name, category_description):
    """
    Dynamically generate a detailed system prompt for each category
    """
    return f"""
Generate high-quality, challenging quiz questions with the following guidelines:

General Requirements:
- Create 50 unique, thought-provoking quiz questions
- Ensure questions are specifically relevant to UK audiences
- Avoid questions with obvious answers embedded in the question
- Target a challenging but fair difficulty level
- Each question must have:
  * One correct answer
  * Three plausible but incorrect choices
  * Clear connection to the {category_name} category

Specific Category Context:
{category_description}

Output Format (JSON Array):
[{{
    "question": "Detailed, nuanced question text",
    "answer": "Correct, precise answer",
    "choice1": "Plausible but incorrect option 1",
    "choice2": "Plausible but incorrect option 2",
    "choice3": "Plausible but incorrect option 3",
    "category": "{category_name}"
}}, ...]

Important: Demonstrate depth of knowledge, avoid trivial or easily guessable questions.
"""

def generate_quiz_questions(config_file='quiz-categories.yaml'):
    """
    Generate quiz questions for each category using Gemini API
    """
    # Configure Gemini API
    genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
    model = genai.GenerativeModel('gemini-pro')

    # Read categories from YAML
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)

    for category in config['categories']:
        try:
            # Generate system prompt
            system_prompt = generate_system_prompt(
                category['name'], 
                category['description']
            )
            
            # Generate questions
            generation_prompt = (
                f"Please generate 50 unique, high-quality quiz questions "
                f"for the {category['name']} category following the specified guidelines."
            )
            
            response = model.generate_content(
                f"{system_prompt}\n\n{generation_prompt}"
            )

            # Process and save JSON
            try:
                # Remove potential markdown formatting
                json_content = response.text.strip('```json').strip('```')
                questions = json.loads(json_content)

                # Validate questions
                if not questions or len(questions) != 50:
                    logging.warning(f"Generated {len(questions)} questions for {category['name']}, expected 50")

                # Save to JSON
                filename = f"{category['name'].lower()}_questions.json"
                with open(filename, 'w', encoding='utf-8') as outfile:
                    json.dump(questions, outfile, indent=2, ensure_ascii=False)

                logging.info(f"Successfully generated questions for {category['name']}")

            except (json.JSONDecodeError, ValueError) as json_err:
                logging.error(f"JSON processing error for {category['name']}: {json_err}")
                logging.error(f"Raw response: {response.text}")

        except Exception as e:
            logging.error(f"Error generating questions for {category['name']}: {e}")

def main():
    generate_quiz_questions()

if __name__ == "__main__":
    main()