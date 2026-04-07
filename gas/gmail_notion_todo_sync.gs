/**
 * Gmail → Notion ToDo 同期スクリプト (Google Apps Script)
 *
 * 【動作】
 *   Gmail で "NotionToDo" ラベルを付けたメールを Notion ToDo DB に自動登録する。
 *   処理済みメールには "NotionToDo/Done" ラベルを付けて二重登録を防止する。
 *
 * 【フィールドマッピング】
 *   メール件名       → Title
 *   本文サマリ       → 説明（ANTHROPIC_API_KEY があれば Claude で生成、なければ先頭300文字）
 *   受信日時         → 開始時刻
 *   ページ本文       → 送信者・受信日時 ＋ メール本文全文
 *
 * 【セットアップ手順】
 *   1. Google Apps Script (script.google.com) で新しいプロジェクトを作成
 *   2. このファイルの内容を貼り付けて保存
 *   3. 「プロジェクトの設定」→「スクリプト プロパティ」に以下を追加:
 *
 *        NOTION_TOKEN     secret_xxxxx  （Notion の Integration Token）
 *        TODO_DB_ID       xxxxxxxx...   （Notion ToDo DB の ID ※下記参照）
 *        GMAIL_LABEL      NotionToDo    （任意、デフォルト: NotionToDo）
 *        ANTHROPIC_API_KEY  sk-ant-xxx  （任意、サマリ生成用 Claude API キー）
 *
 *   4. 「実行」→「setupTrigger」を一度実行してトリガーを登録
 *      ※ 初回実行時に Gmail / 外部接続の権限許可ダイアログが表示されます
 *
 * 【TODO_DB_ID の調べ方】
 *   Notion で ToDo DB のページを開き、URL をコピー:
 *     https://www.notion.so/workspace/2257d017-b6a0-8026-867c-000bb0969507?v=...
 *                                      ↑ この部分（ダッシュを除いた32文字）
 *
 * 【手動実行】
 *   「実行」→「run」を選択
 */

// ─── 設定 ────────────────────────────────────────────────────────────────────

function getConfig() {
  const p = PropertiesService.getScriptProperties();
  return {
    notionToken:     p.getProperty('NOTION_TOKEN')      || '',
    todoDbId:        p.getProperty('TODO_DB_ID')         || '',
    gmailLabel:      p.getProperty('GMAIL_LABEL')        || 'NotionToDo',
    gmailDoneLabel:  p.getProperty('GMAIL_DONE_LABEL')   || 'NotionToDo/Done',
    anthropicKey:    p.getProperty('ANTHROPIC_API_KEY')  || '',
  };
}


// ─── トリガー設定 ─────────────────────────────────────────────────────────────

/**
 * 15分ごとの自動実行トリガーを登録する。
 * 「実行」メニューから一度だけ手動で実行してください。
 */
function setupTrigger() {
  // 既存の run トリガーを削除（重複防止）
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === 'run') ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger('run')
    .timeBased()
    .everyMinutes(1)
    .create();

  Logger.log('✅ トリガー設定完了（15分ごとに run が実行されます）');
}


// ─── 処理済み管理 ─────────────────────────────────────────────────────────────
// { threadId: { count: N, processedAt: "ISO文字列" } } で保存

const PROCESSED_KEY = 'processed_threads';

function getProcessedThreads() {
  const raw = PropertiesService.getUserProperties().getProperty(PROCESSED_KEY);
  return raw ? JSON.parse(raw) : {};
}

function getProcessedCount(threadId) {
  return (getProcessedThreads()[threadId] || {}).count || 0;
}

function markAsProcessed(threadId, msgCount) {
  const data = getProcessedThreads();
  data[threadId] = { count: msgCount, processedAt: new Date().toISOString() };

  // 90日以上前のエントリを削除（肥大化防止）
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 90);
  Object.keys(data).forEach(k => {
    if (new Date(data[k].processedAt) < cutoff) delete data[k];
  });

  PropertiesService.getUserProperties().setProperty(PROCESSED_KEY, JSON.stringify(data));
}


// ─── Gmail: 本文取得 ──────────────────────────────────────────────────────────

function getEmailBody(msg) {
  const plain = msg.getPlainBody();
  if (plain && plain.trim()) return plain.trim();

  // HTML をプレーンテキストに変換
  return msg.getBody()
    .replace(/<(script|style)[^>]*>[\s\S]*?<\/\1>/gi, '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/?(p|div|tr|li)[^>]*>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}


// ─── Claude API: サマリ生成 ───────────────────────────────────────────────────

function generateSummary(subject, body, apiKey) {
  if (!apiKey) {
    return body.substring(0, 300) + (body.length > 300 ? '…' : '');
  }

  const prompt =
    '以下のメールを日本語で2〜3文に要約してください。要約のみ返してください。\n\n' +
    `件名: ${subject}\n\n本文:\n${body.substring(0, 4000)}`;

  const res = UrlFetchApp.fetch('https://api.anthropic.com/v1/messages', {
    method: 'post',
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
    },
    payload: JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 300,
      messages: [{ role: 'user', content: prompt }],
    }),
    muteHttpExceptions: true,
  });

  if (res.getResponseCode() !== 200) {
    Logger.log('⚠️ Claude API エラー: ' + res.getContentText());
    return body.substring(0, 300) + '…';
  }

  return JSON.parse(res.getContentText()).content[0].text.trim();
}


// ─── Notion: ブロック変換 ─────────────────────────────────────────────────────

function makeParagraph(text) {
  return {
    object: 'block', type: 'paragraph',
    paragraph: { rich_text: [{ type: 'text', text: { content: text } }] },
  };
}

function bodyToBlocks(body) {
  const MAX = 1900;
  const blocks = [];
  let current = '';

  body.split('\n').forEach(line => {
    // 1行が MAX 超の場合は強制分割
    while (line.length > MAX) {
      if (current) { blocks.push(makeParagraph(current.trimEnd())); current = ''; }
      blocks.push(makeParagraph(line.substring(0, MAX)));
      line = line.substring(MAX);
    }

    if (current.length + line.length + 1 > MAX) {
      if (current) blocks.push(makeParagraph(current.trimEnd()));
      current = line + '\n';
    } else {
      current += line + '\n';
    }
  });

  if (current.trim()) blocks.push(makeParagraph(current.trim()));
  return blocks.length ? blocks : [makeParagraph('')];
}


// ─── Notion: ページ作成 ───────────────────────────────────────────────────────

function notionFetch(url, method, token, body) {
  const res = UrlFetchApp.fetch(url, {
    method: method,
    headers: {
      'Authorization': `Bearer ${token}`,
      'Notion-Version': '2022-06-28',
      'Content-Type': 'application/json',
    },
    payload: JSON.stringify(body),
    muteHttpExceptions: true,
  });

  if (res.getResponseCode() >= 300) {
    throw new Error(`Notion API エラー (${res.getResponseCode()}): ${res.getContentText()}`);
  }
  return JSON.parse(res.getContentText());
}

function createNotionTodo(config, subject, summary, receivedAt, sender, body) {
  const metaText =
    `From: ${sender}\n` +
    `Received: ${Utilities.formatDate(receivedAt, 'Asia/Tokyo', 'yyyy-MM-dd HH:mm')}`;

  const headerBlocks = [
    {
      object: 'block', type: 'callout',
      callout: {
        rich_text: [{ type: 'text', text: { content: metaText } }],
        icon: { type: 'emoji', emoji: '📧' },
        color: 'gray_background',
      },
    },
    { object: 'block', type: 'divider', divider: {} },
  ];

  const contentBlocks = bodyToBlocks(body);
  const allBlocks     = [...headerBlocks, ...contentBlocks];

  const page = notionFetch(
    'https://api.notion.com/v1/pages',
    'post',
    config.notionToken,
    {
      parent: { database_id: config.todoDbId },
      properties: {
        Title:    { title:     [{ type: 'text', text: { content: subject.substring(0, 2000) } }] },
        '説明':   { rich_text: [{ type: 'text', text: { content: summary.substring(0, 2000) } }] },
        '開始時刻': { date: { start: receivedAt.toISOString() } },
        'ステータス': { status: { name: '未着手' } },
        'TaskType':   { select: { name: 'Inbox 📨' } },
      },
      children: allBlocks.slice(0, 100),
    }
  );

  const pageId = page.id;

  // 100ブロックを超える場合は追記
  for (let i = 100; i < allBlocks.length; i += 100) {
    notionFetch(
      `https://api.notion.com/v1/blocks/${pageId}/children`,
      'patch',
      config.notionToken,
      { children: allBlocks.slice(i, i + 100) }
    );
  }

  return pageId;
}


// ─── メイン ───────────────────────────────────────────────────────────────────

function run() {
  const config = getConfig();

  if (!config.notionToken || !config.todoDbId) {
    Logger.log('❌ スクリプトプロパティに NOTION_TOKEN と TODO_DB_ID を設定してください');
    return;
  }

  Logger.log(`[${new Date().toLocaleString('ja-JP')}] 同期開始（ラベル: ${config.gmailLabel}）`);

  // Gmail ラベルを取得
  const label = GmailApp.getUserLabelByName(config.gmailLabel);
  if (!label) {
    Logger.log(`❌ Gmailラベル "${config.gmailLabel}" が見つかりません。Gmail でラベルを作成してください。`);
    return;
  }

  // Done ラベルを取得（なければ作成）
  let doneLabel = GmailApp.getUserLabelByName(config.gmailDoneLabel);
  if (!doneLabel) {
    doneLabel = GmailApp.createLabel(config.gmailDoneLabel);
    Logger.log(`  📌 ラベル作成: "${config.gmailDoneLabel}"`);
  }

  // 対象スレッドを最大50件取得
  const threads = label.getThreads(0, 50);
  if (threads.length === 0) {
    Logger.log('ℹ️  対象メールなし');
    return;
  }
  Logger.log(`  ${threads.length} スレッド検出`);

  let newCount = 0, skipCount = 0, errorCount = 0;

  threads.forEach(thread => {
    const threadId   = thread.getId();
    const msgs       = thread.getMessages();
    const totalCount = msgs.length;
    const lastCount  = getProcessedCount(threadId);  // 前回処理時のメッセージ数（0=未処理）

    // 新着メッセージがなければスキップ
    if (totalCount <= lastCount) {
      skipCount++;
      return;
    }

    try {
      const firstMsg   = msgs[0];
      // 件名はスレッドの最初のメールから（Re: を除去）
      const subject    = firstMsg.getSubject().replace(/^(Re:\s*)+/i, '').trim() || '(件名なし)';
      const sender     = firstMsg.getFrom() || '';

      // 新着メッセージのみ抽出（前回処理分以降）
      const newMsgs    = msgs.slice(lastCount);
      const receivedAt = newMsgs[0].getDate();
      const isReply    = lastCount > 0;  // 再処理（返信あり）かどうか

      // 本文: 新着メッセージのみ結合
      const body = newMsgs.map((msg, i) => {
        const from = msg.getFrom() || '';
        const date = Utilities.formatDate(msg.getDate(), 'Asia/Tokyo', 'yyyy-MM-dd HH:mm');
        const text = getEmailBody(msg) || '(本文なし)';
        const label = isReply ? `返信${lastCount + i + 1}通目` : `${i + 1}通目`;
        return `--- ${label} / ${from} / ${date} ---\n${text}`;
      }).join('\n\n');

      const titlePrefix = isReply ? `[返信あり] ` : '';
      const displaySubject = `${titlePrefix}${subject}`;

      Logger.log(`\n  処理中: ${displaySubject}（新着${newMsgs.length}通）`);

      const summary = generateSummary(subject, body, config.anthropicKey);
      Logger.log(`  サマリ: ${summary.substring(0, 80)}…`);

      const pageId = createNotionTodo(config, displaySubject, summary, receivedAt, sender, body);
      Logger.log(`  ✅ Notion ToDo 作成: ${pageId}`);

      markAsProcessed(threadId, totalCount);
      doneLabel.addToThread(thread);
      newCount++;

    } catch (e) {
      Logger.log(`  ❌ エラー (${threadId}): ${e.message}`);
      errorCount++;
    }
  });

  Logger.log(`\n完了: ${newCount} 件処理, ${skipCount} 件スキップ, ${errorCount} 件エラー`);
}
