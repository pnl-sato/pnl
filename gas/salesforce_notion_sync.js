/**
 * Salesforce → Notion 候補者DB 同期 GAS Web App
 *
 * 【アーキテクチャ】
 *   Salesforce Contact 登録・更新
 *     → Record-Triggered Flow が HTTP POST
 *     → この GAS Web App が受け取り
 *     → Notion 候補者DB に新規作成 or 更新
 *
 * 【スクリプトプロパティの設定】
 *   GAS「プロジェクトの設定」→「スクリプトプロパティ」から以下を追加:
 *   - NOTION_TOKEN : Notion API トークン（ntn_xxx...）
 *   - GAS_SECRET   : 任意の秘密キー（Salesforce Flow 側と合わせる）
 *
 * 【Salesforce Flow の本文（JSON テンプレート）】
 *   Flow の HTTP コールアウトの本文に以下を設定してください。
 *   {!$Record.XXX} の部分は Flow がレコード値で置き換えます。
 *
 *   {
 *     "secret": "GAS_SECRETの値をここに直接記入",
 *     "id": "{!$Record.Id}",
 *     "instanceUrl": "https://your-domain.salesforce.com",
 *     "notionPageId": "{!$Record.Notion_Page_ID__c}",
 *     "lastName": "{!$Record.LastName}",
 *     "firstName": "{!$Record.FirstName}",
 *     "furiganaLastName": "{!$Record.furigana_lastname__c}",
 *     "furiganaFirstName": "{!$Record.furigana_firstname__c}",
 *     "birthdate": "{!$Record.Birthdate}",
 *     "currentPosition": "{!$Record.CurrentPosition__c}",
 *     "currentSalary": "{!$Record.ActualSalary__c}",
 *     "targetSalary": "{!$Record.ActualTargetSalary__c}",
 *     "jobChangeReason": "{!$Record.Tenshokujiku__c}",
 *     "sideWork": "{!$Record.SideWorkRequirement__c}"
 *   }
 *
 *   ※ instanceUrl はご自身の SF 組織の URL を固定値で記入
 *   ※ notionPageId は初回作成後に SF 側へ書き戻す（現在は GAS 側でログ出力のみ）
 */

// ─── 定数 ────────────────────────────────────────────────────────────────────

// Notion 候補者DB の database ID（ダッシュなし32文字）
// collection://2057d017-b6a0-80fc-9e8d-000b3b6ab37e
const CANDIDATE_DB_ID = '2057d017b6a080fc9e8d000b3b6ab37e';
const NOTION_API_BASE = 'https://api.notion.com/v1';

// SideWorkRequirement__c（副業希望）ピックリスト値 → boolean への変換
// SF 組織の実際の値に合わせて編集してください
const SIDE_WORK_POSITIVE_VALUES = ['希望する', 'あり', 'yes', 'true', '1'];


// ─── エントリーポイント ───────────────────────────────────────────────────────

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
    console.error('エラー:', err.message, err.stack);
    return jsonResponse({ status: 'error', message: err.message });
  }
}

// 動作確認用（ブラウザで GAS URL にアクセスして確認）
function doGet() {
  return jsonResponse({ status: 'ok', message: 'Salesforce→Notion GAS Web App is running' });
}


// ─── メイン同期処理 ───────────────────────────────────────────────────────────

function syncToNotion(data) {
  const token = PropertiesService.getScriptProperties().getProperty('NOTION_TOKEN');
  if (!token) throw new Error('スクリプトプロパティに NOTION_TOKEN が設定されていません');

  const sfUrl = `${data.instanceUrl}/${data.id}`;
  const properties = buildProperties(data, sfUrl);

  // Notion_Page_ID__c に値があれば直接更新、なければ SalesForce URL で検索
  let existingPageId = data.notionPageId || null;
  if (!existingPageId) {
    existingPageId = findExistingPage(sfUrl, token);
  }

  if (existingPageId) {
    updatePage(existingPageId, properties, token);
    console.log(`更新完了: pageId=${existingPageId}, name=${getFullName(data)}`);
    return { action: 'updated', pageId: existingPageId, sfUrl };
  } else {
    const pageId = createPage(properties, token);
    // 新規作成時は Notion Page ID をログに出力（SF への書き戻しは手動 or 別途実装）
    console.log(`新規作成完了: pageId=${pageId}, name=${getFullName(data)}`);
    console.log(`→ SF レコード ${data.id} の Notion_Page_ID__c に "${pageId}" を設定してください`);
    return { action: 'created', pageId, sfUrl };
  }
}

function getFullName(data) {
  return [data.lastName, data.firstName].filter(Boolean).join(' ');
}


// ─── Notion API ───────────────────────────────────────────────────────────────

function notionHeaders(token) {
  return {
    'Authorization': `Bearer ${token}`,
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json',
  };
}

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

function buildProperties(data, sfUrl) {
  const props = {};

  // 名前（姓 + 名）
  const fullName = getFullName(data);
  if (fullName) {
    props['名前'] = { title: [{ text: { content: fullName } }] };
  }

  // SalesForce URL（照合キー）
  props['SalesForce'] = { url: sfUrl };

  // 姓（ふりがな）
  if (data.furiganaLastName) {
    props['姓（ふりがな）'] = { rich_text: [{ text: { content: data.furiganaLastName } }] };
  }

  // 名（ふりがな）
  if (data.furiganaFirstName) {
    props['名（ふりがな）'] = { rich_text: [{ text: { content: data.furiganaFirstName } }] };
  }

  // 生年月日
  if (data.birthdate && data.birthdate !== 'null' && data.birthdate !== '') {
    props['生年月日'] = { date: { start: data.birthdate } };
  }

  // ポジション（現職）
  if (data.currentPosition) {
    props['ポジション'] = { rich_text: [{ text: { content: data.currentPosition } }] };
  }

  // 現年収（実数 / 円）
  if (data.currentSalary != null && data.currentSalary !== '' && data.currentSalary !== 'null') {
    props['現年収'] = { number: Number(data.currentSalary) };
  }

  // 最低希望年収（実数 / 円）
  if (data.targetSalary != null && data.targetSalary !== '' && data.targetSalary !== 'null') {
    props['最低希望年収'] = { number: Number(data.targetSalary) };
  }

  // 転職検討理由（転職軸）
  if (data.jobChangeReason && data.jobChangeReason !== 'null') {
    props['転職検討理由'] = { rich_text: [{ text: { content: data.jobChangeReason } }] };
  }

  // 副業希望（ピックリスト → checkbox）
  if (data.sideWork != null && data.sideWork !== '' && data.sideWork !== 'null') {
    const sideWorkBool = SIDE_WORK_POSITIVE_VALUES.includes(String(data.sideWork).toLowerCase());
    props['副業希望'] = { checkbox: sideWorkBool };
  }

  return props;
}

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

function updatePage(pageId, properties, token) {
  // 名前は更新しない（既存値を保持）
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


// ─── ローカルテスト用 ─────────────────────────────────────────────────────────

function testSync() {
  const testData = {
    secret: PropertiesService.getScriptProperties().getProperty('GAS_SECRET'),
    id: '0031234567890ABC',
    instanceUrl: 'https://your-domain.salesforce.com',  // ← 実際の URL に変更
    notionPageId: '',
    lastName: '山田',
    firstName: '太郎',
    furiganaLastName: 'やまだ',
    furiganaFirstName: 'たろう',
    birthdate: '1990-05-15',
    currentPosition: 'エンジニアリングマネージャー',
    currentSalary: 8000000,
    targetSalary: 10000000,
    jobChangeReason: 'キャリアアップ・マネジメント経験を積みたい',
    sideWork: '希望する',
  };

  const result = syncToNotion(testData);
  console.log('テスト結果:', JSON.stringify(result));
}
