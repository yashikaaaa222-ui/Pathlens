"""Plain-language reasoning for a route comparison.

Every sentence is a template filled with numbers from the analysis, so nothing is stated that the data
does not contain. This is also what keeps AI-style explanations grounded: there is no free-form text here.
"""
from __future__ import annotations

from typing import Optional, Sequence

from services.risk_engine import CATEGORIES, Config, RouteAnalysis, Stability


def _pct(x: float) -> int:
    return int(x * 100 + 0.5)


def explain(
    analyses: Sequence[RouteAnalysis],
    *,
    stability: Optional[Stability] = None,
    daylight: Optional[dict] = None,
) -> list[dict]:
    notes: list[dict] = []

    def add(text: str, ids: Optional[list[str]] = None) -> None:
        notes.append({"text": text, "ids": ids or []})

    if not analyses:
        return notes

    if any(a.metrics.simulated for a in analyses):
        add("This view includes simulated demo reports. They do not describe real conditions.")

    if daylight:
        period = daylight["period"]
        if period == "day":
            add("It is daytime at this location, so lighting concerns count for less in the score.")
        elif period == "twilight":
            add("It is around dusk or dawn at this location, so lighting concerns count at reduced weight.")
        else:
            add("It is dark at this location, so lighting concerns count in full.")

    for a in analyses:
        if a.metrics.concern_band == "insufficient":
            add(f"{a.name} has too little evidence ({_pct(a.metrics.coverage)}% of its length) to compare. That does not mean it is free of problems.")

    usable = [a for a in analyses if a.metrics.concern_band != "insufficient"]
    if len(usable) >= 2:
        ordered = sorted(usable, key=lambda a: a.metrics.concern_score)
        a, b = ordered[0], ordered[1]
        gap = b.metrics.concern_score - a.metrics.concern_score
        if gap < 8:
            add(f"{a.name} and {b.name} are close on the evidence available ({a.metrics.concern_score} and {b.metrics.concern_score}). Treat the difference as small.")
        else:
            diffs = sorted(
                ((c, b.metrics.counts.get(c, 0) - a.metrics.counts.get(c, 0)) for c in CATEGORIES),
                key=lambda x: -x[1],
            )
            top = f" The biggest difference is in {CATEGORIES[diffs[0][0]].label.lower()} reports." if diffs[0][1] > 0 else ""
            ids = [e.id for x in (a, b) for e in x.evidence if e.counted]
            add(
                f"{a.name} has fewer counted reports than {b.name} ({a.metrics.counted} against {b.metrics.counted}). "
                f"The longest stretch with a counted issue is {a.metrics.longest_stretch_m} m on {a.name} and {b.metrics.longest_stretch_m} m on {b.name}.{top}",
                ids,
            )
        d_min = round((a.route.duration - b.route.duration) / 60)
        d_km = (a.route.distance - b.route.distance) / 1000
        if abs(d_min) < 1 and abs(d_km) < 0.05:
            add(f"{a.name} and {b.name} are about the same length and time.")
        else:
            add(f"Compared with {b.name}, {a.name} takes {abs(d_min)} min {'longer' if d_min > 0 else 'less'} and is {abs(d_km):.1f} km {'longer' if d_km > 0 else 'shorter'}.")
    elif len(usable) == 1:
        a = usable[0]
        plural = "" if a.metrics.counted == 1 else "s"
        add(f"{a.name} has {a.metrics.counted} counted report{plural}. The other routes have too little evidence to compare against it.")

    for a in usable:
        r = a.metrics.concern_range
        if r and r[1] - r[0] >= 8:
            add(f"{a.name} scores {r[0]}, but {_pct(1 - a.metrics.coverage)}% of it has no recent evidence. If those stretches looked like the rest, the score could be up to {r[1]}.")

    if stability:
        share = _pct(stability.top_share)
        if stability.stable:
            add(f"The ranking holds in {share}% of {stability.trials} trials when the weights are varied by up to {_pct(stability.jitter)}%.")
        else:
            add(f"This comparison is a close call. Varying the weights by up to {_pct(stability.jitter)}% gives the same best route in only {share}% of {stability.trials} trials.")

    helps = [(a.name, a.metrics.informational["help"]) for a in usable if a.metrics.informational["help"]["available"]]
    if helps:
        parts = ", ".join(f"{n} {h['gap_m']} m" for n, h in helps)
        add(f"Longest stretch with no listed help point within {helps[0][1]['radius_m']:.0f} m: {parts}. Help points come from OpenStreetMap and do not enter the score.")
    lamps = [(a.name, a.metrics.informational["lamps"]) for a in usable if a.metrics.informational["lamps"]["available"]]
    if lamps:
        parts = ", ".join(f"{n} {l['per_km']}" for n, l in lamps)
        add(f"Mapped streetlights per km: {parts}. OpenStreetMap coverage varies, so a low number can mean missing map data rather than missing lights. This does not enter the score.")

    if usable:
        add("Evidence covers " + ", ".join(f"{_pct(a.metrics.coverage)}% of {a.name}" for a in usable) + ". Stretches without evidence are unknown, not clear.")
    gappy = [a for a in usable if a.gaps]
    if gappy:
        min_m = Config().gap_min_m
        parts = "; ".join(
            f"{a.name} has {len(a.gaps)} stretch{'es' if len(a.gaps) != 1 else ''} with no recent evidence (longest {a.gaps[0].length_m} m)" for a in gappy
        )
        add(f"Gaps of {min_m:.0f} m or more: {parts}. If you walk one, a report or a 'still there' tap there adds the most.")
    return notes
