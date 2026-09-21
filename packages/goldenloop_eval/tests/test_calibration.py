import pytest

from goldenloop_eval import CalibrationLabel, calibration_metrics


def test_calibration_metrics_keep_human_labels_independent():
    result = calibration_metrics(
        [
            CalibrationLabel(human="pass", judge="pass"),
            CalibrationLabel(human="fail", judge="fail"),
            CalibrationLabel(human="fail", judge="pass"),
            CalibrationLabel(human="pass", judge="fail"),
        ]
    )
    assert result.model_dump() == {
        "samples": 4,
        "agreements": 2,
        "true_positive": 1,
        "true_negative": 1,
        "false_positive": 1,
        "false_negative": 1,
        "agreement": 0.5,
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
    }


def test_calibration_metrics_report_undefined_rates_without_denominators():
    assert calibration_metrics([]).agreement is None
    result = calibration_metrics([CalibrationLabel(human="fail", judge="fail")])
    assert result.precision is result.recall is result.f1 is None


def test_calibration_labels_reject_ambiguous_outcomes():
    with pytest.raises(ValueError):
        CalibrationLabel(human="unknown", judge="pass")


@pytest.mark.parametrize("pairs", [
    [("pass", "fail"), ("fail", "pass")],
    [("pass", "fail")],
    [("fail", "pass")],
])
def test_f1_is_zero_when_predictions_have_errors_but_no_true_positives(pairs):
    result = calibration_metrics(CalibrationLabel(human=human, judge=judge) for human, judge in pairs)
    assert result.f1 == 0.0
