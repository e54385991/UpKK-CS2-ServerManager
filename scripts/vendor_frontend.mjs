import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const assets = [
  ["node_modules/bootstrap/dist/css/bootstrap.min.css", "static/css/bootstrap.min.css"],
  ["node_modules/bootstrap/dist/js/bootstrap.bundle.min.js", "static/js/bootstrap.bundle.min.js"],
  ["node_modules/bootstrap-icons/font/bootstrap-icons.min.css", "static/css/bootstrap-icons.min.css"],
  ["node_modules/bootstrap-icons/font/fonts/bootstrap-icons.woff", "static/fonts/bootstrap-icons.woff"],
  ["node_modules/bootstrap-icons/font/fonts/bootstrap-icons.woff2", "static/fonts/bootstrap-icons.woff2"],
  ["node_modules/alpinejs/dist/cdn.min.js", "static/js/alpine.min.js"],
  ["node_modules/marked/lib/marked.umd.js", "static/js/marked.umd.js"],
  ["node_modules/dompurify/dist/purify.min.js", "static/js/purify.min.js"],
  ["node_modules/@xterm/xterm/css/xterm.css", "static/xterm/xterm.css"],
  ["node_modules/@xterm/xterm/css/xterm.css", "static/css/xterm.css"],
  ["node_modules/@xterm/xterm/lib/xterm.js", "static/xterm/xterm.js"],
  ["node_modules/@xterm/xterm/lib/xterm.js", "static/js/xterm.min.js"],
  ["node_modules/@xterm/addon-attach/lib/addon-attach.js", "static/xterm/xterm-addon-attach.js"],
  ["node_modules/@xterm/addon-fit/lib/addon-fit.js", "static/xterm/xterm-addon-fit.js"],
  ["node_modules/@xterm/addon-fit/lib/addon-fit.js", "static/js/xterm-addon-fit.min.js"],
  ["node_modules/@xterm/addon-search/lib/addon-search.js", "static/js/xterm-addon-search.min.js"],
  ["node_modules/@xterm/addon-unicode11/lib/addon-unicode11.js", "static/js/xterm-addon-unicode11.min.js"],
  ["node_modules/@xterm/addon-web-links/lib/addon-web-links.js", "static/xterm/xterm-addon-web-links.js"],
  ["node_modules/@xterm/addon-web-links/lib/addon-web-links.js", "static/js/xterm-addon-web-links.min.js"]
];

const manifest = JSON.parse(await readFile(resolve(projectRoot, "package.json"), "utf8"));

for (const [source, target] of assets) {
  const sourcePath = resolve(projectRoot, source);
  const targetPath = resolve(projectRoot, target);
  await mkdir(dirname(targetPath), { recursive: true });
  await copyFile(sourcePath, targetPath);
}

// bootstrap-icons expects its fonts beside the source CSS in font/fonts/.
// The application keeps CSS and fonts in separate static directories, so
// rewrite the relative URLs after copying the pinned upstream stylesheet.
const bootstrapIconsCssPath = resolve(projectRoot, "static/css/bootstrap-icons.min.css");
const bootstrapIconsCss = await readFile(bootstrapIconsCssPath, "utf8");
await writeFile(
  bootstrapIconsCssPath,
  bootstrapIconsCss.replaceAll('url("fonts/', 'url("../fonts/'),
  "utf8"
);

const versions = Object.entries(manifest.dependencies)
  .map(([name, version]) => `${name}@${version}`)
  .join(", ");
console.log(`Vendored ${assets.length} frontend assets from ${versions}`);
