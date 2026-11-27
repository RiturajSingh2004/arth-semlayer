# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Deterministic offline DuckDB seed helper for the jaffle_shop dataset."""

import datetime as dt
import random
from pathlib import Path

import duckdb


def seed_connection(
    con: duckdb.DuckDBPyConnection,
    reference_date: dt.date | None = None,
) -> None:
    """Populate ``customers``, ``orders``, and ``payments`` deterministically."""
    rnd = random.Random(42)  # noqa: S311 -- deterministic synthetic fixture data
    today = reference_date or dt.date.today()
    con.execute(
        "CREATE TABLE customers("
        "id INTEGER, name VARCHAR, email VARCHAR, "
        "region VARCHAR, created_at DATE)"
    )
    con.execute(
        "CREATE TABLE orders("
        "id INTEGER, customer_id INTEGER, status VARCHAR, order_date DATE)"
    )
    con.execute(
        "CREATE TABLE payments("
        "id INTEGER, order_id INTEGER, customer_id INTEGER, "
        "method VARCHAR, status VARCHAR, amount DOUBLE, paid_at DATE)"
    )
    customers = [
        (
            idx,
            f"Customer {idx}",
            f"c{idx}@example.com",
            rnd.choice(["EU", "US", "APAC"]),
            today - dt.timedelta(days=rnd.randint(0, 365)),
        )
        for idx in range(1, 61)
    ]
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    orders = []
    payments = []
    for idx in range(1, 301):
        customer_id = rnd.randint(1, 60)
        day = today - dt.timedelta(days=rnd.randint(0, 90))
        order_status = rnd.choices(
            ["completed", "returned", "placed", "shipped"],
            weights=[60, 10, 15, 15],
        )[0]
        orders.append((idx, customer_id, order_status, day))
        payment_method = rnd.choice(["card", "bank", "gift"])
        payment_status = rnd.choices(["success", "failed"], weights=[85, 15])[0]
        amount = round(rnd.uniform(5, 200), 2)
        payments.append(
            (idx, idx, customer_id, payment_method, payment_status, amount, day)
        )
    con.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
    con.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?,?)", payments)


def build_db(
    path: str | Path,
    reference_date: dt.date | None = None,
) -> Path:
    """Create and seed a DuckDB database file at ``path``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    con = duckdb.connect(str(target))
    try:
        seed_connection(con, reference_date=reference_date)
    finally:
        con.close()
    return target
