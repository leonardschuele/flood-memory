FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV FLOOD_MEMORY_DIR=/data
EXPOSE 8080
CMD ["python", "server_remote.py"]
