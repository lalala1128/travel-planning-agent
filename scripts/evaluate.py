import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from statistics import mean
from time import perf_counter

from app.agents.travel_graph import (
    collect_plan_errors,
    run_travel_graph,
)
from app.models import Trip


def evaluation_cases() -> list[dict]:
    start = date.today() + timedelta(days=2)
    return [
        {
            "destination": "成都",
            "start_date": start,
            "days": 3,
            "budget": 2500,
            "interests": ["历史", "美食"],
        },
        {
            "destination": "杭州",
            "start_date": start,
            "days": 2,
            "budget": 1800,
            "interests": ["自然风景", "摄影"],
        },
        {
            "destination": "上海",
            "start_date": start,
            "days": 3,
            "budget": 3500,
            "interests": ["建筑", "博物馆"],
        },
        {
            "destination": "北京",
            "start_date": start,
            "days": 4,
            "budget": 4500,
            "interests": ["历史", "文化"],
        },
        {
            "destination": "长沙",
            "start_date": start,
            "days": 2,
            "budget": 1500,
            "interests": ["美食", "夜景"],
        },
    ]


def evaluate(limit: int, tenant_id: int) -> dict:
    case_results = []

    for case in evaluation_cases()[:limit]:
        trip = Trip(**case, tenant_id=tenant_id)
        started_at = perf_counter()

        try:
            plan = run_travel_graph(trip)
            elapsed = perf_counter() - started_at
            errors = collect_plan_errors(trip, plan)
            case_results.append(
                {
                    "destination": trip.destination,
                    "success": True,
                    "constraint_valid": not errors,
                    "errors": errors,
                    "latency_seconds": round(elapsed, 2),
                    "estimated_cost": plan.total_estimated_cost,
                    "sources": len(plan.sources),
                }
            )
        except Exception as exc:
            elapsed = perf_counter() - started_at
            case_results.append(
                {
                    "destination": trip.destination,
                    "success": False,
                    "constraint_valid": False,
                    "errors": [str(exc)],
                    "latency_seconds": round(elapsed, 2),
                    "estimated_cost": None,
                    "sources": 0,
                }
            )

    total = len(case_results)
    successes = sum(item["success"] for item in case_results)
    valid = sum(item["constraint_valid"] for item in case_results)

    return {
        "evaluated_at": date.today().isoformat(),
        "total_cases": total,
        "success_rate": round(successes / total, 4) if total else 0,
        "constraint_pass_rate": round(valid / total, 4) if total else 0,
        "average_latency_seconds": round(
            mean(item["latency_seconds"] for item in case_results),
            2,
        ) if total else 0,
        "cases": case_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the travel planning workflow.",
    )
    parser.add_argument(
        "--tenant-id",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        choices=range(1, 6),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/evaluation.json"),
    )
    args = parser.parse_args()

    report = evaluate(args.limit, args.tenant_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
