import { readFile } from "node:fs/promises";
import path from "node:path";

// The theme app extension's storefront script, served for the manual Custom Liquid install (shopify/perkpilot-embed.liquid).
// One source of truth: extensions/perkpilot-theme/assets/perkpilot.js.
export async function GET() {
  const file = path.join(process.cwd(), "..", "extensions", "perkpilot-theme", "assets", "perkpilot.js");
  return new Response(await readFile(file, "utf8"), {
    headers: { "content-type": "application/javascript; charset=utf-8", "cache-control": "public, max-age=300", "access-control-allow-origin": "*" },
  });
}
