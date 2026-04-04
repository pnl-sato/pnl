"""
LINE AI チャットボット
- LINE Messaging API からメッセージを受け取り Claude (Haiku) で返答する
- 月ごとの費用を SQLite で管理し、上限に達したら通知する
"""

import base64
import hashlib
import hmac
import os

import anthropic
import requests
from dotenv import load_dotenv
from flask import Flask, abort, request

from cost_tracker import CostTracker

load_dotenv()

# --- 設定 ---
LINE_CHANNEL_SECRET: str = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN: str = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
MONTHLY_BUDGET_YEN: int = int(os.environ.get("MONTHLY_BUDGET_YEN", "500"))

SYSTEM_PROMPT = """
あなたは映画『若大将』シリーズの主人公、「若大将」こと田沼雄一です。
京南大学の学生であり、スポーツ万能（特にスキー、水泳、ボクシング）、楽器もこなすスーパースターです。実家は日本橋のすき焼き屋「田能久」。
性格は竹を割ったように真っ直ぐで、正義感が強く、少しお調子者ですが、根本には育ちの良さと礼儀正しさがあります。

【口調・語彙の特徴】
- 一人称は「ぼく」
- 二人称は「君」「あなた」、ライバルには「おい、青大将！」
- 語尾は「〜だよ」「〜だね」「〜かい？」「〜じゃないか」
- 感嘆詞は「ようし！」「いいぞ！」「しめた！」「いやぁ〜」
- 口癖：「幸せだなあ」「ぼくは死ぬまで君を離さないぞ」「おやじさん、そんなに怒らないでくださいよ」
- 常に明るく、エネルギッシュ。卑屈な態度は一切取らない

【会話のルール】
- 回答は簡潔に2〜3文以内にまとめる。余計な説明や付け足しはしない
- 質問には端的に答え、その後に一言だけ若大将らしいコメントを添える程度にする
- 悩み相談を受けても、最後には「よし、やってみようよ！」「君ならできるさ」と爽やかに励ます
- 良いことがあったら「幸せだなあ」を一言添える程度にとどめる
"""

# --- クライアント初期化 ---
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
cost_tracker = CostTracker(db_path=os.environ.get("DB_PATH", "usage.db"))

app = Flask(__name__)


# ---------------------------------------------------------------------------
# LINE ユーティリティ
# ---------------------------------------------------------------------------

def verify_signature(body: bytes, signature: str) -> bool:
    """LINE からのリクエストの署名を検証する。"""
    digest = hmac.new(LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


def reply_to_line(reply_token: str, message: str) -> None:
    """LINE の reply API でメッセージを返信する。"""
    # LINE は1メッセージ最大5000文字
    if len(message) > 4900:
        message = message[:4900] + "…（省略）"

    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        },
        json={
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": message}],
        },
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_data()
    signature = request.headers.get("X-Line-Signature", "")

    if not verify_signature(body, signature):
        abort(400, "Invalid signature")

    for event in request.json.get("events", []):
        _handle_event(event)

    return "OK"


def _handle_event(event: dict) -> None:
    if event.get("type") != "message":
        return
    if event["message"].get("type") != "text":
        return

    reply_token: str = event["replyToken"]
    user_message: str = event["message"]["text"].strip()

    # 「状況確認」コマンド
    if user_message in ("状況", "利用状況", "/status"):
        reply_to_line(reply_token, cost_tracker.get_status_message(MONTHLY_BUDGET_YEN))
        return

    # 月額上限チェック
    current_cost = cost_tracker.get_monthly_cost_yen()
    if current_cost >= MONTHLY_BUDGET_YEN:
        reply_to_line(
            reply_token,
            f"いやぁ、参ったね！今月はぼくもここまでみたいだよ。\n"
            f"来月になったらまた思いっきり話そうじゃないか！\n\n"
            f"……幸せだなあ。君と話せる日を楽しみにしてるよ。",
        )
        return

    # Claude API 呼び出し
    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        reply_to_line(reply_token, "申し訳ありません、AIとの通信でエラーが発生しました。しばらく経ってから試してください🙏")
        app.logger.error("Anthropic API error: %s", e)
        return

    ai_reply: str = response.content[0].text
    input_tokens: int = response.usage.input_tokens
    output_tokens: int = response.usage.output_tokens

    # 使用量を記録
    cost_tracker.add_usage(input_tokens, output_tokens)
    new_cost = cost_tracker.get_monthly_cost_yen()

    # 80% 超えたら初回だけ警告を添える
    warning = ""
    if new_cost >= MONTHLY_BUDGET_YEN * 0.8 and current_cost < MONTHLY_BUDGET_YEN * 0.8:
        warning = (
            f"\n\nよし、ちょっと聞いてくれよ。"
            f"今月はもうそろそろ終盤だよ。残り少ないけど、悔いのないようにいこうじゃないか！"
        )

    reply_to_line(reply_token, ai_reply + warning)


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
