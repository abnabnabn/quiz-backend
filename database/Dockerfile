FROM postgres:latest

# Set environment variables
ENV POSTGRES_USER=quizuser
ENV POSTGRES_PASSWORD=quizpassword
ENV POSTGRES_DB=quizdb

EXPOSE 5432

# Copy SQL script and JSON data to container
COPY create_and_populate_quiz.sql /docker-entrypoint-initdb.d/
COPY quiz_data/*.json /docker-entrypoint-initdb.d/ 
