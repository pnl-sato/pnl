// ====== 設定（しきい値のみここで変更）======
// しきい値はスクリプトプロパティでも上書き可能:
//   UPPER_THRESHOLD … この値を超えたら「換気してください」(デフォルト: 1000)
//   LOWER_THRESHOLD … この値を下回ったら「換気終了OK」       (デフォルト: 700)
const DEFAULT_UPPER_THRESHOLD = 1000;
const DEFAULT_LOWER_THRESHOLD = 700;
const RETRIES = 3;            // API失敗時の再試行回数
const RETRY_WAIT_MS = 2000;   // 再試行の待ち時間

// ====== プロパティ取得 ======
function getProp_(key) {
  const v = PropertiesService.getScriptProperties().getProperty(key);
  if (!v) throw new Error(`Script property ${key} is not set`);
  return v;
}

// しきい値はオプションなのでデフォルト値を持たせる
function getThresholds_() {
  const props = PropertiesService.getScriptProperties();
  const upper = Number(props.getProperty('UPPER_THRESHOLD') || DEFAULT_UPPER_THRESHOLD);
  const lower = Number(props.getProperty('LOWER_THRESHOLD') || DEFAULT_LOWER_THRESHOLD);
  if (isNaN(upper) || isNaN(lower)) {
    throw new Error('UPPER_THRESHOLD / LOWER_THRESHOLD must be numeric');
  }
  if (lower >= upper) {
    throw new Error(`LOWER_THRESHOLD (${lower}) must be less than UPPER_THRESHOLD (${upper})`);
  }
  return { upper, lower };
}

// ====== 署名ヘッダー作成（v1.1準拠）======
function buildHeaders_() {
  const TOKEN  = getProp_('SWITCHBOT_TOKEN');
  const SECRET = getProp_('SWITCHBOT_SECRET');

  const t = Date.now().toString();
  // UUIDだとハイフンが入ることがあるので英数字だけに整形
  const rawNonce = Utilities.getUuid();
  const nonce = rawNonce.replace(/[^a-zA-Z0-9]/g, '');

  const data = TOKEN + t + nonce;
  const signBytes = Utilities.computeHmacSha256Signature(data, SECRET);
  const sign = Utilities.base64Encode(signBytes).toUpperCase();

  return {
    headers: {
      'Authorization': TOKEN,
      'sign': sign,
      't': t,
      'nonce': nonce
    }
  };
}

// ====== 再試行つきFetch（本文ログ用にmuteHttpExceptions:true）======
function fetchWithRetry_(url, opt = {}, retries = RETRIES) {
  for (let i = 0; i < retries; i++) {
    const res = UrlFetchApp.fetch(url, {
      method: opt.method || 'get',
      headers: opt.headers,
      muteHttpExceptions: true,
      contentType: opt.contentType,
      payload: opt.payload
    });
    const code = res.getResponseCode();
    Logger.log(`HTTP ${code}: ${res.getContentText()}`);
    if (code === 200) return res;

    // 認証・ルート不正は再試行しても無駄なので即終了
    if (code === 401 || code === 403 || code === 404) {
      throw new Error(`SwitchBot API fatal error ${code}`);
    }
    Utilities.sleep(RETRY_WAIT_MS);
  }
  throw new Error('Max retries reached');
}

// ====== Slack通知 ======
function sendSlackAlert_(text) {
  const webhook = getProp_('SLACK_WEBHOOK');
  const res = UrlFetchApp.fetch(webhook, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ text })
  });
  Logger.log(`Slack ${res.getResponseCode()}: ${res.getContentText()}`);
}

// ====== メイン（上下限ロック式）======
function checkCO2() {
  try {
    const DEVICE_ID = getProp_('DEVICE_ID');
    const url = `https://api.switch-bot.com/v1.1/devices/${DEVICE_ID}/status`;
    const { upper, lower } = getThresholds_();

    const headers = buildHeaders_();
    const res = fetchWithRetry_(url, headers);
    const body = JSON.parse(res.getContentText());
    const co2 = body && body.body && body.body.CO2;

    if (typeof co2 !== 'number') {
      throw new Error(`CO2 not found in response: ${res.getContentText()}`);
    }
    Logger.log(`CO2=${co2} (upper=${upper}, lower=${lower})`);

    const stateKey = 'alertState'; // 'alerted' のときは換気中ロック
    const state = PropertiesService.getScriptProperties().getProperty(stateKey);

    // 上限超え → 換気お願い（ロックON）
    if (co2 >= upper && state !== 'alerted') {
      sendSlackAlert_(`⚠️ CO2 ${co2} ppm。換気してください。`);
      PropertiesService.getScriptProperties().setProperty(stateKey, 'alerted');
      return;
    }

    // 下限到達 → 換気終了（ロックOFF）
    if (co2 <= lower && state === 'alerted') {
      sendSlackAlert_(`✅ CO2 ${co2} ppm。換気を終了できます。`);
      PropertiesService.getScriptProperties().deleteProperty(stateKey);
      return;
    }

    // 中間域 or 状態維持 → 何もしない
  } catch (e) {
    // エラーはSlackにも通知しておくと運用がラク
    Logger.log(`ERROR: ${e.message}`);
    try {
      sendSlackAlert_(`🛠 エラー: ${e.message}`);
    } catch (_) {}
    // ここでthrowしない＝トリガーは継続
  }
}

// ====== しきい値設定ヘルパー（スクリプトエディタから手動実行）======
// upper / lower を変えたいときはここを編集して一度実行する
function configureThresholds() {
  const upper = 1000; // ← 変更したい値に書き換えて実行
  const lower = 700;  // ← 変更したい値に書き換えて実行

  if (lower >= upper) {
    throw new Error(`lower (${lower}) must be less than upper (${upper})`);
  }

  PropertiesService.getScriptProperties().setProperties({
    UPPER_THRESHOLD: String(upper),
    LOWER_THRESHOLD: String(lower)
  });
  Logger.log(`Thresholds set: upper=${upper}, lower=${lower}`);
}
