// Loaded only by the separate active plan. No traffic or cookie values exported.
var URI = Java.type('org.apache.commons.httpclient.URI');
var Sender = Java.type('org.parosproxy.paros.network.HttpSender');
var Files = Java.type('java.nio.file.Files');
var Paths = Java.type('java.nio.file.Paths');
var Lock = Java.type('java.util.concurrent.locks.ReentrantLock');
var HashSet = Java.type('java.util.HashSet');
var lock = new Lock();
var urls = new HashSet();
var stats = {requests: 0, responses: 0, active_requests: 0, active_responses: 0, spider_urls: 0, capped: false, blocked: false, refused: 0};
var host = __HOST__;
var output = __OUTPUT__;
function save() {
    Files.writeString(Paths.get(output), JSON.stringify(stats));
}
function sendingRequest(msg, initiator, helper) {
    lock.lock();
    try {
        var uri = msg.getRequestHeader().getURI();
        var valid = String(uri.getScheme()) === 'https' && String(uri.getHost()) === host &&
            (uri.getPort() === -1 || uri.getPort() === 443) && uri.getUserinfo() === null;
        if (valid && initiator === Sender.SPIDER_INITIATOR) {
            var url = String(uri.toString());
            if (!urls.contains(url) && urls.size() >= 50) {
                valid = false;
                stats.capped = true;
            } else {
                urls.add(url);
                stats.spider_urls = urls.size();
            }
        }
        if (!valid) {
            // The exact-host proxy refuses this sentinel before DNS or a dial.
            msg.getRequestHeader().setURI(new URI('https://radix-refused.invalid/', true));
            stats.refused++;
        } else {
            stats.requests++;
            if (initiator === Sender.ACTIVE_SCANNER_INITIATOR) stats.active_requests++;
        }
        save();
    } finally { lock.unlock(); }
}
function responseReceived(msg, initiator, helper) {
    lock.lock();
    try {
        var status = msg.getResponseHeader().getStatusCode();
        if (String(msg.getRequestHeader().getURI().getHost()) === host && status >= 100) {
            stats.responses++;
            if (initiator === Sender.ACTIVE_SCANNER_INITIATOR) stats.active_responses++;
        }
        var mitigation = msg.getResponseHeader().getHeader('cf-mitigated');
        var body = String(msg.getResponseBody().toString());
        if ([401, 403, 429, 503].indexOf(status) >= 0 || mitigation === 'challenge' ||
            /<title>\s*(just a moment|attention required|access denied|verify you are human)/i.test(body) ||
            /(?:cf-chl-|challenges\.cloudflare\.com|autumbra.{0,100}(?:blocked|challenge))/i.test(body)) stats.blocked = true;
        save();
    } finally { lock.unlock(); }
}
