"""
main.py

FastAPI service for compliance case management + vendor orchestration.

Endpoints:
  POST /cases              create a new case
  POST /cases/{id}/run     orchestrate identity/sanctions/document checks
  GET  /cases/{id}         get case + all vendor check results
  GET  /cases              list cases
  GET  /health             liveness probe
  GET  /ready               readiness probe (checks DB connectivity)
  GET  /metrics             basic request/latency counters

Run:  uvicorn app.main:app --reload
"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import Case, CaseStatus, VendorCheck, get_db, init_db
from app.vendors import CheckOutcome, VendorResult, run_case_checks

# --- Structured logging setup -----------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", '
           '"logger": "%(name)s", "message": "%(message)s"}',
)
logger = logging.getLogger("orchestration_api")

# --- Basic in-memory metrics (production would use Prometheus client) -
metrics = {"requests_total": 0, "checks_total": 0, "checks_failed": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database initialized")
    yield


app = FastAPI(title="Compliance Vendor Orchestration API", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request, call_next):
    start = time.monotonic()
    metrics["requests_total"] += 1
    response = await call_next(request)
    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(f'{{"method": "{request.method}", "path": "{request.url.path}", '
                f'"status": {response.status_code}, "duration_ms": {duration_ms}}}')
    return response


class CaseCreate(BaseModel):
    customer_name: str
    customer_country: str


class CaseOut(BaseModel):
    id: int
    customer_name: str
    customer_country: str
    status: str

    class Config:
        from_attributes = True


@app.post("/cases", response_model=CaseOut)
def create_case(payload: CaseCreate, db: Session = Depends(get_db)):
    case = Case(customer_name=payload.customer_name,
                customer_country=payload.customer_country,
                status=CaseStatus.pending)
    db.add(case)
    db.commit()
    db.refresh(case)
    logger.info(f'{{"event": "case_created", "case_id": {case.id}}}')
    return case


@app.post("/cases/{case_id}/run")
async def run_checks(case_id: int, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    case.status = CaseStatus.in_progress
    db.commit()

    outcomes: list[CheckOutcome] = await run_case_checks(case.customer_country)

    for outcome in outcomes:
        metrics["checks_total"] += 1
        if outcome.result in (VendorResult.FAIL, VendorResult.UNAVAILABLE):
            metrics["checks_failed"] += 1
        check_status = "success" if outcome.result != VendorResult.UNAVAILABLE else "failed"
        check = VendorCheck(
            case_id=case.id, vendor_name=outcome.vendor_name,
            check_type=outcome.check_type, status=check_status,
            raw_response=str(outcome.raw_response),
            normalized_result=outcome.result.value,
            latency_ms=outcome.latency_ms,
        )
        db.add(check)
        logger.info(f'{{"event": "vendor_check_complete", "case_id": {case.id}, '
                    f'"vendor": "{outcome.vendor_name}", "result": "{outcome.result.value}", '
                    f'"latency_ms": {outcome.latency_ms}, "attempts": {outcome.attempts}}}')

    results = [o.result for o in outcomes]
    if any(r == VendorResult.FAIL for r in results):
        case.status = CaseStatus.failed
    elif any(r in (VendorResult.MANUAL_REVIEW, VendorResult.UNAVAILABLE) for r in results):
        case.status = CaseStatus.review
    else:
        case.status = CaseStatus.passed

    db.commit()
    logger.info(f'{{"event": "case_decision", "case_id": {case.id}, '
                f'"status": "{case.status.value}"}}')

    return {
        "case_id": case.id,
        "status": case.status.value,
        "checks": [
            {"vendor": o.vendor_name, "type": o.check_type, "result": o.result.value,
             "latency_ms": o.latency_ms, "attempts": o.attempts}
            for o in outcomes
        ],
    }


@app.get("/cases/{case_id}")
def get_case(case_id: int, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return {
        "id": case.id, "customer_name": case.customer_name,
        "status": case.status.value,
        "checks": [
            {"vendor": c.vendor_name, "type": c.check_type,
             "result": c.normalized_result, "latency_ms": c.latency_ms}
            for c in case.checks
        ],
    }


@app.get("/cases")
def list_cases(db: Session = Depends(get_db)):
    cases = db.query(Case).all()
    return [{"id": c.id, "customer_name": c.customer_name, "status": c.status.value} for c in cases]


@app.get("/health")
def health():
    """Liveness probe -- is the process up at all."""
    return {"status": "ok"}


@app.get("/ready")
def ready(db: Session = Depends(get_db)):
    """Readiness probe -- is the service actually able to serve traffic
    (i.e. can it reach the database)."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}") from e


@app.get("/metrics")
def get_metrics():
    """Basic observability endpoint. A production service would expose
    this in Prometheus text format via prometheus_client instead."""
    failure_rate = (metrics["checks_failed"] / metrics["checks_total"]
                     if metrics["checks_total"] else 0.0)
    return {**metrics, "check_failure_rate": round(failure_rate, 3)}