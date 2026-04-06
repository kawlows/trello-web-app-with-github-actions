@echo off
python -m pip install -r requirements.txt
uvicorn main:app --reload