#!/usr/bin/env python3
"""QSpace 布局中枢
- POST /layout : 上报布局（后改赢：syncAt 更新才接收）
- GET  /layout : 拉取最新
- GET  /status : 健康状态
存储：~/qspace-hub/layout.json（唯一真源）+ archive/YYYYMMDD-HHMMSS-机器.json（保留 20 份）
"""
import json, os, time, datetime, logging, ipaddress, hmac, secrets
from logging.handlers import RotatingFileHandler
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HUB = os.path.expanduser('~/qspace-hub')
LATEST = os.path.join(HUB, 'layout.json')
ARCHIVE = os.path.join(HUB, 'archive')
PORT = 18878
MAX_ARCHIVE = 20
MAX_BODY = 1_000_000          # 请求体上限 1MB，防超大 JSON 打爆内存
MAX_ITEMS = 500
MAX_COORD = 1000         # 单轴坐标上限（SPEC 实际最大 21x9，留余量）
SYNC_FUTURE_TOLERANCE = 300    # 时间戳最多允许超前 5 分钟（防未来时间戳永久锁死中枢）
ALLOWED_NETS = [ipaddress.ip_network(n) for n in
                ("127.0.0.1/32", "192.168.113.0/24", "192.168.191.0/24")]

# 共享密钥：网段白名单挡不住 host 网络模式的 Docker 容器（源 IP = 宿主 IP）
# 必须再叠一层令牌 —— 只有持密钥的客户端才能读写布局
TOKEN_FILE = os.path.join(HUB, 'hub-token.txt')

def load_token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except Exception:
        return ''

def token_ok(provided):
    exp = load_token()
    if not exp:
        return True          # 未配置密钥 → 仅靠网段白名单（首次启动过渡）
    return hmac.compare_digest(provided or '', exp)

def peer_allowed(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in net for net in ALLOWED_NETS)

# 请求方 IP → 机器名(网络)。三台 Mac 各有 LAN / ZeroTier 两个地址，日志据此区分来源。
PEER_MAP = {
    '192.168.113.11': 'MINIPC/LAN', '192.168.191.9': 'MINIPC/ZT',
    '192.168.113.13': 'Air/LAN',    '192.168.191.21': 'Air/ZT',
    '192.168.113.14': 'MBP/LAN',    '192.168.191.26': 'MBP/ZT',
    '192.168.113.17': 'Studio/LAN', '192.168.191.17': 'Studio/ZT',
    '192.168.113.18': 'Studio/LAN2',
    '127.0.0.1': 'localhost',
}

def peer_name(ip):
    return PEER_MAP.get(ip, ip)

os.makedirs(ARCHIVE, exist_ok=True)
logger = logging.getLogger('hub')
logger.setLevel(logging.INFO)
_h = RotatingFileHandler(os.path.join(HUB, 'hub.log'), maxBytes=5 * 1024 * 1024,
                         backupCount=3, encoding='utf-8')
_h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%Y-%m-%d %H:%M:%S'))
logger.addHandler(_h)

def load_latest():
    if os.path.exists(LATEST):
        try:
            with open(LATEST) as f:
                return json.load(f)
        except Exception:
            return None
    return None

def save_latest(data):
    os.makedirs(HUB, exist_ok=True)
    os.makedirs(ARCHIVE, exist_ok=True)
    # layout.json 含桌面文件名 → 收紧权限到 600（仅属主可读）
    tmp = LATEST + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, LATEST)
    ts = datetime.datetime.fromtimestamp(data.get('syncAt', time.time())).strftime('%Y%m%d-%H%M%S')
    src = str(data.get('sourceMachine', 'unknown')).replace('/', '_').replace(' ', '_')
    with open(os.path.join(ARCHIVE, f'{ts}-{src}.json'), 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    files = sorted(f for f in os.listdir(ARCHIVE) if f.endswith('.json'))
    for f in files[:-MAX_ARCHIVE]:
        try:
            os.remove(os.path.join(ARCHIVE, f))
        except Exception:
            pass

class H(BaseHTTPRequestHandler):
    timeout = 10   # 单连接读写超时，防半开连接占用线程
    def _gate(self):
        """双重门禁：①来源网段白名单 ②共享密钥
        （host 网络模式的 Docker 容器源 IP = 宿主 IP，只靠网段白名单挡不住）"""
        ip = self.client_address[0]
        if not peer_allowed(ip):
            logger.warning("REJECT net: %s %s", ip, self.path)
            self._json(403, {'error': 'forbidden'})
            return False
        provided = (self.headers.get('X-Hub-Token') or '').strip()
        if not token_ok(provided):
            logger.warning("REJECT token: %s %s", ip, self.path)
            self._json(401, {'error': 'unauthorized'})
            return False
        return True

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()

    def do_GET(self):
        try:
            self._do_GET_inner()
        except Exception as e:
            logger.exception("Unhandled exception in do_GET: %s", e)
            try:
                self._json(500, {'error': 'internal error'})
            except Exception:
                pass

    def _do_GET_inner(self):
        if not self._gate():
            return
        if self.path == '/layout':
            cur = load_latest()
            peer = peer_name(self.client_address[0])
            if cur:
                logger.info("GET /layout  %s  <- hub[%s syncAt=%s items=%d]",
                            peer, cur.get('sourceMachine'), cur.get('syncAt'), len(cur.get('items', [])))
            else:
                logger.info("GET /layout  %s  <- hub(empty)", peer)
            self._json(200, cur or {})
        elif self.path == '/status':
            cur = load_latest()
            n_arch = len([f for f in os.listdir(ARCHIVE) if f.endswith('.json')]) if os.path.isdir(ARCHIVE) else 0
            self._json(200, {
                'ok': True,
                'hasLayout': cur is not None,
                'syncAt': cur.get('syncAt') if cur else None,
                'sourceMachine': cur.get('sourceMachine') if cur else None,
                'iconCount': len(cur.get('items', [])) if cur else 0,
                'archiveCount': n_arch,
                'serverTime': int(time.time()),
            })
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self):
        try:
            self._do_POST_inner()
        except Exception as e:
            # 任何未预料异常都不能打断连接（fuzz 实测 items=12345 → len() TypeError → HTTP 000）
            logger.exception("Unhandled exception in do_POST: %s", e)
            try:
                self._json(500, {'error': 'internal error'})
            except Exception:
                pass

    def _do_POST_inner(self):
        if not self._gate():
            return
        if self.path != '/layout':
            self._json(404, {'error': 'not found'})
            return
        n = int(self.headers.get('Content-Length', 0) or 0)
        if n > MAX_BODY:
            logger.warning("POST /layout rejected: body too large (%d bytes) from %s", n, self.client_address[0])
            self._json(413, {'error': 'payload too large'})
            return
        try:
            data = json.loads(self.rfile.read(n))
        except Exception as e:
            logger.warning("POST /layout bad json: %s", e)
            self._json(400, {'error': str(e)})
            return
        if not isinstance(data, dict):
            logger.warning("POST /layout rejected: body is not an object")
            self._json(400, {'error': 'body must be a JSON object'})
            return
        at = int(data.get('syncAt', 0) or 0)
        src = str(data.get('sourceMachine', '?'))[:100]
        now = int(time.time())
        if at <= 0:
            logger.warning("POST /layout from %s rejected: missing syncAt", src)
            self._json(400, {'error': 'missing syncAt'})
            return
        if at > now + SYNC_FUTURE_TOLERANCE:
            logger.warning("POST /layout from %s rejected: syncAt too far in future (%d vs now %d)", src, at, now)
            self._json(400, {'error': 'syncAt too far in future'})
            return
        # ---- items 结构校验（必须在 len() 之前，否则 items=12345 会 TypeError 打断连接）----
        items = data.get('items')
        if not isinstance(items, list) or not items:
            logger.warning("POST /layout from %s rejected: bad items type", src)
            self._json(400, {'error': 'items must be a non-empty array'})
            return
        if len(items) > MAX_ITEMS:
            logger.warning("POST /layout from %s rejected: too many items (%d > %d)", src, len(items), MAX_ITEMS)
            self._json(400, {'error': f'too many items (max {MAX_ITEMS})'})
            return
        def _bad_item(it):
            if not isinstance(it, dict): return True
            if not isinstance(it.get('name'), str): return True
            c, r = it.get('col'), it.get('row')
            # bool 是 int 子类，必须特判；坐标必须落在合理范围（1..MAX_COORD）
            if not isinstance(c, int) or isinstance(c, bool): return True
            if not isinstance(r, int) or isinstance(r, bool): return True
            if not (1 <= c <= MAX_COORD) or not (1 <= r <= MAX_COORD): return True
            return False
        bad = [it for it in items if _bad_item(it)]
        if bad:
            logger.warning("POST /layout from %s rejected: %d malformed items (first: %r)",
                           src, len(bad), bad[0])
            self._json(400, {'error': f'{len(bad)} malformed items (need name:str, col:int 1-{MAX_COORD}, row:int 1-{MAX_COORD})'})
            return
        cnt = len(items)
        cur = load_latest()
        cur_at = int(cur.get('syncAt', 0) or 0) if cur else 0
        if at <= cur_at:
            logger.info("POST /layout  %s @ %s  syncAt=%d items=%d  -> REJECTED (hub has %d from %s)",
                        src, peer_name(self.client_address[0]), at, cnt, cur_at, cur.get('sourceMachine') if cur else '-')
            self._json(200, {'accepted': False, 'reason': 'older', 'latestSyncAt': cur_at})
            return
        try:
            save_latest(data)
        except Exception as e:
            logger.error("POST /layout  %s @ %s  save FAILED: %s", src, peer_name(self.client_address[0]), e)
            self._json(500, {'error': 'save failed'})
            return
        logger.info("POST /layout  %s @ %s  syncAt=%d items=%d  -> ACCEPTED (fp=%s)",
                    src, peer_name(self.client_address[0]), at, cnt, str(data.get('layoutFingerprint', ''))[:8])
        self._json(200, {'accepted': True, 'syncAt': at})

    def log_message(self, *args):
        pass

if __name__ == '__main__':
    os.makedirs(ARCHIVE, exist_ok=True)
    logger.info("hub starting on 0.0.0.0:%d (dir=%s)", PORT, HUB)
    print(f'qspace-hub listening on 0.0.0.0:{PORT} -> {LATEST}', flush=True)
    # 多线程 + 超时：单线程时一个挂起的 POST 会阻塞所有机器的轮询
    srv = ThreadingHTTPServer(('0.0.0.0', PORT), H)
    srv.daemon_threads = True
    logger.info("hub listening (threaded, timeout=%ss)", 10)
    srv.serve_forever()
