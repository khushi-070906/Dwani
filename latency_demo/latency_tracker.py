import time
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["segment", "streaming"]


@dataclass
class TranslationTiming:
    segment_id: str
    target_language: str
    mode: Mode
    duration_ms: float
    timestamp: float = field(default_factory=time.time)


class LatencyTracker:
    """Records per-component latency (voice-activity, ASR, translation, broadcast)
    and exposes running aggregates for the reporting endpoint."""

    def __init__(self):
        self._translation_records: list[TranslationTiming] = []
        self._other_records: dict[str, list[float]] = defaultdict(list)

    async def timed_translate(
        self,
        translation_backend,
        text: str,
        target_language: str,
        segment_id: str,
        mode: Mode,
    ) -> str:
        start = time.perf_counter()
        result = await translation_backend.translate(text, target_language)
        duration_ms = (time.perf_counter() - start) * 1000

        self._translation_records.append(
            TranslationTiming(
                segment_id=segment_id,
                target_language=target_language,
                mode=mode,
                duration_ms=duration_ms,
            )
        )
        return result

    def record_component(self, component: str, duration_ms: float):
        self._other_records[component].append(duration_ms)

    def _summarize(self, values: list[float]) -> dict:
        if not values:
            return {"count": 0}
        sorted_vals = sorted(values)
        p95_idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * 0.95))
        return {
            "count": len(values),
            "mean_ms": round(statistics.mean(values), 2),
            "median_ms": round(statistics.median(values), 2),
            "p95_ms": round(sorted_vals[p95_idx], 2),
            "max_ms": round(max(values), 2),
        }

    def translation_stats(self, mode: Mode | None = None) -> dict:
        records = self._translation_records
        if mode is not None:
            records = [r for r in records if r.mode == mode]
        return self._summarize([r.duration_ms for r in records])

    def translation_stats_by_language(self) -> dict[str, dict]:
        by_lang: dict[str, list[float]] = defaultdict(list)
        for r in self._translation_records:
            by_lang[r.target_language].append(r.duration_ms)
        return {lang: self._summarize(vals) for lang, vals in by_lang.items()}

    def segment_total_translation_time(self, segment_id: str) -> float:
        return sum(
            r.duration_ms
            for r in self._translation_records
            if r.segment_id == segment_id
        )

    def full_report(self) -> dict:
        return {
            "translation": {
                "overall": self.translation_stats(),
                "segment_mode": self.translation_stats(mode="segment"),
                "streaming_mode": self.translation_stats(mode="streaming"),
                "by_language": self.translation_stats_by_language(),
            },
            "other_components": {
                name: self._summarize(vals)
                for name, vals in self._other_records.items()
            },
        }
