"""Held-out eval set: different phrasing/values than questions.yaml. Run: python eval/make_heldout.py
Do NOT tune prompts against these; run once per prompt version and report as-is."""
import yaml

Q = []


def add(cat, q, call, gold, role="finance", expect="answer"):
    Q.append({"category": cat, "q": q, "role": role, "expect": expect, "call": call, "gold_sql": gold})


P = "FROM payments WHERE status = 'success'"
add("single", "Total sales", {"metric": "revenue"}, f"SELECT SUM(amount) {P}")
add("single", "Number of orders", {"metric": "order_count"}, "SELECT COUNT(*) FROM orders")
add("single", "Mean payment amount for successful payments", {"metric": "avg_payment"}, f"SELECT AVG(amount) {P}")
add("single", "How many customers made a payment?", {"metric": "active_customers_paid"},
    "SELECT COUNT(DISTINCT customer_id) FROM payments WHERE amount > 0")
add("single", "How many customers signed up in the last 30 days?", {"metric": "new_customers"},
    "SELECT COUNT(*) FROM customers WHERE created_at >= current_date - 30")
add("grouped", "Sales by method", {"metric": "revenue", "dims": ["method"]},
    f"SELECT method, SUM(amount) {P} GROUP BY method")
add("grouped", "Orders per status", {"metric": "order_count", "dims": ["status"]},
    "SELECT status, COUNT(*) FROM orders GROUP BY status")
add("filtered", "How many customers are in the EU?", {"metric": "customer_count", "filters": {"region": "EU"}},
    "SELECT COUNT(*) FROM customers WHERE region = 'EU'")
add("filtered", "How many shipped orders?", {"metric": "order_count", "filters": {"status": "shipped"}},
    "SELECT COUNT(*) FROM orders WHERE status = 'shipped'")
add("filtered", "Total sales paid by gift", {"metric": "revenue", "filters": {"method": "gift"}},
    f"SELECT SUM(amount) {P} AND method = 'gift'")
add("role", "(EU analyst) Number of customers per region", {"metric": "customer_count", "dims": ["region"]},
    "SELECT region, COUNT(*) FROM customers WHERE region = 'EU' GROUP BY region", role="eu_analyst")
add("role", "(EU analyst) What is the average payment?", {"metric": "avg_payment"}, "", role="eu_analyst", expect="refuse")
add("role", "(guest) How many orders?", {"metric": "order_count"}, "", role="guest", expect="refuse")
add("abstain", "What is the refund rate?", None, "", expect="refuse")
add("abstain", "Revenue by country", {"metric": "revenue", "dims": ["country"]}, "", expect="refuse")
add("abstain", "Orders by customer region", {"metric": "order_count", "dims": ["region"]}, "", expect="refuse")
for s in ("shipped", "returned"):
    add("multi-hop", f"Total sales from customers with a {s} order",
        {"metric": "revenue", "graph": {"start": "Customer", "path": ["HAS_ORDER"],
                                        "filters": {"Order": {"status": s}}}},
        f"SELECT SUM(amount) {P} AND customer_id IN (SELECT customer_id FROM orders WHERE status = '{s}')")
yaml.safe_dump(Q, open(__file__.replace("make_heldout.py", "heldout.yaml"), "w"), sort_keys=False)
print(len(Q), "held-out questions")