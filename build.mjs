import { mkdir, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
const root = fileURLToPath(new URL('.', import.meta.url));
await mkdir(join(root, 'dist'), { recursive: true });
for (const file of ['index.html', 'style.css', 'app.js', 'settlement.js']) await copyFile(join(root, file), join(root, 'dist', file));
console.log('前端资源已生成到 dist。当前版本需要 Python 后端，不能只部署静态网页。');
