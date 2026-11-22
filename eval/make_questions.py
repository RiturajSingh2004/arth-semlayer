"""Generates eval/questions.yaml from compact templates. Run: python eval/make_questions.py"""
import yaml

Q = []


def add(cat, q, call, gold, role="finance", expect="answer"):
    Q.append({"category": cat, "q": q, "role": role, "expect": expect, "call": call, "gold_sql": gold})


P = "FROM payments WHERE status = 'success'"
add("single", "What is our revenue?", {"metric": "revenue"}, f"SELECT SUM(amount) {P}")
add("single", "How many orders have we had?", {"metric": "order_count"}, "SELECT COUNT(*) FROM orders")
add("single", "What is the average successful payment?", {"metric": "avg_payment"}, f"SELECT AVG(amount) {P}")
add("single", "How many customers ordered in the last 30 days?", {"metric": "active_customers_30d"},
    "SELECT COUNT(DISTINCT customer_id) FROM orders WHERE order_date >= current_date - 30")
add("single", "How many customers have a completed order?", {"metric": "active_customers_completed"},
    "SELECT COUNT(DISTINCT customer_id) FROM orders WHERE status = 'completed'")
add("single", "How many customers have a positive payment?", {"metric": "active_customers_paid"},
    "SELECT COUNT(DISTINCT customer_id) FROM payments WHERE amount > 0")
add("single", "How many customers do we have?", {"metric": "customer_count"}, "SELECT COUNT(*) FROM customers")
add("single", "How many new customers in the last 30 days?", {"metric": "new_customers"},
    "SELECT COUNT(*) FROM customers WHERE created_at >= current_date - 30")
add("grouped", "Revenue by payment method", {"metric": "revenue", "dims": ["method"]},
    f"SELECT method, SUM(amount) {P} GROUP BY method")
add("grouped", "Order count by status", {"metric": "order_count", "dims": ["status"]},
    "SELECT status, COUNT(*) FROM orders GROUP BY status")
add("grouped", "Customers by region", {"metric": "customer_count", "dims": ["region"]},
    "SELECT region, COUNT(*) FROM customers GROUP BY region")
for m in ("card", "bank", "gift"):
    add("filtered", f"Revenue from {m} payments", {"metric": "revenue", "filters": {"method": m}},
        f"SELECT SUM(amount) {P} AND method = '{m}'")
for s in ("completed", "placed", "returned"):
    add("filtered", f"How many {s} orders?", {"metric": "order_count", "filters": {"status": s}},
        f"SELECT COUNT(*) FROM orders WHERE status = '{s}'")
for r in ("APAC", "US"):
    add("filtered", f"How many customers in {r}?", {"metric": "customer_count", "filters": {"region": r}},
        f"SELECT COUNT(*) FROM customers WHERE region = '{r}'")
add("role", "(EU analyst) How many customers do we have?", {"metric": "customer_count"},
    "SELECT COUNT(*) FROM customers WHERE region = 'EU'", role="eu_analyst")
add("role", "(EU analyst) Customers by region", {"metric": "customer_count", "dims": ["region"]},
    "SELECT region, COUNT(*) FROM customers WHERE region = 'EU' GROUP BY region", role="eu_analyst")
add("role", "(EU analyst) How many orders?", {"metric": "order_count"},
    "SELECT COUNT(*) FROM orders", role="eu_analyst")
add("role", "(EU analyst) What is our revenue?", {"metric": "revenue"}, "", role="eu_analyst", expect="refuse")
add("role", "(guest) What is our revenue?", {"metric": "revenue"}, "", role="guest", expect="refuse")
add("role", "(guest) How many customers?", {"metric": "customer_count"}, "", role="guest", expect="refuse")
add("role", "Customers by email address", {"metric": "customer_count", "dims": ["email"]}, "", expect="refuse")
for q in ("What is our churn rate?", "What is our gross margin?", "What is our NPS?",
          "What is the average customer age?"):
    add("abstain", q, None, "", expect="refuse")
for s in ("completed", "returned", "placed"):
    add("multi-hop", f"Revenue from customers who have a {s} order",
        {"metric": "revenue", "graph": {"start": "Customer", "path": ["HAS_ORDER"],
                                        "filters": {"Order": {"status": s}}}},
        f"SELECT SUM(amount) {P} AND customer_id IN (SELECT customer_id FROM orders WHERE status = '{s}')")
yaml.safe_dump(Q, open(__file__.replace("make_questions.py", "questions.yaml"), "w"), sort_keys=False)
print(len(Q), "questions")
