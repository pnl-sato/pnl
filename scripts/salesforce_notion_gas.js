/**
 * Salesforce → Notion 候補者DB 同期 GAS Web App
 *
 * 【アーキテクチャ】
 *   Salesforce Contact 登録・更新
 *     → Record-Triggered Flow が HTTP POST
 *     → この GAS Web App が受け取り
 *     → Notion 候補者DB に新規作成 or 更新
 *
 * 【セットアップ手順】
 *   1. Google Apps Script (script.google.com) で新規プロジェクト作成
 *   2. このコードを貼り付けて保存
 *   3. スクリプトプロパティを設定（下記参照）
 *   4. Web App としてデプロイ
 *   5. Salesforce Remote Site Settings に GAS URL を追加
 *   6. Salesforce Flow を設定（下記参照）
 *
 * 【スクリプトプロパティの設定】
 *   「プロジェクトの設定」→「スクリプトプロパティ」から以下を追加:
 *   - NOTION_TOKEN : Notion API トークン（secret_xxx...）
 *   - GAS_SECRET   : 任意の秘密キー（英数字で自由に設定、Salesforce Flow 側と合わせる）
 *
 * 【Web App デプロイ手順】
 *   「デプロイ」→「新しいデプロイ」
 *   - 種類: ウェブアプリ
 *   - 次のユーザーとして実行: 自分
 *   - アクセスできるユーザー: 全員
 *   → デプロイ後に表示される URL をコピーして Salesforce Flow に設定する
 *
 * 【Salesforce Flow 設定手順】
 *   1. フローを開く（設定 → フロー → 新規フロー）
 *   2. 「レコードトリガーフロー」を選択
 *   3. オブジェクト: 取引先責任者（Contact）
 *   4. トリガー: 「レコードが作成または更新された」
 *   5. 条件なし（全件対象）or 必要に応じてフィルタ設定
 *   6. アクションを追加 → 「HTTP コールアウト」
 *   7. 以下を設定:
 *      - URL    : GAS Web App の URL（デプロイ後に取得）
 *      - メソッド: POST
 *      - ヘッダー: Content-Type = application/json
 *      - 本文   : 下記 JSON テンプレートを使用
 *   8. Salesforce 設定 → リモートサイトの設定 に以下を追加:
 *      - リモートサイトのURL: https://script.google.com
 *
 * 【Flow の本文（JSON テンプレート）】
 *   以下を Flow の HTTP コールアウト本文に設定してください。
 *   {!変数名} の部分は Flow のマージ項目で置き換えます。
 *
 *   {
 *     "secret": "ここに GAS_SECRET の値を直接記入",
 *     "id": "{!$Record.Id}",
 *     "instanceUrl": "https://your-domain.salesforce.com",
 *     "lastName": "{!$Record.LastName}",
 *     "firstName": "{!$Record.FirstName}",
 *     "birthdate": "{!$Record.Birthdate}",
 *     "title": "{!$Record.Title}",
 *     "currentSalary": "{!$Record.現在年収のAPIカスタムフィールド名__c}",
 *     "desiredSalary": "{!$Record.希望年収のAPIカスタムフィールド名__c}",
 *     "jobChangeReason": "{!$Record.転職理由のAPIカスタムフィールド名__c}",
 *     "sideJob": "{!$Record.副業希望のAPIカスタムフィールド名__c}"
 *   }
 *
 *   ※ カスタムフィールドがない項目は行ごと削除してください
 *   ※ instanceUrl はご自身の SF 組織の URL に固定値で記入してください
 */

// ─── 定数 ────────────────────────────────────────────────────────────────────

// Notion 候補者DB の database ID（ダッシュなし32文字）
// collection://2057d017-b6a0-80fc-9e8d-000b3b6ab37e → 以下の値
const CANDIDATE_DB_ID = '2057d017b6a080fc9e8d000b3b6ab37e';
const NOTION_API_BASE = 'https://api.notion.com/v1';


// ─── エントリーポイント ───────────────────────────────────────────────────────

/**
 * Salesforce Flow からの POST を受け取る
 */
function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);

    // 秘密キー認証
    const secret = PropertiesService.getScriptProperties().getProperty('GAS_SECRET');
    if (secret && data.secret !== secret) {
      return jsonResponse({ status: 'error', message: 'Unauthorized' });
    }

    const result = syncToNotion(data);
    console.log(`同期完了: ${JSON.stringify(result)}`);
    return jsonResponse({ status: 'success', ...result });

  } catch (err) {
    console.error('エラー:', err.message);
    return jsonResponse({ status: 'error', message: err.message });
  }
}

/**
 * 動作確認用（ブラウザで GAS URL にアクセスして確認）
 */
function doGet() {
  return jsonResponse({ status: 'ok', message: 'Salesforce→Notion GAS Web App is running' });
}


// ─── メイン同期処理 ───────────────────────────────────────────────────────────

function syncToNotion(data) {
  const token = PropertiesService.getScriptProperties().getProperty('NOTION_TOKEN');
  if (!token) throw new Error('スクリプトプロパティに NOTION_TOKEN が設定されていません');

  const sfUrl = `${data.instanceUrl}/${data.id}`;
  const existingPageId = findExistingPage(sfUrl, token);
  const properties = buildProperties(data, sfUrl);

  if (existingPageId) {
    updatePage(existingPageId, properties, token);
    return { action: 'updated', pageId: existingPageId, sfUrl };
  } else {
    const pageId = createPage(properties, token);
    return { action: 'created', pageId, sfUrl };
  }
}


// ─── Notion API ───────────────────────────────────────────────────────────────

function notionHeaders(token) {
  return {
    'Authorization': `Bearer ${token}`,
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json',
  };
}

/**
 * SalesForce URL で候補者DB を検索し、既存ページの ID を返す（なければ null）
 */
function findExistingPage(sfUrl, token) {
  const res = UrlFetchApp.fetch(
    `${NOTION_API_BASE}/databases/${CANDIDATE_DB_ID}/query`,
    {
      method: 'post',
      headers: notionHeaders(token),
      payload: JSON.stringify({
        filter: { property: 'SalesForce', url: { equals: sfUrl } },
        page_size: 1,
      }),
      muteHttpExceptions: true,
    }
  );
  const json = JSON.parse(res.getContentText());
  const results = json.results || [];
  return results.length > 0 ? results[0].id : null;
}

/**
 * SF の Contact データから Notion プロパティ辞書を構築する
 */
function buildProperties(data, sfUrl) {
  const props = {};

  // 名前（姓 + 名）
  const lastName  = (data.lastName  || '').trim();
  const firstName = (data.firstName || '').trim();
  const fullName  = [lastName, firstName].filter(Boolean).join(' ');
  if (fullName) {
    props['名前'] = { title: [{ text: { content: fullName } }] };
  }

  // SalesForce URL（照合キー・必須）
  props['SalesForce'] = { url: sfUrl };

  // 生年月日
  if (data.birthdate) {
    props['生年月日'] = { date: { start: data.birthdate } };
  }

  // 現在の肩書き
  if (data.title) {
    props['ポジション'] = { rich_text: [{ text: { content: data.title } }] };
  }

  // 現年収（円）
  if (data.currentSalary != null && data.currentSalary !== '') {
    props['現年収'] = { number: Number(data.currentSalary) };
  }

  // 最低希望年収（円）
  if (data.desiredSalary != null && data.desiredSalary !== '') {
    props['最低希望年収'] = { number: Number(data.desiredSalary) };
  }

  // 転職検討理由
  if (data.jobChangeReason) {
    props['転職検討理由'] = { rich_text: [{ text: { content: String(data.jobChangeReason) } }] };
  }

  // 副業希望
  if (data.sideJob != null && data.sideJob !== '') {
    props['副業希望'] = { checkbox: data.sideJob === true || data.sideJob === 'true' };
  }

  return props;
}

/**
 * 候補者DB に新規ページを作成し、page_id を返す
 */
function createPage(properties, token) {
  const res = UrlFetchApp.fetch(`${NOTION_API_BASE}/pages`, {
    method: 'post',
    headers: notionHeaders(token),
    payload: JSON.stringify({
      parent: { database_id: CANDIDATE_DB_ID },
      properties,
    }),
    muteHttpExceptions: true,
  });
  const json = JSON.parse(res.getContentText());
  if (!json.id) throw new Error(`Notion ページ作成失敗: ${res.getContentText()}`);
  return json.id;
}

/**
 * 既存ページを更新する（名前は上書きしない）
 */
function updatePage(pageId, properties, token) {
  const { '名前': _omit, ...updateProps } = properties;
  if (Object.keys(updateProps).length === 0) return;

  const res = UrlFetchApp.fetch(`${NOTION_API_BASE}/pages/${pageId}`, {
    method: 'patch',
    headers: notionHeaders(token),
    payload: JSON.stringify({ properties: updateProps }),
    muteHttpExceptions: true,
  });
  if (res.getResponseCode() >= 400) {
    throw new Error(`Notion ページ更新失敗: ${res.getContentText()}`);
  }
}


// ─── ユーティリティ ───────────────────────────────────────────────────────────

function jsonResponse(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}


// ─── ローカルテスト用（GAS エディタから実行して動作確認） ─────────────────────

/**
 * GAS エディタの「実行」ボタンで動作テストする場合はここを実行。
 * 実際の Salesforce データをハードコードして Notion への書き込みを確認できる。
 */
function testSync() {
  const testData = {
    secret: PropertiesService.getScriptProperties().getProperty('GAS_SECRET'),
    id: '0031234567890ABC',
    instanceUrl: 'https://your-domain.salesforce.com',  // ← 実際の URL に変更
    lastName: '山田',
    firstName: '太郎',
    birthdate: '1990-05-15',
    title: 'エンジニアリングマネージャー',
    currentSalary: 8000000,
    desiredSalary: 10000000,
    jobChangeReason: 'キャリアアップのため',
    sideJob: false,
  };

  const result = syncToNotion(testData);
  console.log('テスト結果:', JSON.stringify(result));
}
