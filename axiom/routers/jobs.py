from fastapi import APIRouter, Depends

from axiom.api_domains import jobs as jobs_domain
from axiom.api_security import require_operator_access

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_operator_access)])


@router.get("/api/jobs")
def get_jobs(status: str | None = None, limit: int = 50):
    return jobs_domain.get_jobs_compat(status=status, limit=limit)


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return jobs_domain.get_job_compat(job_id)


@router.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str):
    return jobs_domain.cancel_job_compat(job_id)
