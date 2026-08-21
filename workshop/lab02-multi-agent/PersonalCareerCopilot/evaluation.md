---
title: Personal Career Copilot Evaluation
description: Run local quality evaluations for the Personal Career Copilot workflow
ms.date: 2026-08-20
ms.topic: how-to
---

## Evaluation scope

The evaluation suite invokes the live four-agent workflow and measures three
quality gates:

* Task adherence uses a pass threshold of 1 and checks completion of the required career-plan contract.
* Groundedness uses a pass threshold of 3 out of 5 and detects claims that the resume or job description does not support.
* Relevance uses a pass threshold of 3 out of 5 and checks whether the roadmap addresses the supplied input.

The starter dataset contains six resume and job-description scenarios. It
covers strong and partial matches, a career transition, certification gaps, a
missing job description, and a sparse resume.

> [!IMPORTANT]
> Evaluations call the deployed Foundry model and Microsoft Learn MCP. They
> require network access, Azure authentication, and may incur model usage cost.

## Setup

The existing `PersonalCareerCopilot/.env` must provide these variables:

```env
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
AZURE_AI_MODEL_DEPLOYMENT_NAME=<supported-chat-model-deployment>
```

Authenticate with Azure CLI, activate the workspace `.venv`, and install the
local evaluation dependencies:

```bash
az login
source ../.venv/bin/activate
python -m pip install -r requirements-evaluation.txt
```

The judge model reuses the workflow model deployment and
`DefaultAzureCredential`. The suite derives the Azure OpenAI endpoint from the
Foundry project endpoint. No API key is required.

For a nonstandard project endpoint, set these optional overrides:

```env
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_AI_EVALUATION_IS_REASONING_MODEL=true
```

Reasoning mode is selected automatically for GPT-5 and o-series deployment
names. Set the optional override only when a custom deployment alias prevents
automatic detection.

## Run evaluations

Use VS Code Test Explorer for the integrated workflow:

1. Open the **Testing** panel.
2. Refresh tests if discovery has not run.
3. Expand `evaluations/test_personal_career_copilot.py`.
4. Run one scenario or the full suite.

Run the first case from a terminal as a smoke test:

```bash
python -m pytest evaluations/test_personal_career_copilot.py -k strong_azure_fit -v
```

Run all six cases:

```bash
python -m pytest -v
```

## View results

Each run writes a timestamped report to `test-results/`. The report includes the
agent response plus score, pass/fail result, threshold, and judge reason for
each metric.

To inspect a report, right-click its JSON file in VS Code and select
**Open in Data Viewer**.

## Update the dataset

Edit `evaluations/career_copilot_dataset.jsonl`. Each line is one JSON object
with these fields:

* `id`: Stable test identifier
* `query`: Complete resume and job-description input sent to the workflow
* `context`: Source facts supplied to the groundedness evaluator

Keep candidate qualifications and job requirements explicit in `context`.
Planning recommendations such as learning links, time estimates, and project
ideas can be identified as allowed generated guidance.

## Interpret failures

A failed quality gate is useful evidence, not necessarily a test defect. Review
the saved response and judge reason before changing prompts or thresholds.

* Task-adherence failures usually indicate missing roadmap sections or gap cards
* Groundedness failures usually indicate invented candidate or role facts
* Relevance failures usually indicate generic advice or an unhandled edge case

The `missing_job_description` case intentionally checks that the workflow asks
for the absent job description instead of inventing target-role requirements.
