from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from azure.ai.evaluation import (
    GroundednessEvaluator,
    RelevanceEvaluator,
    TaskAdherenceEvaluator,
)
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = Path(__file__).with_name("career_copilot_dataset.jsonl")
RESULTS_DIR = PROJECT_ROOT / "test-results"

sys.path.insert(0, str(PROJECT_ROOT))

from main import create_workflow_agent, get_required_environment_variable  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", override=True)

TASK_CONTRACT = """\
Analyze the supplied resume against the supplied job description and produce an
objective, evidence-based career plan. The final response must include a
personalized learning roadmap for the target role, a separate detailed card for
every missing skill and certification, a recommended learning order, a
week-by-week timeline summary, and a motivational note. Each gap card must
include skill, priority, current level, target level, suggested resources,
estimated time, and a quick-win project. High- and medium-priority gaps should
include Microsoft Learn links. Do not invent candidate qualifications or job
requirements. If no job description is supplied, explain that matching cannot
be completed and ask for the missing job description instead of inventing a
target role.
"""


def load_test_cases() -> list[dict[str, str]]:
    """Load and validate JSONL evaluation cases."""
    cases: list[dict[str, str]] = []
    required_fields = {"id", "query", "context"}

    with DATASET_PATH.open(encoding="utf-8") as dataset:
        for line_number, line in enumerate(dataset, start=1):
            if not line.strip():
                continue

            case = json.loads(line)
            missing_fields = required_fields.difference(case)
            if missing_fields:
                missing = ", ".join(sorted(missing_fields))
                raise ValueError(
                    f"{DATASET_PATH.name}:{line_number} is missing: {missing}"
                )
            cases.append(case)

    if not cases:
        raise ValueError(f"{DATASET_PATH} contains no evaluation cases")

    return cases


TEST_CASES = load_test_cases()


def get_judge_endpoint() -> str:
    """Return an explicit or Foundry-derived Azure OpenAI endpoint."""
    explicit_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    if explicit_endpoint:
        return explicit_endpoint.rstrip("/")

    project_endpoint = get_required_environment_variable("FOUNDRY_PROJECT_ENDPOINT")
    project_host = urlparse(project_endpoint).hostname or ""
    foundry_suffix = ".services.ai.azure.com"
    if not project_host.endswith(foundry_suffix):
        raise RuntimeError(
            "Set AZURE_OPENAI_ENDPOINT for evaluation because it cannot be "
            "derived from FOUNDRY_PROJECT_ENDPOINT."
        )

    resource_name = project_host.removesuffix(foundry_suffix)
    return f"https://{resource_name}.openai.azure.com"


def is_reasoning_judge(deployment_name: str) -> bool:
    """Detect reasoning deployments, with an override for custom aliases."""
    override = os.getenv("AZURE_AI_EVALUATION_IS_REASONING_MODEL", "").strip()
    if override:
        normalized_override = override.lower()
        if normalized_override not in {"true", "false"}:
            raise RuntimeError(
                "AZURE_AI_EVALUATION_IS_REASONING_MODEL must be true or false."
            )
        return normalized_override == "true"

    normalized_name = deployment_name.lower()
    return normalized_name.startswith(("gpt-5", "o1", "o3", "o4"))


def extract_final_response(agent_response: Any) -> str:
    """Return the last non-empty message emitted by the workflow agent."""
    for message in reversed(agent_response.messages):
        text = getattr(message, "text", None)
        if text:
            return str(text)

    raise AssertionError("The workflow completed without a text response")


def summarize_metric(metric: str, result: dict[str, Any]) -> dict[str, Any]:
    """Keep stable evaluator fields in the persisted report."""
    threshold = 1 if metric == "task_adherence" else result.get(
        f"{metric}_threshold"
    )
    return {
        "score": result.get(f"{metric}_score", result.get(metric)),
        "result": result.get(f"{metric}_result"),
        "reason": result.get(f"{metric}_reason"),
        "threshold": threshold,
    }


@pytest.fixture(scope="session")
def workflow_agent():
    """Create one stateless WorkflowAgent instance for this evaluation run."""
    return create_workflow_agent()


@pytest.fixture(scope="session")
def evaluators() -> Iterator[dict[str, Any]]:
    """Create Foundry-backed evaluators that reuse Azure CLI authentication."""
    credential = DefaultAzureCredential()
    deployment_name = get_required_environment_variable(
        "AZURE_AI_MODEL_DEPLOYMENT_NAME"
    )
    reasoning_judge = is_reasoning_judge(deployment_name)
    model_config = {
        "azure_endpoint": get_judge_endpoint(),
        "azure_deployment": deployment_name,
        "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
    }

    yield {
        "task_adherence": TaskAdherenceEvaluator(
            model_config,
            credential=credential,
            threshold=1,
            is_reasoning_model=reasoning_judge,
        ),
        "groundedness": GroundednessEvaluator(
            model_config,
            credential=credential,
            threshold=3,
            is_reasoning_model=reasoning_judge,
        ),
        "relevance": RelevanceEvaluator(
            model_config,
            credential=credential,
            threshold=3,
            is_reasoning_model=reasoning_judge,
        ),
    }

    credential.close()


@pytest.fixture(scope="session")
def evaluation_report() -> Iterator[list[dict[str, Any]]]:
    """Persist evaluation outcomes for inspection in VS Code Data Viewer."""
    results: list[dict[str, Any]] = []
    yield results

    RESULTS_DIR.mkdir(exist_ok=True)
    generated_at = datetime.now(UTC)
    report_path = RESULTS_DIR / (
        f"evaluation_results_{generated_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    report_path.write_text(
        json.dumps(
            {
                "generated_at": generated_at.isoformat(),
                "dataset": DATASET_PATH.name,
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.evaluation
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    TEST_CASES,
    ids=[case["id"] for case in TEST_CASES],
)
async def test_personal_career_copilot_quality(
    case: dict[str, str],
    workflow_agent,
    evaluators: dict[str, Any],
    evaluation_report: list[dict[str, Any]],
) -> None:
    """Run one workflow case and require every configured quality gate to pass."""
    case_result: dict[str, Any] = {"id": case["id"]}

    try:
        agent_response = await workflow_agent.run(case["query"])
        response = extract_final_response(agent_response)
        case_result["response"] = response
        task_query = [
            {"role": "system", "content": TASK_CONTRACT},
            {
                "role": "user",
                "content": [{"type": "text", "text": case["query"]}],
            },
        ]
        task_response = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": response}],
            }
        ]

        metric_results = {
            "task_adherence": await asyncio.to_thread(
                evaluators["task_adherence"],
                query=task_query,
                response=task_response,
            ),
            "groundedness": await asyncio.to_thread(
                evaluators["groundedness"],
                query=case["query"],
                response=response,
                context=case["context"],
            ),
            "relevance": await asyncio.to_thread(
                evaluators["relevance"],
                query=case["query"],
                response=response,
            ),
        }

        case_result["metrics"] = {
            metric: summarize_metric(metric, result)
            for metric, result in metric_results.items()
        }
    except Exception as exc:
        case_result["error"] = f"{type(exc).__name__}: {exc}"
        evaluation_report.append(case_result)
        raise

    evaluation_report.append(case_result)
    failures = [
        f"{metric}: {result.get(f'{metric}_reason', 'no reason returned')}"
        for metric, result in metric_results.items()
        if result.get(f"{metric}_result") != "pass"
    ]
    assert not failures, "\n".join(failures)
