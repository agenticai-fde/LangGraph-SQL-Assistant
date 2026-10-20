"""Natural-language-to-SQL graph with self-correction.

load_schema -> generate_sql -> validate_sql -> execute_sql -> answer
                   ^               | invalid       | database error
                   +---------------+---------------+   (up to MAX_ATTEMPTS, then give_up)

Safety is layered: the Python validator rejects anything but a single SELECT, and the
database function run_readonly_sql executes as a role that can only read
customers, products and orders.
"""
import json
import re
from datetime import date
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from postgrest.exceptions import APIError

from config import get_db, get_llm

MAX_ATTEMPTS = 3
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|merge|call|"
    r"execute|vacuum|lock|comment|refresh|listen|notify|pg_sleep|pg_read_file|dblink)\b", re.I)

RELATIONSHIPS = """Relationships:
- orders.customer_id -> customers.id
- orders.product_id -> products.id
Notes:
- orders.status is one of: placed, shipped, delivered, cancelled, returned
- customers.plan is one of: basic, premium, enterprise
- orders.total is the order value in INR (already quantity x price)"""

SQL_PROMPT = """You write PostgreSQL queries for a retail database. Today is {today}.

{schema}

{relationships}

Rules:
- Return ONE SELECT statement only, no explanation, no markdown.
- Only use the tables and columns listed above.
- Use ILIKE for case-insensitive text matching.
- Give computed columns readable aliases. Round money to 2 decimals.
- Add ORDER BY when the question implies ranking; LIMIT 50 unless asked otherwise.

Question: {question}
{feedback}
SQL:"""

ANSWER_PROMPT = """Question: {question}
SQL used: {sql}
Result rows ({count} total, first {shown} shown as JSON):
{rows}

Answer the question in 1-3 sentences using only these results. Mention key numbers.
If there are no rows, say that nothing matched."""


class SQLState(TypedDict, total=False):
    question: str
    schema: str
    sql: str
    error: Optional[str]
    attempts: int
    rows: list[dict]
    answer: str


def load_schema(state: SQLState) -> dict:
    rows = get_db().rpc("run_readonly_sql", {"query": (
        "select table_name, column_name, data_type from information_schema.columns "
        "where table_schema = 'public' order by table_name, ordinal_position"
    )}).execute().data
    tables: dict[str, list[str]] = {}
    for r in rows:
        tables.setdefault(r["table_name"], []).append(f"{r['column_name']} {r['data_type']}")
    schema = "Tables:\n" + "\n".join(f"- {t}({', '.join(cols)})" for t, cols in tables.items())
    return {"schema": schema, "attempts": 0, "error": None}


def generate_sql(state: SQLState) -> dict:
    feedback = ""
    if state.get("error"):
        feedback = (f"\nYour previous query:\n{state['sql']}\nfailed with this error:\n"
                    f"{state['error']}\nWrite a corrected query.\n")
    response = get_llm().invoke(SQL_PROMPT.format(
        today=date.today().isoformat(), schema=state["schema"],
        relationships=RELATIONSHIPS, question=state["question"], feedback=feedback))
    sql = re.sub(r"^```(?:sql)?|```$", "", response.content.strip(), flags=re.I | re.M).strip()
    return {"sql": sql, "attempts": state["attempts"] + 1, "error": None}


def validate_sql(state: SQLState) -> dict:
    sql = state["sql"].rstrip("; \n\t")
    if not re.match(r"^\s*(select|with)\b", sql, re.I):
        return {"error": "The query must be a single SELECT statement."}
    if ";" in sql:
        return {"error": "Only one statement is allowed (remove the extra semicolons)."}
    if match := FORBIDDEN.search(sql):
        return {"error": f"Keyword '{match.group(0)}' is not allowed in a read-only query."}
    return {"sql": sql}


def execute_sql(state: SQLState) -> dict:
    try:
        rows = get_db().rpc("run_readonly_sql", {"query": state["sql"]}).execute().data
    except APIError as e:
        return {"error": e.message or str(e)}
    return {"rows": rows or []}


def answer(state: SQLState) -> dict:
    rows = state["rows"]
    response = get_llm().invoke(ANSWER_PROMPT.format(
        question=state["question"], sql=state["sql"], count=len(rows),
        shown=min(len(rows), 30), rows=json.dumps(rows[:30], default=str)))
    return {"answer": response.content}


def give_up(state: SQLState) -> dict:
    return {"answer": f"Sorry, I couldn't produce a working query after {MAX_ATTEMPTS} attempts. "
                      f"Last error: {state['error']}", "rows": []}


def after_check(next_node: str):
    def route(state: SQLState) -> str:
        if not state.get("error"):
            return next_node
        return "generate_sql" if state["attempts"] < MAX_ATTEMPTS else "give_up"
    return route


builder = StateGraph(SQLState)
for name, fn in [("load_schema", load_schema), ("generate_sql", generate_sql),
                 ("validate_sql", validate_sql), ("execute_sql", execute_sql),
                 ("answer", answer), ("give_up", give_up)]:
    builder.add_node(name, fn)

builder.add_edge(START, "load_schema")
builder.add_edge("load_schema", "generate_sql")
builder.add_edge("generate_sql", "validate_sql")
builder.add_conditional_edges("validate_sql", after_check("execute_sql"),
                              ["execute_sql", "generate_sql", "give_up"])
builder.add_conditional_edges("execute_sql", after_check("answer"),
                              ["answer", "generate_sql", "give_up"])
builder.add_edge("answer", END)
builder.add_edge("give_up", END)

graph = builder.compile()
