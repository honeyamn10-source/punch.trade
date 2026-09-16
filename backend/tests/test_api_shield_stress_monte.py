"""Risk Shield + Stress Lab + Monte Carlo endpoint tests."""

from fastapi.testclient import TestClient

from app import api

TOKEN = {"X-Punch-Token": "punch-demo-token"}


class TestRiskShield:
    def test_shield_blocks_orders(self):
        with TestClient(api.app) as client:
            # lift shield first (state is process-global; be deterministic)
            client.post("/api/risk/shield", json={"on": False}, headers=TOKEN)
            r = client.get("/api/risk/shield", headers=TOKEN)
            assert r.status_code == 200
            assert "shieldOn" in r.json()

            # engage
            r = client.post("/api/risk/shield", json={"on": True}, headers=TOKEN)
            assert r.status_code == 200
            assert r.json()["shieldOn"] is True

            # an order must now be rejected with SHIELD_ACTIVE
            r = client.post(
                "/api/orders",
                json={
                    "broker": "paper",
                    "symbol": "RELIANCE",
                    "side": "buy",
                    "qty": 1,
                    "entry": 100.0,
                    "targetPrice": 101.0,
                    "stopLoss": 99.0,
                    "clientRequestId": "shield-test",
                },
                headers=TOKEN,
            )
            assert r.status_code == 409
            body = r.json()
            assert body["error"]["code"] == "SHIELD_ACTIVE"

            # lift
            r = client.post("/api/risk/shield", json={"on": False}, headers=TOKEN)
            assert r.json()["shieldOn"] is False


class TestStressLab:
    def test_scenarios_list(self):
        with TestClient(api.app) as client:
            r = client.get("/api/stress/scenarios", headers=TOKEN)
            assert r.status_code == 200
            scenarios = r.json()["scenarios"]
            assert len(scenarios) >= 10
            types = {s["type"] for s in scenarios}
            assert "market_crash" in types

    def test_stress_run(self):
        with TestClient(api.app) as client:
            r = client.post(
                "/api/stress/run",
                json={
                    "metrics": {
                        "net_return": 0.10,
                        "max_drawdown_pct": 15.0,
                        "sharpe": 1.2,
                        "cost_bps": 12.0,
                        "volatility": 30.0,
                    }
                },
                headers=TOKEN,
            )
            assert r.status_code == 200
            body = r.json()
            assert body["total_scenarios"] > 0
            assert body["passed"] + body["failed"] == body["total_scenarios"]
            assert "worst_max_drawdown_pct" in body

    def test_stress_requires_metrics(self):
        with TestClient(api.app) as client:
            r = client.post("/api/stress/run", json={"metrics": {}}, headers=TOKEN)
            assert r.status_code == 400


class TestMonteCarlo:
    def test_monte_carlo_endpoint(self):
        with TestClient(api.app) as client:
            r = client.post(
                "/api/v1/analysis/monte-carlo",
                json={
                    "returns": [
                        0.02,
                        -0.01,
                        0.015,
                        0.0,
                        0.03,
                        -0.02,
                        0.01,
                        0.005,
                        -0.005,
                        0.025,
                        0.012,
                        -0.008,
                    ]
                },
                headers=TOKEN,
            )
            assert r.status_code == 200
            body = r.json()
            assert body["analysis"]["ending_equity"]["mean"] > 0
            assert "prob_positive" in body["expectancy"]

    def test_monte_carlo_requires_returns(self):
        with TestClient(api.app) as client:
            r = client.post(
                "/api/v1/analysis/monte-carlo", json={"returns": [0.01, 0.02]}, headers=TOKEN
            )
            assert r.status_code == 400
