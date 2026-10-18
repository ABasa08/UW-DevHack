import { readFile } from "node:fs/promises";
import path from "node:path";

// Styles for the manual install; same file the theme app extension ships.
export async function GET() {
  const file = path.join(process.cwd(), "..", "extensions", "perkpilot-theme", "assets", "perkpilot.css");
  return new Response(await readFile(file, "utf8"), {
    headers: { "content-type": "text/css; charset=utf-8", "cache-control": "public, max-age=300", "access-control-allow-origin": "*" },
  });
}
