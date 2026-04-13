/**
 * Google カレンダー 作業ブロッカー
 *
 * カレンダーに「Online:」を含む予定が作成・更新されたとき、
 * 前後 30 分に「作業」予定を自動で挿入する。
 *
 *   例: "Online: 候補者Aさん面談" 14:00〜15:00
 *       -> "作業" 13:30〜14:00（前バッファ）
 *       -> "作業" 15:00〜15:30（後バッファ）
 *
 * [セットアップ（初回のみ）]
 * 1. https://script.google.com でプロジェクトを新規作成
 * 2. このファイルの内容を貼り付けて保存
 * 3. 関数リストから「setup」を選択して実行
 *    -> カレンダー変更トリガーが自動登録される
 *
 * 以後はカレンダーを変更するたびに自動で動く。手動実行は runManually() を使う。
 */

var ONLINE_PREFIX   = 'Online:';
var WORK_TITLE      = '作業';
var BUFFER_MINUTES  = 30;
var LOOK_AHEAD_DAYS = 30;

// ===========================================================================
// セットアップ
// ===========================================================================

/**
 * カレンダー変更トリガーを登録する。初回のみ手動で実行すること。
 */
function setup() {
  // 既存の同名トリガーを削除（重複防止）
  ScriptApp.getProjectTriggers().forEach(function(trigger) {
    if (trigger.getHandlerFunction() === 'onEventUpdated') {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  ScriptApp.newTrigger('onEventUpdated')
    .forUserCalendar(Session.getActiveUser().getEmail())
    .onEventUpdated()
    .create();

  Logger.log('トリガーを登録しました。カレンダーの変更を検知して自動実行されます。');
}

// ===========================================================================
// メインハンドラ
// ===========================================================================

/**
 * カレンダーに変更があるたびに自動で呼ばれる。
 * Online: 予定を検索し、前後に「作業」ブロックを挿入する。
 */
function onEventUpdated(e) {
  var calendar = CalendarApp.getDefaultCalendar();
  var now      = new Date();
  var until    = new Date(now.getTime() + LOOK_AHEAD_DAYS * 24 * 60 * 60 * 1000);

  var events = calendar.getEvents(now, until);
  var onlineEvents = events.filter(function(ev) {
    return ev.getTitle().indexOf(ONLINE_PREFIX) !== -1;
  });

  if (onlineEvents.length === 0) return;

  var props = PropertiesService.getScriptProperties();

  onlineEvents.forEach(function(event) {
    // 終日イベントはスキップ
    if (event.isAllDayEvent()) return;

    var eventId     = event.getId();
    var lastUpdated = event.getLastUpdated().toISOString();
    var stateKey    = 'ev_' + eventId;

    // updated が変わっていなければ処理済み -> スキップ
    if (props.getProperty(stateKey) === lastUpdated) return;

    var title    = event.getTitle();
    var start    = event.getStartTime();
    var end      = event.getEndTime();
    var bufferMs = BUFFER_MINUTES * 60 * 1000;

    var preStart = new Date(start.getTime() - bufferMs);
    var postEnd  = new Date(end.getTime() + bufferMs);

    Logger.log('処理中: ' + title + ' (' + formatTime(start) + '〜' + formatTime(end) + ')');

    // 前バッファ
    if (!workBlockExists(calendar, preStart, start)) {
      calendar.createEvent(WORK_TITLE, preStart, start, {
        description: '「' + title + '」の前作業時間'
      });
      Logger.log('  前作業ブロック作成: ' + formatTime(preStart) + '〜' + formatTime(start));
    } else {
      Logger.log('  前作業ブロック: 既に存在、スキップ');
    }

    // 後バッファ
    if (!workBlockExists(calendar, end, postEnd)) {
      calendar.createEvent(WORK_TITLE, end, postEnd, {
        description: '「' + title + '」の後作業時間'
      });
      Logger.log('  後作業ブロック作成: ' + formatTime(end) + '〜' + formatTime(postEnd));
    } else {
      Logger.log('  後作業ブロック: 既に存在、スキップ');
    }

    props.setProperty(stateKey, lastUpdated);
  });
}

// ===========================================================================
// ユーティリティ
// ===========================================================================

/**
 * 指定した時間帯に「作業」イベントが既に存在するか確認する。
 * 開始・終了が完全一致するものだけ対象にする。
 */
function workBlockExists(calendar, start, end) {
  var candidates = calendar.getEvents(start, end);
  return candidates.some(function(ev) {
    return ev.getTitle() === WORK_TITLE
      && ev.getStartTime().getTime() === start.getTime()
      && ev.getEndTime().getTime()   === end.getTime();
  });
}

/**
 * 時刻を "MM/dd HH:mm" 形式にフォーマットする。
 */
function formatTime(date) {
  return Utilities.formatDate(date, Session.getScriptTimeZone(), 'MM/dd HH:mm');
}

// ===========================================================================
// 手動テスト
// ===========================================================================

/**
 * トリガーなしで手動テスト実行する。
 * Apps Script エディタの「実行」ボタンから呼び出す。
 */
function runManually() {
  onEventUpdated(null);
}
