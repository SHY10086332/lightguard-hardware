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
  let rel;
  try {
    rel = decodeURIComponent(req.url.split('?')[0]);
  } catch (e) {
    // 形如 "/%" 的非法编码会让 decodeURIComponent 抛 URIError；
    // 不接住会直接把 Node 进程打崩（采集中断），这里按 400 处理。
    res.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('400 bad request');
    return;
  }
  if (rel === '/' || rel === '') rel = '/index.html';
  // 反斜杠统一成正斜杠，避免 Windows 上 "..\\" 绕过
  rel = rel.replace(/\\/g, '/');

  // 防目录穿越：前缀比较必须带路径分隔符，否则同级的 web_evil / web2 等兄弟目录会被放行
  const target = path.normalize(path.join(ROOT, rel));
  const within = target === ROOT || target.startsWith(ROOT + path.sep);
  if (!within) {
    res.writeHead(403); res.end('forbidden'); return;
  }

  fs.readFile(target, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('404 not found');
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
