"""Tests unitarios para el sistema de feedback (mejora continua)."""
import pytest

from app.services.feedback import FeedbackStore


@pytest.fixture
def store(tmp_path):
    s = FeedbackStore(tmp_path / "feedback.json")
    yield s
    s.reset()


class TestFeedbackStore:
    def test_vote_tracks_counts(self, store):
        result = store.vote("risk_reward", True, "wallet1")
        assert result["useful"] == 1
        assert result["not_useful"] == 0

        store.vote("risk_reward", True, "wallet2")
        store.vote("risk_reward", False, "wallet3")
        result = store.vote("risk_reward", True, "wallet4")
        assert result["useful"] == 3
        assert result["not_useful"] == 1

    def test_score_is_neutral_without_votes(self, store):
        assert store.signal_scores() == {}

    def test_score_trends_up_with_positive_votes(self, store):
        for i in range(5):
            store.vote("hold_time_bias", True, f"w{i}")
        score = store.signal_scores()["hold_time_bias"]
        assert score > 0.7

    def test_score_trends_down_with_negative_votes(self, store):
        for i in range(5):
            store.vote("position_sizing", False, f"w{i}")
        score = store.signal_scores()["position_sizing"]
        assert score < 0.4

    def test_persistence_across_instances(self, tmp_path):
        path = tmp_path / "feedback.json"
        s1 = FeedbackStore(path)
        s1.vote("risk_reward", True, "w1")
        s1.vote("risk_reward", True, "w2")

        s2 = FeedbackStore(path)  # nueva instancia lee del mismo archivo
        scores = s2.signal_scores()
        assert scores["risk_reward"] > 0.7

    def test_stats(self, store):
        store.vote("risk_reward", True, "w1")
        store.vote("cost_reduction", False, "w2")
        stats = store.stats()
        assert stats["total_votes"] == 2
        assert stats["signals"] == 2
        assert "risk_reward" in stats["signal_scores"]
