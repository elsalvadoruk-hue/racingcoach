/**
 * iRacing 주행 코치 - 세션 로그 접수 창구 (Google Apps Script)
 *
 * 앱의 "보내기" 버튼과 "세션 로그 자동 보내기"가 보낸 로그 파일을 내 Google Drive의 폴더 하나에 저장합니다.
 * 올리기만 받습니다. 이 주소로는 폴더의 파일을 읽거나 지울 수 없습니다.
 *
 * 설치 방법은 같은 폴더의 "설치 방법.md"를 보세요.
 */

// 1) 로그를 모을 Drive 폴더의 ID. 폴더를 열었을 때 주소 맨 끝의 긴 글자입니다.
//    https://drive.google.com/drive/folders/이부분이_ID
var FOLDER_ID = '1abVdFut9s7QYgXljyOgAiEUwmO1c3gR8';

// 2) 받는 한도. 넘으면 받지 않습니다.
var MAX_BYTES = 1500000;      // 파일 하나의 크기
var MAX_PER_DAY = 300;        // 하루에 받는 파일 수 (장난으로 채우는 것을 막습니다)

// 3) 보관 기간. 앱의 자동 보내기 동의 창에 "받은 날로부터 1년이 지나면 지웁니다"라고 적혀 있습니다.
var KEEP_DAYS = 365;

function doPost(e) {
  try {
    var body = JSON.parse(e.postData.contents);
    if (body.format !== 'iracing-coach-log-upload') return reply({ ok: false, error: 'not a coach log' });
    if (!/^coach-log-[A-Za-z0-9-]{1,80}\.zip$/.test(body.name || '')) return reply({ ok: false, error: 'bad name' });

    var bytes = Utilities.base64Decode(body.data || '');
    if (bytes.length < 100 || bytes.length > MAX_BYTES) return reply({ ok: false, error: 'bad size' });

    // 진짜 주행 코치 로그인지: 압축을 풀어 안의 manifest.json을 확인합니다.
    var blob = Utilities.newBlob(bytes, 'application/zip', body.name);
    var manifest = null;
    var files = Utilities.unzip(blob);
    for (var i = 0; i < files.length; i++) {
      if (files[i].getName() === 'manifest.json') manifest = JSON.parse(files[i].getDataAsString('UTF-8'));
    }
    if (!manifest || manifest.format !== 'iracing-coach-log') return reply({ ok: false, error: 'not a coach log' });

    if (!underDailyLimit()) return reply({ ok: false, error: 'daily limit' });

    var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd-HHmmss');
    var file = DriveApp.getFolderById(FOLDER_ID).createFile(blob.setName(stamp + '_' + body.name));
    var note = String(body.note || '').slice(0, 500);
    file.setDescription([
      note ? '메모: ' + note : '메모 없음',
      '게임: ' + (manifest.game || '?') + ' / 트랙: ' + (manifest.track || '?') + ' / 차량: ' + (manifest.car || '?') + ' / 세션: ' + (manifest.session_type || '?'),
      '길이: ' + manifest.minutes + '분 / 앱 버전: ' + (manifest.app_version || body.app_version || '?'),
    ].join('\n'));
    return reply({ ok: true });
  } catch (error) {
    return reply({ ok: false, error: 'could not read the upload' });
  }
}

// 브라우저로 주소를 열었을 때: 살아 있는지만 알려 줍니다.
function doGet() {
  return reply({ ok: true, service: 'iracing-coach-log-upload' });
}

// 보관 기간이 지난 로그를 휴지통으로 옮깁니다. 트리거(일 단위 타이머)로 매일 실행하세요.
function deleteOldLogs() {
  var limit = new Date(Date.now() - KEEP_DAYS * 24 * 3600 * 1000);
  var files = DriveApp.getFolderById(FOLDER_ID).getFiles();
  while (files.hasNext()) {
    var file = files.next();
    if (file.getDateCreated() < limit) file.setTrashed(true);
  }
}

function underDailyLimit() {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var store = PropertiesService.getScriptProperties();
    var today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd');
    var count = store.getProperty('day') === today ? Number(store.getProperty('count') || 0) : 0;
    if (count >= MAX_PER_DAY) return false;
    store.setProperties({ day: today, count: String(count + 1) });
    return true;
  } finally {
    lock.releaseLock();
  }
}

function reply(data) {
  return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
}
