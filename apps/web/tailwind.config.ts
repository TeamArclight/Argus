import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}"
  ],
  theme: {
    extend: {
      colors: {
        void: "#020329",
        midnight: "#03045A",
        frost: "#C9D6E8",
        glacier: "#EAF2FA",
        pass: "#00E676",
        high: "#FFB020",
        critical: "#FF4D4D",
        unknown: "#9D7BFF",
        steel: "#5FA8D3",
      }
    }
  },
  plugins: []
};

export default config;
