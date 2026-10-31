import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTypeScript,
  { settings: { next: { rootDir: "frontend/" } }, rules: { "@next/next/no-html-link-for-pages": "off" } },
  globalIgnores(["frontend/.next/**", "frontend/node_modules/**"]),
]);
