// YouTube Data API v3とGASでSpreadSheetをいじる練習をしながら
// REALITYの歌ってみたリストをつくりたい話
// https://docs.google.com/spreadsheets/d/1wKa3VCHcaDbEfxKouLfGT7m42VRUS7cGB1JfhBHfPY4
let DAYS = 14; // 日数指定
// 1件(channel/playlist)あたりのAPIページ取得上限。巨大リストでのハング/タイムアウト防止。
// 削除・非公開エントリが多いプレイリストは中身がスカスカのページが増え、実本数が少なくてもページ数が嵩むため、
// 少し余裕を持たせている（40ページ≒最大2000スロット、実行時間は1件あたり数秒程度）。
const MAX_PAGES_PER_ID = 40;
// 空ページ（itemsが空）がこの回数連続したら打ち切る。削除/非公開が多いプレイリストで、
// 実アイテムを取り切った後もAPIが「空items＋継続トークン」を延々返し続ける癖への対処。
const MAX_EMPTY_PAGES = 2;
const LOG_SS_ID = "1wKa3VCHcaDbEfxKouLfGT7m42VRUS7cGB1JfhBHfPY4"; // ログ書き込み先スプレッドシートID
var LOG_BUFFER = []; // ログをためて最後に一括書き込みするためのバッファ
var QUOTA_EXCEEDED = false; // quota超過を検知したら立てる。以降のAPI呼び出しを止めて連鎖失敗を防ぐ

// channelId/playlistId が無効（トリム後4文字未満）の際にはスキップ対象と判定する。
// （実在するchannelId/playlistId/ハンドルはいずれも3文字以上あるため）
function isInvalidId(id) {
  var s = (id == null ? "" : id + "").trim();
  return s.length < 4;
}

// APIエラーの種類判定。quota超過とnotFound（削除/非公開/無効）を区別する。
function isQuotaError(e) {
  var s = (e == null ? "" : e + "");
  return s.indexOf("quota") >= 0 || s.indexOf("exceeded your") >= 0;
}
function isNotFoundError(e) {
  var s = (e == null ? "" : e + "");
  return s.indexOf("cannot be found") >= 0 || s.indexOf("not be found") >= 0;
}

// スプレッドシートにメニュー追加
function onOpen() {  
  var entries = [
    {name: "チャンネルの動画をリストアップ", functionName: "listByChannelId"},
    {name: "プレイリストの動画をリストアップ", functionName: "listByPlaylistId"},
    {name: "チャンネルとプレイリストの最近" + DAYS + "日以内の動画をリストアップ", functionName: "listByChannelorPlaylistId"},
    ];
  SpreadsheetApp.getActiveSpreadsheet().addMenu("YouTube", entries);
}

// ハンドル（@なんたら）からchannelIdを返す。
// まずChannels.list(forHandle)（quota 1）で解決し、見つからない時だけ従来のSearch.list（quota 100）にフォールバック。
function getChannelIdFromKeyword(value) {
  var handle = (value == null ? "" : value + "").trim();

  // 1) forHandleで直接解決（quota 1）
  try {
    var res = YouTube.Channels.list('id', { forHandle: handle });
    if (res.items && res.items.length > 0) {
      mylog("forHandle=" + handle + " channelId=" + res.items[0].id);
      return res.items[0].id;
    }
    mylog("forHandle=" + handle + " 見つからず、Search.listにフォールバック");
  } catch(e) {
    mylog("forHandle=" + handle + " error: " + e + " Search.listにフォールバック");
  }

  // 2) フォールバック: キーワード検索（quota 100。稀にしか通らない想定）
  try {
    var res = YouTube.Search.list('snippet',{
      maxResults: 10,
      type: `channel`,
      q: handle
    });

    mylog("q=" + handle + " length=" + res.items.length);
    for (var i = 0; i < res.items.length; i++) {
      mylog("[" + i + "] channelId=" + res.items[i].id.channelId
       + " title=" + res.items[i].snippet.channelTitle
       + " description=" + res.items[i].snippet.description);
    }

    if (res.items.length > 0) {
      return res.items[0].id.channelId;
    }
  } catch(e) {
    // do nothing
  }
  return null;
}

// channelIdまたはハンドルから検索結果でvideoリストをつくる
// アクティブなセルに書かれた文字列をchannelIdまたはハンドルとして読み取り、2行目からリストを生成する。
function listByChannelId() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var value = sheet.getActiveCell().getValue();
  mylog("activeCellValue=" + value);

  // 無効（3文字未満）の際にはスキップする
  if (isInvalidId(value)) {
    mylog("skip, invalid channelId");
    flushLog();
    return;
  }

  // @から始まる際にはChannelIdじゃなくてハンドルとして検索からChannelId化を試みる
  if ((value + "").startsWith("@")) {
    value = getChannelIdFromKeyword(value);
    sheet.getActiveCell().setValue(value); // update
  }

  appendLines(sheet, 2, getVideosFromChannel(value), {});
  flushLog();
}

// playlistIdからvideoリストをつくる
// アクティブなセルに書かれた文字列をchannelIdとして読み取り、2行目からリストを生成する。
function listByPlaylistId() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var value = sheet.getActiveCell().getValue();
  mylog("activeCellValue=" + value);

  // 無効（3文字未満）の際にはスキップする
  if (isInvalidId(value)) {
    mylog("skip, invalid playlistId");
    flushLog();
    return;
  }

  appendLines(sheet, 2, getVideosFromPlaylistId(value), {});
  flushLog();
}

// channdlIdまたはPlaylistIdのリストからDAYS日以内のvideoリストをつくる
// アクティブなセルの文字列リストを読み取り、"output"シートの2行目から結果を書き込む
// "PL"から始まる文字列の際にPlaylistIdと判断し、それ以外をchannelIdとする
function listByChannelorPlaylistId() {
  var activeSheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var outputSheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("output");
  var values = activeSheet.getActiveRange().getValues();
  mylog("activeCellValues.Count=" + values.length);

  var line = 2;
  var processed = 0; // 処理した人数（10人ごとにログを書き出すためのカウンタ）
  for (var i = 0; i < values.length; i++) {
    // quota超過を検知したら以降は全て失敗するので中断（赤行の量産を防ぐ）
    if (QUOTA_EXCEEDED) {
      myalert("quota超過のため中断。未処理=" + (values.length - i) + "件");
      break;
    }

    var id = values[i][0] + ""; // 選択したセルの左端がidとする
    mylog("id=" + id);

    // 無効（3文字未満）の際にはその人をスキップする
    if (isInvalidId(id)) {
      mylog("skip, invalid id");
      continue;
    }

    var artist = {};
    if (values[i].length >= 5) {
      // 選択したセルの横幅が4以上の際、id, name, REALITY profile URL, Twitter URL, YouTube ch URLとする
      artist["id"] = id;
      artist["name"] = values[i][1];
      artist["reality_url"] = values[i][2];
      artist["twitter_url"] = values[i][3];
      artist["youtube_url"] = values[i][4];
      mylog("name=" + artist["name"] + " reality=" + artist["reality_url"] + " twitter=" + artist["twitter_url"] + " youtube=" + artist["youtube_url"]);
    }

    // 最近N日に絞る
    var dateDaysAgo = new Date();
    dateDaysAgo.setDate(dateDaysAgo.getDate() - DAYS);

    if (id.indexOf("PL") === 0) { // Playlist id
      line = appendLines(outputSheet, line, getVideosFromPlaylistId(id, dateDaysAgo), artist);
    } else { // Channel id
      line = appendLines(outputSheet, line, getVideosFromChannel(id, dateDaysAgo), artist);
    }

    // 全員終わるまで待たず、10人ごとに途中経過のログを書き出す
    processed++;
    if (processed % 10 === 0) {
      flushLog();
    }
  }
  flushLog(); // 残り（10人未満の端数）を書き出す
}

// 人（artist）ごとにまとめて一括書き込みする。
// 動画1本ずつsetValueするとAPI往復が多くタイムアウトするため、
// 一旦メモリ上で2次元配列を組み立て、setValuesでまとめて書き込む。
function appendLines(sheet, startLine, videoItems, artist) {
  // 書き込む行データをメモリ上に組み立てる
  var rowsAtoH = []; // 1〜8列（name, reality, twitter, youtube, date, image, title, url）
  var rowsJtoK = []; // 10〜11列（登録日, ダミー改行）。9列目のnoteは触らない
  var nowDate = Utilities.formatDate(new Date(), "JST", "yyyy/MM/dd");

  for (var j = 0; j < videoItems.length; j++) {
    var video = videoItems[j];
    if (video == null) {
      continue;
    }
    // private属性が確認できない、publicの際のみappendする
    if (video.status != null && video.status.privacyStatus == "private") {
      continue;
    }
    var row = buildRow(video, artist, nowDate);
    if (row == null) {
      continue; // サムネなし/削除動画などはスキップ
    }
    rowsAtoH.push(row.atoH);
    rowsJtoK.push(row.jtoK);
  }

  if (rowsAtoH.length === 0) {
    return startLine;
  }

  // 4列目が空の最初の行を1回のバッチ読みで探す
  var line = findNextBlankLine(sheet, startLine, 4);

  // 人ごとに一括書き込み（setValuesは2回だけ）
  sheet.getRange(line, 1, rowsAtoH.length, 8).setValues(rowsAtoH);
  sheet.getRange(line, 10, rowsJtoK.length, 2).setValues(rowsJtoK);

  return line + rowsAtoH.length;
}

// 1本の動画から書き込み用の行データ（配列）を組み立てる。書き込まない動画はnullを返す。
function buildRow(video, artist, nowDate) {
  var snippet = video.snippet;
  if (!snippet.thumbnails["default"] || !snippet.thumbnails["default"].url || snippet.title == "Deleted video") {
    return null;
  }
  mylog(snippet.title);
  var videoId = "";
  // 動画公開日、Search APIではsnippet.publishedAtで取得できる
  var date = Utilities.formatDate(new Date(snippet.publishedAt), "JST", "yyyy/MM/dd");
  if (video.id != null) {
    videoId = video.id.videoId;
  }
  if (video.contentDetails != null) {
    // 動画公開日、PlaylistItems APIではcontentDetails.videoPublishedAtで取得できる。
    // snippet.publishedAtはプレイリストへの追加日で違うので上書きする。
    date = Utilities.formatDate(new Date(video.contentDetails.videoPublishedAt), "JST", "yyyy/MM/dd");
    videoId = video.contentDetails.videoId;
  }

  var name = "", realityUrl = "", twitterUrl = "", youtubeUrl = "";
  if (artist != null) {
    name = artist["name"] != null ? artist["name"] : "";
    realityUrl = artist["reality_url"] != null ? artist["reality_url"] : "";
    twitterUrl = artist["twitter_url"] != null ? artist["twitter_url"] : "";
    youtubeUrl = artist["youtube_url"] != null ? artist["youtube_url"] : "";
  }
  var imageUrl = snippet.thumbnails["default"].url;

  return {
    atoH: [
      name,
      realityUrl,
      twitterUrl,
      youtubeUrl,
      date,
      "=IMAGE(\"" + imageUrl + "\")", // セル内に画像を貼る
      snippet.title,
      "https://www.youtube.com/watch?v=" + videoId
    ],
    jtoK: [
      nowDate,        // register date
      "\n\n\n\n"      // dummy \n x4
    ]
  };
}

// startLine以降でcol列が空の最初の行番号を1回のバッチ読みで探す
function findNextBlankLine(sheet, startLine, col) {
  var lastRow = sheet.getLastRow();
  if (startLine > lastRow) {
    return startLine;
  }
  var values = sheet.getRange(startLine, col, lastRow - startLine + 1, 1).getValues();
  for (var i = 0; i < values.length; i++) {
    if (values[i][0] === "" || values[i][0] === null) {
      return startLine + i;
    }
  }
  return lastRow + 1;
}
    
// channelId(UC...)をアップロード用プレイリストID(UU...)に変換する。
// UCで始まらない際はnullを返す。
function toUploadsPlaylistId(channelId) {
  var s = (channelId == null ? "" : channelId + "").trim();
  if (s.indexOf("UC") === 0) {
    return "UU" + s.substring(2);
  }
  return null;
}

// チャンネルの動画リストを取得する。
// Search.list(quota 100)は使わず、アップロード用プレイリストをPlaylistItems.list(quota 1)で読む。
// アップロードは新しい順に並ぶので、日付フィルタを超えたら早期に打ち切る(stopWhenOld=true)。
function getVideosFromChannel(channelId, publishedAfterDate) {
  var uploadsPlaylistId = toUploadsPlaylistId(channelId);
  if (uploadsPlaylistId == null) {
    myalert("channelId=" + channelId + " uploadsプレイリスト変換に失敗（UC始まりでない/無効の可能性）");
    return [];
  }
  return getVideosFromPlaylistId(uploadsPlaylistId, publishedAfterDate, true);
}

// stopWhenOld=trueの際、publishedAfterDateより古い動画に達したらページングを打ち切る。
// アップロード用プレイリストのように新しい順に並ぶものにのみ指定すること
// （手動プレイリストは時系列順とは限らないので指定しない）。
function getVideosFromPlaylistId(playlistId, publishedAfterDate, stopWhenOld) {
  // 既にquota超過を検知していたら、API呼び出しをせず即返す（連鎖失敗と赤行の量産を防ぐ）
  if (QUOTA_EXCEEDED) {
    return [];
  }
  var nextToken = "";
  var items = [];
  var done = false;
  var pages = 0;
  var emptyPages = 0; // 連続した空ページの数
  while (nextToken != null && !done && !QUOTA_EXCEEDED) {
   try {
    var res = YouTube.PlaylistItems.list('snippet,status,contentDetails',{
        maxResults: 50,
        playlistId: playlistId,
        pageToken: nextToken
      });
      for (var i = 0; i < res.items.length; i++) {
        // publishedAfterDateが指定されていたら、それより前の動画は追加しない
        if (publishedAfterDate != null) {
          // videoPublishedAt が欠損/不正な際も「古い」扱いにして早期打ち切りを効かせる（ハング防止）
          var raw = res.items[i].contentDetails ? res.items[i].contentDetails.videoPublishedAt : null;
          var videoPublishedDate = raw ? new Date(raw) : null;
          var isOld = (videoPublishedDate == null) || isNaN(videoPublishedDate.getTime()) || (publishedAfterDate > videoPublishedDate);
          if (isOld){
            mylog ("skip, old videoPublishedAt=" + videoPublishedDate + " title=" + res.items[i].snippet.title);
            if (stopWhenOld) {
              // 新しい順に並ぶ前提。古いものが出たら以降も全て古いのでこれ以上ページを取らない
              done = true;
              break;
            }
            continue;
          }
        }
        items.push(res.items[i]);
      }
      nextToken = res.nextPageToken;

      // 空ページが連続したら打ち切る（削除/非公開が多いPLでAPIが空items＋継続トークンを返し続ける癖への対処）。
      // 正常終了扱いなので赤アラートにはしない。
      if (res.items.length === 0) {
        emptyPages++;
        if (emptyPages >= MAX_EMPTY_PAGES) {
          mylog("playlistId=" + playlistId + " 空ページが" + emptyPages + "回連続したため打ち切り（取得=" + items.length + "件）");
          break;
        }
      } else {
        emptyPages = 0;
      }

      // ページ数の安全上限。巨大プレイリスト/チャンネルで延々ページングしてタイムアウトするのを防ぐ
      pages++;
      if (pages >= MAX_PAGES_PER_ID) {
        myalert("playlistId=" + playlistId + " ページ数上限(" + MAX_PAGES_PER_ID + ")で打ち切り（巨大すぎる可能性。取得=" + items.length + "件）");
        break;
      }
   } catch(e) {
     if (isQuotaError(e)) {
       // quota超過。以降のAPI呼び出しは全て失敗するので実行全体を中断させる
       QUOTA_EXCEEDED = true;
       myalert("quota超過を検知（以降のAPI呼び出しを中断）: " + e);
     } else if (isNotFoundError(e)) {
       // 本当に見つからない＝削除/非公開/無効なプレイリスト
       myalert("playlistId=" + playlistId + " 見つからない（削除/非公開/無効）: " + e);
     } else {
       myalert("playlistId=" + playlistId + " APIエラー: " + e);
     }
     break;
   }
  }
  // 日付フィルタなしで0件はプレイリストが無効/閉鎖の可能性が高いのでアラート
  if (items.length === 0 && publishedAfterDate == null && !QUOTA_EXCEEDED) {
    myalert("playlistId=" + playlistId + " count=0（無効/閉鎖の可能性）");
  } else {
    mylog("playlistId=" + playlistId + " count=" + items.length);
  }
  return items;
}

const ALERT_FONT_COLOR = "#cc0000"; // アラート行の文字色（赤）。背景色と違い、内容を削除すると見た目も消える
const ALERT_MARK = "🔴 "; // アラート行の値の先頭に付けるマーカー。書式でなく内容なので削除で完全に消える

// debug用。ログはメモリのLOG_BUFFERにためておき、flushLog()でまとめて1回だけ書き込む。
// 以前は呼び出しごとにopenById+appendRowしており、動画数ぶんネットワーク往復が発生してタイムアウトの原因だった。
function mylog(value) {
  console.log(value);
  LOG_BUFFER.push({date: new Date(), value: value, alert: false});
}

// アラート用のログ。CHANNEL/PLAYLIST IDが無効・閉鎖だった等の際に使い、flushLog()で赤文字＋先頭マーカーにする。
function myalert(value) {
  console.error(value);
  LOG_BUFFER.push({date: new Date(), value: ALERT_MARK + value, alert: true});
}

// ためたログを一括で書き出す。各メニュー関数の最後（return直前）で呼ぶこと。
// openByIdの引数は書き込み権限のある（本スクリプトを動作させる）スプレッドシートのIDに書き換えて使う。
function flushLog() {
  if (LOG_BUFFER.length === 0) {
    return;
  }
  try {
    var ss = SpreadsheetApp.openById(LOG_SS_ID);
    var sheet = ss.getSheetByName("log");
    var startRow = sheet.getLastRow() + 1;
    var values = LOG_BUFFER.map(function(e) { return [e.date, e.value]; });
    sheet.getRange(startRow, 1, values.length, 2).setValues(values);
    // アラート行だけ文字色を赤くする（背景色と違い、内容削除で見た目も消える）。アラートは稀なので個別に着色
    for (var i = 0; i < LOG_BUFFER.length; i++) {
      if (LOG_BUFFER[i].alert) {
        sheet.getRange(startRow + i, 1, 1, 2).setFontColor(ALERT_FONT_COLOR);
      }
    }
  } catch(e) {
    console.log("flushLog error: " + e);
  }
  LOG_BUFFER = [];
}
