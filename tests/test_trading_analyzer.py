"""Tests unitarios para TradingAnalyzer (sin red)."""
from datetime import datetime, timedelta

import pytest

from app.models.schemas import Transaction, TransactionType
from app.services.trading_analyzer import TradingAnalyzer

BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
SOL = "So11111111111111111111111111111111111111112"


def make_tx(
    tx_type,
    token,
    token_amount,
    sol_amount,
    price,
    minutes_ago=60,
    signature="sig",
    fee=0.0,
):
    return Transaction(
        signature=signature,
        timestamp=datetime.now() - timedelta(minutes=minutes_ago),
        type=tx_type,
        token_address=token,
        token_symbol="BONK" if token == BONK else "TOKEN",
        token_amount=token_amount,
        sol_amount=sol_amount,
        price_per_token=price,
        fee=fee,
    )


class TestMetrics:
    def test_empty_analysis(self):
        analyzer = TradingAnalyzer([])
        metrics = analyzer.calculate_metrics()
        assert metrics.total_trades == 0
        assert metrics.win_rate == 0
        assert metrics.total_pnl == 0
        assert analyzer.analyze_all_tokens() == []
        assert analyzer.analyze_patterns() == []
        assert isinstance(analyzer.generate_recommendations(), list)

    def test_winning_and_losing_trades(self):
        transactions = [
            # Compra 1000 BONK por 10 SOL
            make_tx(TransactionType.BUY, BONK, 1000, 10, 0.01, minutes_ago=120, signature="buy1"),
            # Venta 1000 BONK por 15 SOL -> +5 SOL
            make_tx(TransactionType.SELL, BONK, 1000, 15, 0.015, minutes_ago=60, signature="sell1"),
            # Compra 1000 TOKEN2 por 10 SOL
            make_tx(TransactionType.BUY, "TOKEN2", 1000, 10, 0.01, minutes_ago=50, signature="buy2"),
            # Venta 500 TOKEN2 por 3 SOL -> -2 SOL (aprox)
            make_tx(TransactionType.SELL, "TOKEN2", 500, 3, 0.006, minutes_ago=10, signature="sell2"),
        ]

        analyzer = TradingAnalyzer(transactions)
        metrics = analyzer.calculate_metrics()

        assert metrics.total_trades == 2
        assert metrics.winning_trades == 1
        assert metrics.losing_trades == 1
        assert metrics.win_rate == 50.0
        assert metrics.total_pnl == pytest.approx(3.0, abs=0.01)
        assert metrics.profit_factor == pytest.approx(5.0 / 2.0, abs=0.1)
        assert metrics.avg_hold_time_seconds > 0

    def test_avg_hold_time(self):
        transactions = [
            make_tx(TransactionType.BUY, BONK, 1000, 10, 0.01, minutes_ago=120, signature="buy1"),
            make_tx(TransactionType.SELL, BONK, 1000, 12, 0.012, minutes_ago=60, signature="sell1"),
        ]
        analyzer = TradingAnalyzer(transactions)
        metrics = analyzer.calculate_metrics()
        # 60 minutos de hold = 3600 segundos
        assert metrics.avg_hold_time_seconds == pytest.approx(3600, abs=5)


class TestTokenAnalysis:
    def test_token_stats(self):
        transactions = [
            make_tx(TransactionType.BUY, BONK, 1000, 10, 0.01, minutes_ago=120, signature="buy1"),
            make_tx(TransactionType.SELL, BONK, 400, 6, 0.015, minutes_ago=60, signature="sell1"),
        ]

        analyzer = TradingAnalyzer(transactions)
        tokens = analyzer.analyze_all_tokens()

        assert len(tokens) == 1
        token = tokens[0]
        assert token.token_address == BONK
        assert token.total_bought == 1000
        assert token.total_sold == 400
        assert token.current_holdings == pytest.approx(600)
        assert token.is_still_holding is True
        assert token.trades_count == 2

    def test_roi_percent(self):
        transactions = [
            make_tx(TransactionType.BUY, BONK, 1000, 10, 0.01, minutes_ago=120, signature="buy1"),
            make_tx(TransactionType.SELL, BONK, 1000, 15, 0.015, minutes_ago=60, signature="sell1"),
        ]
        analyzer = TradingAnalyzer(transactions)
        token = analyzer.analyze_all_tokens()[0]
        # Ganancia de 5 SOL sobre 10 invertidos = 50%
        assert token.roi_percent == pytest.approx(50.0, abs=0.5)


class TestPersonalizedRecommendations:
    def test_risk_reward_ratio_detected(self):
        """Ganancias pequeñas y pérdidas grandes -> recomendación de riesgo/recompensa."""
        transactions = []
        for i, (t, price) in enumerate([
            (10, 0.0105),   # compra 1000 por 10 SOL
            (9, 0.009),     # vende perdiendo ~1 SOL
            (10, 0.0105),   # compra 1000 por 10 SOL
            (9.5, 0.0095),  # vende perdiendo ~0.5 SOL
            (10, 0.0105),   # compra
            (11, 0.011),    # vende ganando ~0.5 SOL
        ]):
            is_sell = i % 2 == 1
            transactions.append(make_tx(
                TransactionType.SELL if is_sell else TransactionType.BUY,
                f"T{i // 2}", 1000, t, price,
                minutes_ago=120 - i * 20,
                signature=f"sig{i}",
            ))

        analyzer = TradingAnalyzer(transactions)
        recs = analyzer.generate_recommendations()
        types = [r.type for r in recs]
        assert "risk_reward" in types

    def test_hold_time_bias_detected(self):
        """Aguantar perdedores mucho más que ganadores -> recomendación de sesgo."""
        transactions = [
            # Ganador: vendido rápido (10 min)
            make_tx(TransactionType.BUY, "WIN", 1000, 10, 0.01, minutes_ago=130, signature="b1"),
            make_tx(TransactionType.SELL, "WIN", 1000, 12, 0.012, minutes_ago=120, signature="s1"),
            # Perdedor: aguantado 20 horas
            make_tx(TransactionType.BUY, "LOSS", 1000, 10, 0.01, minutes_ago=1200, signature="b2"),
            make_tx(TransactionType.SELL, "LOSS", 1000, 6, 0.006, minutes_ago=0, signature="s2"),
        ]
        analyzer = TradingAnalyzer(transactions)
        recs = analyzer.generate_recommendations()
        types = [r.type for r in recs]
        assert "hold_time_bias" in types

    def test_high_win_rate_negative_pnl(self):
        """Win rate alto con P&L negativo -> recomendación de asimetría."""
        transactions = []
        # 4 trades: 3 ganadores pequeños, 1 perdedor grande
        for i in range(3):
            transactions.append(make_tx(TransactionType.BUY, f"T{i}", 1000, 10, 0.01, minutes_ago=200 - i * 30, signature=f"b{i}"))
            transactions.append(make_tx(TransactionType.SELL, f"T{i}", 1000, 10.5, 0.0105, minutes_ago=190 - i * 30, signature=f"s{i}"))
        transactions.append(make_tx(TransactionType.BUY, "BIG", 1000, 10, 0.01, minutes_ago=60, signature="bb"))
        transactions.append(make_tx(TransactionType.SELL, "BIG", 1000, 4, 0.004, minutes_ago=30, signature="sb"))

        analyzer = TradingAnalyzer(transactions)
        recs = analyzer.generate_recommendations()
        types = [r.type for r in recs]
        assert "asymmetry" in types

    def test_revenge_trading_detected(self):
        """Compra grande justo después de una venta -> revenge trading."""
        transactions = [
            make_tx(TransactionType.BUY, "A", 1000, 5, 0.005, minutes_ago=40, signature="b0"),
            make_tx(TransactionType.BUY, "A", 1000, 5, 0.005, minutes_ago=30, signature="b1"),
            make_tx(TransactionType.SELL, "A", 1000, 4, 0.004, minutes_ago=15, signature="s1"),
            # 5 minutos después, reentra con el doble
            make_tx(TransactionType.BUY, "B", 1000, 12, 0.012, minutes_ago=10, signature="b2"),
        ]
        analyzer = TradingAnalyzer(transactions)
        recs = analyzer.generate_recommendations()
        types = [r.type for r in recs]
        assert "revenge_trading" in types

    def test_buy_only_wallet_no_fake_losses(self):
        """Wallet que solo compra: no debe marcar pérdida total ni ROI -100%."""
        transactions = [
            make_tx(TransactionType.BUY, "A", 1000, 10, 0.01, minutes_ago=60, signature="b1"),
            make_tx(TransactionType.BUY, "A", 500, 5, 0.01, minutes_ago=30, signature="b2"),
            make_tx(TransactionType.BUY, "B", 2000, 8, 0.004, minutes_ago=20, signature="b3"),
            make_tx(TransactionType.BUY, "C", 3000, 3, 0.001, minutes_ago=10, signature="b4"),
            make_tx(TransactionType.BUY, "D", 4000, 4, 0.001, minutes_ago=5, signature="b5"),
        ]
        analyzer = TradingAnalyzer(transactions)
        metrics = analyzer.calculate_metrics()

        # Sin ventas: no hay P&L realizado, pero la actividad sí se refleja
        assert metrics.total_pnl == 0
        assert metrics.total_trades == 5  # transacciones reales
        assert metrics.winning_trades == 0
        assert metrics.total_volume == pytest.approx(30.0, abs=0.01)
        assert metrics.avg_trade_size == pytest.approx(6.0, abs=0.01)

        # Ningún token debe mostrar pérdida fabricada por no haber vendido
        for token in analyzer.analyze_all_tokens():
            assert token.realized_pnl == 0
            assert token.roi_percent == 0
            assert token.is_still_holding is True

        # La recomendación de falta de salida debe aparecer
        types = [r.type for r in analyzer.generate_recommendations()]
        assert "no_exit_strategy" in types

    def test_recommendations_sorted_by_priority(self):
        transactions = [
            make_tx(TransactionType.BUY, "A", 1000, 10, 0.01, minutes_ago=200, signature="b1"),
            make_tx(TransactionType.SELL, "A", 1000, 5, 0.005, minutes_ago=100, signature="s1"),
        ]
        analyzer = TradingAnalyzer(transactions)
        recs = analyzer.generate_recommendations()
        if len(recs) > 1:
            order = {"high": 0, "medium": 1, "low": 2}
            priorities = [order[r.priority] for r in recs]
            assert priorities == sorted(priorities)


class TestWalletProfile:
    def test_accumulator_profile(self):
        """Wallet que solo compra -> perfil Acumulador."""
        transactions = [
            make_tx(TransactionType.BUY, "A", 1000, 10, 0.01, minutes_ago=60, signature="b1"),
            make_tx(TransactionType.BUY, "A", 500, 5, 0.01, minutes_ago=30, signature="b2"),
            make_tx(TransactionType.BUY, "B", 2000, 8, 0.004, minutes_ago=20, signature="b3"),
        ]
        analyzer = TradingAnalyzer(transactions)
        profile = analyzer.detect_profile()
        assert profile.style == "accumulator"
        assert profile.label == "Acumulador"
        assert profile.emoji
        assert profile.description
        assert profile.weaknesses  # sin plan de salida

    def test_balanced_profile(self):
        """Mezcla de compras/ventas con tiempos variados -> Equilibrado."""
        transactions = [
            make_tx(TransactionType.BUY, "A", 1000, 10, 0.01, minutes_ago=500, signature="b1"),
            make_tx(TransactionType.SELL, "A", 1000, 12, 0.012, minutes_ago=400, signature="s1"),
            make_tx(TransactionType.BUY, "B", 1000, 10, 0.01, minutes_ago=300, signature="b2"),
            make_tx(TransactionType.SELL, "B", 1000, 9, 0.009, minutes_ago=200, signature="s2"),
            make_tx(TransactionType.BUY, "C", 1000, 10, 0.01, minutes_ago=100, signature="b3"),
            make_tx(TransactionType.SELL, "C", 1000, 11, 0.011, minutes_ago=50, signature="s3"),
        ]
        analyzer = TradingAnalyzer(transactions)
        profile = analyzer.detect_profile()
        assert profile.style == "balanced"

    def test_profile_signal_added_to_recommendations(self):
        """La recomendación de perfil se incluye en el generador."""
        transactions = [
            make_tx(TransactionType.BUY, "A", 1000, 10, 0.01, minutes_ago=60, signature="b1"),
            make_tx(TransactionType.BUY, "B", 2000, 8, 0.004, minutes_ago=30, signature="b2"),
        ]
        analyzer = TradingAnalyzer(transactions)
        recs = analyzer.generate_recommendations()
        types = [r.type for r in recs]
        assert any(t.startswith("profile_") for t in types)


class TestRecommendations:
    def test_recommendations_generated(self):
        # Pérdidas grandes en varios tokens -> recomendación de stop loss
        transactions = [
            make_tx(TransactionType.BUY, BONK, 1000, 10, 0.01, minutes_ago=240, signature="buy1"),
            make_tx(TransactionType.SELL, BONK, 1000, 5, 0.005, minutes_ago=230, signature="sell1"),
            make_tx(TransactionType.BUY, "T2", 1000, 10, 0.01, minutes_ago=200, signature="buy2"),
            make_tx(TransactionType.SELL, "T2", 1000, 5, 0.005, minutes_ago=190, signature="sell2"),
            make_tx(TransactionType.BUY, "T3", 1000, 10, 0.01, minutes_ago=160, signature="buy3"),
            make_tx(TransactionType.SELL, "T3", 1000, 5, 0.005, minutes_ago=150, signature="sell3"),
        ]
        analyzer = TradingAnalyzer(transactions)
        recs = analyzer.generate_recommendations()
        assert isinstance(recs, list)
        assert len(recs) > 0
        # Todas las recomendaciones tienen título y descripción
        for rec in recs:
            assert rec.title
            assert rec.description


class TestTimeOfDay:
    def test_best_hour_detected(self):
        """Trades cerrados en horas distintas -> detecta la mejor franja por P&L."""
        transactions = [
            # Trade ganador a las 14:00
            Transaction(
                signature="b1",
                timestamp=datetime(2026, 1, 1, 13, 0),
                type=TransactionType.BUY,
                token_address="A",
                token_symbol="A",
                token_amount=1000,
                sol_amount=10,
                price_per_token=0.01,
            ),
            Transaction(
                signature="s1",
                timestamp=datetime(2026, 1, 1, 14, 0),
                type=TransactionType.SELL,
                token_address="A",
                token_symbol="A",
                token_amount=1000,
                sol_amount=15,
                price_per_token=0.015,
            ),
            # Trade perdedor a las 6:00
            Transaction(
                signature="b2",
                timestamp=datetime(2026, 1, 1, 5, 0),
                type=TransactionType.BUY,
                token_address="B",
                token_symbol="B",
                token_amount=1000,
                sol_amount=10,
                price_per_token=0.01,
            ),
            Transaction(
                signature="s2",
                timestamp=datetime(2026, 1, 1, 6, 0),
                type=TransactionType.SELL,
                token_address="B",
                token_symbol="B",
                token_amount=1000,
                sol_amount=5,
                price_per_token=0.005,
            ),
        ]
        analyzer = TradingAnalyzer(transactions)
        tod = analyzer.analyze_time_of_day()
        assert tod is not None
        assert tod.best_hour == 14
        assert tod.best_hour_pnl == pytest.approx(5.0, abs=0.01)
        assert tod.worst_hour == 6
        assert tod.best_hour_trades == 1
        assert len(tod.by_hour) == 2
        assert "14:00" in tod.summary

    def test_no_closed_trades_returns_none(self):
        """Wallet que solo compra: no hay trades cerrados -> None."""
        transactions = [
            make_tx(TransactionType.BUY, "A", 1000, 10, 0.01, minutes_ago=60, signature="b1"),
            make_tx(TransactionType.BUY, "B", 2000, 8, 0.004, minutes_ago=30, signature="b2"),
        ]
        analyzer = TradingAnalyzer(transactions)
        assert analyzer.analyze_time_of_day() is None

    def test_by_hour_counts_wins(self):
        """El desglose por hora incluye trades y wins."""
        transactions = [
            Transaction(
                signature="b1", timestamp=datetime(2026, 1, 1, 10, 0),
                type=TransactionType.BUY, token_address="A", token_symbol="A",
                token_amount=1000, sol_amount=10, price_per_token=0.01,
            ),
            Transaction(
                signature="s1", timestamp=datetime(2026, 1, 1, 11, 0),
                type=TransactionType.SELL, token_address="A", token_symbol="A",
                token_amount=1000, sol_amount=12, price_per_token=0.012,
            ),
        ]
        analyzer = TradingAnalyzer(transactions)
        tod = analyzer.analyze_time_of_day()
        assert tod is not None
        hour_entry = [h for h in tod.by_hour if h["hour"] == 11][0]
        assert hour_entry["trades"] == 1
        assert hour_entry["wins"] == 1

    def test_best_hours_recommendation_generated(self):
        """Con trades cerrados, la recomendación de ventana óptima aparece."""
        transactions = [
            Transaction(
                signature="b1", timestamp=datetime(2026, 1, 1, 13, 0),
                type=TransactionType.BUY, token_address="A", token_symbol="A",
                token_amount=1000, sol_amount=10, price_per_token=0.01,
            ),
            Transaction(
                signature="s1", timestamp=datetime(2026, 1, 1, 14, 0),
                type=TransactionType.SELL, token_address="A", token_symbol="A",
                token_amount=1000, sol_amount=15, price_per_token=0.015,
            ),
        ]
        analyzer = TradingAnalyzer(transactions)
        recs = analyzer.generate_recommendations()
        types = [r.type for r in recs]
        assert "best_hours" in types
        rec = [r for r in recs if r.type == "best_hours"][0]
        assert rec.data["best_hour"] == 14
        assert rec.data["best_pnl"] == pytest.approx(5.0, abs=0.01)


class TestTradingScore:
    def _closed_winning_trades(self):
        """Trades cerrados rentables: gana +5 SOL con un solo trade."""
        return [
            Transaction(
                signature="b1", timestamp=datetime(2026, 1, 1, 13, 0),
                type=TransactionType.BUY, token_address="A", token_symbol="A",
                token_amount=1000, sol_amount=10, price_per_token=0.01,
            ),
            Transaction(
                signature="s1", timestamp=datetime(2026, 1, 1, 14, 0),
                type=TransactionType.SELL, token_address="A", token_symbol="A",
                token_amount=1000, sol_amount=15, price_per_token=0.015,
            ),
        ]

    def test_score_computed_with_closed_trades(self):
        """Con trades cerrados el score se calcula y está en 0-100."""
        analyzer = TradingAnalyzer(self._closed_winning_trades())
        score = analyzer.calculate_trading_score()
        assert score is not None
        assert 0 <= score.total <= 100
        # Trade ganador -> rentabilidad y consistencia altas
        assert score.rentabilidad > 40
        assert score.consistencia >= 60

    def test_score_none_without_closed_trades(self):
        """Sin trades cerrados no hay score (evita notas falsas)."""
        transactions = [
            make_tx(TransactionType.BUY, "A", 1000, 10, 0.01, minutes_ago=60, signature="b1"),
        ]
        analyzer = TradingAnalyzer(transactions)
        assert analyzer.calculate_trading_score() is None

    def test_score_grade_and_dimensions(self):
        """El score expone grade, label y las 4 dimensiones con summary."""
        analyzer = TradingAnalyzer(self._closed_winning_trades())
        score = analyzer.calculate_trading_score()
        assert score.grade in ("A", "B", "C", "D", "E")
        assert score.label
        assert score.summary
        for dim in ("rentabilidad", "consistencia", "riesgo", "eficiencia"):
            assert 0 <= getattr(score, dim) <= 100
