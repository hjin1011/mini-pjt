# CLAUDE.md 기술 스택: Python 3.14, FastAPI + uvicorn
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY web/ ./web/
COPY data/ ./data/
COPY SERVICE.md CLAUDE.md README.md ./

# AWS 자격증명·LangSmith 키는 이미지에 넣지 않는다 (CLAUDE.md 코드 규칙: 비밀 값은
# .env에서 읽고 코드에 적지 않는다). 실행 시 `docker run --env-file .env ...`로 주입한다.
# data/app.db는 컨테이너를 지우면 함께 사라지므로, 기록을 남기려면 볼륨으로 마운트한다:
#   docker run --env-file .env -p 8813:8813 -v "$(pwd)/data:/app/data" <image>

EXPOSE 8813

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8813"]
