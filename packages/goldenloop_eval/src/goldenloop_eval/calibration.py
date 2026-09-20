from collections.abc import Iterable
from typing import Literal

from pydantic import ConfigDict

from .models import Model


class CalibrationLabel(Model):
    model_config = ConfigDict(extra="forbid", strict=True)

    human: Literal["pass", "fail"]
    judge: Literal["pass", "fail"]


class CalibrationMetrics(Model):
    samples: int
    agreements: int
    true_positive: int
    true_negative: int
    false_positive: int
    false_negative: int
    agreement: float | None
    precision: float | None
    recall: float | None
    f1: float | None


def calibration_metrics(labels: Iterable[CalibrationLabel]) -> CalibrationMetrics:
    values = list(labels)
    true_positive = sum(label.human == label.judge == "pass" for label in values)
    true_negative = sum(label.human == label.judge == "fail" for label in values)
    false_positive = sum(label.human == "fail" and label.judge == "pass" for label in values)
    false_negative = sum(label.human == "pass" and label.judge == "fail" for label in values)
    samples = len(values)
    predicted_positive = true_positive + false_positive
    actual_positive = true_positive + false_negative
    precision = true_positive / predicted_positive if predicted_positive else None
    recall = true_positive / actual_positive if actual_positive else None
    f1_denominator = 2 * true_positive + false_positive + false_negative
    f1 = 2 * true_positive / f1_denominator if f1_denominator else None
    return CalibrationMetrics(
        samples=samples,
        agreements=true_positive + true_negative,
        true_positive=true_positive,
        true_negative=true_negative,
        false_positive=false_positive,
        false_negative=false_negative,
        agreement=(true_positive + true_negative) / samples if samples else None,
        precision=precision,
        recall=recall,
        f1=f1,
    )
