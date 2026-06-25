from fastapi import APIRouter, Depends

from axiom.api_domains import memory as memory_domain
from axiom.api_security import require_operator_access

router = APIRouter(tags=["memory"], dependencies=[Depends(require_operator_access)])


@router.get("/api/memory/overview")
def get_memory_overview(limit: int = 24):
    return memory_domain.get_memory_overview(limit=limit)


@router.post("/api/memory/search")
async def search_memory(body: memory_domain.MemorySearchRequest):
    return await memory_domain.search_memory_records(body)


@router.get("/api/memory/maintenance/preview")
def get_memory_maintenance_preview(older_than_days: int = 14, limit: int = 200):
    return memory_domain.get_memory_maintenance_preview(
        older_than_days=older_than_days,
        limit=limit,
    )


@router.post("/api/memory/maintenance/run")
def post_memory_maintenance(body: memory_domain.MemoryMaintenanceRequest):
    return memory_domain.run_memory_maintenance(body)


@router.get("/api/memory/item/{source}/{item_id:path}")
def get_memory_item(source: str, item_id: str):
    return memory_domain.get_memory_item(source, item_id)


@router.put("/api/memory/item/{source}/{item_id:path}/annotation")
def put_memory_annotation(source: str, item_id: str, body: memory_domain.MemoryAnnotationBody):
    return memory_domain.update_memory_annotation(source, item_id, body)


@router.post("/api/memory/item/{source}/{item_id:path}/action")
async def post_memory_action(source: str, item_id: str, body: memory_domain.MemoryActionBody):
    return await memory_domain.apply_memory_action(source, item_id, body)
