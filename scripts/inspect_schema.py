import logging

from infrahub_sdk import InfrahubClient
from infrahub_sdk.exceptions import SchemaNotFoundError


def _v(value):
    # Enum values (such as a relationship kind) print as "Component", not "RelationshipKind.COMPONENT"
    return getattr(value, "value", value)


async def list_kinds(client: InfrahubClient, branch: str) -> None:
    schemas = await client.schema.all(branch=branch)
    by_namespace: dict[str, list[str]] = {}
    skipped: dict[str, int] = {}
    for kind, schema in schemas.items():
        schema_type = type(schema).__name__.replace("SchemaAPI", "")
        if schema_type in ("Profile", "Template"):
            skipped[schema_type] = skipped.get(schema_type, 0) + 1
            continue
        name = f"{kind}*" if schema_type == "Generic" else kind
        by_namespace.setdefault(schema.namespace, []).append(name)
    print(f"Kinds on branch {branch} (* = generic)")
    for namespace in sorted(by_namespace):
        print(f"  {namespace:14} {', '.join(sorted(by_namespace[namespace]))}")
    for schema_type, count in skipped.items():
        print(f"  not listed: {count} {schema_type} kinds")


async def show_kind(client: InfrahubClient, branch: str, kind: str) -> None:
    try:
        schema = await client.schema.get(kind=kind, branch=branch)
    except SchemaNotFoundError:
        print(f"{kind}: NOT FOUND on branch {branch}\n")
        return
    schema_type = type(schema).__name__.replace("SchemaAPI", "")
    print(f"{kind} (type {schema_type}, branch {branch})")
    for prop in ("inherit_from", "used_by", "hierarchical", "hierarchy", "human_friendly_id", "uniqueness_constraints"):
        if hasattr(schema, prop):
            print(f"  {prop:24} {getattr(schema, prop)}")
    for attr in schema.attributes:
        choices = [c["name"] if isinstance(c, dict) else getattr(c, "name", c) for c in (attr.choices or [])]
        extra = f" choices={choices}" if choices else ""
        print(
            f"  attribute     {attr.name:28} kind={_v(attr.kind):10} optional={attr.optional} "
            f"unique={getattr(attr, 'unique', None)} inherited={getattr(attr, 'inherited', None)} "
            f"default={attr.default_value}{extra}"
        )
    for rel in schema.relationships:
        print(
            f"  relationship  {rel.name:28} peer={rel.peer:24} kind={_v(rel.kind):10} "
            f"cardinality={_v(rel.cardinality)} optional={rel.optional} "
            f"inherited={getattr(rel, 'inherited', None)} identifier={getattr(rel, 'identifier', None)}"
        )
    print()


async def run(client: InfrahubClient, log: logging.Logger, branch: str, kind: str = "", **kwargs) -> None:
    kinds = [k.strip() for k in kind.split(",") if k.strip()]
    if not kinds:
        await list_kinds(client, branch)
        return
    for k in kinds:
        await show_kind(client, branch, k)