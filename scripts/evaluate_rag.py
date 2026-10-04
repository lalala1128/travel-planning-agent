import json
from pathlib import Path

from app.services.rag import get_rag_service


TOP_K = 3
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = PROJECT_ROOT / "evaluation" / "rag_cases.json"
REPORT_PATH = PROJECT_ROOT / "reports" / "rag_evaluation.json"


def load_cases() -> list[dict]:
    return json.loads(
        CASES_PATH.read_text(encoding="utf-8")
    )


def evaluate() -> dict:
    service = get_rag_service()
    cases = load_cases()

    case_results = []
    positive_count = 0
    positive_hits = 0
    reciprocal_rank_sum = 0.0
    negative_count = 0
    negative_rejections = 0

    for case in cases:
        results = service.search(
            query=case["query"],
            city=case["city"],
            limit=TOP_K,
        )

        if case["should_match"]:
            positive_count += 1
            expected_terms = case["expected_terms"]

            rank = next(

                    index
                    for index, result in enumerate(
                        results,
                        start=1,
                    )
                    if all(
                    term in result.text
                    for term in expected_terms
                    )
            )

            passed = rank is not None

            if passed:
                positive_hits += 1
                reciprocal_rank_sum += 1 / rank
        else:
            negative_count += 1
            rank = None
            passed = len(results) == 0

            if passed:
                negative_rejections += 1

        case_results.append(
            {
                "query": case["query"],
                "passed": passed,
                "relevant_rank": rank,
                "returned_count": len(results),
                "top_score": (
                    round(results[0].final_score, 4)
                    if results
                    else None
                ),
            }
        )

    return {
        "top_k": TOP_K,
        "total_cases": len(cases),
        "hit_at_k": (
            round(positive_hits / positive_count, 4)
            if positive_count
            else 0
        ),
        "mrr": (
            round(reciprocal_rank_sum / positive_count, 4)
            if positive_count
            else 0
        ),
        "negative_rejection_rate": (
            round(negative_rejections / negative_count, 4)
            if negative_count
            else 0
        ),
        "cases": case_results,
    }

def main() -> None:
    service = get_rag_service()

    try:
        report = evaluate()

        REPORT_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        REPORT_PATH.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        service.client.close()


if __name__ == "__main__":
    main()