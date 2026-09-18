#!/bin/bash
# .venv를 만들고 의존성을 설치한 뒤 API 서버를 띄운다.
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m uvicorn src.api:app --reload
