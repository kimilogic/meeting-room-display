(function () {
    "use strict";
    var pollTimer = null;
    var inFlight = false;
    var lastSuccess = null;
    var clockBase = null;
    var clockReceived = null;
    var utcOffset = 540;
    var roomMatch = /^\/room\/([a-z0-9]+(?:-[a-z0-9]+)*)\/?$/.exec(window.location.pathname);
    var roomId = roomMatch ? roomMatch[1] : "idoutai";
    var apiUrl = "/api/room/" + encodeURIComponent(roomId) + "/status";
    var lastData = null;
    var connectionOk = false;
    var boundaryTimer = null;

    function updateControls() {
        if (roomId !== "idoutai") { return; }
        var panel = byId("ad-hoc-controls");
        var start = byId("ad-hoc-start");
        var stop = byId("ad-hoc-stop");
        if (!panel || !start || !stop) { return; }
        var active = lastData && lastData.ad_hoc_active === true;
        var free = lastData && lastData.status === "free" && lastData.is_demo === false;
        panel.style.display = active || free ? "block" : "none";
        start.style.display = free && !active ? "inline-block" : "none";
        stop.style.display = active ? "inline-block" : "none";
        start.disabled = inFlight || !connectionOk || !lastData || !lastData.ad_hoc_can_start;
        stop.disabled = inFlight || !connectionOk || !active;
    }

    function byId(id) { return document.getElementById(id); }
    function put(id, value) { byId(id).textContent = value; }
    function pad(value) { return value < 10 ? "0" + value : String(value); }
    function organizerText(event) {
        return event && typeof event.organizer === "string" && event.organizer ? event.organizer : "－";
    }
    function tick() {
        if (clockBase === null) { return; }
        var time = new Date(clockBase + (new Date().getTime() - clockReceived) + utcOffset * 60000);
        put("clock", pad(time.getUTCHours()) + ":" + pad(time.getUTCMinutes()) + ":" + pad(time.getUTCSeconds()));
        put("date", time.getUTCFullYear() + "年" + pad(time.getUTCMonth() + 1) + "月" + pad(time.getUTCDate()) + "日（" + "日月火水木金土".charAt(time.getUTCDay()) + "）");
        if (connectionOk && lastData && lastData.status === "ad-hoc" && typeof lastData.ad_hoc_started_epoch_ms === "number") {
            var started = new Date(lastData.ad_hoc_started_epoch_ms + utcOffset * 60000);
            var minutes = Math.max(0, Math.floor((clockBase + new Date().getTime() - clockReceived - lastData.ad_hoc_started_epoch_ms) / 60000));
            put("status-detail", "使用開始 " + pad(started.getUTCHours()) + ":" + pad(started.getUTCMinutes()) + " · 経過 " + minutes + "分");
        }
    }
    function eventValid(event) {
        return event && typeof event.title === "string" && typeof event.time_text === "string" &&
            (event.state === "current" || event.state === "past" || event.state === "upcoming");
    }
    function validate(data) {
        if (data && data.room_id && data.room_id !== roomId) { throw new Error("Unexpected room"); }
        if (!data || (data.status !== "busy" && data.status !== "free" && data.status !== "unknown" && data.status !== "ad-hoc") ||
                typeof data.room_name !== "string" || typeof data.status_text !== "string" ||
                typeof data.status_detail !== "string" || typeof data.updated_text !== "string" ||
                typeof data.server_epoch_ms !== "number" || !isFinite(data.server_epoch_ms) ||
                typeof data.utc_offset_minutes !== "number" || !isFinite(data.utc_offset_minutes) ||
                Object.prototype.toString.call(data.events) !== "[object Array]" ||
                (data.current !== null && !eventValid(data.current)) ||
                (data.next !== null && !eventValid(data.next))) { throw new Error("Invalid display data"); }
        var i;
        for (i = 0; i < data.events.length; i += 1) {
            if (!eventValid(data.events[i])) { throw new Error("Invalid event"); }
        }
    }
    function render(data) {
        validate(data);
        lastData = data;
        connectionOk = true;
        window.clearTimeout(boundaryTimer);
        if (roomId === "idoutai" && data.ad_hoc_active && typeof data.ad_hoc_next_reservation_ms === "number") {
            boundaryTimer = window.setTimeout(retrieve, Math.min(86400000, Math.max(50, data.ad_hoc_next_reservation_ms - data.server_epoch_ms + 50)));
        }
        var list = document.createDocumentFragment();
        var i, row, times, title, mark, organizer;
        for (i = 0; i < data.events.length; i += 1) {
            row = document.createElement("li");
            row.className = data.events[i].state;
            times = document.createElement("span");
            times.className = "agenda-time";
            times.textContent = data.events[i].time_text;
            title = document.createElement("span");
            title.textContent = data.events[i].title;
            row.appendChild(times);
            row.appendChild(title);
            organizer = document.createElement("span");
            organizer.className = "agenda-organizer";
            organizer.textContent = "予約者：" + organizerText(data.events[i]);
            row.appendChild(organizer);
            if (data.events[i].state === "current") {
                mark = document.createElement("span");
                mark.className = "agenda-mark";
                mark.textContent = "使用中";
                row.appendChild(mark);
            }
            list.appendChild(row);
        }
        if (data.events.length === 0) {
            row = document.createElement("li");
            row.textContent = data.events_empty_text || "本日の予定はありません";
            list.appendChild(row);
        }
        put("room-name", data.room_name);
        put("source-label", data.is_demo ? "ダミーデータ" : "Microsoft 365");
        put("status-text", data.status_text);
        put("status-detail", data.status_detail);
        byId("status-panel").className = "status-panel " + data.status;
        put("current-time", data.current ? data.current.time_text : "—");
        put("current-title", data.current ? data.current.title : "現在の予定はありません");
        put("current-organizer", "予約者：" + organizerText(data.current));
        put("next-time", data.next ? data.next.time_text : "—");
        put("next-title", data.next ? data.next.title : (data.next_empty_text || "本日の残りの予定はありません"));
        put("next-organizer", "予約者：" + organizerText(data.next));
        while (byId("events").firstChild) { byId("events").removeChild(byId("events").firstChild); }
        byId("events").appendChild(list);
        put("updated", "最終更新 " + data.updated_text);
        clockBase = data.server_epoch_ms;
        utcOffset = data.utc_offset_minutes;
        clockReceived = new Date().getTime();
        lastSuccess = data.updated_text === "—" ? null : data.updated_text;
        tick();
        byId("connection").className = data.warning ? "connection failed" : "connection";
        put("connection", data.connection_text || "接続中");
        updateControls();
    }
    function failure() {
        connectionOk = false;
        updateControls();
        byId("connection").className = "connection failed";
        put("connection", lastSuccess ? "通信失敗 · 状態・予定は前回取得時点の内容です。自動再試行します。" : "通信失敗 · 予定を取得できません。自動再試行します。");
        if (roomId === "idoutai" && lastData && lastData.ad_hoc_active) {
            put("status-text", "状態未確認");
            put("status-detail", "通信を確認しています（臨時使用の記録あり）");
            byId("status-panel").className = "status-panel unknown";
        }
        if (!lastSuccess) {
            put("status-text", "未取得");
            put("status-detail", "接続を確認してください");
        }
    }
    function retrieve(action) {
        if (action !== "start" && action !== "stop") { action = null; }
        if (action && roomId !== "idoutai") { return; }
        if (inFlight) { return; }
        window.clearTimeout(pollTimer);
        inFlight = true;
        updateControls();
        var xhr = new XMLHttpRequest();
        var done = false;
        var watchdog;
        var started = new Date().getTime();
        function finish(ok) {
            if (done) { return; }
            done = true;
            window.clearTimeout(watchdog);
            if (ok) {
                try { render(JSON.parse(xhr.responseText)); } catch (error) { failure(); }
            } else if (action && (xhr.status === 409 || xhr.status === 503)) {
                try {
                    var rejected = JSON.parse(xhr.responseText);
                    render(rejected);
                    byId("connection").className = "connection failed";
                    put("connection", rejected.operation_error || "操作を完了できませんでした");
                } catch (error) { failure(); }
            } else {
                failure();
                if (action) { put("connection", "操作結果を確認できません。自動再送はせず、状態を再取得します。"); }
            }
            inFlight = false;
            updateControls();
            pollTimer = window.setTimeout(retrieve, Math.max(1000, 15000 - (new Date().getTime() - started)));
        }
        watchdog = window.setTimeout(function () { finish(false); xhr.abort(); }, 8000);
        xhr.onreadystatechange = function () {
            if (xhr.readyState === 4) { finish(xhr.status === 200); }
        };
        xhr.onerror = function () { finish(false); };
        try {
            xhr.open(action ? "POST" : "GET", action ? "/api/room/idoutai/ad-hoc/" + action : apiUrl + "?_=" + started, true);
            if (action) { xhr.setRequestHeader("X-Room-Action", "1"); }
            xhr.send(null);
        } catch (error) { finish(false); }
    }
    window.setInterval(tick, 1000);
    if (roomId === "idoutai") {
        if (byId("ad-hoc-start")) { byId("ad-hoc-start").onclick = function () { retrieve("start"); }; }
        if (byId("ad-hoc-stop")) { byId("ad-hoc-stop").onclick = function () { retrieve("stop"); }; }
    }
    window.addEventListener("pageshow", retrieve, false);
    window.addEventListener("online", retrieve, false);
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) { retrieve(); }
    }, false);
    retrieve();
}());
