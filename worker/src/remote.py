"""Authenticated HTTP boundary; no inference dependencies in the edge runtime."""
import re
from urllib.parse import urlsplit

JOB_ID = re.compile(r"^[a-f0-9]{12}$")
EVIDENCE_NAME = re.compile(r"^e\d+-[\d.]+\.jpg$")


class RemoteError(RuntimeError):
    pass


class RemoteInference:
    def __init__(self, url, key, request, timeout_ms=30000):
        parsed = urlsplit(str(url))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RemoteError("INFERENCE_API_URL must be an HTTPS service origin")
        if not key:
            raise RemoteError("INFERENCE_API_KEY is required")
        self.url = str(url).rstrip("/")
        self.key = str(key)
        self.request = request
        self.timeout_ms = max(1000, min(int(timeout_ms), 60000))

    async def call(self, path, method="GET", body=None, binary=False):
        return await self.request(self.url + path, method=method, body=body,
                                  headers={"X-API-Key": self.key}, timeout_ms=self.timeout_ms, binary=binary)

    async def submit(self, form):
        value = await self.call("/api/v1/analyze", "POST", form)
        job_id = value.get("job_id", "")
        if not JOB_ID.fullmatch(job_id):
            raise RemoteError("Inference service returned an invalid job ID")
        return job_id

    def job_path(self, job_id):
        if not JOB_ID.fullmatch(str(job_id)):
            raise RemoteError("Invalid remote job ID")
        return "/api/v1/jobs/" + job_id

    async def status(self, job_id):
        value = await self.call(self.job_path(job_id))
        if value.get("status") not in ("processing", "done", "failed"):
            raise RemoteError("Inference service returned an invalid status")
        return value

    async def results(self, job_id):
        result = await self.call(self.job_path(job_id) + "/results")
        if not isinstance(result.get("violations"), list) or not isinstance(result.get("analysis"), dict):
            raise RemoteError("Inference service returned an invalid result")
        verdict = result.get("verdict")
        analysis = result["analysis"]
        if verdict not in {"insufficient_evidence", "supported_violation_candidate", "no_violation_observed"} or type(analysis.get("is_mock")) is not bool:
            raise RemoteError("Inference result lacks a safe decision and mock provenance")
        if analysis["is_mock"] and (verdict != "insufficient_evidence" or result["violations"] or analysis.get("events")):
            raise RemoteError("Mock inference cannot produce compliance findings")
        return result

    async def evidence(self, job_id, name):
        self.job_path(job_id)
        if not EVIDENCE_NAME.fullmatch(name):
            raise RemoteError("Invalid evidence name")
        return await self.call(f"/api/jobs/{job_id}/evidence/{name}", binary=True)
