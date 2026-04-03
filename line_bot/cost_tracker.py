"""
月ごとのAPI使用量・費用を SQLite で管理するモジュール。
"""

import sqlite3
from datetime import datetime
from pathlib import Path

# Claude Haiku 4.5 の料金（USD per 1M tokens）
HAIKU_INPUT_PRICE_USD_PER_M = 0.80
HAIKU_OUTPUT_PRICE_USD_PER_M = 4.00

# 為替レート（概算）
USD_TO_YEN = 150


class CostTracker:
    def __init__(self, db_path: str = "usage.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    month        TEXT    NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    created_at   TEXT    NOT NULL
                )
            """)

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        """1回の API 呼び出し結果を記録する。"""
        month = datetime.now().strftime("%Y-%m")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO usage (month, input_tokens, output_tokens, created_at) VALUES (?, ?, ?, ?)",
                (month, input_tokens, output_tokens, datetime.now().isoformat()),
            )

    def get_monthly_usage(self, month: str | None = None) -> dict:
        """指定月（デフォルト: 今月）のトークン合計を返す。"""
        if month is None:
            month = datetime.now().strftime("%Y-%m")
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT SUM(input_tokens), SUM(output_tokens) FROM usage WHERE month = ?",
                (month,),
            ).fetchone()
        return {
            "month": month,
            "input_tokens": row[0] or 0,
            "output_tokens": row[1] or 0,
        }

    def get_monthly_cost_yen(self, month: str | None = None) -> float:
        """指定月の推定費用（円）を返す。"""
        usage = self.get_monthly_usage(month)
        cost_usd = (
            usage["input_tokens"] * HAIKU_INPUT_PRICE_USD_PER_M / 1_000_000
            + usage["output_tokens"] * HAIKU_OUTPUT_PRICE_USD_PER_M / 1_000_000
        )
        return cost_usd * USD_TO_YEN

    def get_status_message(self, budget_yen: int) -> str:
        """現在の利用状況サマリーを返す（ユーザー向けメッセージ用）。"""
        usage = self.get_monthly_usage()
        cost = self.get_monthly_cost_yen()
        pct = cost / budget_yen * 100
        return (
            f"📊 {usage['month']} の利用状況\n"
            f"推定費用: 約{int(cost)}円 / {budget_yen}円（{pct:.0f}%）\n"
            f"入力トークン: {usage['input_tokens']:,}\n"
            f"出力トークン: {usage['output_tokens']:,}"
        )
