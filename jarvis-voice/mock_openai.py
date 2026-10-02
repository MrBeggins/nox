"""Минимальный mock OpenAI-совместимого сервера для теста мульти-мозга Джарвиса."""
from fastapi import FastAPI, Request
import uvicorn
app = FastAPI()

@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    user = ""
    for m in body.get("messages", []):
        if m.get("role") == "user":
            user = m.get("content", "")
    reply = f"Это ответ локального GPT-бэкенда. Ты спросил: {user}. Мульти-мозг работает, сэр."
    return {"choices": [{"message": {"role": "assistant", "content": reply}}]}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8199, log_level="warning")
