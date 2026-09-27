import * as esbuild from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';

const isWatch = process.argv.includes('--watch');
const backendUrl = (process.env.PELICAN_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const backendOrigin = new URL(backendUrl);
const isLocal = backendOrigin.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(backendOrigin.hostname) && backendOrigin.port === '8000';
if ((!isLocal && backendOrigin.protocol !== 'https:') || backendOrigin.origin !== backendUrl) {
  throw new Error('PELICAN_BACKEND_URL must be a localhost:8000 or HTTPS origin, without a path.');
}

// Clean and ensure dist directory exists
if (fs.existsSync('dist') && !isWatch) {
  const entries = fs.readdirSync('dist');
  for (const entry of entries) {
    fs.rmSync(path.join('dist', entry), { recursive: true, force: true });
  }
}
if (!fs.existsSync('dist')) {
  fs.mkdirSync('dist', { recursive: true });
}
if (!fs.existsSync('dist/icons')) {
  fs.mkdirSync('dist/icons', { recursive: true });
}

function copyStaticFiles() {
  // Copy manifest
  if (fs.existsSync('src/manifest.json')) {
    const manifest = JSON.parse(fs.readFileSync('src/manifest.json', 'utf8'));
    if (!isLocal) manifest.host_permissions.push(`${backendOrigin.origin}/*`);
    fs.writeFileSync('dist/manifest.json', JSON.stringify(manifest, null, 2) + '\n');
  }
  fs.writeFileSync('dist/pelican-config.js', `window.PELICAN_BACKEND_URL = ${JSON.stringify(backendUrl)};\n`);
  // Copy sidepanel HTML and CSS
  if (fs.existsSync('src/sidepanel/sidepanel.html')) {
    fs.copyFileSync('src/sidepanel/sidepanel.html', 'dist/sidepanel.html');
  }
  if (fs.existsSync('src/sidepanel/styles.css')) {
    fs.copyFileSync('src/sidepanel/styles.css', 'dist/sidepanel.css');
  }
  // The full Pelican dashboard and first-run import live inside the extension.
  const websiteDir = path.resolve('..', 'website');
  for (const file of ['dashboard.html', 'dashboard.css', 'dashboard.js', 'onboarding.html', 'onboarding.js']) {
    fs.copyFileSync(path.join(websiteDir, file), path.join('dist', file));
  }
  for (const file of ['dashboard.html', 'onboarding.html']) {
    const target = path.join('dist', file);
    const html = fs.readFileSync(target, 'utf8').replace('</head>', '  <script src="./pelican-config.js"></script>\n</head>');
    fs.writeFileSync(target, html);
  }
  fs.mkdirSync('dist/assets', { recursive: true });
  for (const asset of ['instrument-sans-latin.woff2', 'pelican-head.png']) {
    fs.copyFileSync(path.join(websiteDir, 'assets', asset), path.join('dist/assets', asset));
  }
  // Copy content CSS
  if (fs.existsSync('src/content/styles.css')) {
    fs.copyFileSync('src/content/styles.css', 'dist/content.css');
  }
  // Copy icons
  if (fs.existsSync('src/icons')) {
    const icons = fs.readdirSync('src/icons');
    for (const icon of icons) {
      fs.copyFileSync(path.join('src/icons', icon), path.join('dist/icons', icon));
    }
  }
  console.log('✓ Copied static files to dist/');
}

const buildOptions = [
  // 1. Background Service Worker
  {
    entryPoints: ['src/background/index.ts'],
    outfile: 'dist/background.js',
    bundle: true,
    format: 'esm',
    target: ['chrome110'],
    sourcemap: true,
    define: { __PELICAN_BACKEND_URL__: JSON.stringify(backendUrl) },
  },
  // 2. Content Script (IIFE for isolated content world)
  {
    entryPoints: ['src/content/index.ts'],
    outfile: 'dist/content.js',
    bundle: true,
    format: 'iife',
    target: ['chrome110'],
    sourcemap: true,
    define: { __PELICAN_BACKEND_URL__: JSON.stringify(backendUrl) },
  },
  // 3. Side Panel Script
  {
    entryPoints: ['src/sidepanel/index.ts'],
    outfile: 'dist/sidepanel.js',
    bundle: true,
    format: 'esm',
    target: ['chrome110'],
    sourcemap: true,
    define: { __PELICAN_BACKEND_URL__: JSON.stringify(backendUrl) },
  },
];

async function runBuild() {
  copyStaticFiles();
  try {
    for (const opt of buildOptions) {
      if (isWatch) {
        const ctx = await esbuild.context(opt);
        await ctx.watch();
      } else {
        await esbuild.build(opt);
      }
    }
    console.log('✓ Extension build completed successfully!');
  } catch (err) {
    console.error('Build failed:', err);
    process.exit(1);
  }
}

runBuild();
