from datetime import datetime, timezone
from typing import Any

class MetricsCollector:
    """Lightweight operational metrics collector without high-cardinality sensitive labels."""

    def __init__(self):
        self.request_count = 0
        self.error_count = 0
        self.job_completed_count = 0
        self.job_failed_count = 0
        self.provider_success_count = 0
        self.provider_failure_count = 0
        self.start_time = datetime.now(timezone.utc)

    def record_request(self, status_code: int):
        self.request_count += 1
        if status_code >= 400:
            self.error_count += 1

    def record_job(self, status: str):
        if status == "COMPLETED":
            self.job_completed_count += 1
        elif status == "FAILED":
            self.job_failed_count += 1

    def record_provider(self, success: bool):
        if success:
            self.provider_success_count += 1
        else:
            self.provider_failure_count += 1

    def get_metrics(self) -> dict[str, Any]:
        uptime_seconds = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        return {
            "uptime_seconds": round(uptime_seconds, 2),
            "requests_total": self.request_count,
            "errors_total": self.error_count,
            "jobs_completed_total": self.job_completed_count,
            "jobs_failed_total": self.job_failed_count,
            "provider_successes_total": self.provider_success_count,
            "provider_failures_total": self.provider_failure_count,
        }

metrics_collector = MetricsCollector()
