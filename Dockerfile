# Use official Python slim image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PORT 3001

# Set work directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Create persistent data directory
RUN mkdir -p /app/data

# Expose port (Render injects $PORT at runtime)
EXPOSE $PORT

# Run the application — use shell form so $PORT is expanded at runtime
CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-3001}"
