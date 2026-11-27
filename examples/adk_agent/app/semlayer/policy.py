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
"""Access policy evaluation: default deny, certified rules only, deny beats allow."""

from dataclasses import dataclass, field

from .models import Rule, State


@dataclass
class Decision:
    """Result of evaluating RBAC rules for a ``(role, metric)`` pair."""

    allowed: bool
    reason: str = ""
    row_filter: str | None = None
    deny_dims: set[str] = field(default_factory=set)


def evaluate(rules: list[Rule], role: str, metric: str) -> Decision:
    """Evaluate certified rules for ``role`` requesting ``metric``."""
    matching = [
        rule
        for rule in rules
        if rule.state is State.certified
        and rule.role == role
        and rule.metric in ("*", metric)
    ]
    deny_dims = {dim for rule in matching for dim in rule.deny_dims}
    if any(rule.effect == "deny" and not rule.deny_dims for rule in matching):
        return Decision(False, "denied by rule", deny_dims=deny_dims)
    allows = [rule for rule in matching if rule.effect == "allow"]
    if not allows:
        return Decision(
            False, f"no allow rule for role '{role}'", deny_dims=deny_dims
        )
    if any(rule.row_filter is None for rule in allows):
        return Decision(True, row_filter=None, deny_dims=deny_dims)
    combined_filter = " OR ".join(
        sorted({f"({rule.row_filter})" for rule in allows if rule.row_filter})
    )
    return Decision(True, row_filter=combined_filter, deny_dims=deny_dims)


def unrestricted(rules: list[Rule], role: str) -> bool:
    """Return True if ``role`` has an unfiltered wildcard allow with no hard deny."""
    live = [
        rule
        for rule in rules
        if rule.state is State.certified and rule.role == role
    ]
    if any(rule.effect == "deny" and not rule.deny_dims for rule in live):
        return False
    return any(
        rule.effect == "allow"
        and rule.metric == "*"
        and rule.row_filter is None
        for rule in live
    )
