"""Validate ticket schemas, Markdown parity, issue mappings, and dependency DAG.

Only Python's standard library is required. This checks the deliberately small
JSON Schema vocabulary used by the two committed schema files; it is not a
general-purpose implementation of the JSON Schema specification.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

TICKET_DIR = Path(__file__).resolve().parent
REPO_ROOT = TICKET_DIR.parent.parent


def _matches_type(value: object, kind: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "null": value is None,
    }.get(kind, False)


def _schema_check(
    value: object, schema: dict, path: str, errors: list[str], schema_dir: Path
) -> None:
    if "$ref" in schema:
        target = (schema_dir / schema["$ref"]).resolve()
        if not target.is_relative_to(TICKET_DIR) or target.suffix != ".json":
            errors.append(f"{path}: unsupported nonlocal schema reference")
            return
        _schema_check(value, json.loads(target.read_text()), path, errors, target.parent)
        return
    expected = schema.get("type")
    if expected:
        kinds = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, kind) for kind in kinds):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: unsupported value {value!r}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required field {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{path}: unknown field {key}")
        for key, item in value.items():
            if key in properties:
                _schema_check(item, properties[key], f"{path}.{key}", errors, schema_dir)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{path}: duplicate items")
        for index, item in enumerate(value):
            _schema_check(item, schema.get("items", {}), f"{path}[{index}]", errors, schema_dir)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is too short")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
        if schema.get("format") == "date":
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("invalid date")
                date.fromisoformat(value)
            except ValueError:
                errors.append(f"{path}: expected an ISO calendar date")
        if schema.get("format") == "uri":
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.netloc:
                errors.append(f"{path}: expected an absolute HTTPS URL")
    if type(value) in (int, float) and "minimum" in schema:
        if value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")


def _body_check(ticket: dict, errors: list[str]) -> None:
    ticket_id = ticket["id"]
    path = (REPO_ROOT / ticket["body_path"]).resolve()
    if not path.is_relative_to(TICKET_DIR) or path.name != f"{ticket_id}.md":
        errors.append(f"{ticket_id}: body path escapes tickets or has the wrong filename")
        return
    if not path.is_file():
        errors.append(f"{ticket_id}: missing Markdown body {ticket['body_path']}")
        return
    body = path.read_text(encoding="utf-8")
    if not body.startswith(f"# {ticket_id} — {ticket['title']}\n"):
        errors.append(f"{ticket_id}: Markdown title differs from catalog")
    for field, label in (("status", "Status"), ("priority", "Priority"), ("area", "Area")):
        if not re.search(rf"^\*\*{label}:\*\* {re.escape(ticket[field])}$", body, re.MULTILINE):
            errors.append(f"{ticket_id}: Markdown {field} differs from catalog")
    if not re.search(
        rf"^\*\*Milestone:\*\* {re.escape(ticket['milestone'])} — ", body, re.MULTILINE
    ):
        errors.append(f"{ticket_id}: Markdown milestone differs from catalog")
    for heading in (
        "Outcome and user value",
        "Scope",
        "Non-goals",
        "Acceptance checks",
        "Dependencies",
        "Risks and evidence boundaries",
        "Source and reuse references",
        "Implementation and qualification evidence",
    ):
        if f"\n## {heading}\n" not in body:
            errors.append(f"{ticket_id}: missing Markdown section {heading}")
    for acceptance in ticket["acceptance_checks"]:
        if f"- [ ] {acceptance}" not in body and f"- [x] {acceptance}" not in body:
            errors.append(f"{ticket_id}: acceptance check missing from body: {acceptance}")
    for dependency in ticket["dependencies"]:
        if f"[{dependency}]({dependency}.md)" not in body:
            errors.append(f"{ticket_id}: dependency {dependency} missing from body")
    issue = ticket["github_issue"]
    if issue["url"] is not None and issue["url"] not in body:
        errors.append(f"{ticket_id}: GitHub issue link missing from Markdown body")


def validate(catalog_path: Path) -> tuple[list[str], list[str], dict | None]:
    errors: list[str] = []
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        schema = json.loads((TICKET_DIR / "catalog.schema.json").read_text(encoding="utf-8"))
        _schema_check(catalog, schema, "catalog", errors, TICKET_DIR)
    except (OSError, ValueError) as exc:
        return [f"Cannot read catalog/schema: {exc}"], [], None
    if errors:
        return errors, [], catalog

    milestones = {item["id"]: item for item in catalog["milestones"]}
    if len(milestones) != len(catalog["milestones"]):
        errors.append("Duplicate milestone IDs")
    sources = {item["id"]: item for item in catalog["sources"]}
    if len(sources) != len(catalog["sources"]):
        errors.append("Duplicate source IDs")
    for source in sources.values():
        if source["kind"] == "upstream_code":
            revision = source["revision"]
            if not re.fullmatch(r"[a-f0-9]{40}", revision):
                errors.append(f"{source['id']}: upstream code must name a full commit SHA")
            elif f"/blob/{revision}/" not in source["url"]:
                errors.append(f"{source['id']}: source URL does not match the reviewed commit")
    tickets = {item["id"]: item for item in catalog["tickets"]}
    if len(tickets) != len(catalog["tickets"]):
        errors.append("Duplicate ticket IDs")
    titles = [item["title"].casefold() for item in catalog["tickets"]]
    if len(set(titles)) != len(titles):
        errors.append("Duplicate ticket titles")
    issue_numbers: set[int] = set()
    for ticket in catalog["tickets"]:
        ticket_id = ticket["id"]
        if ticket["milestone"] not in milestones:
            errors.append(f"{ticket_id}: unknown milestone {ticket['milestone']}")
        for source in ticket["source_refs"]:
            if source not in sources:
                errors.append(f"{ticket_id}: unknown source reference {source}")
        for dependency in ticket["dependencies"]:
            if dependency not in tickets:
                errors.append(f"{ticket_id}: unknown dependency {dependency}")
            elif dependency == ticket_id:
                errors.append(f"{ticket_id}: depends on itself")
        if ticket["status"] in {"implemented", "done"} and not ticket["evidence"]:
            errors.append(f"{ticket_id}: {ticket['status']} requires qualification evidence")
        issue = ticket["github_issue"]
        if (issue["number"] is None) != (issue["url"] is None):
            errors.append(f"{ticket_id}: issue number and URL must be set together")
        if issue["number"] is not None:
            expected = f"https://github.com/mfreeze77/exais-football-ai/issues/{issue['number']}"
            if issue["url"] != expected:
                errors.append(f"{ticket_id}: issue URL and number disagree")
            if issue["number"] in issue_numbers:
                errors.append(f"{ticket_id}: GitHub issue mapped more than once")
            issue_numbers.add(issue["number"])
        _body_check(ticket, errors)
    mapping_path = TICKET_DIR / "GITHUB_ISSUES.json"
    if mapping_path.is_file():
        try:
            mapping_rows = json.loads(mapping_path.read_text(encoding="utf-8"))
            if not isinstance(mapping_rows, list):
                raise ValueError("expected a list of issue mappings")
            mappings = {row["id"]: row for row in mapping_rows}
            if len(mappings) != len(mapping_rows):
                errors.append("GITHUB_ISSUES.json has duplicate ticket IDs")
            for ticket in tickets.values():
                issue = ticket["github_issue"]
                if issue["number"] is not None:
                    expected = {"id": ticket["id"], **issue}
                    if mappings.get(ticket["id"]) != expected:
                        errors.append(
                            f"{ticket['id']}: issue mapping snapshot differs from catalog"
                        )
            for ticket_id in mappings.keys() - tickets.keys():
                errors.append(f"GITHUB_ISSUES.json maps unknown ticket {ticket_id}")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"Cannot validate GITHUB_ISSUES.json: {exc}")
    untracked = {path.stem for path in TICKET_DIR.glob("EFA-*.md") if path.stem not in tickets}
    if untracked:
        errors.append(f"Ticket bodies absent from catalog: {', '.join(sorted(untracked))}")

    visiting: list[str] = []
    visited: set[str] = set()
    order: list[str] = []

    def visit(ticket_id: str) -> None:
        if ticket_id in visiting:
            cycle = visiting[visiting.index(ticket_id) :] + [ticket_id]
            errors.append("Dependency cycle: " + " -> ".join(cycle))
            return
        if ticket_id in visited or ticket_id not in tickets:
            return
        visiting.append(ticket_id)
        for dependency in tickets[ticket_id]["dependencies"]:
            visit(dependency)
        visiting.pop()
        visited.add(ticket_id)
        order.append(ticket_id)

    for ticket_id in sorted(tickets):
        visit(ticket_id)
    return errors, order, catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=TICKET_DIR / "catalog.json")
    parser.add_argument("--show-order", action="store_true", help="Print a valid dependency order")
    args = parser.parse_args()
    errors, order, catalog = validate(args.catalog)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"Ticket validation failed: {len(errors)} error(s).", file=sys.stderr)
        return 1
    assert catalog is not None
    checks = sum(len(ticket["acceptance_checks"]) for ticket in catalog["tickets"])
    mapped = sum(ticket["github_issue"]["number"] is not None for ticket in catalog["tickets"])
    print(
        f"Validated {len(order)} tickets, {len(catalog['milestones'])} milestones, "
        f"{checks} acceptance checks, and {mapped} GitHub issue mappings; "
        "dependency DAG is acyclic."
    )
    if args.show_order:
        print("Dependency order: " + ", ".join(order))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
