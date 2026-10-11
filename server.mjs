import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('.', import.meta.url));
const child = spawn('python', ['web_server.py'], { cwd: root, stdio: 'inherit' });
child.on('error', error => { console.error(`启动失败：${error.message}。请运行 python web_server.py。`); process.exitCode = 1; });
child.on('exit', code => { process.exitCode = code || 0; });
process.on('SIGINT', () => child.kill('SIGINT'));
