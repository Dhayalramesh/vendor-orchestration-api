"""
vendors.py

Vendor orchestration layer: calls multiple compliance-check "vendors" for
a given case, with per-vendor timeout, retry with backoff, and fallback to
a secondary vendor if the primary fails -- the core resilience pattern
this kind of platform needs, since a single flaky third-party vendor
should never take down the whole onboarding flow.

Vendors here are simulated locally (random latency + occasional failure)
since real compliance-vendor APIs require paid contracts. The
orchestration logic itself -- timeouts, retries, fallback chains, response
normalization -- is the real, portfolio-relevant part; swapping a mock
vendor call for a real httpx.AsyncClient.get(vendor_url) call is a
one-line change (see `call_real_vendor` for the shape that would take).
"""
import asyncio
import random
import time
from dataclasses import dataclass
from enum import Enum


class VendorResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    MANUAL_REVIEW = "manual_review"
    UNAVAILABLE = "unavailable"


@dataclass
class CheckOutcome:
    vendor_name: str
    check_type: str
    result: VendorResult
    latency_ms: int
    raw_response: dict
    attempts: int


class VendorTimeoutError(Exception):
    pass


class VendorUnavailableError(Exception):
    pass


async def _simulate_vendor_call(vendor_name: str, check_type: str,
                                 failure_rate: float = 0.15,
                                 timeout_rate: float = 0.05) -> dict:
    """Simulates a real vendor HTTP call: variable latency, occasional
    failure/timeout. In production this would be an httpx.AsyncClient
    request to the vendor's actual REST endpoint."""
    latency = random.uniform(0.05, 0.4)
    await asyncio.sleep(latency)

    roll = random.random()
    if roll < timeout_rate:
        raise VendorTimeoutError(f"{vendor_name} timed out after {latency:.2f}s")
    if roll < timeout_rate + failure_rate:
        raise VendorUnavailableError(f"{vendor_name} returned 503")

    # Simulated normalized-ish raw vendor payload
    risk_score = random.uniform(0, 1)
    return {
        "vendor": vendor_name,
        "check_type": check_type,
        "risk_score": round(risk_score, 3),
        "raw_status": "clear" if risk_score < 0.3 else ("flag" if risk_score < 0.7 else "hit"),
    }


async def call_real_vendor(vendor_url: str, payload: dict, timeout: float = 5.0) -> dict:
    """Shape of the real implementation -- shown but unused by default
    since no real vendor endpoint/API key is available for this
    portfolio project. See README for how to swap this in."""
    import httpx
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(vendor_url, json=payload)
        resp.raise_for_status()
        return resp.json()


def normalize_response(raw: dict) -> VendorResult:
    """Maps each vendor's own status vocabulary to our internal result
    enum -- the "vendor response mapping" the JD calls out directly,
    since every real compliance vendor has its own status strings."""
    status = raw.get("raw_status")
    mapping = {
        "clear": VendorResult.PASS,
        "flag": VendorResult.MANUAL_REVIEW,
        "hit": VendorResult.FAIL,
    }
    return mapping.get(status, VendorResult.MANUAL_REVIEW)


async def call_vendor_with_resilience(
    vendor_name: str,
    check_type: str,
    max_retries: int = 2,
    timeout: float = 1.0,
    fallback_vendor: str | None = None,
) -> CheckOutcome:
    """Orchestrates a single check with retry + timeout, falling back to
    a secondary vendor if the primary is exhausted -- so one vendor being
    down doesn't fail the whole case."""
    start = time.monotonic()
    attempts = 0
    last_error = None

    for attempt in range(1, max_retries + 1):
        attempts = attempt
        try:
            raw = await asyncio.wait_for(
                _simulate_vendor_call(vendor_name, check_type), timeout=timeout
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            return CheckOutcome(
                vendor_name=vendor_name, check_type=check_type,
                result=normalize_response(raw), latency_ms=latency_ms,
                raw_response=raw, attempts=attempts,
            )
        except (asyncio.TimeoutError, VendorTimeoutError, VendorUnavailableError) as e:
            last_error = e
            await asyncio.sleep(0.1 * attempt)  # simple exponential-ish backoff

    if fallback_vendor:
        try:
            raw = await asyncio.wait_for(
                _simulate_vendor_call(fallback_vendor, check_type), timeout=timeout
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            return CheckOutcome(
                vendor_name=fallback_vendor, check_type=check_type,
                result=normalize_response(raw), latency_ms=latency_ms,
                raw_response=raw, attempts=attempts + 1,
            )
        except Exception:
            pass

    latency_ms = int((time.monotonic() - start) * 1000)
    return CheckOutcome(
        vendor_name=vendor_name, check_type=check_type,
        result=VendorResult.UNAVAILABLE, latency_ms=latency_ms,
        raw_response={"error": str(last_error)}, attempts=attempts,
    )


async def run_case_checks(customer_country: str) -> list[CheckOutcome]:
    """Runs all required checks for a case concurrently -- identity,
    sanctions, and document -- each with its own resilience chain."""
    checks = [
        call_vendor_with_resilience("IdentityVendorA", "identity",
                                      fallback_vendor="IdentityVendorB"),
        call_vendor_with_resilience("SanctionsVendorX", "sanctions",
                                      fallback_vendor="SanctionsVendorY"),
        call_vendor_with_resilience("DocVendorPrimary", "document"),
    ]
    return await asyncio.gather(*checks)