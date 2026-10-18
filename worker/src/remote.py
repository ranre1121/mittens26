"""Authenticated HTTP boundary; no inference dependencies in the edge runtime."""
import math
import re
from urllib.parse import urlsplit

JOB_ID = re.compile(r"^[a-f0-9]{12}$")
EVIDENCE_NAME = re.compile(r"^e\d+-[\d.]+\.jpg$")


class RemoteError(RuntimeError):
    pass


class RemoteInference:
    def __init__(self, url, key, request, timeout_ms=30000):
        parsed = urlsplit(str(url))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
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
        if not isinstance(value, dict):
            raise RemoteError("Inference service returned an invalid submission")
        job_id = value.get("job_id", "")
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise RemoteError("Inference service returned an invalid job ID")
        return job_id

    def job_path(self, job_id):
        if not JOB_ID.fullmatch(str(job_id)):
            raise RemoteError("Invalid remote job ID")
        return "/api/v1/jobs/" + job_id

    async def status(self, job_id):
        value = await self.call(self.job_path(job_id))
        if not isinstance(value, dict) or value.get("status") not in ("processing", "done", "failed"):
            raise RemoteError("Inference service returned an invalid status")
        return value

    async def results(self, job_id):
        result = await self.call(self.job_path(job_id) + "/results")
        if not isinstance(result, dict) or not isinstance(result.get("violations"), list) or not isinstance(result.get("analysis"), dict):
            raise RemoteError("Inference service returned an invalid result")
        verdict = result.get("verdict")
        analysis = result["analysis"]
        if verdict not in {"insufficient_evidence", "supported_violation_candidate", "no_violation_observed"} or type(analysis.get("is_mock")) is not bool:
            raise RemoteError("Inference result lacks a safe decision and mock provenance")
        if analysis["is_mock"] and (verdict != "insufficient_evidence" or result["violations"] or analysis.get("events")):
            raise RemoteError("Mock inference cannot produce compliance findings")
        video = result.get("video", {})
        if not isinstance(video, dict) or ("duration_sec" in video and not finite_time(video["duration_sec"])):
            raise RemoteError("Inference result has an invalid video duration")
        frames = analysis.get("frames", [])
        if not isinstance(frames, list) or len(frames) > 3600:
            raise RemoteError("Inference result has invalid evidence frames")
        refs = set()
        for frame in frames:
            if not isinstance(frame, dict) or not isinstance(frame.get("frame_ref"), str) or not EVIDENCE_NAME.fullmatch(frame["frame_ref"]) or not finite_time(frame.get("timestamp_s")):
                raise RemoteError("Inference result has an invalid evidence reference")
            if frame["frame_ref"] in refs:
                raise RemoteError("Inference result repeats an evidence reference")
            refs.add(frame["frame_ref"])
        if len(result["violations"]) > 200:
            raise RemoteError("Inference result exceeds the finding limit")
        for event in result["violations"]:
            if not isinstance(event, dict) or any(not isinstance(event.get(key), str) for key in ("event_id", "event_type", "type", "description")) or not isinstance(event.get("rules"), list) or not isinstance(event.get("evidence_frames"), list):
                raise RemoteError("Inference result has an invalid finding")
            if any(not finite_time(event.get(key)) for key in ("timecode_sec", "start_sec", "end_sec")) or not event["start_sec"] <= event["timecode_sec"] <= event["end_sec"]:
                raise RemoteError("Inference result has an invalid event interval")
            if not event["evidence_frames"] or len(event["evidence_frames"]) > 16:
                raise RemoteError("Inference finding must contain bounded evidence")
            for frame in event["evidence_frames"]:
                if not isinstance(frame, dict) or not isinstance(frame.get("path"), str) or not EVIDENCE_NAME.fullmatch(frame["path"].rsplit("/", 1)[-1]) or not finite_time(frame.get("timestamp_sec")):
                    raise RemoteError("Inference finding has invalid evidence")
        return result

    async def ready(self):
        value = await self.call("/api/ready")
        if not isinstance(value, dict) or type(value.get("ready")) is not bool:
            raise RemoteError("Inference service returned invalid readiness")
        return value

    async def models(self):
        value = await self.call("/api/v1/models")
        if not isinstance(value, dict) or not isinstance(value.get("model_id"), str) or not isinstance(value.get("model_version"), str) or type(value.get("is_mock")) is not bool:
            raise RemoteError("Inference service returned invalid model provenance")
        return value

    async def evidence(self, job_id, name):
        self.job_path(job_id)
        if not EVIDENCE_NAME.fullmatch(name):
            raise RemoteError("Invalid evidence name")
        return await self.call(f"/api/jobs/{job_id}/evidence/{name}", binary=True)


def finite_time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0
