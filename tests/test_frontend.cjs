// Dependency-free transport/DOM simulation, not a real browser or iPad test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness(pathname = '/') {
    const elements = {};
    function element() {
        return {
            textContent: '', className: '', children: [], style: {},
            get firstChild() { return this.children[0]; },
            appendChild(child) { this.children.push(child); },
            removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
        };
    }
    const timers = new Map();
    const requests = [];
    let timerId = 0;
    function XHR() { requests.push(this); }
    XHR.prototype.open = function (method, url, async) {
        this.method = method;
        this.url = url;
        assert.equal(async, true);
        const room = pathname.indexOf('/room/') === 0 ? pathname.split('/')[2] : 'idoutai';
        if (method === 'POST') {
            assert.ok(url === '/api/room/' + room + '/ad-hoc/start' ||
                url === '/api/room/' + room + '/ad-hoc/stop');
        } else {
            assert.equal(method, 'GET');
            assert.ok(url.indexOf('/api/room/' + room + '/status?_=') === 0);
        }
    };
    XHR.prototype.setRequestHeader = function (key, value) { assert.equal(key, 'X-Room-Action'); assert.equal(value, '1'); };
    XHR.prototype.send = function () {};
    XHR.prototype.abort = function () {};
    XHR.prototype.respond = function (body, status = 200) {
        this.status = status;
        this.responseText = JSON.stringify(body);
        this.readyState = 4;
        this.onreadystatechange();
    };
    const window = {
        location: {pathname},
        setTimeout(fn, delay) { timers.set(++timerId, {fn, delay}); return timerId; },
        clearTimeout(id) { timers.delete(id); },
        setInterval() {}, addEventListener() {}
    };
    const document = {
        getElementById(id) { return elements[id] || (elements[id] = element()); },
        createElement: element, createDocumentFragment: element, addEventListener() {}
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8'), {
        window, document, XMLHttpRequest: XHR, Date, JSON, Math, Object, String, Error, isFinite
    });
    function runTimer(min, max) {
        const entry = [...timers.entries()].find(([, t]) => t.delay >= min && t.delay <= max);
        assert.ok(entry, 'expected scheduled retry/poll');
        timers.delete(entry[0]);
        entry[1].fn();
    }
    return {elements, requests, runTimer};
}

const event = {title: '朝会', time_text: '09:00 – 10:00', state: 'current', organizer: '試験予約者'};
const good = {
    status: 'busy', room_name: '表示PoC', status_text: '使用中', status_detail: '10:00まで',
    updated_text: '09/09 09:30:00', server_epoch_ms: 1788913800000,
    utc_offset_minutes: 540, events: [event], current: event, next: null
};
const h = harness();
h.requests[0].respond(good);
assert.equal(h.elements['status-text'].textContent, '使用中');
assert.equal(h.elements['current-title'].textContent, '朝会');
assert.equal(h.elements['current-organizer'].textContent, '予約者：試験予約者');
assert.equal(h.elements['next-organizer'].textContent, '予約者：－');
assert.equal(h.elements.events.children[0].children[0].children[2].textContent, '予約者：試験予約者');
const previousUpdate = h.elements.updated.textContent;
h.runTimer(14000, 15000);
h.requests[1].onerror();
assert.match(h.elements.connection.textContent, /通信失敗/);
assert.equal(h.elements['status-text'].textContent, '使用中');
assert.equal(h.elements.updated.textContent, previousUpdate);
assert.equal(h.elements['current-organizer'].textContent, '予約者：試験予約者');
h.runTimer(14000, 15000);
h.requests[2].respond({...good, status: 'free', status_text: '空室', current: null});
assert.equal(h.elements['status-text'].textContent, '空室');
assert.equal(h.elements.connection.className, 'connection');
h.runTimer(14000, 15000);
h.requests[3].respond({...good, is_demo: false, warning: true, connection_text: '更新遅延', status: 'unknown', status_text: '状態未確認'});
assert.equal(h.elements['status-text'].textContent, '状態未確認');
assert.equal(h.elements['source-label'].textContent, 'Microsoft 365');
assert.equal(h.elements.connection.className, 'connection failed');
assert.equal(h.elements.updated.textContent, previousUpdate);
h.runTimer(14000, 15000);
h.requests[4].respond({...good, is_demo: true, warning: false, connection_text: '接続中', status: 'free', status_text: '空室'});
assert.equal(h.elements['source-label'].textContent, 'ダミーデータ');
h.runTimer(14000, 15000);
h.requests[5].respond({});
assert.equal(h.elements['status-text'].textContent, '空室');
assert.match(h.elements.connection.textContent, /通信失敗/);
const firstFailure = harness();
firstFailure.requests[0].respond(null, 503);
assert.equal(firstFailure.elements['status-text'].textContent, '未取得');
const timeout = harness();
timeout.runTimer(8000, 8000);
assert.equal(timeout.elements['status-text'].textContent, '未取得');
const nextOrganizer = harness();
nextOrganizer.requests[0].respond({...good, current: null, next: {...event, organizer: 'long-address@example.invalid'}});
assert.equal(nextOrganizer.elements['next-organizer'].textContent, '予約者：long-address@example.invalid');
const oldPayload = harness();
oldPayload.requests[0].respond({...good, current: {title:'旧データ', time_text:'09:00 – 10:00', state:'current'}});
assert.equal(oldPayload.elements['current-organizer'].textContent, '予約者：－');
for (const room of ['idoutai', 'bc-first', 'bc-second', 'ds-asagaya', 'ds-tachikawa-1', 'ds-tachikawa-2']) {
    const roomPage = harness('/room/' + room);
    roomPage.requests[0].respond({...good, room_id:room, room_name:room});
    assert.equal(roomPage.elements['room-name'].textContent, room);
    roomPage.runTimer(14000, 15000);
    roomPage.requests[1].respond({...good, room_id:'wrong-room', room_name:'WRONG'});
    assert.equal(roomPage.elements['room-name'].textContent, room);
    assert.equal(roomPage.elements.connection.className, 'connection failed');
}
const adHoc = harness('/room/idoutai');
const free = {...good, status:'free', status_text:'空室', current:null, is_demo:false, ad_hoc_active:false, ad_hoc_can_start:true};
adHoc.requests[0].respond(free);
assert.equal(adHoc.elements['ad-hoc-start'].disabled, false);
adHoc.elements['ad-hoc-start'].onclick();
assert.equal(adHoc.requests[1].method, 'POST');
assert.equal(adHoc.elements['ad-hoc-start'].disabled, true);
adHoc.elements['ad-hoc-start'].onclick();
assert.equal(adHoc.requests.length, 2);
const active = {...free, status:'ad-hoc', status_text:'臨時使用中', ad_hoc_active:true, ad_hoc_can_start:false, ad_hoc_started_epoch_ms:good.server_epoch_ms-480000};
adHoc.requests[1].respond(active);
assert.match(adHoc.elements['status-detail'].textContent, /経過 8分/);
assert.equal(adHoc.elements['ad-hoc-start'].style.display, 'none');
assert.equal(adHoc.elements['ad-hoc-stop'].style.display, 'inline-block');
adHoc.elements['ad-hoc-stop'].onclick();
adHoc.requests[2].onerror();
assert.equal(adHoc.elements['ad-hoc-stop'].disabled, true);
assert.equal(adHoc.elements['status-text'].textContent, '状態未確認');
adHoc.runTimer(14000,15000);
assert.equal(adHoc.requests[3].method, 'GET');
adHoc.requests[3].respond(free);
assert.equal(adHoc.elements['ad-hoc-stop'].style.display, 'none');
adHoc.elements['ad-hoc-start'].onclick();
adHoc.requests[4].respond({...free, ad_hoc_can_start:false, operation_error:'開始不可'},409);
assert.equal(adHoc.elements['ad-hoc-start'].disabled, true);
assert.equal(adHoc.elements.connection.textContent,'開始不可');
const otherRoom = harness('/room/bc-first');
otherRoom.requests[0].respond({...good,room_id:'bc-first'});
assert.equal(otherRoom.elements['ad-hoc-start'].style.display, 'none');
const boundary = harness('/room/idoutai');
boundary.requests[0].respond({...active, ad_hoc_next_reservation_ms:good.server_epoch_ms + 10000});
boundary.runTimer(10050,10050);
assert.equal(boundary.requests[1].method,'GET');
boundary.requests[1].respond({...good, is_demo:false, ad_hoc_active:false, ad_hoc_can_start:false});
assert.equal(boundary.elements['status-text'].textContent,'使用中');
assert.equal(boundary.elements['ad-hoc-stop'].style.display,'none');
for (const room of ['idoutai', 'bc-first', 'bc-second', 'ds-asagaya', 'ds-tachikawa-1', 'ds-tachikawa-2']) {
    const page = harness('/room/' + room);
    page.requests[0].respond({...free, room_id:room});
    assert.equal(page.elements['ad-hoc-start'].style.display, 'inline-block');
    page.elements['ad-hoc-start'].onclick();
    page.requests[1].respond({...active, room_id:room});
    assert.match(page.elements['status-detail'].textContent, /経過 8分/);
    assert.equal(page.elements['ad-hoc-stop'].style.display, 'inline-block');
    page.elements['ad-hoc-stop'].onclick();
    page.requests[2].respond({...free, room_id:room});
    assert.equal(page.elements['ad-hoc-stop'].style.display, 'none');
    page.runTimer(14000,15000);
    page.requests[3].onerror();
    assert.equal(page.elements['ad-hoc-start'].style.display, 'none');
    page.runTimer(14000,15000);
    page.requests[4].respond({...free, room_id:room, status:'unknown', warning:true, ad_hoc_can_start:false});
    assert.equal(page.elements['ad-hoc-start'].style.display, 'none');
    page.runTimer(14000,15000);
    page.requests[5].respond({...active, room_id:room, ad_hoc_next_reservation_ms:good.server_epoch_ms+10000});
    page.runTimer(10050,10050);
    page.requests[6].respond({...good, room_id:room, is_demo:false, ad_hoc_active:false, ad_hoc_can_start:false});
    assert.equal(page.elements['status-text'].textContent, '使用中');
    assert.equal(page.elements['ad-hoc-stop'].style.display, 'none');
}
console.log('PASS: XHR polling, room isolation, ad-hoc POST, double-tap guard, elapsed minutes, failure/recovery and rejected operations.');
