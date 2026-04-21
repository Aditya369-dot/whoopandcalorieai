from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field


DEFAULT_BASE_URL = "https://api.prod.whoop.com/developer/v2"


class WhoopClientError(Exception):
    """Raised when the WHOOP API returns an error or cannot be reached."""


class WhoopScore(BaseModel):
    strain: Optional[float] = None
    kilojoule: Optional[float] = None
    average_heart_rate: Optional[int] = None
    max_heart_rate: Optional[int] = None


class WhoopRecoveryScore(BaseModel):
    user_calibrating: Optional[bool] = None
    recovery_score: Optional[int] = None
    resting_heart_rate: Optional[int] = None
    hrv_rmssd_milli: Optional[float] = None
    spo2_percentage: Optional[float] = None
    skin_temp_celsius: Optional[float] = None


class WhoopCycle(BaseModel):
    id: int
    user_id: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    timezone_offset: Optional[str] = None
    score_state: Optional[str] = None
    score: Optional[WhoopScore] = None


class WhoopRecovery(BaseModel):
    cycle_id: int
    sleep_id: Optional[str] = None
    user_id: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    score_state: Optional[str] = None
    score: Optional[WhoopRecoveryScore] = None


class WhoopWorkoutScore(BaseModel):
    strain: Optional[float] = None
    average_heart_rate: Optional[int] = None
    max_heart_rate: Optional[int] = None
    kilojoule: Optional[float] = None


class WhoopWorkout(BaseModel):
    id: str
    v1_id: Optional[int] = None
    user_id: int
    sport_name: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    timezone_offset: Optional[str] = None
    score_state: Optional[str] = None
    score: Optional[WhoopWorkoutScore] = None


class WhoopSleepStageSummary(BaseModel):
    total_in_bed_time_milli: Optional[int] = None
    total_awake_time_milli: Optional[int] = None
    total_no_data_time_milli: Optional[int] = None
    total_light_sleep_time_milli: Optional[int] = None
    total_slow_wave_sleep_time_milli: Optional[int] = None
    total_rem_sleep_time_milli: Optional[int] = None
    sleep_cycle_count: Optional[int] = None
    disturbance_count: Optional[int] = None


class WhoopSleepNeeded(BaseModel):
    baseline_milli: Optional[int] = None
    need_from_sleep_debt_milli: Optional[int] = None
    need_from_recent_strain_milli: Optional[int] = None
    need_from_recent_nap_milli: Optional[int] = None


class WhoopSleepScore(BaseModel):
    stage_summary: Optional[WhoopSleepStageSummary] = None
    sleep_needed: Optional[WhoopSleepNeeded] = None
    respiratory_rate: Optional[float] = None
    sleep_performance_percentage: Optional[int] = None
    sleep_consistency_percentage: Optional[int] = None
    sleep_efficiency_percentage: Optional[float] = None


class WhoopSleep(BaseModel):
    id: str
    cycle_id: int
    v1_id: Optional[int] = None
    user_id: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    timezone_offset: Optional[str] = None
    nap: Optional[bool] = None
    score_state: Optional[str] = None
    score: Optional[WhoopSleepScore] = None


class WhoopUserProfile(BaseModel):
    user_id: int
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class WhoopBodyMeasurement(BaseModel):
    height_meter: Optional[float] = None
    weight_kilogram: Optional[float] = None
    max_heart_rate: Optional[int] = None


class WhoopCollection(BaseModel):
    records: List[Dict[str, Any]] = Field(default_factory=list)
    next_token: Optional[str] = None


class WhoopDailySnapshot(BaseModel):
    date: Optional[str] = None
    cycle: Optional[WhoopCycle] = None
    recovery: Optional[WhoopRecovery] = None
    sleep: Optional[WhoopSleep] = None
    workouts: List[WhoopWorkout] = Field(default_factory=list)


class WhoopClient:
    """
    Minimal WHOOP API client built around the current v2 API surface.

    Expected env vars for local use:
    - WHOOP_ACCESS_TOKEN
    - WHOOP_BASE_URL (optional)
    """

    def __init__(
        self,
        access_token: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: int = 20,
    ) -> None:
        if not access_token:
            raise ValueError("access_token is required")

        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "WhoopClient":
        access_token = os.getenv("WHOOP_ACCESS_TOKEN", "").strip()
        base_url = os.getenv("WHOOP_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
        return cls(access_token=access_token, base_url=base_url)

    def _request(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_query = {
            key: value
            for key, value in (query or {}).items()
            if value is not None and value != ""
        }
        url = f"{self.base_url}{path}"
        if clean_query:
            url = f"{url}?{urlencode(clean_query)}"

        request = Request(
            url=url,
            method=method.upper(),
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WhoopClientError(
                f"WHOOP API error {exc.code} for {path}: {detail or exc.reason}"
            ) from exc
        except URLError as exc:
            raise WhoopClientError(f"Unable to reach WHOOP API: {exc.reason}") from exc

        if not body:
            return {}

        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise WhoopClientError(f"WHOOP API returned invalid JSON for {path}") from exc

    def get_user_profile(self) -> WhoopUserProfile:
        payload = self._request("GET", "/user/profile/basic")
        return WhoopUserProfile.model_validate(payload)

    def get_body_measurement(self) -> WhoopBodyMeasurement:
        payload = self._request("GET", "/user/measurement/body")
        return WhoopBodyMeasurement.model_validate(payload)

    def list_cycles(
        self,
        limit: int = 10,
        start: Optional[str] = None,
        end: Optional[str] = None,
        next_token: Optional[str] = None,
    ) -> List[WhoopCycle]:
        payload = self._request(
            "GET",
            "/cycle",
            query={
                "limit": min(max(limit, 1), 25),
                "start": start,
                "end": end,
                "nextToken": next_token,
            },
        )
        records = WhoopCollection.model_validate(payload).records
        return [WhoopCycle.model_validate(item) for item in records]

    def get_cycle(self, cycle_id: int) -> WhoopCycle:
        payload = self._request("GET", f"/cycle/{cycle_id}")
        return WhoopCycle.model_validate(payload)

    def get_latest_cycle(self) -> Optional[WhoopCycle]:
        cycles = self.list_cycles(limit=1)
        return cycles[0] if cycles else None

    def get_recovery_for_cycle(self, cycle_id: int) -> Optional[WhoopRecovery]:
        try:
            payload = self._request("GET", f"/cycle/{cycle_id}/recovery")
        except WhoopClientError as exc:
            if " 404 " in f" {exc} ":
                return None
            raise
        return WhoopRecovery.model_validate(payload)

    def get_current_recovery(self) -> Optional[WhoopRecovery]:
        cycle = self.get_latest_cycle()
        if not cycle:
            return None
        return self.get_recovery_for_cycle(cycle.id)

    def get_sleep_for_cycle(self, cycle_id: int) -> Optional[WhoopSleep]:
        try:
            payload = self._request("GET", f"/cycle/{cycle_id}/sleep")
        except WhoopClientError as exc:
            if " 404 " in f" {exc} ":
                return None
            raise
        return WhoopSleep.model_validate(payload)

    def list_sleep(
        self,
        limit: int = 10,
        start: Optional[str] = None,
        end: Optional[str] = None,
        next_token: Optional[str] = None,
    ) -> List[WhoopSleep]:
        payload = self._request(
            "GET",
            "/activity/sleep",
            query={
                "limit": min(max(limit, 1), 25),
                "start": start,
                "end": end,
                "nextToken": next_token,
            },
        )
        records = WhoopCollection.model_validate(payload).records
        return [WhoopSleep.model_validate(item) for item in records]

    def get_sleep(self, sleep_id: str) -> WhoopSleep:
        payload = self._request("GET", f"/activity/sleep/{sleep_id}")
        return WhoopSleep.model_validate(payload)

    def list_workouts(
        self,
        limit: int = 10,
        start: Optional[str] = None,
        end: Optional[str] = None,
        next_token: Optional[str] = None,
    ) -> List[WhoopWorkout]:
        payload = self._request(
            "GET",
            "/activity/workout",
            query={
                "limit": min(max(limit, 1), 25),
                "start": start,
                "end": end,
                "nextToken": next_token,
            },
        )
        records = WhoopCollection.model_validate(payload).records
        return [WhoopWorkout.model_validate(item) for item in records]

    def get_workout(self, workout_id: str) -> WhoopWorkout:
        payload = self._request("GET", f"/activity/workout/{workout_id}")
        return WhoopWorkout.model_validate(payload)

    def get_daily_snapshot(self, date: Optional[str] = None) -> WhoopDailySnapshot:
        """
        Returns a normalized daily bundle for meal recommendation logic.

        If no date is provided, this uses the latest cycle available.
        Date format is expected to be YYYY-MM-DD.
        """
        target_date = date

        if date:
            start = f"{date}T00:00:00Z"
            end = f"{date}T23:59:59Z"
            cycles = self.list_cycles(limit=1, start=start, end=end)
            cycle = cycles[0] if cycles else None
            workouts = self.list_workouts(limit=25, start=start, end=end)
        else:
            cycle = self.get_latest_cycle()
            workouts = []

        recovery = self.get_recovery_for_cycle(cycle.id) if cycle else None
        sleep = self.get_sleep_for_cycle(cycle.id) if cycle else None

        if not target_date and cycle and cycle.start:
            target_date = _date_part(cycle.start)

        return WhoopDailySnapshot(
            date=target_date,
            cycle=cycle,
            recovery=recovery,
            sleep=sleep,
            workouts=workouts,
        )


def _date_part(value: str) -> Optional[str]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None
