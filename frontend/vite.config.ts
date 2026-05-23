import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRootLogo = path.resolve(__dirname, "..", "F5-logo-F5-rgb.svg");
const publicDir = path.resolve(__dirname, "public");

/** Favicon from repo-root F5-logo-F5-rgb.svg: copy to public/ and inline in index.html. */
function repoRootFaviconPlugin(): Plugin {
  const copyTargets = ["favicon.svg", "F5-logo-F5-rgb.svg"];

  const faviconLinkTag = (): string => {
    const svg = fs.readFileSync(repoRootLogo, "utf8");
    const href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
    return `<link rel="icon" type="image/svg+xml" href="${href}" />`;
  };

  const copyLogoToPublic = () => {
    if (!fs.existsSync(repoRootLogo)) {
      throw new Error(`Missing favicon source: ${repoRootLogo}`);
    }
    fs.mkdirSync(publicDir, { recursive: true });
    for (const name of copyTargets) {
      fs.copyFileSync(repoRootLogo, path.join(publicDir, name));
    }
  };

  return {
    name: "repo-root-favicon",
    buildStart: copyLogoToPublic,
    configureServer() {
      copyLogoToPublic();
    },
    transformIndexHtml(html) {
      const tag = faviconLinkTag();
      if (/<link rel="icon"[^>]*>/i.test(html)) {
        return html.replace(/<link rel="icon"[^>]*>/i, tag);
      }
      return html.replace("</head>", `    ${tag}\n  </head>`);
    },
  };
}

export default defineConfig({
  plugins: [repoRootFaviconPlugin(), react()],
  server: {
    host: true,
    fs: {
      allow: [path.resolve(__dirname, "..")],
    },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
        timeout: 600_000,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
