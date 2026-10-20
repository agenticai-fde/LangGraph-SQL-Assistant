"""FastAPI service for the SQL assistant.

    uvicorn api:app --port 8003      then open http://127.0.0.1:8003/docs
"""
import os
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from ollama import ResponseError
from pydantic import BaseModel, Field

from graph import graph, load_schema

API_KEY = os.getenv("API_KEY")  # when set, every request needs header X-API-Key
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: Optional[str] = Security(api_key_header)):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")


app = FastAPI(title="SQL Assistant",
              description="Natural-language questions over customers, products and orders, "
                          "answered with read-only SQL.",
              dependencies=[Depends(require_api_key)])


@app.exception_handler(ResponseError)
def llm_unavailable(request, exc: ResponseError):
    return JSONResponse(status_code=503, content={"detail": f"LLM unavailable: {exc.error}"})


class QueryRequest(BaseModel):
    question: str = Field(description="e.g. 'Top 3 customers by total spend'")


class QueryResponse(BaseModel):
    answer: str
    sql: Optional[str] = None
    attempts: int
    succeeded: bool
    row_count: int
    rows: list[dict[str, Any]]


@app.post("/query", response_model=QueryResponse, operation_id="querySql")
def query(data: QueryRequest):
    out = graph.invoke({"question": data.question})
    rows = out.get("rows", [])
    return QueryResponse(answer=out["answer"], sql=out.get("sql"), attempts=out["attempts"],
                         succeeded=not out.get("error"), row_count=len(rows), rows=rows)


@app.get("/schema", operation_id="getSchema")
def schema():
    return {"schema": load_schema({})["schema"]}


@app.get("/health")
def health():
    return {"status": "ok"}
