from pathlib import Path
import os
from typing import Optional

from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row


# Works with both the newer and the older mcp package
try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

load_dotenv(Path(__file__).parent / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]

mcp = FastMCP("expense-tracker")


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id            SERIAL PRIMARY KEY,
                amount        NUMERIC(12, 2) NOT NULL,
                category      TEXT NOT NULL,
                description   TEXT DEFAULT '',
                expense_date  DATE NOT NULL DEFAULT CURRENT_DATE
            )
        """)


@mcp.tool()
def add_expense(
    amount: float,
    category: str,
    description: str = "",
    expense_date: Optional[str] = None,
):
    """Add an expense. expense_date is YYYY-MM-DD (defaults to today)."""
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO expenses (amount, category, description, expense_date)
            VALUES (%s, %s, %s, COALESCE(%s, CURRENT_DATE))
            RETURNING id, amount, category, description, expense_date
            """,
            (amount, category.strip().lower(), description, expense_date),
        ).fetchone()
    return row


@mcp.tool()
def list_expenses(
    category: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 20,
):
    """List expenses, newest first. Filter by category, description text, or YYYY-MM-DD date range."""
    query = "SELECT id, amount, category, description, expense_date FROM expenses"
    conditions = []
    params = []

    if category:
        conditions.append("category = %s")
        params.append(category.strip().lower())
    if search:
        conditions.append("description ILIKE %s")
        params.append(f"%{search}%")
    if start_date:
        conditions.append("expense_date >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("expense_date <= %s")
        params.append(end_date)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY expense_date DESC, id DESC LIMIT %s"
    params.append(limit)

    with get_conn() as conn:
        return conn.execute(query, params).fetchall()


@mcp.tool()
def edit_expense(
    expense_id: int,
    amount: Optional[float] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    expense_date: Optional[str] = None,
):
    """Edit an expense by id. Only the fields you pass are changed."""
    fields = {
        "amount": amount,
        "category": category.strip().lower() if category else None,
        "description": description,
        "expense_date": expense_date,
    }
    # keep only the fields the caller actually passed
    changes = {k: v for k, v in fields.items() if v is not None}
    if not changes:
        return "Nothing to update"

    set_clause = ", ".join(f"{col} = %s" for col in changes)
    params = list(changes.values()) + [expense_id]

    with get_conn() as conn:
        row = conn.execute(
            f"""
            UPDATE expenses SET {set_clause}
            WHERE id = %s
            RETURNING id, amount, category, description, expense_date
            """,
            params,
        ).fetchone()
    return row if row else f"Expense {expense_id} not found"


@mcp.tool()
def delete_expense(expense_id: int):
    """Delete an expense by id."""
    with get_conn() as conn:
        row = conn.execute(
            "DELETE FROM expenses WHERE id = %s RETURNING id, amount, category, description",
            (expense_id,),
        ).fetchone()
    return row if row else f"Expense {expense_id} not found"


@mcp.tool()
def summarize_expenses(
    group_by: str = "category",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """Summarise spending. group_by is category, month, or day. Dates are YYYY-MM-DD."""
    group_options = {
        "category": "category",
        "month": "to_char(expense_date, 'YYYY-MM')",
        "day": "expense_date::text",
    }
    if group_by not in group_options:
        return f"group_by must be one of {list(group_options)}"

    group_expr = group_options[group_by]
    conditions = []
    params = []

    if start_date:
        conditions.append("expense_date >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("expense_date <= %s")
        params.append(end_date)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    query = f"""
        SELECT {group_expr} AS grp,
               COUNT(*)               AS num_expenses,
               SUM(amount)            AS total,
               ROUND(AVG(amount), 2)  AS average
        FROM expenses
        {where}
        GROUP BY 1
        ORDER BY total DESC
    """

    with get_conn() as conn:
        return conn.execute(query, params).fetchall()


if __name__ == "__main__":
    init_db()
    if os.environ.get("PORT"):
        mcp.run(transport="http", host="0.0.0.0", port=int(os.environ["PORT"]))
    else:
        mcp.run()