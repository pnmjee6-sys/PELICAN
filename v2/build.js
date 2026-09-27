import * as esbuild from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';

const isWatch = process.argv.includes('--watch');

// Ensure dist directory exists
if (!fs.existsSync('dist')) {
  fs.mkdirSync('dist', { recursive: true });
}
if (!fs.existsSync('dist/icons')) {
  fs.mkdirSync('dist/icons', { recursive: true });
}

function copyStaticFiles() {
  // Copy manifest
  if (fs.existsSync('src/manifest.json')) {
    fs.copyFileSync('src/manifest.json', 'dist/manifest.json');
  }
  // Copy sidepanel HTML and CSS
  if (fs.existsSync('src/sidepanel/sidepanel.html')) {
    fs.copyFileSync('src/sidepanel/sidepanel.html', 'dist/sidepanel.html');
  }
  if (fs.existsSync('src/sidepanel/styles.css')) {
    fs.copyFileSync('src/sidepanel/styles.css', 'dist/sidepanel.css');
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
  },
  // 2. Content Script (IIFE for isolated content world)
  {
    entryPoints: ['src/content/index.ts'],
    outfile: 'dist/content.js',
    bundle: true,
    format: 'iife',
    target: ['chrome110'],
    sourcemap: true,
  },
  // 3. Side Panel Script
  {
    entryPoints: ['src/sidepanel/index.ts'],
    outfile: 'dist/sidepanel.js',
    bundle: true,
    format: 'esm',
    target: ['chrome110'],
    sourcemap: true,
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
