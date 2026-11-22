# Manual (non-demo) end-to-end run: draft, resolve conflicts, certify each kind, link entities, eval.
# Run from the project root: bash scripts/manual_run.sh
set -e
S="uv run semlayer"
uv run python examples/jaffle_shop/seed.py
$S ingest
$S draft --roles finance,eu_analyst
$S conflicts
$S conflicts --pick revenue_v1
$S reject sum_amount
$S conflicts --split active_customers --names active_customers_v1=active_customers_30d active_customers_v2=active_customers_completed active_customers_v3=active_customers_paid
for n in $($S review --kind metric | awk '{print $2}'); do $S certify $n --accept-suggested; done
for n in $($S review --kind entity | awk '{print $2}'); do $S certify $n --kind entity --owner data-team; done
for n in $($S review --kind edge | awk '{print $2}'); do $S certify $n --kind edge --owner data-team; done
$S rule-add finance_all --role finance
$S rule-add eu_customers --role eu_analyst --metric customer_count --row-filter "region = 'EU'"
$S rule-add eu_orders --role eu_analyst --metric order_count
for n in $($S review --kind rule | awk '{print $2}'); do $S certify $n --kind rule --owner security-team; done
$S edit revenue entity=Customer entity_key=customer_id
$S certify revenue
$S edit order_count entity=Order entity_key=id
$S certify order_count
$S eval --oracle
