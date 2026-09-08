from datetime import datetime, timezone
import statistics
import time
import pytest

from app.compliance.engine import ComplianceEngine
from app.schemas.canonical import (
    ComplianceStatus,
    FactRead,
    OperatorEnum,
    RequirementType,
    TenderRequirementRead,
    VerificationResultRead,
    VerificationSource,
    VerificationStatus,
)


def generate_synthetic_workload(
    rule_count: int = 120,
    bidders_count: int = 10,
    facts_per_field: int = 3,
) -> tuple[list[TenderRequirementRead], list[list[FactRead]], list[list[VerificationResultRead]]]:
    """Generates synthetic procurement compliance evaluation workloads with mixed operators, conflicts, and multi-facts."""
    fixed_ts = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    operators = [
        OperatorEnum.GTE,
        OperatorEnum.EQ,
        OperatorEnum.GT,
        OperatorEnum.LTE,
        OperatorEnum.DATE_BEFORE,
        OperatorEnum.COUNT_GTE,
        OperatorEnum.EXISTS,
        OperatorEnum.NOT_EXISTS,
        OperatorEnum.IN,
    ]

    rules: list[TenderRequirementRead] = []
    for i in range(rule_count):
        op = operators[i % len(operators)]
        field = f"criteria.field_{i}"
        unit = "INR" if op in (OperatorEnum.GT, OperatorEnum.GTE, OperatorEnum.LT, OperatorEnum.LTE) else None

        if op in (OperatorEnum.GT, OperatorEnum.GTE, OperatorEnum.LT, OperatorEnum.LTE):
            exp_val = (i + 1) * 1000000
        elif op == OperatorEnum.EQ:
            exp_val = "ACTIVE"
        elif op == OperatorEnum.DATE_BEFORE:
            exp_val = "2026-12-31"
        elif op == OperatorEnum.COUNT_GTE:
            exp_val = 3
        elif op in (OperatorEnum.EXISTS, OperatorEnum.NOT_EXISTS):
            exp_val = True if op == OperatorEnum.EXISTS else False
        elif op == OperatorEnum.IN:
            exp_val = ["CLASS_A", "CLASS_B", "CLASS_C"]
        else:
            exp_val = 100

        rules.append(
            TenderRequirementRead(
                id=f"REQ-BENCH-{i:04d}",
                tender_id="TENDER-BENCH",
                clause=f"Clause {i+1}.0",
                requirement_type=RequirementType.CUSTOM,
                field=field,
                operator=op,
                expected_value=exp_val,
                unit=unit,
                mandatory=(i % 5 != 0),  # 20% optional rules
                confidence=1.0,
                requires_verification=(i % 3 == 0),
                is_approved=True,
                created_at=fixed_ts,
            )
        )

    all_bidders_facts: list[list[FactRead]] = []
    all_bidders_verifications: list[list[VerificationResultRead]] = []

    for b in range(bidders_count):
        bidder_id = f"BIDDER-BENCH-{b:03d}"
        bidder_facts: list[FactRead] = []
        bidder_verifications: list[VerificationResultRead] = []

        for i, rule in enumerate(rules):
            # Generate 1 to facts_per_field facts
            fact_count = (i % facts_per_field) + 1
            for f in range(fact_count):
                # 10% conflict injection
                is_conflict = (f > 0 and (i + b) % 10 == 0)
                fact_val: any = None

                if rule.operator in (OperatorEnum.GT, OperatorEnum.GTE, OperatorEnum.LT, OperatorEnum.LTE):
                    base_num = (i + 1) * 1200000
                    fact_val = base_num * 2 if is_conflict else base_num
                elif rule.operator == OperatorEnum.EQ:
                    fact_val = "INACTIVE" if is_conflict else "ACTIVE"
                elif rule.operator == OperatorEnum.DATE_BEFORE:
                    fact_val = "2027-01-01" if is_conflict else "2026-05-15"
                elif rule.operator == OperatorEnum.COUNT_GTE:
                    fact_val = ["CERT-1"] if is_conflict else ["CERT-1", "CERT-2", "CERT-3", "CERT-4"]
                elif rule.operator in (OperatorEnum.EXISTS, OperatorEnum.NOT_EXISTS):
                    fact_val = False if is_conflict else True
                elif rule.operator == OperatorEnum.IN:
                    fact_val = "CLASS_Z" if is_conflict else "CLASS_A"

                bidder_facts.append(
                    FactRead(
                        id=f"FACT-B{b}-R{i}-F{f}",
                        document_id=f"DOC-B{b}",
                        bidder_id=bidder_id,
                        field=rule.field,
                        value=fact_val,
                        confidence=0.95,
                        metadata_json={"currency": "INR"} if rule.unit else {},
                        created_at=fixed_ts,
                    )
                )

            # Verification result generation for verified rules
            if rule.requires_verification:
                bidder_verifications.append(
                    VerificationResultRead(
                        id=f"VER-B{b}-R{i}",
                        bidder_id=bidder_id,
                        field=rule.field,
                        claimed_value=None,
                        verified_value="ACTIVE" if rule.operator == OperatorEnum.EQ else None,
                        status=VerificationStatus.VERIFIED if (i + b) % 7 != 0 else VerificationStatus.MISMATCH,
                        source=VerificationSource.GST_AUTHORIZED_API,
                        checked_at=fixed_ts,
                    )
                )

        all_bidders_facts.append(bidder_facts)
        all_bidders_verifications.append(bidder_verifications)

    return rules, all_bidders_facts, all_bidders_verifications


def test_compliance_engine_synthetic_benchmark(capsys):
    """Benchmarks compliance engine across 120 rules x 10 bidders (1,200 rule evaluations) with multi-facts and conflicts."""
    rule_count = 120
    bidders_count = 10
    rules, all_facts, all_verifications = generate_synthetic_workload(
        rule_count=rule_count, bidders_count=bidders_count, facts_per_field=3
    )

    eval_ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    evaluation_durations: list[float] = []

    # Warm-up run
    for req in rules[:10]:
        ComplianceEngine.evaluate(req, all_facts[0], all_verifications[0], evaluation_timestamp=eval_ts)

    # Timed benchmark run across all bidders and rules
    total_evaluations = 0
    t_start = time.perf_counter()

    for b in range(bidders_count):
        facts = all_facts[b]
        verifications = all_verifications[b]
        bidder_id = f"BIDDER-BENCH-{b:03d}"

        for req in rules:
            t0 = time.perf_counter()
            res = ComplianceEngine.evaluate(
                rule=req,
                facts=facts,
                verification_results=verifications,
                context={"bidder_id": bidder_id, "evaluation_timestamp": eval_ts},
                evaluation_timestamp=eval_ts,
            )
            t1 = time.perf_counter()
            evaluation_durations.append((t1 - t0) * 1000)  # ms
            total_evaluations += 1
            assert res.status in (
                ComplianceStatus.PASS,
                ComplianceStatus.FAIL,
                ComplianceStatus.REVIEW_REQUIRED,
                ComplianceStatus.UNKNOWN,
                ComplianceStatus.NOT_APPLICABLE,
            )

    t_end = time.perf_counter()
    total_time_ms = (t_end - t_start) * 1000

    median_ms = statistics.median(evaluation_durations)
    mean_ms = statistics.mean(evaluation_durations)
    sorted_durations = sorted(evaluation_durations)
    p95_index = int(len(sorted_durations) * 0.95)
    p95_ms = sorted_durations[p95_index]
    throughput_ops_per_sec = total_evaluations / (t_end - t_start)

    output_report = (
        f"\n{'='*60}\n"
        f"COMPLIANCE ENGINE BENCHMARK REPORT\n"
        f"{'='*60}\n"
        f"Rules Count:                {rule_count}\n"
        f"Bidders Count:              {bidders_count}\n"
        f"Total Rule Evaluations:     {total_evaluations}\n"
        f"Total Benchmark Time:       {total_time_ms:.2f} ms\n"
        f"Throughput:                 {throughput_ops_per_sec:.2f} evals/sec\n"
        f"Mean Latency per Rule:      {mean_ms:.4f} ms\n"
        f"Median Latency per Rule:    {median_ms:.4f} ms\n"
        f"p95 Latency per Rule:       {p95_ms:.4f} ms\n"
        f"{'='*60}\n"
    )
    print(output_report)

    # In-memory evaluation is expected to be well below sub-millisecond target per rule
    assert total_evaluations == rule_count * bidders_count
    assert median_ms < 5.0  # Safe upper threshold for CI across diverse hardware

