"""Ask questions about customers, products and orders in plain English.

    python main.py                                       interactive
    python main.py "Top 3 customers by total spend"      single question
"""
import sys

from graph import graph


def print_table(rows: list[dict], limit: int = 10) -> None:
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    shown = rows[:limit]
    widths = [max(len(c), *(len(str(r.get(c))) for r in shown)) for c in cols]
    line = lambda values: "  ".join(str(v).ljust(w) for v, w in zip(values, widths))
    print(line(cols))
    print(line("-" * w for w in widths))
    for r in shown:
        print(line(r.get(c) for c in cols))
    if len(rows) > limit:
        print(f"... {len(rows) - limit} more rows")


def ask(question: str) -> None:
    result = graph.invoke({"question": question})
    print(f"\nSQL ({result['attempts']} attempt(s)):\n  {result.get('sql', '-')}\n")
    print_table(result.get("rows", []))
    print(f"\nAnswer: {result['answer']}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ask(" ".join(sys.argv[1:]))
    else:
        print("SQL assistant over customers, products and orders. Blank line to quit.")
        while question := input("\nQ: ").strip():
            try:
                ask(question)
            except Exception as e:
                print(f"[error] {e}")
