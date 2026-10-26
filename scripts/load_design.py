"""Load design/fabric-design.yml (the single FabricDesign object) into Infrahub.

`infrahubctl object load` cannot load this file: several JSON fields use keys such as "1", "1400" or "OOB-NET-TEAM",
which infrahub-sdk 1.23.2 writes unquoted into the GraphQL mutation (invalid GraphQL). This script sends the values as
GraphQL variables instead. Upsert by name, so a rerun updates the same object.

    infrahubctl run scripts/load_design.py --branch <branch>
"""
import yaml
from infrahub_sdk import InfrahubClient

DESIGN_FILE = "design/fabric-design.yml"
MUTATION = "mutation Load($data: FabricDesignUpsertInput!) { FabricDesignUpsert(data: $data) { ok object { id } } }"


async def run(client: InfrahubClient, log, branch: str, **kwargs) -> None:
    doc = next(yaml.safe_load_all(open(DESIGN_FILE)))
    items = doc["spec"]["data"]
    if doc["spec"]["kind"] != "FabricDesign" or len(items) != 1:
        raise ValueError(f"{DESIGN_FILE}: expected exactly one FabricDesign object")
    data = {field: {"value": value} for field, value in items[0].items()}
    result = await client.execute_graphql(query=MUTATION, variables={"data": data}, branch_name=branch)
    if not result.get("FabricDesignUpsert", {}).get("ok"):
        raise ValueError(f"FabricDesign load failed: {result}")
    print(f"FabricDesign {items[0]['name']} loaded on {branch}: {result['FabricDesignUpsert']['object']['id']} ({len(data)} fields)")
