// Apply the desired Uptime Kuma state (JSON from scripts/uptime-kuma-setup.py) over Uptime Kuma's socket.io API.
// Runs inside the uptime-kuma pod (its node + socket.io-client); copied in and run by scripts/uptime-kuma-setup.sh.
//   node uptime-kuma-apply.js <desired.json> [--dry-run]
// Owns only what carries the desired tag: creates/updates those monitors and groups, deletes tagged ones no longer
// wanted, keeps the Discord notification, the vpn-proxy entry and the status pages in step. Never prints the password
// or webhook.
const fs = require("fs");
const { io } = require("/app/node_modules/socket.io-client");

const want = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const dry = process.argv.includes("--dry-run");
const BASE_URL = "http://127.0.0.1:3001";

const s = io(BASE_URL, { transports: ["websocket"], reconnection: false });
const call = (ev, ...args) => new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`${ev}: no answer`)), 30000);
    s.emit(ev, ...args, (r) => { clearTimeout(t); resolve(r); });
});
const must = async (ev, ...args) => {
    const r = await call(ev, ...args);
    if (r && r.ok === false) throw new Error(`${ev}: ${r.msg}`);
    return r;
};
const next = (ev) => new Promise((resolve) => s.once(ev, resolve));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const log = (...a) => console.log(...a);

let notifications = [], proxies = [];
s.on("notificationList", (d) => { notifications = d; });
s.on("proxyList", (d) => { proxies = d; });
async function monitorList() {
    const p = next("monitorList");
    await must("getMonitorList");
    return Object.values(await p);
}

// what the UI sends for a new monitor, minus what each type sets itself
const DEFAULTS = { accepted_statuscodes: ["200-299"], conditions: [], kafkaProducerBrokers: [], kafkaProducerSaslOptions: {},
    rabbitmqNodes: [], active: true };

async function main() {
    const listed = next("monitorList");
    const login = await call("login", { username: want.user, password: want.password, token: "" });
    if (login.tokenRequired) throw new Error("the account has 2FA on; this script can't log in with it");
    if (!login.ok) throw new Error(`login: ${login.msg}`);
    await listed;
    let changes = 0;
    const act = async (msg, fn) => { changes++; log(`${dry ? "would " : ""}${msg}`); return dry ? null : fn(); };

    // Discord notification (default for new monitors)
    const n = want.notification;
    const curN = notifications.find((x) => x.name === n.name);
    const curCfg = curN ? JSON.parse(curN.config) : null;
    let notifID = curN?.id ?? null;
    if (curCfg && Object.keys(n).every((k) => same(curCfg[k], n[k])) && Boolean(curN.isDefault) === n.isDefault) {
        log(`notification ${n.name}: ok`);
    } else {
        const r = await act(`${curN ? "update" : "add"} notification ${n.name}`, () => must("addNotification", n, notifID));
        if (r) notifID = r.id;
    }
    const notificationIDList = notifID ? { [notifID]: true } : {};

    // the vpn-proxy, matched on host + port (monitors flagged viaProxy use it)
    const p = want.proxy;
    const curP = proxies.find((x) => x.host === p.host && x.port === p.port);
    let proxyID = curP?.id ?? null;
    const bit = (v) => (typeof v === "boolean" ? Number(v) : v);   // SQLite hands booleans back as 0/1
    if (curP && Object.keys(p).every((k) => bit(curP[k]) === bit(p[k]))) {
        log(`proxy ${p.host}:${p.port}: ok`);
    } else {
        const r = await act(`${curP ? "update" : "add"} proxy ${p.host}:${p.port}`, () => must("addProxy", p, proxyID));
        if (r) proxyID = r.id;
    }

    // ownership tag
    let tag = (await must("getTags")).tags.find((t) => t.name === want.tag);
    if (!tag) tag = (await act(`add tag ${want.tag}`, () => must("addTag", { name: want.tag, color: "#2563EB" })))?.tag;
    const owned = (m) => m.tags.some((t) => t.name === want.tag);

    async function ensure(list, name, fields) {
        const cur = list.find((m) => m.name === name);
        if (cur && !owned(cur)) {
            log(`monitor ${name}: exists without the ${want.tag} tag (made in the UI?), left alone`);
            return cur.id;
        }
        if (!cur) {
            const r = await act(`add monitor ${name}`, () => must("add", { ...DEFAULTS, ...fields, name }));
            if (r && tag) await must("addMonitorTag", tag.id, r.monitorID, "");
            return r?.monitorID ?? null;
        }
        const diff = Object.keys(fields).filter((k) =>
            k === "notificationIDList" ? !same(Object.keys(cur[k]).filter((i) => cur[k][i]).map(Number).sort(),
                Object.keys(fields[k]).map(Number).sort()) : !same(cur[k], fields[k]));
        if (!diff.length) { log(`monitor ${name}: ok`); return cur.id; }
        await act(`update monitor ${name} (${diff.join(", ")})`, () => must("editMonitor", { ...cur, ...fields, name, id: cur.id }));
        return cur.id;
    }

    // groups, then the monitors in them
    let list = await monitorList();
    const groupID = {};
    for (const g of want.groups) {
        groupID[g] = await ensure(list, g, { type: "group", parent: null, interval: 60, maxretries: 0, notificationIDList: {} });
    }
    list = await monitorList();
    const monitorID = {};
    for (const [name, m] of Object.entries(want.monitors)) {
        const { group, viaProxy, ...fields } = m;
        monitorID[name] = await ensure(list, name,
            { ...fields, parent: groupID[group] ?? null, proxyId: viaProxy ? proxyID : null, notificationIDList });
    }

    // prune: tagged monitors this run doesn't want (children before their groups)
    list = await monitorList();
    const wanted = new Set([...want.groups, ...Object.keys(want.monitors)]);
    const stale = list.filter((m) => owned(m) && !wanted.has(m.name)).sort((a, b) => (a.type === "group") - (b.type === "group"));
    for (const m of stale) await act(`delete monitor ${m.name}`, () => must("deleteMonitor", m.id, false));

    // status pages: one public group per monitor group they show, in the order above
    const shape = (gs) => gs.map((g) => [g.name, g.monitorList.map((m) => m.id)]);
    for (const sp of want.statusPages) {
        const res = await fetch(`${BASE_URL}/api/status-page/${sp.slug}`);
        const cur = res.ok ? await res.json() : null;
        if (!cur) await act(`add status page /status/${sp.slug}`, () => must("addStatusPage", sp.title, sp.slug));
        const groups = sp.groups.map((g) => ({
            name: g,
            monitorList: Object.entries(want.monitors).filter(([, m]) => m.group === g).map(([name]) => ({ id: monitorID[name] })),
        }));
        if (cur && cur.config.title === sp.title && same(shape(cur.publicGroupList), shape(groups))) {
            log(`status page /status/${sp.slug}: ok`);
            continue;
        }
        await act(`save status page /status/${sp.slug}`, async () => {
            const { config } = await must("getStatusPage", sp.slug);
            return must("saveStatusPage", sp.slug, { ...config, title: sp.title, domainNameList: config.domainNameList ?? [] },
                config.icon, groups);
        });
    }
    log(changes ? `${changes} change(s)${dry ? " (dry run, nothing applied)" : ""}` : "everything already set");
}

s.on("connect", () => main().then(() => { s.close(); process.exit(0); },
    (e) => { console.error(`error: ${e.message}`); s.close(); process.exit(1); }));
s.on("connect_error", (e) => { console.error(`can't reach Uptime Kuma: ${e.message}`); process.exit(1); });
