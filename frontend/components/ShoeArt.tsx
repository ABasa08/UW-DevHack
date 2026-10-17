// Stylized placeholder product illustration (no third-party imagery).
function luminance(hex: string) {
  const n = parseInt(hex.replace("#", ""), 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}

export default function ShoeArt({
  color = "#1f2328",
  accent = "#e07a2f",
  className = "",
  flip = false,
}: {
  color?: string;
  accent?: string;
  className?: string;
  flip?: boolean;
}) {
  const light = luminance(color) > 0.7;
  const line = light ? "#9aa0a9" : "rgba(255,255,255,0.55)";
  const midsole = light ? "#e3e1da" : "#f4f3ef";
  return (
    <svg viewBox="0 0 240 140" className={className} aria-hidden="true" style={flip ? { transform: "scaleX(-1)" } : undefined}>
      <ellipse cx="122" cy="130" rx="96" ry="6" fill="rgba(22,24,29,0.10)" />
      <path d="M22 118 L212 118 C221 118 227 114 228 109 L229 115 C227 123 219 127 209 127 L36 127 C27 127 21 123 20 117 Z" fill="#2a2d33" />
      <path d="M18 104 C18 95 26 91 40 91 L196 91 C214 91 226 97 228 107 C229 115 222 119 210 119 L34 119 C24 119 18 113 18 104 Z" fill={midsole} />
      <path d="M40 104 L200 104" stroke={accent} strokeWidth="3" strokeLinecap="round" opacity="0.9" />
      <path
        d="M28 93 C28 70 38 51 58 43 C72 37 86 39 96 46 C108 54 122 60 140 64 C168 70 196 76 212 86 C220 90 222 92 220 93 Z"
        fill={color}
        stroke={light ? "#c9c6be" : "none"}
        strokeWidth="1.2"
      />
      <path d="M172 72 C192 76 208 82 216 90 L220 93 L178 93 C176 86 174 78 172 72 Z" fill={light ? "#e6e4de" : "rgba(255,255,255,0.10)"} />
      <path d="M66 86 L152 69 L158 75 L74 92 Z" fill={accent} />
      <path d="M56 45 C62 35 80 33 93 42" stroke={line} strokeWidth="2.5" fill="none" strokeLinecap="round" />
      {[0, 1, 2, 3, 4].map((i) => (
        <path key={i} d={`M${104 + i * 11} ${53 + i * 3} l7 9`} stroke={line} strokeWidth="2.4" strokeLinecap="round" />
      ))}
      <circle cx="44" cy="80" r="3" fill={line} />
    </svg>
  );
}
