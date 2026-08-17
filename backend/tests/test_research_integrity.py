"""Tests for trial_ledger, DSR, PBO, final_test_lock."""

import pytest

from app import trial_ledger
from app.research import deflated_sharpe, pbo, final_test_lock


def _mock_trial(sharpe: float, train_sharpe: float = 0, test_sharpe: float = 0) -> dict:
    return {
        "sharpe": sharpe,
        "train_sharpe": train_sharpe or sharpe,
        "test_sharpe": test_sharpe or sharpe,
    }


class TestTrialLedger:
    def test_append_and_retrieve(self):
        init_trial_ledger()
        bars = [{"ts": float(i * 60), "close": 100 + i} for i in range(200)]
        splits = (bars[:140], bars[140:170], bars[170:])
        param_snap = {"entry": {"period": 14}, "sl_pct": 1.0}
        metrics = {"splits": {"train": {"sharpe": 1.2}, "val": {"sharpe": 1.0}, "test": {"sharpe": 0.9}}}
        gate = {"passed": True, "score": 85, "checks": []}

        rec = trial_ledger.append_trial(
            strategy_id="rsi-reversal",
            strategy_version="1.0.0",
            parameter_snapshot=param_snap,
            bars=bars,
            splits=splits,
            research_metrics=metrics,
            quality_gate=gate,
            notes="initial trial",
        )

        assert rec.trial_id
        assert rec.strategy_id == "rsi-reversal"
        assert rec.quality_gate["passed"] is True

        fetched = trial_ledger.get_trial(rec.trial_id)
        assert fetched is not None
        assert fetched.trial_id == rec.trial_id
        assert fetched.parameter_snapshot == param_snap
        assert fetched.research_metrics == metrics

    def test_list_trials(self):
        init_trial_ledger()
        bars = [{"ts": float(i * 60), "close": 100 + i} for i in range(200)]
        splits = (bars[:140], bars[140:170], bars[170:])
        for i in range(3):
            trial_ledger.append_trial(
                strategy_id="ema-breakout",
                strategy_version="1.0.0",
                parameter_snapshot={"tp_pct": float(i + 1)},
                bars=bars,
                splits=splits,
                research_metrics={"test": i},
                quality_gate={"passed": True},
            )
        all_trials = trial_ledger.list_trials(limit=10)
        assert len(all_trials) >= 3
        ema_trials = trial_ledger.list_trials(strategy_id="ema-breakout", limit=10)
        assert len(ema_trials) == 3

    def test_fingerprint_dedupe(self):
        init_trial_ledger()
        bars = [{"ts": float(i * 60), "close": 100 + i} for i in range(200)]
        splits = (bars[:140], bars[140:170], bars[170:])
        param_snap = {"entry": {"period": 14}}
        trial_ledger.append_trial(
            strategy_id="rsi-reversal",
            strategy_version="1.0.0",
            parameter_snapshot=param_snap,
            bars=bars,
            splits=splits,
            research_metrics={},
            quality_gate={"passed": True},
        )
        # Different strategy, same data
        trial_ledger.append_trial(
            strategy_id="ema-breakout",
            strategy_version="1.0.0",
            parameter_snapshot={"tp_pct": 2.0},
            bars=bars,
            splits=splits,
            research_metrics={},
            quality_gate={"passed": True},
        )
        dupes = trial_ledger.trials_for_fingerprint(bars[0]["ts"])  # wrong - need fingerprint
        # Use actual fingerprint
        fp = trial_ledger._fingerprint_bars(bars)
        dupes = trial_ledger.trials_for_fingerprint(fp)
        assert len(dupes) == 2


class TestDSR:
    def test_insufficient_trials(self):
        trials = [_mock_trial(1.5), _mock_trial(1.2)]
        result = deflated_sharpe(trials, min_trials=5)
        assert result["dsr_prob"] == 0.0
        assert "error" in result

    def test_basic_dsr(self):
        trials = [_mock_trial(s) for s in [0.8, 1.0, 1.2, 1.5, 2.0, 1.8, 1.1]]
        result = deflated_sharpe(trials, benchmark_sharpe=0.0, min_trials=5)
        assert result["n_trials"] == 7
        assert result["max_observed_sharpe"] == 2.0
        assert 0 <= result["dsr_prob"] <= 1
        assert result["expected_max_sharpe"] > 0


class TestPBO:
    def test_insufficient_trials(self):
        trials = [_mock_trial(1.0, 1.0, 1.0), _mock_trial(1.2, 1.2, 0.8)]
        result = pbo(trials, min_trials=10)
        assert result["pbo"] == 1.0
        assert "error" in result

    def test_basic_pbo(self):
        # Create trials where train Sharpe correlates with test Sharpe (low PBO)
        trials = [
            _mock_trial(1.5, 1.5, 1.4),
            _mock_trial(1.4, 1.4, 1.3),
            _mock_trial(1.3, 1.3, 1.2),
            _mock_trial(1.2, 1.2, 1.1),
            _mock_trial(1.1, 1.1, 1.0),
            _mock_trial(1.0, 1.0, 0.9),
            _mock_trial(0.9, 0.9, 0.8),
            _mock_trial(0.8, 0.8, 0.7),
            _mock_trial(0.7, 0.7, 0.6),
            _mock_trial(0.6, 0.6, 0.5),
        ]
        result = pbo(trials, min_trials=10)
        assert result["n_trials"] == 10
        assert 0 <= result["pbo"] <= 1
        assert result["n_combinations"] > 0


class TestFinalTestLock:
    def test_passes_when_gate_passed_and_test_sharpe_good(self):
        trial = {
            "qualityGate": {
                "passed": True,
                "checks": [
                    {"name": "train edge positive", "passed": True, "detail": "ok"},
                    {"name": "val edge positive", "passed": True, "detail": "ok"},
                    {"name": "walk-forward consistency", "passed": True, "detail": "ok"},
                ],
            }
        }
        result = final_test_lock(trial, test_sharpe=1.0, min_test_sharpe=0.5)
        assert result["locked"] is True

    def test_fails_when_test_sharpe_low(self):
        trial = {"qualityGate": {"passed": True, "checks": []}}
        result = final_test_lock(trial, test_sharpe=0.2, min_test_sharpe=0.5)
        assert result["locked"] is False
        assert "min" in result["reason"].lower()

    def test_fails_when_gate_failed(self):
        trial = {"qualityGate": {"passed": False, "checks": [{"name": "val edge positive", "passed": False}]}}
        result = final_test_lock(trial, test_sharpe=2.0, min_test_sharpe=0.5)
        assert result["locked"] is False

    def test_fails_when_test_used_in_gate(self):
        trial = {
            "qualityGate": {
                "passed": True,
                "checks": [
                    {"name": "test edge positive", "passed": True, "detail": "peeking!"}
                ],
            }
        }
        result = final_test_lock(trial, test_sharpe=1.5, min_test_sharpe=0.5)
        assert result["locked"] is False
        assert "peeking" in result["reason"].lower()


def init_trial_ledger():
    trial_ledger.init_trial_ledger()