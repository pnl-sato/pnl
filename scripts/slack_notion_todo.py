"""
Slack → Notion ToDo 作成Bot

スラッシュコマンド `/todo` でモーダルを開き、
Notion の ToDo DB にタスクを作成する。

使い方:
    python slack_notion_todo.py

環境変数 (.env):
    SLACK_BOT_TOKEN      - xoxb-...
    SLACK_SIGNING_SECRET - Slack App の Signing Secret
    NOTION_TOKEN         - Notion Integration Token
    NOTION_TODO_DB_ID    - ToDo DB の Database ID
    NOTION_POSITION_DB_ID - ポジション DB の Database ID
"""

import os
import logging
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import httpx

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_TODO_DB_ID = os.environ["NOTION_TODO_DB_ID"]
NOTION_POSITION_DB_ID = os.environ["NOTION_POSITION_DB_ID"]

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def search_notion_positions(query: str) -> list[dict]:
    """NotionのポジションDBを検索してSlack external_select用の選択肢を返す"""
    url = f"https://api.notion.com/v1/databases/{NOTION_POSITION_DB_ID}/query"
    payload = {
        "filter": {
            "property": "名前",
            "title": {"contains": query},
        },
        "sorts": [{"property": "名前", "direction": "ascending"}],
        "page_size": 20,
    }
    with httpx.Client() as client:
        resp = client.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
        resp.raise_for_status()
    results = resp.json().get("results", [])
    options = []
    for page in results:
        title_prop = page.get("properties", {}).get("名前", {})
        title_parts = title_prop.get("title", [])
        name = "".join(t.get("plain_text", "") for t in title_parts)
        if name:
            options.append({
                "text": {"type": "plain_text", "text": name[:75]},
                "value": page["id"],
            })
    return options


def create_notion_todo(
    title: str,
    position_id: str | None,
    assignee_slack_id: str | None,
    status: str | None,
    due_date: str | None,
) -> str:
    """Notion ToDo DBにページを作成してURLを返す"""
    properties: dict = {
        "Title": {"title": [{"text": {"content": title}}]},
        "TaskType": {"select": {"name": "Inbox 📨"}},
        "Category": {"select": {"name": "Pole&Line"}},
    }

    if status:
        properties["ステータス"] = {"status": {"name": status}}

    if position_id:
        properties["ポジション"] = {"relation": [{"id": position_id}]}

    if due_date:
        properties["完了日時"] = {"date": {"start": due_date}}

    payload = {
        "parent": {"database_id": NOTION_TODO_DB_ID},
        "properties": properties,
    }

    with httpx.Client() as client:
        resp = client.post(
            "https://api.notion.com/v1/pages",
            headers=NOTION_HEADERS,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()

    return resp.json().get("url", "")


# ── /todo スラッシュコマンド ───────────────────────────────────────────────────

@app.command("/todo")
def open_todo_modal(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "create_todo_modal",
            "title": {"type": "plain_text", "text": "Create Task"},
            "submit": {"type": "plain_text", "text": "Save"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "task_name",
                    "label": {"type": "plain_text", "text": "Task Name"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "task_name_input",
                        "placeholder": {"type": "plain_text", "text": "Write a task name"},
                    },
                },
                {
                    "type": "input",
                    "block_id": "project_name",
                    "label": {"type": "plain_text", "text": "Project Name"},
                    "optional": True,
                    "element": {
                        "type": "external_select",
                        "action_id": "project_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Search for a project or paste a link",
                        },
                        "min_query_length": 3,
                    },
                },
                {
                    "type": "input",
                    "block_id": "assignee",
                    "label": {"type": "plain_text", "text": "Assignee (任意)"},
                    "optional": True,
                    "element": {
                        "type": "users_select",
                        "action_id": "assignee_select",
                        "placeholder": {"type": "plain_text", "text": "Search for an assignee"},
                    },
                },
                {
                    "type": "input",
                    "block_id": "status",
                    "label": {"type": "plain_text", "text": "Status (任意)"},
                    "optional": True,
                    "element": {
                        "type": "static_select",
                        "action_id": "status_select",
                        "placeholder": {"type": "plain_text", "text": "オプションを選択する"},
                        "options": [
                            {"text": {"type": "plain_text", "text": "未着手"}, "value": "未着手"},
                            {"text": {"type": "plain_text", "text": "進行中"}, "value": "進行中"},
                            {"text": {"type": "plain_text", "text": "完了"}, "value": "完了"},
                        ],
                    },
                },
                {
                    "type": "input",
                    "block_id": "due_date",
                    "label": {"type": "plain_text", "text": "DueDate (任意)"},
                    "optional": True,
                    "element": {
                        "type": "datepicker",
                        "action_id": "due_date_picker",
                        "placeholder": {"type": "plain_text", "text": "日付を選択"},
                    },
                },
            ],
        },
    )


# ── Project Name の外部検索（3文字以上で呼ばれる） ────────────────────────────

@app.options("project_select")
def handle_project_search(ack, payload):
    query = payload.get("value", "")
    if len(query) < 3:
        ack(options=[])
        return
    try:
        options = search_notion_positions(query)
    except Exception as e:
        logger.error("Notion search failed: %s", e)
        options = []
    ack(options=options)


# ── モーダル送信 ──────────────────────────────────────────────────────────────

@app.view("create_todo_modal")
def handle_todo_submission(ack, body, client, logger):
    ack()

    values = body["view"]["state"]["values"]
    user_id = body["user"]["id"]

    task_name = values["task_name"]["task_name_input"]["value"]

    project_val = values.get("project_name", {}).get("project_select", {})
    position_id = (
        project_val.get("selected_option", {}) or {}
    ).get("value")

    assignee_val = values.get("assignee", {}).get("assignee_select", {})
    assignee_slack_id = assignee_val.get("selected_user")

    status_val = values.get("status", {}).get("status_select", {})
    status = (status_val.get("selected_option") or {}).get("value")

    due_date_val = values.get("due_date", {}).get("due_date_picker", {})
    due_date = due_date_val.get("selected_date")

    try:
        page_url = create_notion_todo(
            title=task_name,
            position_id=position_id,
            assignee_slack_id=assignee_slack_id,
            status=status,
            due_date=due_date,
        )
        client.chat_postMessage(
            channel=user_id,
            text=f"✅ タスクを作成しました: <{page_url}|{task_name}>",
        )
    except Exception as e:
        logger.error("Failed to create Notion todo: %s", e)
        client.chat_postMessage(
            channel=user_id,
            text=f"❌ タスクの作成に失敗しました: {e}",
        )


# ── 起動 ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if app_token:
        # Socket Mode（ngrok不要）
        handler = SocketModeHandler(app, app_token)
        logger.info("Starting in Socket Mode...")
        handler.start()
    else:
        # HTTP Mode
        port = int(os.environ.get("PORT", 3000))
        logger.info("Starting HTTP server on port %d...", port)
        app.start(port=port)
