"""Deterministic demo data (DuckDB) and a query log with deliberate conflicts."""
import datetime as dt
import json
import random
from pathlib import Path

import duckdb

LOG = [  # (user, repeats, sql)
    ("alice", 6, "SELECT SUM(amount) AS revenue FROM payments WHERE status = 'success'"),
    ("alice", 3, "SELECT method, SUM(amount) AS revenue FROM payments WHERE status = 'success' GROUP BY method"),
    ("bob", 2, "SELECT SUM(amount) AS revenue FROM payments"),
    ("bob", 1, "SELECT SUM(amount) FROM payments"),
    ("alice", 4, "SELECT COUNT(DISTINCT customer_id) AS active_customers FROM orders WHERE order_date >= current_date - 30"),
    ("bob", 2, "SELECT COUNT(DISTINCT customer_id) AS active_customers FROM orders WHERE status = 'completed'"),
    ("carol", 1, "SELECT COUNT(DISTINCT customer_id) AS active_customers FROM payments WHERE amount > 0"),
    ("bob", 5, "SELECT COUNT(*) AS order_count FROM orders"),
    ("bob", 3, "SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status"),
    ("carol", 3, "SELECT AVG(amount) AS avg_payment FROM payments WHERE status = 'success'"),
    ("carol", 2, "SELECT region, COUNT(*) AS customer_count FROM customers GROUP BY region"),
    ("carol", 1, "SELECT COUNT(*) AS customer_count FROM customers"),
    ("carol", 2, "SELECT COUNT(*) AS new_customers FROM customers WHERE created_at >= current_date - 30"),
    ("alice", 2, "SELECT c.region, SUM(p.amount) AS revenue FROM payments p JOIN customers c ON c.id = p.customer_id GROUP BY c.region"),
    ("bob", 1, "this is not sql"),
]


def build_db(path: str | Path) -> None:
    Path(path).unlink(missing_ok=True)
    rnd, today = random.Random(42), dt.date.today()
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE customers(id INTEGER, name VARCHAR, email VARCHAR, region VARCHAR, created_at DATE)")
    con.execute("CREATE TABLE orders(id INTEGER, customer_id INTEGER, status VARCHAR, order_date DATE)")
    con.execute("CREATE TABLE payments(id INTEGER, order_id INTEGER, customer_id INTEGER, method VARCHAR, status VARCHAR, amount DOUBLE, paid_at DATE)")
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", [
        (i, f"Customer {i}", f"c{i}@example.com", rnd.choice(["EU", "US", "APAC"]),
         today - dt.timedelta(rnd.randint(0, 365))) for i in range(1, 61)])
    orders, payments = [], []
    for i in range(1, 301):
        cid, day = rnd.randint(1, 60), today - dt.timedelta(rnd.randint(0, 90))
        orders.append((i, cid, rnd.choices(["completed", "returned", "placed", "shipped"], [60, 10, 15, 15])[0], day))
        payments.append((i, i, cid, rnd.choice(["card", "bank", "gift"]),
                         rnd.choices(["success", "failed"], [85, 15])[0], round(rnd.uniform(5, 200), 2), day))
    con.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
    con.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?,?)", payments)
    con.close()


def write_log(path: str | Path) -> None:
    lines = [json.dumps({"user": u, "sql": s}) for u, n, s in LOG for _ in range(n)]
    Path(path).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    here = Path(__file__).parent
    build_db(here / "jaffle.duckdb")
    write_log(here / "query_log.jsonl")
    print("seeded", here)
