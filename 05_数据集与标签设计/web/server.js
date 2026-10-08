/*
 * server.js —— 数据集采集台的极简本地静态服务（零依赖，只用 Node 内置模块）
 * 为什么需要它：浏览器的 Web Serial / File System Access 要求"安全上下文"，
 * http://127.0.0.1 算安全上下文，而 file:// 不保证。所以起一个本地小服务。
 *
 * 启动：node server.js        （或双击 7_网页采集.bat）
 * 访问：http://127.0.0.1:8788/
 */
const http = require('http');
const fs   = require('fs');
const path = require('path');

const PORT = 8788;
const HOST = '127.0.0.1';
const ROOT = path.join(__dirname);          // 只服务 web 目录内的文件

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js'  : 'text/javascript; charset=utf-8',
  '.css' : 'text/css; charset=utf-8',
  '.png' : 'image/png',
  '.jpg' : 'image/jpeg',
  '.ico' : 'image/x-icon'
};

const server = http.createServer((req, res) => {
  let rel = decodeURIComponent(req.url.split('?')[0]);
  if (rel === '/' || rel === '') rel = '/index.html';

  // 防目录穿越
  const target = path.normalize(path.join(ROOT, rel));
  if (!target.startsWith(ROOT)) {
    res.writeHead(403); res.end('forbidden'); return;
  }

  fs.readFile(target, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('404 not found: ' + rel);
      return;
    }
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(target).toLowerCase()] || 'application/octet-stream',
      'Cache-Control': 'no-store'
    });
    res.end(data);
  });
});

server.on('error', (e) => {
  if (e.code === 'EADDRINUSE') {
    console.error('端口 ' + PORT + ' 已被占用：可能采集台已经在运行了。');
    console.error('直接打开 http://127.0.0.1:' + PORT + '/ 即可。');
  } else {
    console.error('启动失败：', e.message);
  }
  process.exit(1);
});

server.listen(PORT, HOST, () => {
  console.log('==============================================');
  console.log('  LightGuard 数据集采集台');
  console.log('  请在 Chrome / Edge 打开：');
  console.log('    http://' + HOST + ':' + PORT + '/');
  console.log('  关闭本窗口即停止服务。');
  console.log('==============================================');
});
